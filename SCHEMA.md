# Схема БД `stats.db` (nodes-tester)

Единое SQLite-хранилище всего состояния тестера (после переноса с `score.csv` /
`switch_state.json` / файлового репортера). Одно соединение `sqlite3`
(`check_same_thread=False`) под общим `Lock`; режим **WAL** для параллельного чтения
(дашборд открывает БД в `mode=ro`). Источник схемы — `nodes_tester/storage.py` (`_SCHEMA`).

**Связующий ключ — CRC ноды** (`crc`, 8-hex из тега `…-out [CRC]`): стабильный
fingerprint настроек ноды (считает `sing-box-subscribe`), поэтому история переживает
переименование тегов. Почти все таблицы ссылаются на ноду по `crc`.

**Ретеншн.** `cleanup()` (раз в сутки в непрерывном режиме + при остановке): удаляет
ноды с `last_seen` старше `storage.retention_days` и КАСКАДОМ (по `crc`) все их строки во
всех таблицах; плюс возрастной кап сырья (`results/traffic/activations/score_history/
node_events` старше `retention_days` и `endpoints` по `last_seen`) — у живых нод тоже.
`VACUUM` — отдельно (`vacuum()`, по cron).

---

## nodes — описание ноды + присутствие

```sql
CREATE TABLE nodes (
  crc          TEXT PRIMARY KEY,   -- CRC ноды (ключ связи со всеми таблицами)
  tag          TEXT,               -- полный тег ноды (напр. "LUNA-vless(reality)-ru-out [78b59099]")
  provider     TEXT,               -- провайдер из имени (напр. LUNA, hynet-XYZ89)
  protocol     TEXT,               -- протокол из имени (vless(reality), wg, hysteria2, …)
  country      TEXT,               -- страна из имени (2-буквенный код: ru, de, …)
  label        TEXT,               -- доп. поле из [..] перед [CRC] (напр. AI), опц.
  type         TEXT,               -- тип outbound/endpoint sing-box (vless, wireguard, …)
  server       TEXT,               -- хост ноды (для host-aware обхода)
  server_port  INTEGER,            -- порт ноды
  payload      TEXT,               -- JSON полей ноды, из которых считается CRC
  crc_ok       INTEGER,            -- 1 = пересчитанный CRC совпал с [CRC] в теге
  first_seen   INTEGER,            -- unix ts первого попадания в БД (= появление в конфиге)
  last_seen    INTEGER,            -- unix ts последнего присутствия в selector (touch_seen)
  present      INTEGER,            -- 1 = сейчас в selector (подписке), 0 = ушла
  banned       INTEGER             -- 1 = ручной бан из админки (пропускается в тестах)
);
```

- Заполняется из `nodes.json` (выход `sing-box-subscribe`, путь `storage.nodes_file`):
  читаются **и `outbounds`, и `endpoints`** (в sing-box 1.11+ wireguard/AmneziaWG — в
  `endpoints`), CRC пересчитывается и сверяется (`crc_ok`).
- `last_seen` обновляется `touch_seen()` каждый прогон для нод, реально присутствующих в
  selector, — защищает живые ноды от ретеншна.
- `present` ведёт `reconcile_presence()`: переход 0→1 пишет `node_events.added`, 1→0 —
  `node_events.removed`.
- `banned` ставит/снимает админка (`Storage.set_banned` → `nodes.banned`); забаненную ноду
  `Runner._enumerate_nodes` пропускает (не тестируется/не скорится). `load_nodes` флаг не
  трогает, поэтому бан переживает перезагрузку `nodes.json`.

## traffic — временной ряд объёма по нодам

```sql
CREATE TABLE traffic (
  ts         INTEGER,   -- unix ts flush-агрегата
  crc        TEXT,      -- нода (CRC из chains[0]); для не-нодовых цепочек — тег группы
  up         INTEGER,   -- отдано за интервал, байт (дельта)
  down       INTEGER,   -- принято за интервал, байт (дельта)
  conns      INTEGER,   -- новых соединений за интервал
  is_tester  INTEGER    -- 1 = собственный трафик тестера (chain содержит nodes-tester)
);
CREATE INDEX idx_traffic_ts  ON traffic(ts);
CREATE INDEX idx_traffic_crc ON traffic(crc);
```

Пишет `TrafficCollector` (поллит Clash API `/connections`, дельта-учёт по id соединения),
агрегат раз в `storage.traffic.flush_interval`. `is_tester=0` — боевой трафик.

## endpoints — трафик по назначениям (куда ходит нода)

