#Requires -RunAsAdministrator
<#
.SYNOPSIS
  Kill switch для sing-box на Windows: исходящий трафик только через TUN и сам sing-box.
.DESCRIPTION
  on     — включить (sing-box должен быть запущен, TUN поднят);
  off    — выключить и вернуть правила, отключённые при включении;
  status — состояние и поиск утечек (разрешающие правила, появившиеся после включения).
  Правила брандмауэра постоянные: после перезагрузки до старта sing-box сети нет.
  Ядро, TUN-адаптер и локальная подсеть определяются на месте; адресов и путей настраивать не нужно.
  Страница входа в гостевой Wi-Fi (captive portal) при включённом kill switch не откроется: off, вход, on.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File killswitch.ps1 on
#>
param(
    [Parameter(Position = 0)][ValidateSet('on', 'off', 'status')][string]$Action = 'status',
    [string]$SingBox,   # путь к ядру; по умолчанию — служба sing-box-daemon или запущенный sing-box
    [string]$TunAlias,  # имя TUN-адаптера; по умолчанию — найденный автоматически
    [switch]$NoLan      # не разрешать прямой доступ в локальную подсеть
)
$ErrorActionPreference = 'Stop'
$Group = 'sing-box kill switch'
$StateDir = Join-Path $env:ProgramData 'singbox-killswitch'
$DisabledFile = Join-Path $StateDir 'disabled-rules.txt'
$BackupFile = Join-Path $StateDir 'firewall-before-killswitch.wfw'

# Ядро, которое держит туннель. В GUI sing-box для Windows это служба sing-box-daemon;
# sing-box.exe из Program Files — только интерфейс (Electron), сам в сеть мимо TUN не ходит.
function Find-SingBox {
    if ($SingBox) { return (Resolve-Path $SingBox).Path }
    $svc = Get-CimInstance Win32_Service -Filter "Name='sing-box-daemon'" -ErrorAction SilentlyContinue
    if ($svc -and $svc.PathName -match '^"?([^"]+?\.exe)') { return $Matches[1] }
    $p = Get-Process -Name 'sing-box*' -ErrorAction SilentlyContinue | Where-Object Path | Select-Object -First 1
    if (-not $p) { throw 'sing-box не запущен: запусти ядро или укажи -SingBox <путь к ядру>' }
    return $p.Path
}

function Find-Tun {
    if ($TunAlias) { return (Get-NetAdapter -Name $TunAlias).Name }
    $found = @(Get-NetAdapter | Where-Object {
            $_.Status -eq 'Up' -and ($_.InterfaceDescription -match 'Wintun|sing-box' -or $_.Name -match '^(tun|sing)')
        })
    if ($found.Count -ne 1) {
        Get-NetAdapter | Format-Table Name, InterfaceDescription, Status -AutoSize | Out-Host
        throw "TUN-адаптер не определён однозначно (найдено: $($found.Count)); укажи -TunAlias <Name из списка>"
    }
    return $found[0].Name
}

# Разрешающие исходящие правила вне нашей группы: через них трафик шёл бы мимо туннеля.
# ActiveStore — итоговая политика (локальная + групповая и др.), только для чтения.
function Get-LeakRules {
    Get-NetFirewallRule -PolicyStore ActiveStore -Direction Outbound -Action Allow -Enabled True |
        Where-Object Group -ne $Group
}

# Те же правила локальной политики: менять их можно только в PersistentStore.
function Get-LocalLeakRules {
    Get-NetFirewallRule -PolicyStore PersistentStore -Direction Outbound -Action Allow -Enabled True |
        Where-Object Group -ne $Group
}

