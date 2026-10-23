[English](CONFIG.md) | **Русский**

# config.json

[О проекте](../README.ru.md) · [Сборка конфига sing-box](CONFIG_ASSEMBLY.ru.md) · [Админка](USAGE.ru.md) · [Модель безопасности](SECURITY_MODEL.ru.md)

Все настройки nodes-tester хранятся в одном файле `config.json`: подписки, группы и их проверки,
тестер, рейтинг, переключение, конвейер, админка и уведомления. Формат устроен как `config.json`
самого sing-box:

- разделы верхнего уровня — по сущностям; группы и подписки — массивы объектов с `tag`;
- длительности — строками: `"250ms"`, `"5s"`, `"1h30m"`, `"30d"`;
- скорость и объём — числами с единицей в имени поля: `_mbps`, `_mb`;
- значения по умолчанию в файл писать не нужно; админка показывает их серым в пустых полях форм;
- неизвестное поле — ошибка с путём: `groups[1].require.gemeni: неизвестное поле (есть: anthropic, country, …)`.

В пакете OpenWrt файл лежит в `/etc/nodes-tester/config.json` с правами `0600`: в нём URL подписок
и токены. Рядом — `config.schema.json` для подсказок в редакторе (`"$schema": "config.schema.json"`).
Каталог данных (`nodes.json`, `whnodes.json`, `stats.db`, `raw/`) задаётся `data_dir` в
`/etc/config/nodes-tester`, по умолчанию `/opt/nodes-tester`.

Изменения вступают в силу после перезапуска тестера: админка на OpenWrt делает его сама после
сохранения, вручную — `/etc/init.d/nodes-tester restart`. Соединения пользователей при этом не рвутся. Подписки, фильтры и структура групп меняют `nodes.json` и попадают в
sing-box только со следующей сборкой конвейера.

## Минимальный конфиг

```json
{
  "api": { "secret": "<секрет API-сервиса sing-box>" },
  "subscriptions": [
    { "tag": "PROV", "url": "https://<подписка>" }
  ],
  "groups": [
    { "tag": "all" }
  ],
  "admin": { "token": "<токен админки>" }
}
```

## Полный пример

Значения по умолчанию показаны, чтобы были видны все поля.

