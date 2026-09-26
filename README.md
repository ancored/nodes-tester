# nodes-tester: управление прокси-нодами sing-box

`nodes-tester` автоматизирует работу с прокси-нодами на роутере OpenWrt. Проект загружает подписки, собирает конфигурацию sing-box, тестирует ноды, рассчитывает рейтинг, переключает рабочие селекторы и показывает состояние системы в веб-админке.

Проект рассчитан на уже работающий sing-box. Собственного прокси-ядра у тестера нет.

## Быстрый старт

| Задача | Куда перейти |
|---|---|
| Установить пакет на OpenWrt | [Сборка и установка пакета](openwrt/README.md) |
| Запустить проект из исходников | [Установка](#2-установка), затем [запуск](#4-запуск) |
| Подключить тестер к sing-box | [Настройка sing-box](#1-настройка-sing-box-один-раз) |
| Настроить подписки | [`nodes_fetch`](nodes_fetch/README.md) |
| Настроить фильтры, имена и группы | [`nodes_config`](nodes_config/README.md) |
| Разобраться с рейтингом | [Модель рейтинга](score.md) |
| Посмотреть структуру SQLite | [Схема `stats.db`](SCHEMA.md) |
| Найти планы и технический долг | [Бэклог](BACKLOG.md) |

Для запуска из исходников нужен Python 3.8 или новее. Для сборки веб-интерфейса нужны Node.js и npm; готовая статика уже лежит в `dashboard/static/`.

## Состав проекта

Проект состоит из трёх Python-пакетов, которые образуют конвейер, веб-админки и набора
shell-скриптов для роутера:

| Компонент | Тип | Назначение |
|---|---|---|
| [`nodes_fetch`](nodes_fetch/README.md) | Python-пакет, CLI | **Загрузка подписок.** Скачивает подписки (URL, файлы, `happ://`, AmneziaWG), разбирает ссылки 14 протоколов (vless, vmess, trojan, ss, hysteria2, tuic, anytls, wg/awg и др.) в sing-box outbounds и пишет `raw_nodes.json`. Ничего не фильтрует и не переименовывает. |
| [`nodes_config`](nodes_config/README.md) | Python-пакет, CLI | **Сборка конфигурации.** Из `raw_nodes.json` фильтрует ноды, даёт им единые имена со стабильным CRC, строит selector/urltest-группы по регионам и пишет фрагмент конфига sing-box `nodes.json` (только если что-то изменилось). |
| [`nodes_tester`](#как-работает-тестер) | Python-пакет, демон | **Тестирование и переключение.** Через Clash API по очереди выбирает каждую ноду, гоняет через неё тесты (связность, задержка, jitter, скорость и замедление, доступность сайтов), считает рейтинг, переключает рабочие селекторы, собирает статистику трафика. Всё состояние хранится в SQLite (`stats.db`). |
| [`dashboard`](#8-веб-админка-управление--контроль--конфиг) | веб-админка (Vue 3 SPA и HTTP-бэкенд на stdlib) | **Просмотр и управление.** Рейтинг, история, трафик, жизненный цикл нод; карантин, бан и ручное переключение; запуск прогонов и живой лог; редактор конфигов. |
| [`scripts/router`](#структура-репозитория) | shell-скрипты (POSIX sh) | **Интеграция с роутером.** Конвейер `pipeline.sh` сначала загружает подписки, затем собирает и применяет конфигурацию sing-box. Скрипты также собирают клиентские конфиги, создают снимок установки и выполняют откат. |

Общие библиотеки: `naming` - единая схема имён нод, регионы и CRC (её используют и
`nodes_config`, и `nodes_tester`); `nodes_common` - формат `raw_nodes.json` и атомарная
запись файлов. `subscribe` - временная обёртка совместимости со старыми конфигами.

```
 подписки ─► nodes_fetch ─► raw_nodes.json ─► nodes_config ─► nodes.json ─► sing-box
                  ▲                                                          ▲    │
                  └──────────── scripts/router/pipeline.sh (cron) ───────────┘    │ Clash API
                                                                                  │ + SOCKS5
                                            dashboard ◄──── stats.db ◄─── nodes_tester
```

Каждый пакет можно запускать и тестировать отдельно: `python3 -m nodes_fetch`, `python3 -m nodes_config`, `python3 -m nodes_tester` и `python3 -m dashboard`.

### Рабочие конфиги и секреты

Рабочие конфиги не хранятся в git. Файлы `config/config.json`, `config/providers.json`, `config/user_nodes.json`, `config/awg/*.conf` и `scripts/router/clients.list` добавлены в `.gitignore`. Создайте их из шаблонов:

```bash
cp config/config.example.json config/config.json
cp config/providers.example.json config/providers.json
cp config/user_nodes.example.json config/user_nodes.json
cp config/awg/de.conf.example config/awg/de.conf
cp scripts/router/clients.list.example scripts/router/clients.list
```

После копирования задайте `clash_api.secret`, `dashboard.token` и URL подписок. Файлы `user_nodes.json`, `awg/*.conf` и `clients.list` нужны только для соответствующих сценариев.

## Как работает тестер

В отличие от clash-speedtest / V2ray-Tester-Pro, тестер **не поднимает своё
прокси-ядро**: sing-box уже запущен со всеми нодами. Тестер через **Clash API**
переключает selector на очередную ноду, гоняет тестовый трафик через **SOCKS5**
inbound, меряет, пишет рейтинг и (опционально) сам переключает боевые ноды.

```
              ┌──────────────────── роутер ────────────────────┐
  nodes-tester┤                                                 │
      │ 1. PUT /proxies/nodes-tester = <нода>   (Clash API)     │
      │ 2. HTTP через SOCKS5 inbound ─► route ─► nodes-tester ─► нода ─► интернет
      │ 3. замер (TTFB, jitter, download[скорость+троттлинг], reachability, exit-IP)
      │ 4. рейтинг + сырые результаты + история → SQLite `stats.db`
      │ 5. (опц.) автопереключение боевых селекторов на лучшую ноду
      └─────────────────────────────────────────────────────────┘
```

**Определение региона ноды** задаётся в `region_groups.recognition` (структура
`nodes-tester` всегда плоская - все ноды прямыми членами, один PUT на ноду):

| Режим | Как берётся регион |
|---|---|
| `parse` | из страны в имени ноды (eu/us/ru/other) |
| `manually` | по спискам `region_groups.list` |

**Порядок обхода** - host-aware (анти-ТСПУ): очередь раскладывается так, чтобы ноды
одного хоста стояли максимально далеко, плюс зазор `run.min_host_gap` (сек) между
обращениями к одному хосту с РАЗНЫМ портом/протоколом. Групповых/межпрогонных пауз нет.

## Тесты

| Тест           | Метрики                                                   |
|----------------|-----------------------------------------------------------|
| `connectivity` | факт выхода в интернет, выходной IP, страна, colo          |
| `latency`      | TTFB (time-to-first-byte), мс                              |
| `jitter`       | avg/min/max латентности, jitter (stdev), packet loss       |
| `download`     | за ОДНУ закачку файла: скорость (Мбит/с), throttle_ratio (замедление ТСПУ по байтовым окнам), hold_ratio (устойчивость); `limited` при 429 |
| `reachability` | доступность 10-15 целей параллельно (селективная блокировка)|

Какие тесты активны - список `run.default.tests_enabled` (переопределяем по группе/
региону). Опции каждого теста - в секции `tests`. `download` - единый транспорт-тест
(раньше был отдельный `stability`, качавший те же 10 МБ; слиты). URL теста переопределяется
`url_by_region` (например, для RU используется `speedtest.selectel.ru/10MB`), сервис connectivity -
`service_by_region`. Реестр расширяемый - см. [Добавить тест](#добавить-свой-тест).


## 1. Настройка sing-box (один раз)

Нужны три вещи. **Clash API у вас уже включён** (`experimental.clash_api`,
`external_controller: 192.168.1.1:9090`).

### а) SOCKS5 inbound - точка входа тестера
```json
{ "type": "socks", "tag": "nodes-tester-in", "listen": "127.0.0.1", "listen_port": 2080 }
```

### б) selector-группа `nodes-tester` - один плоский селектор со всеми нодами
```json
{ "type": "selector", "tag": "nodes-tester",
  "outbounds": ["LUNA-vless|reality-lt-out [1111]", "hynet-XYZ89-vmess|http|tls-us-out [a734b55d]", "..."],
  "interrupt_exist_connections": true }
```
Генерируется `nodes_config` при `emit.nodes_tester: true` (groups_params). Тестер выбирает ноду прямо в нём,
регион берёт из имени ноды (`recognition: parse`).

Имя leaf-ноды: `<Провайдер>-<Протокол>-<Страна>-out [метка] [CRC]` (метка опц.), где
протокол - `база|транспорт|маскировка` (`vless|grpc|reality`). Генерируется
стадией `nodes_config` - см. [nodes_config/README.md](nodes_config/README.md). Тестер разбирает его на
Провайдер / Протокол / Страна / CRC. `interrupt_exist_connections: true` важно -
при переключении старые соединения рвутся, трафик идёт через новую ноду.

### в) route-правило - трафик из inbound в группу
В `route.rules` (первым правилом):
```json
{ "inbound": ["nodes-tester-in"], "outbound": "nodes-tester" }
```
После правки - перезапустить sing-box.


## 2. Установка

**На OpenWrt - пакетом** (`.apk` для 25.x, `.ipk` для 24.10): сборка в OpenWrt SDK,
установка через `apk add`, сервисы procd, настройки в UCI - см. [openwrt/README.md](openwrt/README.md).

**Из исходников** (любой Linux, разработка):
```bash
pip install -r requirements.txt
```
Зависимости: `nodes_tester` - `requests`, `PySocks`, `jsonschema`; `nodes_fetch` - `requests`,
`PyYAML`, `ruamel.yaml`; `nodes_config` - только стандартная библиотека; `dashboard` читает
config.json через `nodes_tester.config`, поэтому ему нужен `jsonschema`.
Нужен Python 3.8 или новее.

## 3. Конфигурация (`config.json`)

```bash
cp config/config.example.json config/config.json
```

Конфиг - JSON. Полный набор полей с дефолтами находится в `config/config.example.json`, а
машиночитаемая схема для автодополнения и проверки в IDE - в `config/config.schema.json`
(файлы ссылаются на неё через `"$schema"`).

Структура (сокращённо):

```jsonc
{
  "clash_api": { "base_url": "http://192.168.1.1:9090", "secret": "…", "timeout": 5 },

  "testing_groups": [                     // тест-юниты; пока используется первый
    { "tag": "main",
      "connection": { "type": "socks5", "host": "127.0.0.1", "port": 2080 },
      "selector": { "group": "nodes-tester", "exclude": ["DIRECT","REJECT"] } }
  ],

  "region_groups": {
    "enabled": true,
    "recognition": "parse",                 // parse | manually
    "list": [ { "tag": "eu", "nodes_list": ["node_tag_1"] } ],  // для manually
    "exclude": ["ru"]                       // регионы, которые не тестировать
  },

  "run": {
    "default": { "tests_enabled": ["connectivity","latency","jitter","download","reachability"],
                 "loop": true, "rotation_bound": true, "pass_pause": 0, "switch_delay": 1.0,
                 "min_host_gap": 120, "rounds": 1, "request_timeout": 10, "restore_selection": true,
                 "heavy_candidates": 3, "heavy_veto_hours": 6 },
    "testing_groups_specifics": [ { "testing_group_tag": "main", "default_overrides": { "min_host_gap": 180 } } ],
    "region_groups_specifics":  [ { "region_group_tag": "ru",   "default_overrides": { "tests_enabled": ["connectivity"] } } ]
  },

  "tests":     { "download": { "url_by_region": {"ru":"https://speedtest.selectel.ru/10MB"}, "window_bytes": 2000000, "duration": 20 }, "…": {} },
  "report":    { "console": true },       // только консольная сводка; сырьё - в SQLite
  "scoring":   { "enabled": true },
  "switching": { "enabled": true },        // ВНИМАНИЕ: меняет БОЕВЫЕ группы
  "monitor":   { "enabled": true },
  "storage":   { "enabled": true, "nodes_file": "/etc/sing-box-subscribe/nodes.json",
                 "traffic": { "enabled": true } },
  "cooldown":  { "enabled": true, "max_skip": 32, "garbage_hours": 72 }, // пауза/карантин
  "dashboard": { "enabled": true, "host": "0.0.0.0", "port": 8088,
                 "token": "", "read_open": true }    // веб-админка (см. §8)
}
```

**Послойные параметры прогона.** Сначала применяются значения `default`, затем переопределения `testing_group` и `region`. Перечисленные поля объединяются неглубоко.
Так можно, напр., гонять в RU только `connectivity`, а `min_host_gap` увеличить для
группы `main`.

> `base_url` содержит адрес `external_controller` (обычно `192.168.1.1:9090`, а не `127.0.0.1`).
> `config.json` содержит секрет - держите его вне git (уже в `.gitignore`).

## 4. Запуск

```bash
python -m nodes_tester --config config/config.json
```

По умолчанию (`run.default.loop: true`) работает **непрерывно**: прогоняет все ноды,
пауза `pass_pause`, затем **перечитывает список нод из Clash API** (подхватывая
изменения конфига sing-box) и повторяет - до **Ctrl+C** (тогда исходный выбор
селекторов восстанавливается). Для конечного числа прогонов задайте `loop: false` и `rounds: N`.

```bash
python -m nodes_tester --list-tests
```

Пример вывода:
```
Группа 'nodes-tester' (recognition=parse); тесты: connectivity, latency, jitter, download, reachability; rotation_bound (прогон к сроку ротации), Ctrl+C для остановки
===== Прогон #1: нод 24 =====
  lt 1111: conn=OK[LT 5.6.7.8]  ttfb=71.4ms  jitter=4.2ms loss=0.0%  dl=48.6Mbps thr=0.9  reach=15/15
  … анти-ТСПУ пауза 120s (хост 5.6.7.8)
── Фаза 2 · тяжёлый download-veto - нод: 3 ──
  lt 2222: heavy=OK 47.9Mbps
```

## 5. Результаты

**Всё состояние - в одной SQLite `stats.db`.** Отдельного файлового репортера
(`results/results.<format>`), а также старых `score.csv` / `switch_state.json`
больше нет - они слиты в БД (при первом старте однократно мигрируются, если лежат
рядом с БД). Секция `report` теперь управляет только консольной пер-нодной сводкой
(`report.console`).

**SQLite** (секция `storage`, `results/stats.db`) хранит описания нод, трафик, сырые
результаты, рейтинг, историю переключений и журнал жизненного цикла. Всё
связано по **CRC ноды** (стабильный fingerprint настроек, переживает переименования
тегов). **Полная схема со всеми таблицами и «кто пишет/читает» - в [SCHEMA.md](SCHEMA.md).**
Ключевые таблицы:

| Таблица | Смысл |
|---|---|
| `nodes` | описание ноды (server/uuid/tls…), читается из `nodes_file`; CRC сверяется; флаг `present` |
| `results` | сырые результаты тестов (ts, pass_no, crc, test, ok, url, metrics, error) |
| `traffic` | временной ряд объёма по ноде (up/down/conns, is_tester) |
| `endpoints` | топ источников↔назначений по ноде |
| `activations` | история активаций боевых нод (когда / куда / почему переключились) |
| `scores` | снимок рейтинга (замена `score.csv`), перезаписывается каждый прогон |
| `score_history` | точка рейтинга (score/s_run/gate) на каждую ноду каждый прогон - для графиков |
| `switch_state` / `switch_recent` / `switch_activations` | состояние switcher (замена `switch_state.json`) |
| `node_events` | журнал `added`/`removed`/`backoff`/`garbage`/`recovered` по ноде |
| `garbage` | текущий backoff/карантин ноды (не история): `since`/`until`/`streak`/`reason` |
| `meta` | сквозные значения между рестартами (посуточный `pass_day`/`pass_no`) |

Полный DDL всех таблиц, индексы и матрица «кто пишет / читает» - в [SCHEMA.md](SCHEMA.md).

- `results.metrics` - JSON; ключи по тестам: connectivity `exit_ip/country/colo` или
  `asn/as_org`; latency `ttfb_ms`; jitter `jitter_ms/avg_ms/loss_pct`; download
  `speed_mbps/throttle_ratio/hold_ratio/limited`; reachability `total/reached/loss_pct/endpoints`.
- `activations.reason` входит в `init | emergency | emergency-stuck | quality | rotation`; `prev` - CRC предыдущей активной.
- `traffic/endpoints.crc` может быть не-нодовым (`direct-out`, urltest-группа) - тогда строки
  без соответствия в `nodes` (в витрине помечаются `unspecified`/`direct`).

### Очистка БД

Единый процесс `cleanup()` запускается раз в сутки в непрерывном режиме и при остановке. Он удаляет
ноды с `last_seen` старше `retention_days` (30) и **каскадом** все их строки во всех таблицах
(по CRC - без сирот). Историю живых нод не трогает. Строки не-нодовых аутбаундов
(`direct-out`, нераспознанные группы) режутся по тому же возрасту. **VACUUM** - отдельно
(тяжёлый), под cron раз в месяц:
```bash
python3 -m nodes_tester --vacuum -c config/config.json
```

Пример запроса «боевой трафик по ноде»:
```sql
SELECT n.provider, n.country, SUM(t.up), SUM(t.down)
FROM traffic t JOIN nodes n ON n.crc = t.crc
WHERE t.is_tester = 0 GROUP BY t.crc ORDER BY 4 DESC;
```

## 6. Рейтинг и автопереключение

**Рейтинг** (`scoring.enabled`). После каждого прогона считается `score` [0..100] и
пишется в таблицу SQLite `scores` (исчезнувшие ноды удаляются, активные помечаются
`active`). Модель ориентирована на стабильность в условиях РФ: нормализация метрик, около 80 процентов веса на
стабильность, EWMA, штраф за флаппинг и множитель доступности. Провал GATE обнуляет
рейтинг сразу. Полное описание с формулами - в [score.md](score.md).

**Автопереключение** (`switching.enabled` - **opt-in, меняет БОЕВЫЕ группы**).
Порядок решений для региона: **EMERGENCY** немедленно заменяет активную ноду после провала GATE. Затем **QUALITY** проверяет, что кандидат стабильно лучше на `quality_margin`. **ROTATION** примерно раз в три часа распределяет нагрузку. Если условий для переключения нет, активная нода остаётся прежней. Действие проходит по selector-цепочке до production-группы;
`switching.freeze_groups` (по умолчанию `global-auto-out`) и тестовые группы не трогаются.
Состояние хранится в SQLite (`switch_state`, `switch_recent`, `switch_activations`).
Активация считается успешной только при успехе всей цепочки; частичный PUT не меняет `active`.

**Балансировка трафика** (`switching.rotation.load_balance`). При ротации кандидат
выбирается взвешенно так, чтобы размазывать боевой трафик по **осям концентрации** за окно
(`window_hours`): недогруженные **провайдер** (`provider_strength`) и **страна**
(`country_strength`) выпадают чаще - это реальные оси риска (общая инфра/ASN и гео).
Протокол (`protocol_strength`, мягко ~0.3) - лёгкая диверсификация сигнатуры, не нагрузка.
Множители по осям перемножаются (`∝ scale/(scale+bytes)`). Нулевое значение отключает ось; score и GATE
первичны. Только для ROTATION.

**Пауза и карантин** (`cooldown.enabled`, по умолчанию включён). Нода, провалившая GATE
(заблокирована/мертва прямо сейчас), уходит на **паузу** - пропускает следующие прогоны:
**1, 2, 4, 8, 16** (удвоение за каждый подряд провал). Когда пропуск дорастает до
`cooldown.max_skip` (32, т.е. на 6-м провале подряд), нода уходит в **карантин** на
`cooldown.garbage_hours` (72 часа), затем получает одну пробу. Повторный провал возвращает её в карантин.
Успешный gate снимает и паузу, и карантин. Пауза считается по **сквозному** номеру прогона
`meta.pass_seq` (не сбрасывается в полночь, в отличие от посуточного `pass_no`, - иначе
залипание из review.md P1). Смысл - не гонять трафик через дохлые ноды и не раздувать их в
БД. Пропущенная нода сохраняет свой последний рейтинг (не выпадает из `scores`).
`cooldown.base_seconds` - устаревший ключ прежней временной модели паузы, игнорируется.

## 7. Монитор активных нод (`monitor.enabled`)

Фоновый поток (интервал `monitor.interval`), независимо от прогона следит за
активными боевыми нодами: (1) если выбор в селекторах слетел (напр. reload sing-box)
- возвращает по цепочке; (2) пробит ноду через Clash API `/proxies/{node}/delay`
(sing-box сам дозванивается) и при `fails` провалах подряд запускает EMERGENCY.
Если активной ноды больше нет (регенерация сменила теги) - ждёт первого прогона.


## 8. Веб-админка (управление / контроль / конфиг)

Веб-интерфейс (Vue-SPA из `web/`, отдаёт Python) работает в двух режимах:

- **Read-only** - отдельный `python -m dashboard` (открывает `stats.db` в `mode=ro`);
  доступны только просмотр и `/api/*` read-эндпоинты.
- **Full** - сервер встроен в процесс тестера (`dashboard.enabled: true`, поток-демон в
  `Runner`), тогда к живым `Runner/Switcher/Scoreboard/Storage` добавляются
  write/control-эндпоинты.

**Локальный предпросмотр перед развёртыванием:**

```bash
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 python -m dashboard \
  -c config/config.json --host 127.0.0.1 --port 8099 --interval 5
```

Открыть `http://127.0.0.1:8099/`. Это read-only режим: он показывает SPA и данные из
локальной `stats.db`, но не запускает тестер и не переключает ноды. Раздел «Прогоны» в таком
режиме сообщает `tester_not_running`; полноценные действия доступны только во встроенном
Full-режиме.

**Доступ.** Просмотр (`/api/data`, срезы, `/api/status`, `/api/logs`) открыт при
`dashboard.read_open`. Write/control (карантин/бан/switch/прогон) и редактор конфигов
(там секрет) требуют заголовок `X-Admin-Token`, равный `dashboard.token`. Пустой `token`
полностью отключает write/control и редактор. Токен вводится один раз в шапке SPA и
хранится в `localStorage`.

**Разделы SPA.** Обзор / Рейтинг / Результаты / Трафик / Жизненный цикл / Кладбище /
Переключения (только чтение), а также **Управление** / **Прогоны** / **Конфиг** (Фазы 2-4).
«Жизненный цикл» показывает только ноды из подписки (`present=1`); удалённые - только на
«Кладбище» (срез `graveyard`: срок жизни, траектория рейтинга из `score_history`, пик/среднее/
последний, «как ушла», через сколько сотрётся retention'ом).

**Статусы нод - единый словарь UI** (`web/src/status.js`, легенда `StatusLegend` в «Жизненном
цикле» и «Управлении»). Один термин на понятие; значения в БД прежние. Статусы
взаимоисключающие, при нескольких признаках побеждает верхний:

| UI | в БД | смысл | кто ставит |
|---|---|---|---|
| **удалена** | `nodes.present=0` | ушла из подписки; не тестируется; стирается через `retention_days` | подписка |
| **бан** | `nodes.banned=1` | ручное исключение из тестов/рейтинга, бессрочно | админ |
| **карантин** | `garbage.reason='garbage'` | потолок паузы: пропуск `garbage_hours`, затем одна проба | тестер / админ |
| **пауза** | `garbage.reason='backoff'` | после провала GATE пропускает 2^(n-1) прогонов; при `max_skip` переходит в карантин | тестер |
| **активная** | `scores.active=1` | боевая нода региона | switcher |
| **в строю** | `score>0` | тестируется, кандидат | - |
| **рейтинг 0** | `score=0` | тестируется, последний прогон не прошёл / нет замеров | - |

Прежние синонимы, которые больше не используются в UI: «мусор/мусорная» (= карантин),
«backoff» (= пауза), «ушла» (= удалена), «мёртвая/живая» (= рейтинг 0 / в строю).

**Управление нодами** (`dashboard/api_control.py`):
- `POST /api/nodes/{crc}/quarantine` | `/unquarantine` - ручной карантин/снятие
  (`storage.set_backoff`/`clear_backoff`, внутренний статус «мусорная» на `garbage_hours`).
- `POST /api/nodes/{crc}/ban` | `/unban` - флаг `nodes.banned` (персистентный); забаненная
  нода пропускается `Runner._enumerate_nodes` (не тестируется/не скорится).
- `POST /api/regions/{region}/switch` body `{node}` - форс-активация ноды региона
  (`Switcher.force_activate`, reason `manual`).

**Контроль прогонов** (`dashboard/api_control.py`):
- `GET /api/status` - running / номер прогона / активные ноды по регионам / след. ротация /
  живость потоков monitor/traffic.
- `POST /api/run/pass` - внеплановый прогон (`Runner.request_pass`, прерывает ожидание).
- `GET /api/logs?seq=&tail=` - живой лог: кольцевой буфер `LogRing`, в который `Runner.run()`
  дублирует весь `print` (через подмену `sys.stdout/stderr` на `TeeStream`); long-poll по `seq`.

**Редактор конфигов** (`dashboard/api_config.py`): `GET/PUT /api/config`,
`GET /api/config/schema`, `GET/PUT /api/config/providers`. Редактируется именно файл,
из которого загружен `Config.path` (включая нестандартное имя). Подписки - файл
`dashboard.providers_file` (тот же, что читает `nodes_fetch`, напр.
`/root/nodes-data/config-main/providers.json`; относительный путь - от папки config.json;
пустое значение означает `providers.json` рядом с config.json). `config.json` проверяется обязательным
`jsonschema` и семантикой `load_config`; providers - той же `load_providers`, что у `nodes_fetch`
(v2 и совместимый v1). При записи создаётся временный файл в том же каталоге, выполняются flush и fsync, затем `os.replace`. Симлинк
сохраняется (пишется его цель).
Применение изменений в работающем тестере требует перезапуска процесса.

БД-схема дополнена колонкой `nodes.banned` (миграция `ALTER`). Бан исключает ноду только
из тестов, но не из учёта физического присутствия: `present/last_seen` продолжают обновляться,
поэтому retention не стирает ban. Полный список нод для «Управления» - `nodes` в `/api/data`.

**Защита после ревью.** Сервер проверяет путь к статике через `realpath/commonpath`, включая обратные слэши Windows. Отсутствующий JS или CSS возвращает 404; резервная страница SPA открывается только для URL без расширения. Размер HTTP-запроса ограничен 2 МиБ, ошибочный параметр даёт 400, неверный метод - 405.

Фоновые потоки монитора и учёта трафика завершаются до закрытия SQLite. Клиент отправляет запросы к `/api/data` последовательно, сохраняет последний корректный снимок при временной ошибке и сразу применяет новый токен администратора.


## Структура репозитория

Раскладка по каталогам (назначение компонентов - в [Состав проекта](#состав-проекта)):

```
naming/           - ЕДИНЫЙ источник имён/идентичности (общий для обоих)
  identity.py     parse_node, NodeIdentity, region_label
  protocol.py     node_protocol (база|транспорт|маскировка)
  regions.py      coarse_region, страны, флаги
  crc.py          content_crc32, node_payload

nodes_tester/     - ТЕСТЕР  (python -m nodes_tester --config config/config.json)
  config.py       загрузка config.json (testing_groups, region_groups, run-слои)
  clash_api.py    клиент Clash API (proxies, select, delay, connections)
  proxy.py, scoring.py, scoreboard.py, switcher.py,
  monitor.py, storage.py, traffic.py, runner.py, identity.py, __main__.py
  tests/          connectivity, latency, jitter, download (+heavy_download), reachability

nodes_fetch/      - СТАДИЯ FETCH: подписки → raw_nodes.json (python -m nodes_fetch) - см. nodes_fetch/README.md
  fetch.py        providers v1/v2, last-good (stale), guard min_ratio, --only
  sources.py      url / file / folder / happ → узлы парсера
  parsers/, util.py, happ*.py, _chacha.py   (перенесены из subscribe/)
nodes_common/     - контракт raw_nodes.json (raw.py) + атомарная запись/lock (fileio.py)
schemas/          - JSON-схемы артефактов/конфигов (raw_nodes, providers, groups_params)
nodes_config/     - СТАДИЯ CONFIG: raw → nodes.json (python -m nodes_config [migrate]) - см. nodes_config/README.md
  build.py        фильтры → rename → CRC → dedupe → группы (+ отчёт); пишет только при изменении
  rename.py, groups.py, params.py (groups_params v2), migrate.py (конфиги v1 → v2)
subscribe/        - совместимая обёртка `python -m subscribe` над fetch+config (конфиги v1; до Ф6)
scripts/router/   - backup-/rollback-nodes-tester.sh (снимок роутера и быстрый откат, sing-box не трогает,
                    если его конфиг не менялся), shadow-pipeline.sh (теневой прогон fetch→config),
                    pipeline.sh router|clients [--dry-run] (fetch → config → update-rules →
                    apply-nodes / build-clients; sing-box перезапускается только при изменениях,
                    после рестарта - проверка связности и автовозврат прежнего конфига),
                    switch-to-pipeline.sh (однократное переключение cron, в фоне через setsid)
tests/golden/     - golden-харнесс (запись ответов подписок на роутере, офлайн-повтор)

dashboard/        - ВЕБ-АДМИНКА (Vue-SPA + stdlib-бэкенд) - просмотр (рейтинг/трафик/жизненный
                    цикл/результаты/история) + управление нодами (карантин/бан/форс-switch) +
                    контроль прогонов (статус/живой лог/внеплановый прогон) + редактор конфигов
  webapp.py       каркас на stdlib: App (маршруты метод+regex, раздача статики SPA из
                  static/, JSON, проверка X-Admin-Token) + ThreadingHTTPServer
  api_read.py     read-эндпоинты поверх data.collect: /api/data (агрегат) + срезы
                  /api/rating|history|results|traffic|lifecycle|graveyard
  api_control.py  write/control (только при встраивании в Runner, иначе 409): карантин/бан/
                  switch, /api/status, /api/run/pass, /api/logs (long-poll)
  api_config.py   редактор конфигов: GET/PUT config.json (валидация load_config) и
                  providers.json, атомарная запись (temp+rename)
  data.py         выборка/агрегация из stats.db (read-only, mode=ro)
  server.py       build_app(cfg, runner=None) - общий для read-only и встраивания в Runner
  static/         собранный SPA (Vue) - выход `web/ npm run build`; фолбэк - легаси-страница
                  Режимы: отдельный процесс = read-only; блок config.dashboard.enabled +
                  встраивание в Runner = write/control по токену. LAN, авто-обновление

web/              - ИСХОДНИКИ SPA-АДМИНКИ (Vue 3 + Vite) - `npm run build` → dashboard/static/
                    (в git только исходники; node_modules/dist игнорируются)

config/           - пользовательские конфиги
  config.json, config.example.json, config.schema.json,
  providers.json, user_nodes.json, groups_params.json
```

## Добавить свой тест

1. `nodes_tester/tests/mytest.py`:
   ```python
   from .base import BaseTest, TestContext, TestResult, register
   @register
   class MyTest(BaseTest):
       name = "mytest"
       def run(self, ctx: TestContext) -> TestResult:
           return TestResult(self.name, ok=True, metrics={"foo": 1}, url="…")
   ```
2. `from . import mytest` в `nodes_tester/tests/__init__.py`.
3. Опции - в `tests.mytest`, активация - добавить `"mytest"` в `run.default.tests_enabled`.

## Планы

Стадии `nodes_fetch`, `nodes_config` и роутерный конвейер уже работают. Следующие фазы описаны в [спецификации рефакторинга](REFACTOR-MODULES.md): отдельный `nodes_admin`, планировщик и отказ от временной обёртки `subscribe`.

Остальные задачи ведутся в [BACKLOG.md](BACKLOG.md). Среди них многогрупповые прогоны, новые сетевые тесты, горячая перезагрузка конфигурации и графики временных рядов.
