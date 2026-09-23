# Спецификация: 3 модуля + админка-оркестратор + роутерные скрипты

> Статус: **спека v1, решения приняты** (2026-09-23). Реализация — по фазам (§10), код ещё
> не начат.

## 0. Принятые решения

| # | Вопрос | Решение |
|---|---|---|
| D1 | Состав | 3 модуля (`nodes_fetch`, `nodes_config`, `nodes_tester`) + оркестратор (`nodes_admin`) + скрипты на роутере |
| D2 | Репозиторий | Монорепо, общий `naming/`. Независимость = границы пакетов + файлы-контракты |
| D3 | Процессы | **2 сервиса**: `nodes_admin` (демон: планировщик+SPA) и `nodes_tester` (демон). fetch/config — **подпроцессы** админки (краш парсера не валит админку, память освобождается) |
| D4 | Фильтры | **Все фильтры — в nodes-config.** fetch — «честное зеркало» подписок, ничего не выкидывает |
| D5 | Применение | Новый `nodes.json` применяется **автоматически** (хук merge+check+restart), только если реально изменился, и не чаще `min_apply_interval` (отложенное применение, не потеря) |
| D6 | CLI | Каждый модуль работает без админки из командной строки |
| D7 | Порядок | Спека → fetch → nodes-config → тестер/админка (strangler, `subscribe` — обёртка до конца миграции) |
| D8 | Инвариант | **Теги/CRC не меняются.** Новый конвейер на тех же входах даёт побайтно тот же `nodes.json`, что `python -m subscribe` (golden-тест) |

## 1. Общая схема

```
                  ┌───────────── nodes_admin (демон: планировщик · SPA · история) ─────────────┐
                  │  pipeline.json — задания fetch, потоки, действия, политика применения       │
                  └──┬────────────────────┬─────────────────────────┬───────────────┬──────────┘
          subprocess │         subprocess │                  action │       HTTP    │ 127.0.0.1
                     ▼                    ▼                          ▼               ▼
 providers-*.json ─► nodes_fetch ─► raw/<id>.json ─► nodes_config ─► nodes.json ─► apply-nodes.sh ─► sing-box
                                   groups_params/*.json ┘ ▲                        (merge+check+restart)  │
                                   user_nodes/*.json ─────┘                                              ▼
                                                                          nodes_tester ◄── Clash API ────┘
                                                                          (stats.db, control API)
```

Граф зависимостей кода:

```
nodes_fetch  ──▶ (ничего из проекта)
nodes_config ──▶ naming
nodes_tester ──▶ naming
nodes_admin  ──▶ запускает fetch/config как процессы; тестер — по HTTP; stats.db — read-only
```

## 2. Раскладка репозитория и данных

```
naming/          общее ядро идентичности (без изменений)
nodes_fetch/     подписки → raw_nodes.json
  __main__.py    CLI
  fetch.py       оркестрация источников, last-good, guard
  sources.py     url / file / folder / happ
  parsers/       (переезд из subscribe/parsers, пакетные импорты вместо sys.path-хака)
  util.py        b64Decode, genName, getResponse, xhttp_transport (часть tool.py)
  happ.py, happ_decode.py, happ_keys_crypt5.py, _chacha.py
nodes_config/    raw_nodes.json (+groups_params, +user_nodes) → nodes.json
  __main__.py    CLI (+ подкоманда migrate)
  build.py       фильтры → rename → CRC → dedupe → группы → сборка фрагмента
  rename.py      group_meta / custom_rename / rename_user_node (часть tool.py)
  groups.py      (переезд, параметры передаются явно — без глобалов модуля)
  params.py      загрузка/валидация groups_params v2
nodes_tester/    тестер (dashboard-встраивание → control API, §7)
nodes_admin/     оркестратор (из dashboard/ + новое, §8)
web/             исходники SPA → nodes_admin/static/
schemas/         raw_nodes / groups_params / providers / pipeline / tester-config (.schema.json)
scripts/router/  apply-nodes.sh, build-clients.sh, update-rules.sh, init.d/*  (эталоны)
config/                      ← пользовательские конфиги (вне git, кроме *.example)
  pipeline.json
  fetch/providers-main.json, providers-wh.json
  nodes-config/groups_params/router.json, clients.json
  nodes-config/user_nodes/router.json
  tester/config.json
data/                        ← рабочие артефакты (путь задаётся pipeline.paths.data_dir)
  raw/main.json, raw/wh.json
  pipeline.db                история запусков оркестратора
```