```json
{
  "$schema": "config.schema.json",

  "api": { "url": "http://127.0.0.1:9090", "secret": "<секрет>", "timeout": "5s" },

  "subscriptions": [
    { "tag": "LUNA", "url": "https://<…>", "user_agent": "clashmeta" },
    { "tag": "STAR", "url": "https://<…>", "send_device": true, "exclude_names": ["promo"] },
    { "tag": "HAPP", "url": "happ://crypt5/<…>", "happ_headers": { "User-Agent": "Happ/3.0" } },
    { "tag": "AWG", "type": "folder", "path": "awg", "format": "awg", "ext": ".conf" },
    { "tag": "OLD", "url": "https://<…>", "enabled": false },
    { "tag": "WH", "url": "https://<…>", "branch": "clients" },
    { "tag": "my", "type": "inline", "outbounds": [ { "type": "vless", "server": "<…>", "server_port": 443 } ] }
  ],

  "tests": {
    "connectivity": { "service": "cloudflare" },
    "latency": { "url": "https://speed.cloudflare.com/__down?bytes=0" },
    "jitter": { "samples": 5, "interval": "200ms" },
    "download": { "url": "https://speed.cloudflare.com/__down?bytes=5000000", "duration": "20s", "window_mb": 2 },
    "reachability": { "urls": ["https://www.google.com/generate_204"], "timeout": "8s", "workers": 8 },
    "for_region": {
      "ru": {
        "latency": { "url": "https://<адрес в РФ>" },
        "download": { "url": "https://<адрес в РФ>" }
      }
    }
  },

  "checks": {
    "country": { "ttl": "24h" },
    "ip_type": { "ttl": "24h", "url": "http://ip-api.com/batch", "via_tester": true },
    "geo_consistent": { "ttl": "24h" },
    "gemini": { "ttl": "24h", "attempts": 3, "timeout": "20s" },
    "openai": { "ttl": "24h", "attempts": 3, "timeout": "15s" },
    "anthropic": { "ttl": "24h", "attempts": 3, "timeout": "15s" },
    "heavy": { "ttl": "6h", "url": "https://speed.cloudflare.com/__down?bytes=20000000", "duration": "30s", "window_mb": 2 }
  },

  "regions": { "asia": ["jp", "sg", "kr"] },

  "groups": [
    { "tag": "eu", "require": { "country": { "regions": ["eu"] } } },
    { "tag": "us", "require": { "country": { "countries": ["us"] } } },
    {
      "tag": "ai",
      "strict": true,
      "in_global": false,
      "require": {
        "country": { "exclude_countries": ["ru"] },
        "geo_consistent": {},
        "gemini": {}, "openai": {}, "anthropic": {}
      },
      "prefer": { "ip_type": { "kinds": ["residential", "mobile"] } }
    }
  ],

  "rating": {
    "alpha": 0.3,
    "flap_lambda": 0.5,
    "availability": { "floor": 0.7, "full": 0.9 },
    "thresholds": {
      "latency": { "good": "150ms", "bad": "1.5s" },
      "jitter": { "good": "15ms", "bad": "250ms" },
      "loss_gate_percent": 50,
      "download_mbps": { "min": 2, "target": 25 },
      "throttle": { "bad": 0.3, "good": 0.9 },
      "variation": { "good": 0.1, "bad": 0.8 }
    },
    "weights": { "reliability": 0.35, "consistency": 0.2, "throttle": 0.15, "jitter": 0.1, "latency": 0.1, "throughput": 0.1 }
  },

  "selection": {
    "reserve": 3,
    "quality_margin": 15,
    "confirm_cycles": 2,
    "comfort_floor": 50,
    "tier_floor": 75,
    "cooldown": "5m",
    "host_penalty": "3h",
    "rotation": {
      "enabled": true, "interval": "6h", "jitter": "15m",
      "min_dwell": "30m", "spread": "10m", "min_score": 55, "avoid_recent": 2,
      "balance": { "window": "24h", "provider": 1.0, "country": 1.0, "protocol": 0.3 }
    },
    "quarantine": { "after_skips": 32, "duration": "72h", "low_score": 0 },
    "stability": { "enabled": true, "window": "7d", "weight": 1.0, "prior_hours": 2, "provider_min_hours": 5, "min": 0.3 }
  },

  "tester": {
    "socks": "socks5h://127.0.0.1:2080",
    "selector": "nodes-tester",
    "switch_delay": "1s",
    "request_timeout": "10s",
    "host_gap": "2m"
  },

  "monitor": {
    "interval": "10s",
    "reassert_drift": true,
    "silence": "5m",
    "silence_floor_mb": 0,
    "probe": { "url": "", "size_mb": 1, "min_mbps": 1, "timeout": "8s" },
    "fails": 2,
    "retry": "30s",
    "error_burst": 5,
    "silence_min_conns": 1,
    "errors": {
      "enabled": true, "window": "60s", "min": 3, "prev_min": 2,
      "classes": ["connect", "timeout", "reset"], "fresh": "10m", "fresh_min": 2, "probe_confirm": false
    },
    "outage": {
      "enabled": true, "interval": "1m",
      "targets": ["77.88.8.8:443", "vk.com:443", "www.gosuslugi.ru:443"], "fail_quorum": 2, "timeout": "3s",
      "switches": 3, "switches_window": "10m", "nodes": 3, "recheck": "30s", "recover": 3,
      "mark": 8228, "interface": ""
    }
  },

  "pipeline": {
    "timeout": "30m",
    "fetch": { "timeout": "2m", "retries": 3, "proxy": "", "keep_stale": "48h", "min_ratio": 0.5, "happ_keys_url": "" },
    "router": {
      "enabled": true,
      "at": ["03:00"],
      "exclude_types": ["shadowsocksr"],
      "domain_resolver": "bootstrap",
      "selector": { "interrupt_exist_connections": false },
      "urltest": { "interval": "5m", "tolerance": 100, "idle_timeout": "5m", "interrupt_exist_connections": true }
    },
    "clients": {
      "enabled": true,
      "at": ["00:30", "06:30", "12:30", "18:30"],
      "exclude_types": ["shadowsocksr"],
      "exclude_protocols": ["xhttp", "wg"],
      "domain_resolver": "dns-whitelist"
    }
  },

  "storage": { "path": "results/stats.db", "retention": "30d", "traffic": { "enabled": true, "poll": "5s", "flush": "1m" },
               "vacuum": { "every": "30d", "at": "04:00" } },

  "admin": { "enabled": true, "listen": "192.168.1.1", "port": 8088, "token": "<токен>", "public_read": true, "refresh": "10s" },

  "notify": {
    "name": "<имя роутера>",
    "telegram": { "token": "<токен бота>", "chat_id": "<chat_id>" },
    "events": ["switch", "failsafe", "rollback", "pipeline", "subscription", "expiry", "singbox"],
    "switch_reasons": ["emergency", "emergency-stuck"],
    "expiry_warning": "3d"
  },

  "singbox": {
    "init": "/etc/init.d/sing-box",
    "killswitch": { "init": "/etc/init.d/killswitch", "table": "killswitch" },
    "watch": "5s",
    "down_alert": "1m"
  }
}
```

## api

