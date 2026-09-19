# nodes-tester — тестер нод sing-box

Тестер выходных (leaf) нод **sing-box** с рейтингованием, автопереключением боевых
нод и сбором статистики трафика. Запускается на роутере рядом с sing-box.

В отличие от clash-speedtest / V2ray-Tester-Pro, тестер **не поднимает своё
прокси-ядро**: sing-box уже запущен со всеми нодами. Тестер через **Clash API**
переключает selector на очередную ноду, гоняет тестовый трафик через **SOCKS5**
inbound, меряет, пишет рейтинг и (опционально) сам переключает боевые ноды.

> **Документация проекта.** Архитектура и решения — в этом README; полная схема БД —
> в [SCHEMA.md](SCHEMA.md); модель скоринга — в [score.md](score.md); отложенные идеи и
> архив реализованного — в [BACKLOG.md](BACKLOG.md).

## Как это работает

```
              ┌──────────────────── роутер ────────────────────┐
  nodes-tester┤                                                 │
      │ 1. PUT /proxies/nodes-tester = <нода>   (Clash API)     │
      │ 2. HTTP через SOCKS5 inbound ─► route ─► nodes-tester ─► нода ─► интернет
      │ 3. замер (TTFB, jitter, download[скорость+троттлинг], reachability, exit-IP)
      │ 4. рейтинг → score.csv;  сырые результаты → файл + SQLite
      │ 5. (опц.) автопереключение боевых селекторов на лучшую ноду
      └─────────────────────────────────────────────────────────┘
```

**Определение региона ноды** задаётся в `region_groups.recognition` (структура
`nodes-tester` всегда плоская — все ноды прямыми членами, один PUT на ноду):

| Режим | Как берётся регион |
|---|---|
| `parse` | из страны в имени ноды (eu/us/ru/other) |
| `manually` | по спискам `region_groups.list` |

**Порядок обхода** — host-aware (анти-ТСПУ): очередь раскладывается так, чтобы ноды
одного хоста стояли максимально далеко, плюс зазор `run.min_host_gap` (сек) между
обращениями к одному хосту с РАЗНЫМ портом/протоколом. Групповых/межпрогонных пауз нет.

## Тесты

| Тест           | Метрики                                                   |
|----------------|-----------------------------------------------------------|
| `connectivity` | факт выхода в интернет, выходной IP, страна, colo          |
| `latency`      | TTFB (time-to-first-byte), мс                              |
| `jitter`       | avg/min/max латентности, jitter (stdev), packet loss       |
| `download`     | за ОДНУ закачку файла: скорость (Мбит/с), throttle_ratio (замедление ТСПУ по байтовым окнам), hold_ratio (устойчивость); `limited` при 429 |
| `reachability` | доступность 10–15 целей параллельно (селективная блокировка)|