`stats.db` остаётся за тестером (путь — в его конфиге). Оркестратор пишет только `pipeline.db`.

## 3. Контракт `raw_nodes.json` (v1)

```jsonc
{
  "version": 1,
  "generated_at": "2026-09-23T12:00:00Z",
  "providers": "providers-main",          // имя набора (для UI)
  "nodes_hash": "sha256:…",               // хеш секции nodes — триггер пересборки
  "sources": [
    { "provider": "LUNA", "kind": "url", "ok": true,  "count": 42,
      "fetched_at": "…", "last_ok_at": "…", "stale": false, "error": null },
    { "provider": "VSPACE", "kind": "url", "ok": false, "count": 17,
      "fetched_at": "…", "last_ok_at": "…(раньше)", "stale": true,
      "error": "ConnectTimeout" }          // ноды взяты из прошлого raw (last-good)
  ],
  "nodes": [
    { "provider": "LUNA",
      "title": "🇫🇮 Finland | Reality",      // исходное имя из подписки (нужно rename/labels)
      "cc_hint": null,                     // подсказка страны (AWG: из имени файла)
      "outbound": { "type": "vless", "server": "…", "…": "…" } }   // sing-box как есть, БЕЗ tag
  ]
}
```

Правила:
- `outbound` — ровно то, что выдал парсер, без `tag`/`domain_resolver`/`_`-полей. CRC от него
  совпадает с нынешним (payload и так исключает эти поля).
- Порядок `nodes` = порядок подписок в providers + порядок внутри подписки (важно для golden).
- Узлы-endpoints (wireguard/AWG) лежат в том же списке; разнесение в `endpoints[]` — забота
  nodes-config.
- Схема — `schemas/raw_nodes.schema.json`; nodes-config отвергает `version` ≠ 1.

## 4. Модуль `nodes_fetch`

### CLI
```bash
python -m nodes_fetch -p config/fetch/providers-main.json -o data/raw/main.json [--json] [--force] [--dry-run] [--only LUNA]
```
- лог — в stderr; `--json` — одна строка-сводка в stdout (для оркестратора);
- exit: `0` записано; `2` guard отказал в записи (старый файл цел); `1` фатальная ошибка;
- `--dry-run` — ничего не пишет (кнопка «проверить подписку» в админке, вместе с `--only`);
- запись атомарная (temp → fsync → replace) + lock-файл `<output>.lock` (админка и ручной CLI
  не пишут одновременно).

### `providers-*.json` (v2 — только про загрузку)
```jsonc
{
  "$schema": "../../schemas/providers.schema.json",
  "subscribes": [
    { "tag": "LUNA", "url": "https://…", "user_agent": "curl", "enabled": true },
    { "tag": "MEDVED", "url": "happ://crypt5/…", "happ_headers": { "X-Hwid": "…" } },
    { "tag": "AWG", "type": "folder", "path": "awg", "format": "awg", "ext": ".conf" },
    { "tag": "LOCAL", "file": "subs/local.txt" }
  ],
  "fetch": {
    "timeout": 20, "retries": 3,
    "proxy": null,                 // напр. "socks5://127.0.0.1:2080" — качать подписки через туннель
    "stale_max_hours": 48,         // сколько держать last-good ноды упавшей подписки
    "min_ratio": 0.5               // guard: нод стало < 50% от прошлого → не перезаписывать
  }
}
```
Уходит из providers: `save_config_path` (выход задаёт CLI/задание), `exclude_protocol`,
`exclude_countries`, `exclude_node_protocols`, `labels`, `domain_resolver_tag`,
`ex-node-name` (→ groups_params, §5). Выбрасываются legacy-поля upstream: `prefix`, `emoji`,
`subgroup` (ни в одном нашем наборе не используются; rename всё равно перекрывает тег).
`User-Agent` → `user_agent` (старое имя принимается при миграции).