API-сервис sing-box 1.14 (`services[]` с `"type": "api"` в конфиге sing-box). Через него тестер
переключает селекторы и читает счётчики трафика.

| Поле | Тип | По умолчанию | Описание |
|---|---|---|---|
| `url` | строка | `http://127.0.0.1:9090` | Адрес API |
| `secret` | строка | — | Секрет API |
| `timeout` | длительность | `5s` | Тайм-аут запроса |

## subscriptions

Источники нод. Сборка (`nodes_config`) берёт все ноды подписок своей ветви; что из них активировать,
решает тестер по проверкам групп. Страна в названии ноды у провайдера не используется.

| Поле | Тип | По умолчанию | Описание |
|---|---|---|---|
| `tag` | строка | — | Имя провайдера, уникальное; первая часть тегов нод |
| `enabled` | bool | `true` | Использовать подписку |
| `branch` | `router` · `clients` · `both` | `router` | Ветвь сборки: `nodes.json` роутера, `whnodes.json` клиентов или обе |
| `type` | `url` · `file` · `folder` · `inline` · `amnezia` | `url` | Вид источника |
| `url` | строка | — | URL подписки, share-ссылка, `happ://crypt…` или ссылка `vpn://` Amnezia Premium |
| `file` | строка | — | `file`: путь к файлу подписки (относительно `config.json`) |
| `path`, `format`, `ext` | строки | — | `folder`: каталог, формат (`awg`) и расширение файлов (`.conf`); `amnezia`: каталог файлов стран |
| `gateway_key` | строка | — | `amnezia`: файл с публичным ключом шлюза Amnezia (PEM), относительно `config.json` |
| `gateways` | список строк | `["http://gw.amnezia.org/"]` | `amnezia`: адреса шлюза, следующий — при сетевом сбое |
| `relay` | bool | `true` | `amnezia`: брать только конфиги через relay Amnezia; выданный без него сразу отзывается |
| `proxy` | строка | `pipeline.fetch.proxy` | `amnezia`: прокси запросов к шлюзу; через зарубежный выход шлюз выдаёт конфиги без relay |
| `slots` | целое | все слоты | `amnezia`: сколько файлов стран держать |
| `min_rating` | число | `40` | `amnezia`: страна, чья нода держит рейтинг ниже, заменяется |
| `exclude_countries` | список строк | `["ru"]` | `amnezia`: коды стран, которые не выпускать |
| `outbounds` | массив | — | `inline`: свои outbound-ы sing-box; тег им задаёт сборка |
| `user_agent` | строка | Safari | User-Agent HTTP-загрузки: `curl`, `clashmeta` или своя строка |
| `happ_headers` | объект | набор Happ | `happ://`: заголовки поверх набора по умолчанию |
| `send_device` | bool | `false` | URL-подписка: отправлять HWID и описание устройства |
| `exclude_names` | список строк | — | Не брать ноды, в имени которых есть подстрока |

HWID роутера хранится в `device.json`, ключи `happ://crypt…` — в `happ-keys.json`; оба лежат рядом с
`config.json` и создаются автоматически.

### Amnezia Premium

Подписка с `type: "amnezia"` берёт ссылку `vpn://` Amnezia Premium и держит в `path` файлы AmneziaWG по странам
(`<код страны>.conf`, права 0600). Файлы выпускает тестер через шлюз Amnezia (`gateways`) раз в 3 часа.

Запросу из России шлюз выдаёт конфиг через relay Amnezia (в файле — адрес relay и маскирующий пакет `I1`), запросу
из-за границы — прямой на сервер страны, а такой путь из России режется. Поэтому к шлюзу нужно ходить с роутера
напрямую, не через зарубежный прокси или ноду; при включённом `relay` конфиг без relay сразу отзывается, это ошибка
сверки. Открытый HTTP к `gw.amnezia.org` провайдеры режут по заголовку `Host`, поэтому у адреса `http://` с именем
тестер сам резолвит имя и шлёт запрос на IP. Сверка:

- страна, чья нода 6 часов держит рейтинг ниже `min_rating`, считается заблокированной: её файл отзывается
  и удаляется, страна не выпускается, пока есть другие;
- свободные места — не больше `slots` и свободных слотов подписки — занимают случайные, ещё не выпускавшиеся страны;
  когда таких не осталось, снова пробуются заблокированные, начиная с самой давней блокировки;
- файл, чей сервер обновился после выдачи, выпускается заново.

Если ниже порога сразу все страны с рейтингом, дело не в странах (режут протокол или упала сеть) — замены
не делаются. Страны, выпущенные в другом месте (файла в `path` нет), не трогаются: новый выпуск сломал бы тот файл.
Память о пробованных и заблокированных странах — `state.json` в `path`; удалите страну из `blocked`, чтобы
разрешить её снова. Новые файлы попадают в sing-box со следующим опросом подписок и сборкой роутера.

