# nodes-tester — тестер нод sing-box

Тестер выходных (leaf) нод **sing-box** с рейтингованием, автопереключением боевых
нод и сбором статистики трафика. Запускается на роутере рядом с sing-box.

В отличие от clash-speedtest / V2ray-Tester-Pro, тестер **не поднимает своё
прокси-ядро**: sing-box уже запущен со всеми нодами. Тестер через **Clash API**
переключает selector на очередную ноду, гоняет тестовый трафик через **SOCKS5**
inbound, меряет, пишет рейтинг и (опционально) сам переключает боевые ноды.

## Как это работает

```
              ┌──────────────────── роутер ────────────────────┐
  nodes-tester┤                                                 │
      │ 1. PUT /proxies/nodes-tester = <нода>   (Clash API)     │
      │ 2. HTTP через SOCKS5 inbound ─► route ─► nodes-tester ─► нода ─► интернет
      │ 3. замер (TTFB, jitter, download, stability, reachability, exit-IP)
      │ 4. рейтинг → score.csv;  сырые результаты → файл + SQLite
      │ 5. (опц.) автопереключение боевых селекторов на лучшую ноду
      └─────────────────────────────────────────────────────────┘
```

**Определение региона ноды** задаётся в `region_groups.recognition`:

| Режим | Структура `nodes-tester` | Как берётся регион | Переключение |
|---|---|---|---|
| `by_selector` | двухуровневая: `nodes-tester` → `{eu,us,ru,other}-nodes-tester` → leaf | из тега под-селектора | два PUT (регион, затем нода) |
| `parse` | плоская: `nodes-tester` → все leaf | из страны в имени ноды (eu/us/ru/other) | один PUT (нода) |
| `manually` | плоская | по спискам `region_groups.list` | один PUT (нода) |

**Порядок теста** — по группам `Регион · Протокол · Провайдер` (сначала все
EU·vless(reality)·LUNA, затем пауза `group_pause`, потом EU·vless(reality)·VSPACE …).

## Тесты

| Тест           | Метрики                                                   |
|----------------|-----------------------------------------------------------|
| `connectivity` | факт выхода в интернет, выходной IP, страна, colo          |
| `latency`      | TTFB (time-to-first-byte), мс                              |
| `jitter`       | avg/min/max латентности, jitter (stdev), packet loss       |
| `download`     | скорость скачивания, Мбит/с                                 |
| `stability`    | RST-resistance (survive_seconds) **и** throttle_ratio за одну закачку |
| `reachability` | доступность 10–15 целей параллельно (селективная блокировка)|

Какие тесты активны — список `run.default.tests_enabled` (переопределяем по группе/
региону). Опции каждого теста — в секции `tests`. `stability` — тяжёлый, поэтому
`every: N` (раз в N прогонов). Реестр расширяемый — см. [Добавить тест](#добавить-свой-тест).

---

## 1. Настройка sing-box (один раз)

Нужны три вещи. **Clash API у вас уже включён** (`experimental.clash_api`,
`external_controller: 192.168.1.1:9090`).

### а) SOCKS5 inbound — точка входа тестера
```json
{ "type": "socks", "tag": "nodes-tester-in", "listen": "127.0.0.1", "listen_port": 2080 }
```

### б) selector-группа `nodes-tester`

**Для `recognition: by_selector`** — двухуровневая (верхний селектор + региональные):
```json
{ "type": "selector", "tag": "nodes-tester", "default": "eu-nodes-tester",
  "outbounds": ["eu-nodes-tester","us-nodes-tester","ru-nodes-tester","other-nodes-tester"],
  "interrupt_exist_connections": true },
{ "type": "selector", "tag": "eu-nodes-tester",
  "outbounds": ["LUNA-vless|reality-lt-out [1111]", "..."],
  "interrupt_exist_connections": true }
```

**Для `recognition: parse` или `manually`** — один плоский селектор со всеми leaf:
```json
{ "type": "selector", "tag": "nodes-tester",
  "outbounds": ["LUNA-vless|reality-lt-out [1111]", "hynet-XYZ89-vmess|http|tls-us-out [a734b55d]", "..."],
  "interrupt_exist_connections": true }
```

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
    "recognition": "by_selector",          // by_selector | parse | manually
    "list": [ { "tag": "eu", "nodes_list": ["node_tag_1"] } ],  // для manually
    "exclude": ["ru"]                       // регионы, которые не тестировать
  },

  "run": {
    "default": { "tests_enabled": ["connectivity","latency","jitter","download","stability","reachability"],
                 "loop": true, "pass_pause": 60, "switch_delay": 1.0, "group_pause": 30,
                 "rounds": 1, "request_timeout": 10, "restore_selection": true },
    "testing_groups_specifics": [ { "testing_group_tag": "main", "default_overrides": { "group_pause": 60 } } ],
    "region_groups_specifics":  [ { "region_group_tag": "ru",   "default_overrides": { "tests_enabled": ["connectivity"] } } ]
  },

  "tests":     { "stability": { "every": 3, "duration": 20, "window": 5 }, "…": {} },
  "report":    { "enabled": true, "format": "jsonl" },
  "scoring":   { "enabled": true },
  "switching": { "enabled": true },        // ВНИМАНИЕ: меняет БОЕВЫЕ группы
  "monitor":   { "enabled": true },
  "storage":   { "enabled": true, "nodes_file": "/etc/sing-box-subscribe/nodes.json",
                 "traffic": { "enabled": true } }
}
```

**Послойные параметры прогона.** Эффективные `run`-параметры = `default` →
переопределения по `testing_group` → по `region` (мелкий мердж перечисленных полей).
Так можно, напр., гонять в RU только `connectivity`, а `group_pause` увеличить для
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
Группа 'nodes-tester' (recognition=by_selector); тесты: connectivity, latency, jitter, download, stability, reachability; непрерывно, Ctrl+C для остановки
===== Прогон #1: групп 8, нод 24 =====
── EU · vless(reality) · LUNA — нод: 3 ──
  lt 1111: conn=OK[LT 5.6.7.8]  ttfb=71.4ms  jitter=4.2ms loss=0.0%  dl=48.6Mbps  hold=20.0s thr=0.9  reach=15/15
  … пауза между группами 30s
```

## 5. Результаты

**Файл** (секция `report`, если `enabled`): `results/results.<format>`, дозапись **в
начало** (newest-first), старое сохраняется, атомарно. Форматы: `jsonl` (по строке
на ноду), `json` (массив), `csv` (по строке на пару нода×тест). `report.enabled:false`
— писать только в SQLite.

**SQLite** (секция `storage`, `results/stats.db`) — описания нод + трафик + сырые
результаты, всё связано по **CRC ноды**:

| Таблица | Смысл |
|---|---|
| `nodes` | описание ноды (server/uuid/tls…), читается из `nodes_file`; CRC сверяется |
| `results` | сырые результаты тестов (ts, pass_no, crc, test, ok, url, metrics, error) |
| `traffic` | временной ряд объёма по ноде (up/down/conns, is_tester) |
| `endpoints` | топ источников↔назначений по ноде |

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
  proxy.py, reporter.py, scoring.py, scoreboard.py, switcher.py,
  monitor.py, storage.py, traffic.py, runner.py, __main__.py
  tests/          connectivity, latency, jitter, download, stability, reachability

subscribe/        — ПЕРЕИМЕНОВАТЕЛЬ  (python -m subscribe) — см. subscribe/README.md
  main.py, tool.py, groups.py, parsers/

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