### Поведение
- Каждая подписка изолирована: исключение → `ok:false`, берутся её ноды из прошлого raw, если
  `last_ok_at` моложе `stale_max_hours` (`stale:true`), иначе ноды подписки выпадают.
- Guard `min_ratio` против «провайдер отдал пустоту/мусор». `--force` его обходит.
- Прошлый raw-файл — единственный кеш (отдельного состояния нет).
- Парсеры не фильтруют ничего, кроме того, что не удалось распарсить.

## 5. Модуль `nodes_config`

### CLI
```bash
python -m nodes_config --raw data/raw/main.json [--raw data/raw/extra.json] \
    [--groups config/nodes-config/groups_params/router.json] \
    [--user-nodes config/nodes-config/user_nodes/router.json] \
    -o /etc/sing-box-subscribe/nodes.json [--json] [--check]
python -m nodes_config migrate --from config/ --to config/     # старые providers+groups_params → v2
```
- `--raw` можно повторить (контракт — список; в UI пока одиночный выбор);
- **пишет только при изменении** содержимого → нет изменений = нет применения/рестарта;
- `--json` сводка: `changed`, `counts` по регионам, `added`/`removed` теги (diff для UI и
  журнала), `dropped` по причинам фильтров;
- `--check` — собрать и сравнить, ничего не писать;
- exit: `0` ок (changed или нет — в сводке), `1` ошибка (старый nodes.json цел).

### `groups_params` v2 (переиспользуемый «рецепт» потока)
```jsonc
{
  "$schema": "../../../schemas/groups_params.schema.json",
  "filters": {
    "exclude_types": ["shadowsocksr"],          // было providers.exclude_protocol (по типу)
    "exclude_protocols": ["xhttp", "wg"],       // было exclude_node_protocols (токены node_protocol)
    "exclude_countries": ["cn","ua","hk","by","ru"],
    "exclude_names": { "LUNA": ["Russia"], "*": [] }   // было ex-node-name, по исходному title
  },
  "rename": {
    "labels": { "AI": ["Gemini","OpenAI"], "Media": ["Netflix"], "LTE": ["LTE"] },
    "domain_resolver_tag": "bootstrap"
  },
  "selector": { "interrupt_exist_connections": true },
  "urltest":  { "interval": "5m", "tolerance": 100, "idle_timeout": "5m", "interrupt_exist_connections": true },
  "emit": { "nodes_tester": true, "global_failsafe": false, "ensure_regions": ["eu","us","other"] },
  "raw_user_nodes": false
}
```
Файл опционален: без него — дефолты (как сейчас при отсутствии groups_params).
Для набора WH это `clients.json` (`nodes_tester:false`, `global_failsafe:true`,
`raw_user_nodes:true`, `domain_resolver_tag:"dns-whitelist"`, `exclude_protocols:["xhttp","wg"]`).

### Конвейер сборки (порядок = нынешний `finalize_nodes`, чтобы golden совпал)
1. raw → рабочие узлы (`tag=title`, `_provider`, `_file_cc=cc_hint`);
2. фильтры: types → UNGROUPED (голые vless/vmess) → protocols → names;
3. labels (по title) → `group_meta` → `custom_rename`; user_nodes → `rename_user_node`;
4. `domain_resolver` (кроме wireguard) → exclude_countries → `[CRC]` → dedupe → detour-ремап;
5. `groups.build(nodes, params)` → фрагмент `{outbounds, endpoints}`.

`groups.py` перестаёт читать глобальный файл при импорте: параметры передаются аргументом.

## 6. Роутерные скрипты (вне Python)

`update-singbox-config.sh` режется по обязанностям; админка зовёт их как **действия**
(allowlist в pipeline.json — путь задаётся в конфиге, не из браузера):

| Скрипт | Что делает | Кто зовёт |
|---|---|---|
| `update-rules.sh` | git fetch base.json/доменов + `.srs` правила | действие по расписанию (раз в сутки) |
| `apply-nodes.sh <nodes.json>` | `sing-box merge` base+nodes → `check` → mv → restart; при провале check — старый конфиг цел, exit≠0 | применение потока `router` |
| `build-clients.sh <whnodes.json>` | merge-configs.py base_<client>+whnodes → `/etc/sing-box-clients/` | применение потока `clients` |