Какие тесты активны — список `run.default.tests_enabled` (переопределяем по группе/
региону). Опции каждого теста — в секции `tests`. `download` — единый транспорт-тест
(раньше был отдельный `stability`, качавший те же 10 МБ; слиты). URL теста переопределяется
`url_by_region` (напр. RU → `speedtest.selectel.ru/10MB`), сервис connectivity —
`service_by_region`. Реестр расширяемый — см. [Добавить тест](#добавить-свой-тест).

---

## 1. Настройка sing-box (один раз)

Нужны три вещи. **Clash API у вас уже включён** (`experimental.clash_api`,
`external_controller: 192.168.1.1:9090`).

### а) SOCKS5 inbound — точка входа тестера
```json
{ "type": "socks", "tag": "nodes-tester-in", "listen": "127.0.0.1", "listen_port": 2080 }
```

### б) selector-группа `nodes-tester` — один плоский селектор со всеми нодами
```json
{ "type": "selector", "tag": "nodes-tester",
  "outbounds": ["LUNA-vless|reality-lt-out [1111]", "hynet-XYZ89-vmess|http|tls-us-out [a734b55d]", "..."],
  "interrupt_exist_connections": true }
```
Генерируется subscribe при `emit.nodes_tester: true`. Тестер выбирает ноду прямо в нём,
регион берёт из имени ноды (`recognition: parse`).

Имя leaf-ноды: `<Провайдер>-<Протокол>-<Страна>-out [метка] [CRC]` (метка опц.), где
протокол — `база|транспорт|маскировка` (`vless|grpc|reality`). Генерируется
переименователем — см. [subscribe/README.md](subscribe/README.md). Тестер разбирает его на
Провайдер / Протокол / Страна / CRC. `interrupt_exist_connections: true` важно —
при переключении старые соединения рвутся, трафик идёт через новую ноду.

### в) route-правило — трафик из inbound в группу
В `route.rules` (первым правилом):
```json
{ "inbound": ["nodes-tester-in"], "outbound": "nodes-tester" }
```
После правки — перезапустить sing-box.

---

## 2. Установка

```bash
pip install -r requirements.txt
```
Зависимости: тестер — `requests`, `PySocks`; переименователь (`subscribe`) — `PyYAML`,
`ruamel.yaml`. `json`/`sqlite3` — стандартная библиотека. Python 3.8+.

## 3. Конфигурация (`config.json`)

```bash
cp config.example.json config.json
```

Конфиг — JSON. Полный набор полей с дефолтами — в `config.example.json`, а
машиночитаемая схема (автодополнение/валидация в IDE) — в `config.schema.json`
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
  "report":    { "console": true },       // только консольная сводка; сырьё — в SQLite
  "scoring":   { "enabled": true },
  "switching": { "enabled": true },        // ВНИМАНИЕ: меняет БОЕВЫЕ группы
  "monitor":   { "enabled": true },
  "storage":   { "enabled": true, "nodes_file": "/etc/sing-box-subscribe/nodes.json",
                 "traffic": { "enabled": true } },
  "cooldown":  { "enabled": true, "max_skip": 32 }   // backoff по провалу gate
}
```

**Послойные параметры прогона.** Эффективные `run`-параметры = `default` →
переопределения по `testing_group` → по `region` (мелкий мердж перечисленных полей).
Так можно, напр., гонять в RU только `connectivity`, а `min_host_gap` увеличить для
группы `main`.

> `base_url` = адрес `external_controller` (обычно `192.168.1.1:9090`, не `127.0.0.1`).
> `config.json` содержит секрет — держите его вне git (уже в `.gitignore`).

## 4. Запуск

```bash
python -m nodes_tester --config config.json
```

По умолчанию (`run.default.loop: true`) работает **непрерывно**: прогоняет все ноды,
пауза `pass_pause`, затем **перечитывает список нод из Clash API** (подхватывая
изменения конфига sing-box) и повторяет — до **Ctrl+C** (тогда исходный выбор
селекторов восстанавливается). Конечное число прогонов — `loop: false` + `rounds: N`.

```bash
python -m nodes_tester --list-tests
```

Пример вывода:
```
Группа 'nodes-tester' (recognition=parse); тесты: connectivity, latency, jitter, download, reachability; rotation_bound (прогон к сроку ротации), Ctrl+C для остановки
===== Прогон #1: нод 24 =====
  lt 1111: conn=OK[LT 5.6.7.8]  ttfb=71.4ms  jitter=4.2ms loss=0.0%  dl=48.6Mbps thr=0.9  reach=15/15
  … анти-ТСПУ пауза 120s (хост 5.6.7.8)
── Фаза 2 · тяжёлый download-veto — нод: 3 ──
  lt 2222: heavy=OK 47.9Mbps