Ключ шлюза в проект не входит. Это блок `-----BEGIN PUBLIC KEY-----` в официальном приложении AmneziaVPN:
например, в `lib/<abi>/libAmneziaVPN_<abi>.so` APK для Android. В приложении два ключа; рабочий шлюз
принимает, на другой отвечает HTTP 500.

## tests

Замеры рейтинга: `connectivity`, `latency`, `jitter`, `download`, `reachability`. Тестер
проверяет ноду, когда истёк срок годности её замера (см.
[сроки годности](../nodes_tester/README.ru.md#сроки-годности)). Поля задают сроки, адреса и тайм-ауты;
не заданные берутся из кода теста, `timeout` — из `tester.request_timeout`.

У каждого теста `ttl` (по умолчанию `3h`) и `ttl_jitter` (по умолчанию десятая часть `ttl`):
результат живёт от `ttl − ttl_jitter` до `ttl`. Срок задаётся для теста целиком, в `for_region` его нет.

| Тест | Поля |
|---|---|
| `connectivity` | `service` (`cloudflare` по умолчанию) — сервис выходного IP и страны; `timeout` |
| `latency` | `url`, `timeout` |
| `jitter` | `url`, `samples`, `interval`, `timeout` |
| `download` | `url`, `duration`, `window_mb`, `timeout` |
| `reachability` | `urls`, `timeout`, `workers` |

`for_region` — поля тестов для нод с выходом в регионе (по измеренной стране). Блок пишется либо
внутри теста (`"latency": {"for_region": {"ru": {"url": "…"}}}`), либо общим разделом
`tests.for_region.<регион>.<тест>`.

## checks

Проверки с вердиктом «да / нет / неизвестно» и сроком годности `ttl`; `ttl_jitter` (по
умолчанию десятая часть `ttl`) укорачивает срок на случайную долю, чтобы вердикты нод не истекали
разом. Здесь — значения по умолчанию; группа ссылается на проверку по имени в `require` или
`prefer`.

| Проверка | Что проверяет | Поля и умолчания |
|---|---|---|
| `country` | страна выхода по замеру соединения | `ttl` `24h` |
| `ip_type` | тип выходного IP по ip-api: `residential`, `mobile`, `hosting`, `proxy` | `ttl` `24h`, `url`, `timeout` `10s`, `via_tester` `true` |
| `geo_consistent` | страна IP по базе совпадает с измеренной | `ttl` `24h` |
| `gemini`, `openai`, `anthropic` | доступ к AI-сервису | `ttl` `24h`, `attempts` `3`, `timeout` |
| `upstream` | проходит ли через ноду вышестоящий прокси — outbound sing-box типа `socks` с `detour` на группу | `ttl` `6h`, `outbound` (обязателен, если проверка есть в группе), `url` `https://www.cloudflare.com/cdn-cgi/trace`, `attempts` `2`, `timeout` `15s` |
| `heavy` | тяжёлая закачка кандидатов резерва; медленнее `min_mbps` — провал | `ttl` `6h`, `url`, `duration` `30s`, `window_mb` `2`, `min_mbps` `5` |
| `whitelist` | пропустит ли ноду режим белых списков: IP сервера в IP-списке и SNI в списке доменов (домен и поддомены); без TLS — достаточно IP | `ttl` `24h`, `ip_list`, `sni_list` (текстовые списки [ru-mobile-whitelist](https://github.com/ancored/ru-mobile-whitelist)), `dns` `dns-ru`, `timeout` `10s` |

`whitelist` — локальная проверка (тестер раз в 5 минут смотрит, у каких нод истёк вердикт), трафик через ноду не идёт. Домен сервера ноды резолвится
DoH-запросом к серверу `dns`: тегу DNS-сервера `type: https` из работающего конфига sing-box
(`singbox.config`), её группе `type: group` (серверы `https` по очереди) или URL; не нашлось — Cloudflare
`1.1.1.1` (Quad9 требует HTTP/2). Системный резолвер роутера не используется: он отправил бы имя ноды
провайдеру.

`upstream` нужен, когда выход идёт через группу цепочкой — например, SOCKS со статическим IP
(`"detour": "ai-auto-out"`). Нода может быть жива, но не пускать соединения на порт прокси, или сам прокси
не принимает её IP; тогда выход мёртв, хотя остальные проверки ноду пропускают. Резерв через ноду проходит
SOCKS5-рукопожатие с сервером `outbound` (логин и пароль — из работающего конфига sing-box) и запрашивает
`url`; нода проходит, если удались все попытки.

Дорогие проверки (AI, `heavy`) тестер запускает только для нод, уже прошедших дешёвые: gate и
обязательную страну группы.

## regions

Именованные списки стран для условий групп и `for_region` тестов. Встроенные: `eu` (страны Европы),
`us`, `ru` и `other` — всё, что не попало в `eu`, `us` и `ru`. Встроенный регион можно
переопределить.

```json
"regions": { "us": ["us", "ca"], "asia": ["jp", "sg", "kr"] }
```

## groups

Группа — селектор sing-box `{tag}-auto-out`. В нём все ноды ветви `router`, запасной выход и, при
`direct_fallback`, `direct-out`. Активную ноду выбирает тестер.

| Поле | Тип | По умолчанию | Описание |
|---|---|---|---|
| `tag` | строка | — | Латиница в нижнем регистре, цифры, `_`; не `global`, `nodes`, `failsafe` |
| `strict` | bool | `false` | Без кандидатов — `block-out` (трафик группы блокируется), а не `failsafe-out` |
| `direct_fallback` | bool | `false` | Добавить `direct-out`; строгая группа без кандидатов уходит на него вместо `block-out` |
| `in_global` | bool | `true` | Входит в `global-auto-out` |
| `reserve` | число | `selection.reserve` | Кандидатов в резерве |
| `tier_floor` | число | `selection.tier_floor` | Рейтинг ниже — ярус после всех остальных нод группы |
| `require` | объект | — | Обязательные проверки: без свежего успеха нода не кандидат |
| `prefer` | объект | — | Желательные проверки: успехи поднимают ноду в очереди выбора |
| `monitor.errors` | объект | `monitor.errors` | Своё правило аварии по ошибкам соединений для группы; незаданные поля — из `monitor.errors` |

Проверка внутри группы — объект, обычно пустой (`"gemini": {}`). В нём можно задать свой `ttl`, а
у `country` и `ip_type` — условия:

- `country`: `countries`, `regions`, `exclude_countries`, `exclude_regions` (ISO-коды и имена регионов);
- `ip_type`: `kinds` — допустимые типы IP.

Кандидат группы проходит gate, имеет рейтинг больше нуля, не стоит на паузе или в карантине, а все
проверки `require` у него свежие и успешные. Среди кандидатов первыми идут ноды с успехами по
`prefer`, затем — по рейтингу.

Нет кандидатов: нестрогая группа переключается на общий `failsafe-out` (urltest по всем нодам), строгая —
на `block-out`, а при `direct_fallback` — на `direct-out`. `failsafe-out` монитор наблюдает как ноду:
провал зонда `monitor.fails` раз подряд — нестрогая группа с `direct_fallback` уходит на `direct-out`, а
когда `failsafe-out` снова отвечает, возвращается на него. `block-out` берётся из `base.json` sing-box
(`{"type": "block", "tag": "block-out"}`). Без раздела `groups` создаётся одна группа `all`.

## rating

Сглаживание, пороги и веса рейтинга нод.

| Поле | По умолчанию | Описание |
|---|---|---|
| `alpha` | `0.3` | Сглаживание EWMA |
| `flap_lambda` | `0.5` | Штраф за флапы |
| `availability.floor`, `availability.full` | `0.7`, `0.9` | Доступность, при которой и ниже которой рейтинг уменьшается вдвое (множитель 0,5), и доступность полного рейтинга (множитель 1) |
| `thresholds.latency` | `150ms` / `1.5s` | TTFB: `good`, `bad` |
| `thresholds.jitter` | `15ms` / `250ms` | Джиттер: `good`, `bad` |
| `thresholds.loss_gate_percent` | `50` | Потери, при которых нода не проходит gate, % |
| `thresholds.download_mbps` | `2` / `25` | Скорость: `min`, `target` |
| `thresholds.throttle` | `0.3` / `0.9` | Троттлинг: `bad`, `good` |
| `thresholds.variation` | `0.1` / `0.8` | Разброс: `good`, `bad` |
| `weights` | см. пример | Веса составляющих: `reliability`, `consistency`, `throttle`, `jitter`, `latency`, `throughput` |

## selection

Выбор активной ноды в группах.

| Поле | По умолчанию | Описание |
|---|---|---|
| `reserve` | `3` | Кандидатов в резерве группы (проверенных обязательными проверками и `heavy`) |
| `quality_margin` | `15` | На сколько баллов кандидат должен быть лучше для замены по качеству |
| `reserve_margin` | `quality_margin` | На сколько баллов нода должна быть лучше худшего кандидата резерва (того же яруса), чтобы занять его место |
| `confirm_cycles` | `2` | Сколько замеров подряд (активной ноды или лидера резерва) подтверждать замену по качеству |
| `comfort_floor` | `50` | Выше этого рейтинга активную ноду по качеству не меняют |
| `tier_floor` | `75` | Рейтинг ниже — ярус после всех остальных нод группы: слабая нода, прошедшая приоритетные проверки (`prefer`), не обходит сильные, которые их не прошли |
| `cooldown` | `5m` | Пауза между переключениями группы |
| `host_penalty` | `3h` | Сколько не выбирать хост ноды, которую монитор снял аварией; `0` — без штрафа |
| `rotation.enabled` | `true` | Плановая ротация |
| `rotation.interval`, `rotation.jitter` | `6h`, `15m` | Срок ротации и случайный сдвиг; у каждой группы свой срок от её переключения, группы не переключаются разом. Чаще всего нода отказывает в первые минуты после выбора, а не от долгой работы, поэтому каждая ротация — лишний риск |
| `rotation.min_dwell` | `30m` | Минимальное время на ноде |
| `rotation.spread` | `10m` | Плановые ротации разных групп не ближе этого: группа, чей срок наступил, ждёт, пока с ротации другой группы не пройдёт столько (каждая ротация рвёт соединения своей группы) |
| `rotation.min_score` | `55` | Минимальный рейтинг кандидата ротации |
| `rotation.avoid_recent` | `2` | Не возвращаться к последним N нодам |
| `rotation.balance` | выключена | Балансировка по трафику; раздел включает её: `window` `24h`, сила осей `provider` `1.0`, `country` `1.0`, `protocol` `0.3` |
| `quarantine.after_skips` | `32` | После n-го провала подряд нода не проверяется 2^(n-1) сроков `connectivity`; дорос пропуск до N — карантин |
| `quarantine.duration` | `72h` | Длительность карантина |
| `quarantine.low_score` | `0` | Больше нуля — карантин и для слабых нод с рейтингом ниже |
| `stability.enabled` | `true` | Надёжность в боевой работе: рейтинг умножается на `1 / (1 + weight × аварий в час)` |
| `stability.window` | `7d` | За какой срок считаются аварии и часы работы активной |
| `stability.weight` | `1.0` | Вес частоты аварий |
| `stability.prior_hours` | `2` | Частота ноды сглаживается к частоте её поставщика, будто нода проработала столько часов как поставщик; у ноды без истории — частота поставщика |
| `stability.provider_min_hours` | `5` | Поставщик с меньшей работой — средняя частота всех |
| `stability.min` | `0.3` | Множитель не ниже |

Авария засчитывается ноде, которая была активной; аварии после ручного карантина из админки не в счёт. Админка показывает рейтинг уже с этим множителем.

## tester

| Поле | По умолчанию | Описание |
|---|---|---|
| `socks` | `socks5h://127.0.0.1:2080` | SOCKS inbound тестера; `socks5h` — имя резолвит выбранная нода |
| `selector` | `nodes-tester` | Селектор тестера в sing-box |
| `switch_delay` | `1s` | Пауза после переключения селектора тестера |
| `request_timeout` | `10s` | Тайм-аут запроса теста |
| `host_gap` | `2m` | Не чаще одного обращения к хосту с другим портом или протоколом |

## monitor

Надзор за активными нодами. Ошибки соединений через группу читаются из файла `log.output` работающего конфига
sing-box (`singbox.config`); лог не в файле — эти сигналы не работают. Для прокси-ноды такая ошибка — почти всегда
сама нода: не соединиться с ней (`connect`), не ответила вовремя (`timeout`), оборвала соединение (`reset`).

- **Авария по ошибкам** (`errors`): за последнее окно `window` не меньше `min` ошибок и за окно до него не меньше
  `prev_min` — нода заменяется сразу, без зонда: зонд одним соединением часто проходит через ноду, которая отбрасывает
  часть соединений. Первые `fresh` после выбора ноды хватает `fresh_min` ошибок за окно: чаще всего нода отказывает
  сразу после выбора. Ошибки соединений, начатых до смены ноды (время строки минус возраст соединения), относятся к
  прежней ноде и не считаются.
- **Трафик**: идёт боевой трафик — нода жива; за тик `error_burst` ошибок — зонд сразу; тихо дольше `silence` —
  внеплановый зонд, но только если за это время через ноду были соединения или ошибки (`silence_min_conns`): группой
  не пользуются — проверять нечего. Провал — повтор через `retry`, `fails` провалов подряд — авария и замена.
- **Общая авария** (`outage`): отказывает не нода, а общее звено (WAN, DNS нод). Тогда замеры, резерв, ротации и замены
  останавливаются, пока проверки не пройдут `recover` раз подряд, — ноды не получают паузы и штрафы за чужую поломку.
  Прямой выход проверяется раз в `interval` рукопожатием TCP (для имени — и TLS) с `targets` мимо sing-box (его `mark`,
  интерфейс WAN `interface`); не ответили `fail_quorum` целей — авария WAN. `switches` аварийных замен за
  `switches_window` запускают проверку сразу: прямой выход и до `nodes` нод разных поставщиков; прямой жив, а эти ноды
  все молчат — авария нод. Cloudflare и Google целями не берутся: в РФ их часто режут.

| Поле | По умолчанию | Описание |
|---|---|---|
| `interval` | `10s` | Тик монитора |
| `reassert_drift` | `true` | Вернуть выбор селекторов, если его сменили в обход тестера |
| `silence` | `5m` | Тишина дольше — зонд |
| `silence_floor_mb` | `0` | МБ за окно, которые считаются трафиком; `0` — `probe.min_mbps × silence` |
| `probe.url`, `probe.size_mb` | speed.cloudflare.com, `1` | Адрес и объём зонда |
| `probe.min_mbps`, `probe.timeout` | `1`, `8s` | Минимальная скорость зонда и тайм-аут |
| `fails` | `2` | Провалов подряд до аварии |
| `retry` | `30s` | Повтор зонда после провала |
| `error_burst` | `5` | Ошибок соединений через группу за тик — зонд сразу; `0` — выключено |
| `silence_min_conns` | `1` | Меньше соединений за тишину — группой не пользуются, зонда нет; `0` — зонд всегда |
| `errors.enabled` | `true` | Авария по ошибкам соединений |
| `errors.window` | `60s` | Окно счёта ошибок |
| `errors.min`, `errors.prev_min` | `3`, `2` | Ошибок за последнее окно и за окно до него; `prev_min` `0` — без второго условия |
| `errors.classes` | `connect`, `timeout`, `reset` | Какие классы ошибок считать; ещё `canceled` — соединение отменило приложение |
| `errors.fresh`, `errors.fresh_min` | `10m`, `2` | Первые `fresh` после выбора ноды для аварии хватает `fresh_min` ошибок за окно, без условия о предыдущем окне |
| `errors.probe_confirm` | `false` | Перед заменой один зонд; прошёл — нода остаётся |
| `outage.enabled` | `true` | Сторож общей аварии |
| `outage.interval` | `1m` | Фоновая проверка прямого выхода; `0` — только по сигналу |
| `outage.targets` | `77.88.8.8:443`, `vk.com:443`, `www.gosuslugi.ru:443` | Цели проверки прямого выхода, `хост:порт` |
| `outage.fail_quorum`, `outage.timeout` | `2`, `3s` | Сколько целей должны не ответить для аварии WAN; тайм-аут одной цели |
| `outage.switches`, `outage.switches_window` | `3`, `10m` | Аварийных замен за окно — проверка сразу |
| `outage.nodes` | `3` | Сколько нод разных поставщиков проверить по сигналу; `0` — не проверять |
| `outage.recheck`, `outage.recover` | `30s`, `3` | Период проверки во время аварии и удачных проверок подряд для её конца |
| `outage.mark`, `outage.interface` | `8228` (`0x2024`), — | `routing_mark` sing-box и интерфейс WAN для проверок мимо sing-box; интерфейс не задан — `route.default_interface` конфига sing-box |

## pipeline

Конвейер: загрузка подписок (`nodes_fetch`), сборка (`nodes_config`), проверка `sing-box check` и
применение. Админка запускает его по расписанию `at` или вручную.

| Поле | По умолчанию | Описание |
|---|---|---|
| `timeout` | `30m` | Предел длительности запуска |
| `fetch.timeout` | `2m` | Тайм-аут загрузки подписки |
| `fetch.retries` | `3` | Повторов HTTP |
| `fetch.proxy` | — | Прокси загрузки, например `socks5://127.0.0.1:2080` |
| `fetch.keep_stale` | `48h` | Сколько держать прошлые ноды упавшей подписки |
| `fetch.min_ratio` | `0.5` | Нод стало меньше этой доли — `raw` не перезаписывается |
| `fetch.happ_keys_url` | встроенный | Источник ключей `happ://crypt…` |
| `fetch.every` | `3h` | Опрашивать подписки роутера отдельно от сборки: только `raw/router.json`, без сборки и применения; отсчёт — от прошлого опроса или сборки роутера. `0s` — подписки опрашивает только сборка |

Ветви `router` (результат — `nodes.json` для sing-box роутера) и `clients` (`whnodes.json` для
клиентских конфигов) настраиваются одинаково:

| Поле | По умолчанию | Описание |
|---|---|---|
| `enabled` | `true` | Сборка по расписанию |
| `at` | `["03:00"]` у `router`, пусто у `clients` | Время запуска, `ЧЧ:ММ` |
| `exclude_types` | — | Не брать outbound-ы этих типов, например `shadowsocksr` |
| `exclude_protocols` | — | Не брать протоколы и транспорты: `reality`, `xhttp`, `wg`… |
| `domain_resolver` | `bootstrap` | Тег DNS-сервера для адресов нод |
| `selector` | — | Поля selector-ов групп sing-box, например `interrupt_exist_connections` |
| `urltest` | — | Поля urltest `failsafe-out`: `url`, `interval`, `tolerance`, `idle_timeout` |
| `rebuild` | см. ниже | Только `router`: пересборка по деградации |

Пересборка по деградации (`pipeline.router.rebuild`). Перезапуск sing-box рвёт соединения, поэтому
тестер просит конвейер собрать конфиг заново, только когда выполнены оба условия: группа без
кандидатов дольше `degraded_for` (`15m`) или кандидатов меньше резерва + 1 дольше
`few_candidates_for` (`1h`); и в последнем опросе подписок есть новые доступы, а из мёртвых нод
конфига ушли из подписок не меньше `gone_share` (`0.3`). Не чаще `min_interval` (`6h`) после прошлой
сборки роутера и только при `pipeline.router.enabled`. Свежий опрос даёт `fetch.every`; при `0s`
сравнение идёт с raw последней сборки и пересборка не срабатывает. Каждая пересборка — в журнале и уведомлении `pipeline`.

Фильтры ветви защищают sing-box от outbound-ов, которые он не примет; голые vless и vmess (без TLS и Reality)
отбрасываются всегда. Клиентская ветвь собирает одну группу `global-auto-out` со всеми нодами и
`failsafe-out`.

## storage

| Поле | По умолчанию | Описание |
|---|---|---|
| `path` | `results/stats.db` | Файл SQLite; относительный — от каталога данных |
| `retention` | `30d` | Сколько хранить историю |
| `traffic.enabled` | `true` | Собирать пользовательский трафик по нодам |
| `traffic.poll`, `traffic.flush` | `5s`, `1m` | Опрос счётчиков и запись в БД |
| `vacuum.every` | `30d` | Переупаковывать базу (`VACUUM`) раз в этот срок; `0s` — только вручную из админки |
| `vacuum.at` | `04:00` | Время запуска (местное) после истечения срока |

Итог последнего `VACUUM` — размер базы до и после — и кнопка «Выполнить сейчас» — в админке, раздел
«Настройки». Отсчёт срока начинается с первого запуска тестера.

## admin

Веб-админка, встроенная в процесс тестера.

| Поле | По умолчанию | Описание |
|---|---|---|
| `enabled` | `true` | Включить админку |
| `listen` | `0.0.0.0` | Адрес прослушивания; наружу не выставлять |
| `port` | `8088` | Порт |
| `token` | — | Токен администратора; пустой — правка и управление выключены |
| `public_read` | `true` | Просмотр без токена |
| `refresh` | `10s` | Обновление данных в браузере |

## notify

| Поле | По умолчанию | Описание |
|---|---|---|
| `enabled` | `true` | Отправлять уведомления |
| `name` | — | Подпись роутера в начале сообщения |
| `telegram.token`, `telegram.chat_id` | — | Бот и получатель |
| `webhook.url` | — | POST JSON `{event, text, ts}` |
| `proxy` | — | Прокси отправки |
| `via_tester` | `true` | Не прошло напрямую — повторить через SOCKS тестера |
| `events` | все | `switch`, `failsafe`, `rollback`, `pipeline`, `subscription`, `expiry`, `singbox`, `outage` (общая авария: об аварии нод — сразу, об аварии WAN — по её окончании) |
| `switch_reasons` | `emergency`, `emergency-stuck` | Причины переключений, о которых сообщать: ещё `init`, `rotation`, `quality`, `manual` |
| `expiry_warning` | `3d` | Предупреждать о конце подписки заранее |
| `dedupe` | `1h` | Не повторять одинаковое сообщение чаще |
| `max_per_hour` | `20` | Предел сообщений в час |
| `timeout` | `10s` | Тайм-аут отправки |

## singbox

Остановка и запуск sing-box и служба kill switch из админки (OpenWrt).

| Поле | По умолчанию | Описание |
|---|---|---|
| `init` | `/etc/init.d/sing-box` | init-скрипт sing-box |
| `config` | `/etc/sing-box/config.json` | Работающий конфиг: из него `checks.whitelist.dns` берёт DNS-сервер по тегу, `checks.upstream.outbound` — сервер и учётные данные прокси |
| `killswitch.init`, `killswitch.table` | `/etc/init.d/killswitch`, `killswitch` | init-скрипт и таблица nftables `inet` kill switch |
| `watch` | `5s` | Период сторожа |
| `down_alert` | `1m` | Уведомить, если sing-box лежит дольше |

## Секреты

`api.secret`, `admin.token`, `notify.telegram.token` и `subscriptions[].url` дают доступ к sing-box,
админке, боту и подпискам. Перед публикацией фрагмента конфига замените их.