```sql
CREATE TABLE endpoints (
  crc         TEXT,     -- нода
  source_ip   TEXT,     -- источник (LAN-клиент)
  dest_host   TEXT,     -- хост/IP назначения
  network     TEXT,     -- tcp/udp
  up          INTEGER,  -- отдано суммарно, байт (накапливается)
  down        INTEGER,  -- принято суммарно, байт (накапливается)
  flows       INTEGER,  -- число потоков
  last_seen   INTEGER,  -- unix ts последнего обновления
  PRIMARY KEY (crc, source_ip, dest_host, network)
);
```

Upsert (суммирование) из `TrafficCollector`. Ретеншн — по `last_seen`.

## results — сырые результаты тестов

```sql
CREATE TABLE results (
  ts        INTEGER,   -- unix ts прогона ноды (общий для всех её тестов в проходе)
  pass_no   INTEGER,   -- посуточный номер прогона
  crc       TEXT,      -- нода
  test      TEXT,      -- имя теста (connectivity/latency/jitter/download/reachability/heavy_download)
  ok        INTEGER,   -- 1/0 — успех теста
  url       TEXT,      -- фактический url теста
  metrics   TEXT,      -- JSON метрик (ttfb_ms, speed_mbps, throttle_ratio, …)
  error     TEXT       -- текст ошибки (если ok=0)
);
CREATE INDEX idx_results_ts  ON results(ts);
CREATE INDEX idx_results_crc ON results(crc);
```

Строка на (нода, тест, прогон). `ts` даёт таймстемп каждого теста каждой ноды (динамика).

## activations — история переключений активной ноды

```sql
CREATE TABLE activations (
  ts      INTEGER,   -- unix ts переключения
  region  TEXT,      -- коарс-регион (eu/us/ru/other)
  crc     TEXT,      -- нода, ставшая активной
  tag     TEXT,      -- её тег
  reason  TEXT,      -- init | quality | rotation | emergency | emergency-stuck
  score   REAL,      -- score ноды на момент активации
  prev    TEXT       -- CRC предыдущей активной ноды
);
CREATE INDEX idx_activations_ts  ON activations(ts);
CREATE INDEX idx_activations_crc ON activations(crc);
```

Пишет `Switcher._activate` / `_note_emergency_stuck`.

## scores — снимок рейтинга (замена score.csv)

```sql
CREATE TABLE scores (
  crc          TEXT PRIMARY KEY,  -- нода
  node         TEXT,              -- её тег (in-memory ключ Scoreboard)
  provider     TEXT,
  protocol     TEXT,
  region       TEXT,              -- коарс-регион
  country      TEXT,
  label        TEXT,
  active       INTEGER,           -- 1 = активная (боевая) в своём регионе
  score        REAL,              -- итоговый S_final [0..100]
  reliability  REAL,              -- под-скоры компонентов [0..1]
  consistency  REAL,
  throttle     REAL,
  jitter       REAL,
  latency      REAL,
  throughput   REAL,
  score_ewma   REAL,              -- EWMA-состояние (сглаживание между прогонами)
  avail        REAL,              -- доступность (EWMA gate)
  flap         REAL,              -- мера нестабильности рейтинга
  samples      INTEGER,           -- число замеров в EWMA
  last_pass    INTEGER,           -- номер прогона последнего обновления
  heavy_ok     TEXT,              -- "" не проверялась | "1" ok | "0" veto (тяжёлый download)
  heavy_ts     INTEGER            -- ts последней тяжёлой пробы
);
```

Полностью перезаписывается каждый прогон (`Scoreboard.end_pass` → `save_scores`): исчезнувшие
ноды уходят. Дашборд читает рейтинг отсюда.

## score_history — динамика рейтинга

```sql
CREATE TABLE score_history (
  ts      INTEGER,   -- unix ts прогона
  crc     TEXT,      -- нода
  region  TEXT,      -- коарс-регион
  score   REAL,      -- S_final на этот прогон
  s_run   REAL,      -- мгновенный скор прогона (без EWMA)
  gate    INTEGER    -- 1/0 — прошла ли gate в этом прогоне
);
CREATE INDEX idx_score_history_ts  ON score_history(ts);
CREATE INDEX idx_score_history_crc ON score_history(crc);
```

Append по каждой протестированной ноде каждый прогон (`Scoreboard.end_pass`). Для графиков
динамики рейтинга/выживаемости.

## switch_state / switch_recent / switch_activations — состояние switcher (замена switch_state.json)