function Enable-KillSwitch {
    $exe = Find-SingBox
    $tun = Find-Tun
    New-Item -ItemType Directory -Force $StateDir | Out-Null
    if (-not (Test-Path $BackupFile)) {
        netsh advfirewall export "$BackupFile" | Out-Null
        Write-Host "Резервная копия политики: $BackupFile"
    }

    Get-NetFirewallRule -Group $Group -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    $common = @{ Group = $Group; Direction = 'Outbound'; Action = 'Allow'; Profile = 'Any' }
    New-NetFirewallRule @common -DisplayName 'KS: sing-box' -Program $exe | Out-Null
    New-NetFirewallRule @common -DisplayName 'KS: TUN' -InterfaceAlias $tun | Out-Null
    New-NetFirewallRule @common -DisplayName 'KS: DHCP' -Protocol UDP -LocalPort 68 -RemotePort 67 | Out-Null
    if (-not $NoLan) {
        # Локальная подсеть (принтер, NAS) — кроме DNS: имена резолвит только sing-box.
        foreach ($proto in 'TCP', 'UDP') {
            New-NetFirewallRule @common -DisplayName "KS: LAN $proto" -Protocol $proto `
                -RemoteAddress LocalSubnet -RemotePort '1-52', '54-65535' | Out-Null
        }
        New-NetFirewallRule @common -DisplayName 'KS: LAN ICMP' -Protocol ICMPv4 -RemoteAddress LocalSubnet | Out-Null
    }

    $local = @(Get-LocalLeakRules)
    if ($local) {
        # Список — до отключения: если скрипт упадёт посередине, off всё равно вернёт отключённое.
        $names = @($local.Name)
        if (Test-Path $DisabledFile) { $names += Get-Content $DisabledFile }
        $names | Sort-Object -Unique | Set-Content $DisabledFile -Encoding UTF8
        $failed = @()
        foreach ($rule in $local) {
            try { $rule | Disable-NetFirewallRule -ErrorAction Stop }
            catch { $failed += $rule }
        }
        Write-Host "Отключено разрешающих исходящих правил: $($local.Count - $failed.Count) (список — $DisabledFile)"
        if ($failed) {
            Write-Warning "Не удалось отключить:"
            $failed | Format-Table DisplayName, Name -AutoSize | Out-Host
        }
    }
    $foreign = @(Get-LeakRules)
    if ($foreign) {
        Write-Warning "Остались разрешающие правила не из локальной политики (групповая политика и т. п.):"
        $foreign | Format-Table DisplayName, PolicyStoreSourceType -AutoSize | Out-Host
    }

    Set-NetFirewallProfile -All -Enabled True -DefaultOutboundAction Block
    Write-Host "Kill switch включён: sing-box = $exe, TUN = $tun"
    Show-Status
}

function Disable-KillSwitch {
    Set-NetFirewallProfile -All -DefaultOutboundAction NotConfigured
    if (Test-Path $DisabledFile) {
        $restored = 0
        $failed = @()
        foreach ($name in Get-Content $DisabledFile) {
            $r = Get-NetFirewallRule -PolicyStore PersistentStore -Name $name -ErrorAction SilentlyContinue
            if (-not $r) { continue }
            try { $r | Enable-NetFirewallRule -ErrorAction Stop; $restored++ }
            catch { $failed += $name }
        }
        Write-Host "Возвращено правил: $restored"
        if ($failed) {
            $failed | Set-Content $DisabledFile -Encoding UTF8
            Write-Warning "Не удалось вернуть $($failed.Count) правил, список оставлен в $DisabledFile"
        } else {
            Remove-Item $DisabledFile
        }
    }
    Get-NetFirewallRule -Group $Group -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    Write-Host 'Kill switch выключен.'
}

function Show-Status {
    Get-NetFirewallProfile | Format-Table Name, Enabled, DefaultOutboundAction -AutoSize | Out-Host
    $rules = @(Get-NetFirewallRule -Group $Group -ErrorAction SilentlyContinue)
    if (-not $rules) { Write-Host 'Правил kill switch нет.'; return }

    $exeRule = $rules | Where-Object DisplayName -eq 'KS: sing-box'
    $exe = ($exeRule | Get-NetFirewallApplicationFilter).Program
    $tunRule = $rules | Where-Object DisplayName -eq 'KS: TUN'
    $tun = ($tunRule | Get-NetFirewallInterfaceFilter).InterfaceAlias
    $current = try { Find-SingBox } catch { $null }
    $running = Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $exe }
    $adapter = Get-NetAdapter -Name $tun -ErrorAction SilentlyContinue

    Write-Host "Правило sing-box: $exe"
    if ($current -and $current -ne $exe) { Write-Warning "ядро sing-box сейчас $current — выполни: killswitch.ps1 on" }
    elseif (-not $running) { Write-Warning 'ядро sing-box не запущено — сети нет (так и задумано).' }
    Write-Host "Правило TUN: $tun"
    if (-not $adapter) { Write-Warning "адаптера $tun нет (sing-box остановлен или сменилось имя TUN)" }
    elseif ($adapter.Status -ne 'Up') { Write-Warning "адаптер ${tun}: $($adapter.Status)" }

    $leaks = @(Get-LeakRules)
    if ($leaks) {
        Write-Warning "Разрешающие исходящие правила мимо туннеля ($($leaks.Count)); выполни: killswitch.ps1 on"
        $leaks | Format-Table DisplayName, PolicyStoreSourceType -AutoSize | Out-Host
    } else {
        Write-Host 'Посторонних разрешающих исходящих правил нет.'
    }
}

switch ($Action) {
    'on' { Enable-KillSwitch }
    'off' { Disable-KillSwitch }
    'status' { Show-Status }
}
