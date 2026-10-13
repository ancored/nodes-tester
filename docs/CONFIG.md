# config.json

[О проекте](../README.md) · [Сборка конфига sing-box](CONFIG_ASSEMBLY.md) · [Админка](USAGE.md) · [Модель безопасности](SECURITY_MODEL.md)

Все настройки nodes-tester хранятся в одном файле `config.json`: подписки, группы и их проверки,
тестер, рейтинг, переключение, конвейер, админка и уведомления. Формат устроен как `config.json`
самого sing-box:

- разделы верхнего уровня — по сущностям; группы и подписки — массивы объектов с `tag`;
- длительности — строками: `"250ms"`, `"5s"`, `"1h30m"`, `"30d"`;
- скорость и объём — числами с единицей в имени поля: `_mbps`, `_mb`;
- значения по умолчанию в файл писать не нужно;
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
    "cooldown": "5m",
    "rotation": {
      "enabled": true, "interval": "3h", "jitter": "15m", "align": true,
      "min_dwell": "30m", "min_score": 55, "avoid_recent": 2,
      "balance": { "window": "24h", "provider": 1.0, "country": 1.0, "protocol": 0.3 }
    },
    "quarantine": { "after_skips": 32, "duration": "72h", "low_score": 0 }
  },

  "tester": {
    "socks": "socks5h://127.0.0.1:2080",
    "selector": "nodes-tester",
    "follow_rotation": true,
    "pause": "0s",
    "switch_delay": "1s",
    "request_timeout": "10s",
    "host_gap": "2m"
  },

  "monitor": {
    "interval": "20s",
    "reassert_drift": true,
    "silence": "5m",
    "silence_floor_mb": 0,
    "probe": { "url": "", "size_mb": 1, "min_mbps": 1, "timeout": "8s" },
    "fails": 2
  },

  "pipeline": {
    "timeout": "30m",
    "fetch": { "timeout": "2m", "retries": 3, "proxy": "", "keep_stale": "48h", "min_ratio": 0.5, "happ_keys_url": "" },
    "router": {
      "enabled": true,
      "at": ["03:00"],
      "exclude_types": ["shadowsocksr"],
      "domain_resolver": "bootstrap",
      "selector": { "interrupt_exist_connections": true },
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

  "storage": { "path": "results/stats.db", "retention": "30d", "traffic": { "enabled": true, "poll": "5s", "flush": "1m" } },

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
| `type` | `url` · `file` · `folder` · `inline` | `url` | Вид источника |
| `url` | строка | — | URL подписки, share-ссылка или `happ://crypt…` |
| `file` | строка | — | `file`: путь к файлу подписки (относительно `config.json`) |
| `path`, `format`, `ext` | строки | — | `folder`: каталог, формат (`awg`) и расширение файлов (`.conf`) |
| `outbounds` | массив | — | `inline`: свои outbound-ы sing-box; тег им задаёт сборка |
| `user_agent` | строка | Safari | User-Agent HTTP-загрузки: `curl`, `clashmeta` или своя строка |
| `happ_headers` | объект | набор Happ | `happ://`: заголовки поверх набора по умолчанию |
| `send_device` | bool | `false` | URL-подписка: отправлять HWID и описание устройства |
| `exclude_names` | список строк | — | Не брать ноды, в имени которых есть подстрока |

HWID роутера хранится в `device.json`, ключи `happ://crypt…` — в `happ-keys.json`; оба лежат рядом с
`config.json` и создаются автоматически.

## tests

Замеры рейтинга при каждом проходе ноды: `connectivity`, `latency`, `jitter`, `download`,
`reachability`. Поля задают адреса и тайм-ауты; не заданные берутся из кода теста, `timeout` —
из `tester.request_timeout`.

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

Проверки с вердиктом «да / нет / неизвестно» и сроком годности `ttl`. Здесь — значения по
умолчанию; группа ссылается на проверку по имени в `require` или `prefer`.

| Проверка | Что проверяет | Поля и умолчания |
|---|---|---|
| `country` | страна выхода по замеру соединения (каждый проход ноды) | `ttl` `24h` |
| `ip_type` | тип выходного IP по ip-api: `residential`, `mobile`, `hosting`, `proxy` | `ttl` `24h`, `url`, `timeout` `10s`, `via_tester` `true` |
| `geo_consistent` | страна IP по базе совпадает с измеренной | `ttl` `24h` |
| `gemini`, `openai`, `anthropic` | доступ к AI-сервису | `ttl` `24h`, `attempts` `3`, `timeout` |
| `heavy` | тяжёлая закачка кандидатов резерва | `ttl` `6h`, `url`, `duration` `30s`, `window_mb` `2` |
| `whitelist` | вхождение сервера в белые списки | пока не выполняется |

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
| `require` | объект | — | Обязательные проверки: без свежего успеха нода не кандидат |
| `prefer` | объект | — | Желательные проверки: успехи поднимают ноду в очереди выбора |

Проверка внутри группы — объект, обычно пустой (`"gemini": {}`). В нём можно задать свой `ttl`, а
у `country` и `ip_type` — условия:

- `country`: `countries`, `regions`, `exclude_countries`, `exclude_regions` (ISO-коды и имена регионов);
- `ip_type`: `kinds` — допустимые типы IP.

Кандидат группы проходит gate, имеет рейтинг больше нуля, не стоит на паузе или в карантине, а все
проверки `require` у него свежие и успешные. Среди кандидатов первыми идут ноды с успехами по
`prefer`, затем — по рейтингу.

Нет кандидатов: нестрогая группа переключается на общий `failsafe-out` (urltest по всем нодам), строгая —
на `block-out`, а при `direct_fallback` — на `direct-out`. `block-out` берётся из `base.json` sing-box
(`{"type": "block", "tag": "block-out"}`). Без раздела `groups` создаётся одна группа `all`.

## rating

Сглаживание, пороги и веса рейтинга нод.

| Поле | По умолчанию | Описание |
|---|---|---|
| `alpha` | `0.3` | Сглаживание EWMA |
| `flap_lambda` | `0.5` | Штраф за флапы |
| `availability.floor`, `availability.full` | `0.7`, `0.9` | Доступность, ниже которой рейтинг 0, и доступность полного рейтинга |
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
| `confirm_cycles` | `2` | Сколько проходов подряд подтверждать замену по качеству |
| `comfort_floor` | `50` | Выше этого рейтинга активную ноду по качеству не меняют |
| `cooldown` | `5m` | Пауза между переключениями группы |
| `rotation.enabled` | `true` | Плановая ротация |
| `rotation.interval`, `rotation.jitter` | `3h`, `15m` | Срок ротации и случайный сдвиг |
| `rotation.align` | `true` | Сроки всех групп на общей сетке: один проход обслуживает все группы |
| `rotation.min_dwell` | `30m` | Минимальное время на ноде |
| `rotation.min_score` | `55` | Минимальный рейтинг кандидата ротации |
| `rotation.avoid_recent` | `2` | Не возвращаться к последним N нодам |
| `rotation.balance` | выключена | Балансировка по трафику; раздел включает её: `window` `24h`, сила осей `provider` `1.0`, `country` `1.0`, `protocol` `0.3` |
| `quarantine.after_skips` | `32` | После n-го провала подряд нода пропускает 2^(n-1) проходов; дорос пропуск до N — карантин |
| `quarantine.duration` | `72h` | Длительность карантина |
| `quarantine.low_score` | `0` | Больше нуля — карантин и для слабых нод с рейтингом ниже |

## tester

| Поле | По умолчанию | Описание |
|---|---|---|
| `socks` | `socks5h://127.0.0.1:2080` | SOCKS inbound тестера; `socks5h` — имя резолвит выбранная нода |
| `selector` | `nodes-tester` | Селектор тестера в sing-box |
| `follow_rotation` | `true` | Проход к сроку ротации, аварии между проходами ловит монитор; `false` — проходы подряд |
| `pause` | `0s` | Пауза между проходами при `follow_rotation: false` |
| `switch_delay` | `1s` | Пауза после переключения селектора тестера |
| `request_timeout` | `10s` | Тайм-аут запроса теста |
| `host_gap` | `2m` | Не чаще одного обращения к хосту с другим портом или протоколом |

## monitor

Надзор за активными нодами: идёт боевой трафик — нода жива; тихо дольше `silence` — внеплановый
зонд; провал `fails` раз подряд — авария и замена.

| Поле | По умолчанию | Описание |
|---|---|---|
| `interval` | `20s` | Тик монитора |
| `reassert_drift` | `true` | Вернуть выбор селекторов, если его сменили в обход тестера |
| `silence` | `5m` | Тишина дольше — зонд |
| `silence_floor_mb` | `0` | МБ за окно, которые считаются трафиком; `0` — `probe.min_mbps × silence` |
| `probe.url`, `probe.size_mb` | speed.cloudflare.com, `1` | Адрес и объём зонда |
| `probe.min_mbps`, `probe.timeout` | `1`, `8s` | Минимальная скорость зонда и тайм-аут |
| `fails` | `2` | Провалов подряд до аварии |

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
| `fetch.every` | `0s` | Зарезервировано для отдельного опроса подписок |

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
| `rebuild` | — | Только `router`: зарезервировано для пересборки по деградации |

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
| `events` | все | `switch`, `failsafe`, `rollback`, `pipeline`, `subscription`, `expiry`, `singbox` |
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
| `killswitch.init`, `killswitch.table` | `/etc/init.d/killswitch`, `killswitch` | init-скрипт и таблица nftables `inet` kill switch |
| `watch` | `5s` | Период сторожа |
| `down_alert` | `1m` | Уведомить, если sing-box лежит дольше |

## Секреты

`api.secret`, `admin.token`, `notify.telegram.token` и `subscriptions[].url` дают доступ к sing-box,
админке, боту и подпискам. Перед публикацией фрагмента конфига замените их.