```

## 5. Результаты

**Всё состояние — в одной SQLite `stats.db`.** Отдельного файлового репортера
(`results/results.<format>`), а также старых `score.csv` / `switch_state.json`
больше нет — они слиты в БД (при первом старте однократно мигрируются, если лежат
рядом с БД). Секция `report` теперь управляет только консольной пер-нодной сводкой
(`report.console`).

**SQLite** (секция `storage`, `results/stats.db`) — описания нод + трафик + сырые
результаты + рейтинг + история переключений + журнал жизненного цикла нод; всё
связано по **CRC ноды** (стабильный fingerprint настроек, переживает переименования
тегов). **Полная схема со всеми таблицами и «кто пишет/читает» — в [SCHEMA.md](SCHEMA.md).**
Ключевые таблицы:

| Таблица | Смысл |
|---|---|
| `nodes` | описание ноды (server/uuid/tls…), читается из `nodes_file`; CRC сверяется; флаг `present` |
| `results` | сырые результаты тестов (ts, pass_no, crc, test, ok, url, metrics, error) |
| `traffic` | временной ряд объёма по ноде (up/down/conns, is_tester) |
| `endpoints` | топ источников↔назначений по ноде |
| `activations` | история активаций боевых нод (когда / куда / почему переключились) |
| `scores` | снимок рейтинга (замена `score.csv`), перезаписывается каждый прогон |
| `score_history` | точка рейтинга (score/s_run/gate) на каждую ноду каждый прогон — для графиков |
| `switch_state` / `switch_recent` / `switch_activations` | состояние switcher (замена `switch_state.json`) |
| `node_events` | журнал `added`/`removed`/`backoff`/`garbage`/`recovered` по ноде |
| `garbage` | текущий backoff/карантин ноды (не история): `since`/`until`/`streak`/`reason` |
| `meta` | сквозные значения между рестартами (посуточный `pass_day`/`pass_no`) |

Полный DDL всех таблиц, индексы и матрица «кто пишет / читает» — в [SCHEMA.md](SCHEMA.md).

- `results.metrics` — JSON; ключи по тестам: connectivity `exit_ip/country/colo` или
  `asn/as_org`; latency `ttfb_ms`; jitter `jitter_ms/avg_ms/loss_pct`; download
  `speed_mbps/throttle_ratio/hold_ratio/limited`; reachability `total/reached/loss_pct/endpoints`.
- `activations.reason` ∈ `init | emergency | emergency-stuck | quality | rotation`; `prev` — CRC предыдущей активной.
- `traffic/endpoints.crc` может быть не-нодовым (`direct-out`, urltest-группа) — тогда строки
  без соответствия в `nodes` (в витрине помечаются `unspecified`/`direct`).

### Очистка БД

Единый процесс `cleanup()` (раз в сутки в непрерывном режиме + при остановке): удаляет
ноды с `last_seen` старше `retention_days` (30) и **каскадом** все их строки во всех таблицах
(по CRC — без сирот). Историю живых нод не трогает. Строки не-нодовых аутбаундов
(`direct-out`, нераспознанные группы) режутся по тому же возрасту. **VACUUM** — отдельно
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
пишется в `results/score.csv` (исчезнувшие ноды удаляются, активные помечаются
`active`). Модель — stability-first под РФ: нормализация метрик, веса ~80% на
стабильность, EWMA + штраф за флаппинг + множитель доступности; провал GATE обнуляет
рейтинг сразу. Полное описание с формулами — в [score.md](score.md).

**Автопереключение** (`switching.enabled` — **opt-in, меняет БОЕВЫЕ группы**).
Лестница на регион: **EMERGENCY** (активная провалила gate → сразу на лучшую другую)
→ **QUALITY** (кандидат стабильно лучше на `quality_margin`) → **ROTATION** (раз ~3ч,
размазать нагрузку) → stay. Действие — по цепочке вверх: нода → её leaf-группа →
региональный селектор (`eu-auto-out`); `switching.freeze_groups` (по умолчанию
`global-auto-out`) и тестовые группы не трогаются. Состояние — `results/switch_state.json`.

**Балансировка трафика** (`switching.rotation.load_balance`). При ротации кандидат
выбирается взвешенно так, чтобы размазывать боевой трафик по **осям концентрации** за окно
(`window_hours`): недогруженные **провайдер** (`provider_strength`) и **страна**
(`country_strength`) выпадают чаще — это реальные оси риска (общая инфра/ASN и гео).
Протокол (`protocol_strength`, мягко ~0.3) — лёгкая диверсификация сигнатуры, не нагрузка.
Множители по осям перемножаются (`∝ scale/(scale+bytes)`), 0 = ось выключена; score и гейт
первичны. Только для ROTATION.

**Cooldown** (`cooldown.enabled`, по умолчанию включён). Нода, провалившая GATE
(заблокирована/мертва прямо сейчас), пропускает следующие прогоны с экспоненциальным
backoff: **1, 2, 4, 8, …** (удвоение за каждый подряд провал), потолок `cooldown.max_skip`
(32). Успешный gate сбрасывает счётчик. Смысл — не гонять трафик через дохлые ноды и не
раздувать их в БД. Пропущенная нода сохраняет свой последний рейтинг (не выпадает из score.csv).

## 7. Монитор активных нод (`monitor.enabled`)

Фоновый поток (интервал `monitor.interval`), независимо от прогона следит за
активными боевыми нодами: (1) если выбор в селекторах слетел (напр. reload sing-box)
— возвращает по цепочке; (2) пробит ноду через Clash API `/proxies/{node}/delay`
(sing-box сам дозванивается) и при `fails` провалах подряд запускает EMERGENCY.
Если активной ноды больше нет (регенерация сменила теги) — ждёт первого прогона.

---

## Структура репозитория

Репозиторий содержит два инструмента с общей системой имён:

```
naming/           — ЕДИНЫЙ источник имён/идентичности (общий для обоих)
  identity.py     parse_node, NodeIdentity, region_label
  protocol.py     node_protocol (база|транспорт|маскировка)
  regions.py      coarse_region, страны, флаги
  crc.py          content_crc32, node_payload

nodes_tester/     — ТЕСТЕР  (python -m nodes_tester --config config/config.json)
  config.py       загрузка config.json (testing_groups, region_groups, run-слои)
  clash_api.py    клиент Clash API (proxies, select, delay, connections)
  proxy.py, scoring.py, scoreboard.py, switcher.py,
  monitor.py, storage.py, traffic.py, runner.py, identity.py, __main__.py
  tests/          connectivity, latency, jitter, download (+heavy_download), reachability

subscribe/        — ПЕРЕИМЕНОВАТЕЛЬ  (python -m subscribe) — см. subscribe/README.md
  main.py, tool.py, groups.py, parsers/

dashboard/        — ВЕБ-ДАШБОРД  (python -m dashboard) — история переключений, рейтинг,
                    результаты, качество провайдеров, трафик (табы), топ назначений;
                    region-переключатели, пагинация; read-only, stdlib, авто-обновление, LAN

config/           — пользовательские конфиги
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
3. Опции — в `tests.mytest`, активация — добавить `"mytest"` в `run.default.tests_enabled`.

## Дальше

- Многогрупповость `testing_groups` (параллельно, раздельный стейт).
- Слияние с проектом переименования (общий модуль идентичности/CRC, один `nodes.json`).
- Upload speed, streaming-unlock, DNS-leak.