```sql
CREATE TABLE switch_state (
  region          TEXT PRIMARY KEY,  -- коарс-регион
  active          TEXT,              -- тег активной ноды
  last_switch     REAL,              -- ts последнего переключения
  rotate_deadline REAL,             -- ts ближайшей плановой ротации
  quality_count   INTEGER,           -- счётчик подтверждений quality-переключения
  emg_stuck       INTEGER            -- 1 = застряли в emergency без замены
);
CREATE TABLE switch_recent (
  region  TEXT,     -- регион
  seq     INTEGER,  -- порядок (0 = самая свежая)
  node    TEXT,     -- тег ноды из «недавних» (для avoid_recent при ротации)
  PRIMARY KEY (region, seq)
);
CREATE TABLE switch_activations (
  region  TEXT,     -- регион
  node    TEXT,     -- тег ноды
  count   INTEGER,  -- сколько раз активировалась (для взвешивания ротации)
  PRIMARY KEY (region, node)
);
```

Полностью перезаписываются на каждом сохранении (`Switcher._persist` → `save_switch_state`);
регионов немного. Нормализованная замена вложенных структур JSON.

## node_events — пер-нодный журнал жизненного цикла и здоровья

```sql
CREATE TABLE node_events (
  ts      INTEGER,   -- unix ts события
  crc     TEXT,      -- нода
  event   TEXT,      -- added | removed | backoff | garbage | recovered
  reason  TEXT,      -- для backoff/garbage: "backoff"/"garbage"; иначе ""
  streak  INTEGER    -- число подряд провалов gate на момент backoff/garbage
);
CREATE INDEX idx_node_events_ts  ON node_events(ts);
CREATE INDEX idx_node_events_crc ON node_events(crc);
```

Событийный лог — каждое событие отдельной строкой, у ноды их может быть много:

- `added` — нода появилась в selector (подписке). Пишется `reconcile_presence` на каждом
  переходе `present` 0→1 (первое появление и любое повторное).
- `removed` — нода ушла из selector (`present` 1→0). Первичный сигнал «удалена из подписки».
- `backoff` — первый уход в экспоненциальный backoff после провала gate.
- `garbage` — переход в карантин (мусорная): дошла до потолка backoff. Логируется на входе
  в garbage (не на каждый повторный провал) → счётчик `garbage`-событий = «сколько раз
  падала в мусор».
- `recovered` — нода снова прошла gate, backoff/карантин снят.

Пишут: `Runner.reconcile_presence` (added/removed) и `Runner._backoff_update`
(backoff/garbage/recovered).

## garbage — текущая пауза/карантин (не история)

```sql
CREATE TABLE garbage (
  crc        TEXT PRIMARY KEY,  -- нода на паузе/в карантине
  since      INTEGER,           -- ts начала ТЕКУЩЕГО эпизода (не перезаписывается)
  until      INTEGER,           -- карантин: ts, до которого ноду не тестируем; пауза: NULL
  reason     TEXT,              -- backoff (пауза) | garbage (карантин)
  streak     INTEGER,           -- число подряд провалов gate (для удвоения пропуска)
  until_pass INTEGER            -- пауза: не тестируем, пока meta.pass_seq <= until_pass
);
```

Пауза считается в прогонах (сквозной `meta.pass_seq`), карантин — во времени
(`garbage_hours`). Старые строки паузы без `until_pass` (временная модель) пробуются в
ближайшем прогоне, `streak` сохраняется.

Одна строка на ноду — **текущее** состояние (не история; при восстановлении строка
удаляется `clear_backoff`). Историю эпизодов см. в `node_events`. `time_in_garbage` для
дашборда = `now − since`.

## meta — сквозные значения между рестартами

```sql
CREATE TABLE meta (
  key    TEXT PRIMARY KEY,  -- pass_day | pass_no | pass_seq | …
  value  TEXT
);
```

Хранит посуточный номер прогона (`pass_day`/`pass_no`, для отображения) и сквозной
`pass_seq` (монотонный, не сбрасывается в полночь; по нему считается пауза нод).

---

## Кто пишет / читает

| Компонент | Пишет | Читает |
|---|---|---|
| `Runner` | results, node_events (added/removed/backoff/garbage/recovered), meta, вызывает cleanup | — |
| `Scoreboard` | scores, score_history | scores (старт) |
| `Switcher` | switch_state/recent/activations, activations | switch_state/recent/activations (старт) |
| `TrafficCollector` | traffic, endpoints | — |
| `Storage.load_nodes` | nodes | — |
| Админка `api_control` | nodes.banned (бан), garbage (ручной карантин/снятие) | — |
| Дашборд `data.py` | — (ro) | всё |