Старый `update-singbox-config.sh` на переходный период = `update-rules.sh` +
`python3 -m subscribe` (обёртка fetch→config) + `apply-nodes.sh`. Эталоны скриптов и
init.d-юнитов (`nodes-admin`, `nodes-tester`) — в `scripts/router/` этого репо.

## 7. `nodes_tester` — что меняется

- Встроенный дашборд (`dashboard.*`, SPA, read-эндпоинты) **уходит в админку**. Остаётся
  маленький **control API** (секция конфига `control`: `host` 127.0.0.1, `port` 8089, `token`):
  `/status`, `/logs`, `/run/pass`, `/nodes/{crc}/{ban,unban,quarantine,unquarantine}`,
  `/regions/{r}/switch`, `/config` (GET/PUT своего config.json). Код — нынешний
  `api_control.py`/`api_config.py` без изменений логики.
- `storage.nodes_file` указывает на выход потока `router` (как сейчас — путь в конфиге).
- Логика тестов/скоринга/switcher/monitor не трогается.

## 8. `nodes_admin` — оркестратор

### `pipeline.json`
```jsonc
{
  "$schema": "../schemas/pipeline.schema.json",
  "admin": { "host": "0.0.0.0", "port": 8088, "token": "…", "read_open": true },
  "paths": { "data_dir": "/root/nodes-data" },
  "fetch": [
    { "id": "main", "providers": "fetch/providers-main.json", "every": "6h", "enabled": true },
    { "id": "wh",   "providers": "fetch/providers-wh.json",   "every": "12h" }
  ],
  "flows": [
    { "id": "router", "raw": ["main"],
      "groups_params": "nodes-config/groups_params/router.json",
      "user_nodes": "nodes-config/user_nodes/router.json",
      "output": "/etc/sing-box-subscribe/nodes.json",
      "trigger": { "on": "fetch" },                         // деф.; или { "every": "24h" }
      "apply": { "action": "apply-router", "min_interval": "30m" } },
    { "id": "clients", "raw": ["wh"],
      "groups_params": "nodes-config/groups_params/clients.json", "user_nodes": null,
      "output": "/etc/sing-box-subscribe/whnodes.json",
      "apply": { "action": "build-clients", "min_interval": "0" } }
  ],
  "actions": {
    "apply-router":  { "cmd": ["/root/singbox-repo/apply-nodes.sh", "{output}"], "timeout": 180 },
    "build-clients": { "cmd": ["/root/singbox-repo/build-clients.sh", "{output}"], "timeout": 120 },
    "update-rules":  { "cmd": ["/root/singbox-repo/update-rules.sh"], "timeout": 300, "every": "24h" }
  },
  "tester": { "control_url": "http://127.0.0.1:8089", "token": "…", "config": "tester/config.json" }
}
```
Выход fetch-задания = `<data_dir>/raw/<id>.json` (не настраивается — меньше путаницы).
`raw` потока ссылается на id задания (выпадающий список в UI); путь к файлу — только в CLI.

### Семантика планировщика
- fetch-задание по `every` (+ кнопка «сейчас»). После успеха: если `nodes_hash` изменился →
  все потоки с `trigger.on=fetch` и этим raw в `raw` ставятся в очередь.
- Поток также пересобирается при сохранении его groups_params/user_nodes в админке
  (переиспользуемый файл → все потоки, которые его используют).
- `changed` → применение действием. Если с прошлого применения прошло < `min_interval`,
  применение **откладывается** до истечения интервала (схлопывается в одно), а не теряется.
- Один запуск на ресурс одновременно (очередь, lock-файлы — общие с CLI).
- Действие получает подстановки `{output}`, `{flow}`; вывод идёт в лог запуска; таймаут → kill.
- После рестарта демона: `next_run` восстанавливается из `pipeline.db` (без «догоняющей» лавины:
  просроченное задание выполняется один раз).

### `pipeline.db`
`runs(id, kind[fetch|flow|action], ref, started, finished, status, exit_code, summary_json,
log_tail)`, `source_stats(ts, fetch_id, provider, ok, count, stale)` (график числа нод по
провайдеру), `apply_state(flow, last_applied, pending_since, output_hash)`.

