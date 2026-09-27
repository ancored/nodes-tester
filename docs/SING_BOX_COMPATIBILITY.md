# Совместимость с sing-box

[О проекте](../README.md) · [`nodes_fetch`](../nodes_fetch/README.md) · [`nodes_config`](../nodes_config/README.md)

## Главное

Успешный разбор подписки не означает, что полученную ноду примет установленное ядро.
`nodes_fetch` умеет разбирать XHTTP и AmneziaWG (AWG), но эти расширения рассчитаны на
[Leadaxe/sing-box-lx](https://github.com/Leadaxe/sing-box-lx). Обычный sing-box их не
поддерживает.

Если на роутере установлен обычный sing-box, исключите XHTTP и AWG до создания
`nodes.json`. Иначе итоговый конфиг может не пройти `sing-box check`, а sing-box не примет
его при перезапуске.

| Нода | Что создаёт парсер | Требуемое ядро |
|---|---|---|
| VLESS или Trojan с XHTTP | `transport.type: "xhttp"` | `sing-box-lx` |
| AmneziaWG из `.conf` | endpoint `type: "wireguard"` с полями AWG | `sing-box-lx` |
| Обычный WireGuard | endpoint `type: "wireguard"` без полей AWG | зависит от версии обычного sing-box |

Версию и возможности ядра всё равно нужно проверять командой `sing-box version` и на
итоговом объединённом конфиге. Эта таблица описывает расширения, которые добавлены именно
для `sing-box-lx`.

## Где возникает несовместимость

Конвейер разделён на стадии:

```text
providers.json
    -> nodes_fetch
    -> raw_nodes.json
    -> nodes_config
    -> nodes.json
    -> sing-box merge и sing-box check
```

`nodes_fetch` сохраняет всё, что смог разобрать. Он не знает, какое ядро установлено на
роутере, и не применяет фильтры `groups_params.json`. Благодаря этому один raw-файл можно
использовать для разных целей.

Совместимость с целевым ядром задаёт `nodes_config`. Он вычисляет дескриптор протокола:

- XHTTP содержит токен `xhttp`, например `vless|xhttp|tls`;
- WireGuard и AmneziaWG содержат токен `wg`.

Затем `filters.exclude_protocols` удаляет ноды с указанными токенами до переименования,
группировки и записи `nodes.json`.

## Обычный sing-box: безопасная настройка

В `config-main/groups_params.json` добавьте:

```json
{
  "filters": {
    "exclude_protocols": ["xhttp", "wg"]
  }
}
```

Остальные секции `groups_params.json` можно оставить рядом. Например:

```json
{
  "filters": {
    "exclude_types": ["shadowsocksr"],
    "exclude_protocols": ["xhttp", "wg"],
    "exclude_countries": [],
    "exclude_names": {}
  },
  "rename": {
    "labels": {},
    "domain_resolver_tag": "bootstrap"
  },
  "selector": {
    "interrupt_exist_connections": true
  },
  "urltest": {
    "interval": "5m",
    "tolerance": 100,
    "idle_timeout": "5m",
    "interrupt_exist_connections": true
  },
  "emit": {
    "nodes_tester": true,
    "global_failsafe": false
  }
}
```

Токен `wg` исключает все WireGuard endpoints, включая обычный WireGuard. Если обычный
WireGuard нужен, а AWG нет, не добавляйте папку AWG в `providers.json`. Для источника
вида `type: "folder", format: "awg"` это самый прозрачный способ не допустить AWG в raw.
После этого решите отдельно, поддерживает ли ваша версия ядра обычные WireGuard endpoints.

Фильтр должен находиться в `groups_params.json`. Поле `exclude_node_protocols` в
`providers.json` относится к старому формату и используется только совместимой обёрткой
`python -m subscribe` или миграцией конфигов. Сам `nodes_fetch` его игнорирует.

## sing-box-lx: оставить XHTTP и AWG

Если установлен `sing-box-lx`, не добавляйте `xhttp` и `wg` в
`filters.exclude_protocols`. Для AWG добавьте в `providers.json` источник папки:

```json
{
  "type": "folder",
  "path": "awg",
  "format": "awg",
  "ext": ".conf",
  "tag": "AWG"
}
```

Каждый `.conf` становится отдельным endpoint. Имя файла из двух букв используется как
подсказка страны: `de.conf` даёт `de`, `fi.conf` даёт `fi`. DNS из файла не переносится,
потому что DNS настраивается централизованно в sing-box.

XHTTP извлекается из VLESS и Trojan share-ссылок с `type=xhttp`. Параметры транспорта и
`extra` переводятся в поля `sing-box-lx`; camelCase ключи XMUX нормализуются в snake_case.

Версии форка и формата конфигурации должны совпадать. Перед применением всегда проверяйте
кандидат тем бинарником, который реально запускается на роутере.

## Проверка перед применением

Для пакетной установки на OpenWrt:

```sh
nodes-tester pipeline router --dry-run
```

Этот режим скачивает подписки и собирает кандидат, затем выполняет `sing-box merge` и
`sing-box check`, но не заменяет рабочий конфиг и не перезапускает sing-box. При ошибке
прочитайте вывод `sing-box check`: неизвестный транспорт XHTTP или неизвестные поля AWG
означают, что фильтр не включён либо используется неподходящее ядро.

Для ручного запуска можно отдельно проверить сборку:

```sh
python3 -m nodes_fetch -p config/providers.json -o data/raw/main.json --json
python3 -m nodes_config --raw data/raw/main.json \
  --groups config/groups_params.json \
  -o data/nodes.json --check --json
```

`nodes_config --check` проверяет только сборку фрагмента. Окончательную совместимость
подтверждает `sing-box check` после объединения фрагмента с базовым конфигом.