### API и SPA
- `/api/pipeline` — граф: задания/потоки/действия со статусом, последним и следующим запуском;
- `/api/fetch/{id}/run|dry-run`, `/api/flows/{id}/build|apply`, `/api/actions/{id}/run`;
- `/api/runs?kind=&ref=` + `/api/runs/{id}/log`;
- редакторы: providers-*, groups_params/*, user_nodes/* (CRUD файлов, схемная валидация,
  атомарная запись), pipeline.json;
- `/api/tester/*` — прокси на control API тестера; read-разделы тестера — из `stats.db` (ro),
  как сейчас `dashboard/data.py`;
- SPA: **Конвейер** (DAG + кнопки), **Подписки** (наборы, статус источников, история числа нод),
  **Потоки** (форма raw/groups_params/user_nodes/выход/триггер + diff последней сборки),
  **Действия** (запуск, логи) + нынешние разделы тестера.
- Токен/LAN-модель — как в нынешней админке (write и редакторы только по `X-Admin-Token`).

## 9. Инварианты и тесты

1. **Golden CRC/тегов:** фикстуры подписок (офлайн, файлы) → `nodes_fetch` → `nodes_config` ==
   текущий `subscribe` побайтно, для обоих наборов (`config/` и `config_whitelist/`).
2. Контрактные тесты raw_nodes (схема, порядок, отсутствие tag/`_`-полей в outbound).
3. fetch: last-good/stale-истечение, guard `min_ratio`, изоляция упавшей подписки, lock.
4. config: «пишет только при изменении», diff added/removed, каждый фильтр, migrate v1→v2.
5. admin: планировщик на фейковых часах (триггеры, откладывание по min_interval, схлопывание,
   восстановление после рестарта), allowlist действий, таймауты.
6. Существующие 124 теста остаются зелёными на каждой фазе.

## 10. Фазы миграции (каждая заканчивается рабочей системой)

| Фаза | Содержание | Готово, когда |
|---|---|---|
| **Ф0** | Коммит текущего состояния (baseline); снять golden-фикстуры с нынешнего `subscribe` | golden nodes.json для обоих наборов в `tests/fixtures/` |
| **Ф1** | `nodes_fetch` + `schemas/raw_nodes` + providers v2; парсеры переезжают с пакетными импортами | fetch-тесты зелёные; `subscribe` = fetch(в памяти)+старый finalize → golden совпадает |
| **Ф2** | `nodes_config` + groups_params v2 + `migrate`; `subscribe` = fetch→config | golden совпадает по обоим наборам; migrate переносит реальные конфиги |
| **Ф3** | Роутерные скрипты: `update-rules/apply-nodes/build-clients`, старый `.sh` через них | на роутере nodes.json до/после идентичен; sing-box check ок |
| **Ф4** | Тестер: dashboard → control API (localhost); `nodes_admin` с read-разделами и прокси | админка отдельным процессом показывает то же, что сейчас; управление работает через прокси |
| **Ф5** | Оркестратор: pipeline.json, планировщик, pipeline.db, действия, SPA Конвейер/Подписки/Потоки/Действия | полный цикл по расписанию на роутере; cron для подписок не нужен |
| **Ф6** | Уборка: удалить `subscribe/`, `dashboard/`, старые конфиги/cron; доки | нет обёрток; README/SCHEMA/этот файл актуальны |

## 11. Открытые вопросы (не блокируют Ф0–Ф2)

- ~~Где на роутере `data_dir`~~ — роутер x86 с диском 940 ГБ, флэш-износа нет: `/root/nodes-data`.
- **Откат:** `scripts/router/rollback-nodes-tester.sh` (снимок стабильной версии —
  `/root/backups/nodes-tester-stable`). При выкатке новых сервисов (`nodes-admin`) откат их
  сам выключает и убирает. Каждая фаза с деплоем на роутер начинается с `--check` снимка.
- Нужен ли тестеру сигнал «nodes.json применён» (сейчас он сам подхватывает изменения по
  Clash API и `maybe_load_nodes`) — вероятно нет; проверить на Ф5.
- Дедлайн жизни `stale`-нод по умолчанию (48ч?) — уточнить по реальной частоте падений подписок.
