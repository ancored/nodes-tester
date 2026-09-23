# nodes_config — raw_nodes.json → nodes.json

Вторая стадия конвейера (см. [REFACTOR-MODULES.md](../REFACTOR-MODULES.md)): берёт сырые ноды
от [`nodes_fetch`](../nodes_fetch/README.md), фильтрует, переименовывает по единой схеме (общий
пакет [`naming`](../naming)), добавляет CRC, строит selector/urltest-группы и пишет фрагмент
конфига `{"outbounds": [...], "endpoints": [...]}`. Он мёржится в базовый конфиг sing-box:
```
sing-box merge <output.json> -c base.json -c nodes.json
```

## Запуск

```bash
python -m nodes_config --raw data/raw/main.json \
    --groups config/groups_params.json --user-nodes config/user_nodes.json \
    -o /etc/sing-box-subscribe/nodes.json [--json] [--check]
```
- `--raw` можно повторить — ноды нескольких raw идут подряд (одинаковые схлопываются dedupe);
- `--groups`, `--user-nodes` — опциональны (без groups_params — все дефолты);
- **nodes.json пишется только при изменении** (JSON-сравнение с текущим файлом) — нечего
  применять, sing-box не перезапускается; `--check` — собрать и сравнить, не записывая;
- `--json` — сводка в stdout: `changed`, `written`, `added`/`removed` теги, отчёт (нод, групп,
  по регионам, сколько отброшено и почему); лог — stderr. Выход `0` — успех, `1` — ошибка
  (старый файл цел). Запись атомарная, с lock-файлом.

Переход со старых конфигов:
```bash
python -m nodes_config migrate --from config/ --to config-v2/    # v1 → v2, v1 не трогается
```

## groups_params.json (v2)

«Рецепт» потока: один файл можно переиспользовать в нескольких потоках. Схема —
[`schemas/groups_params.schema.json`](../schemas/groups_params.schema.json).

```jsonc
{
  "filters": {
    "exclude_types": ["shadowsocksr"],          // тип outbound (было providers.exclude_protocol)
    "exclude_protocols": ["xhttp", "wg"],       // токены протокола (было exclude_node_protocols)
    "exclude_countries": ["cn", "ua", "ru"],
    "exclude_names": { "LUNA": ["promo"], "*": [] }   // подстрока исходного имени (было ex-node-name)
  },
  "rename": {
    "labels": { "AI": ["Gemini", "OpenAI", "Claude"], "Media": ["Netflix"] },
    "domain_resolver_tag": "bootstrap"
  },
  "selector": { "interrupt_exist_connections": true },
  "urltest":  { "interval": "5m", "tolerance": 100, "idle_timeout": "5m", "interrupt_exist_connections": true },
  "emit": { "nodes_tester": true, "global_failsafe": false, "ensure_regions": ["eu", "us", "other"] },
  "raw_user_nodes": false
}
```

**filters** — всё отсеивание живёт здесь (fetch ничего не выкидывает):
- `exclude_types` — по `type` outbound (`shadowsocksr`, `hysteria2`, …);
- `exclude_protocols` — по токенам дескриптора протокола (`naming.node_protocol`): ловит и
  транспорт-уровень — `"xhttp"` → `vless|xhttp|tls`, `"wg"` → wireguard/awg. Для клиентов на
  ванильном sing-box: `["xhttp", "wg"]`;
- `exclude_countries` — 2-буквенные коды стран;
- `exclude_names` — провайдер (или `*` — все) → подстроки исходного названия ноды.

Голые `vless`/`vmess` (без транспорта и reality, напр. XHTTP до поддержки) отбрасываются всегда.

**rename:**
- `labels` — `{метка: [ключевые слова]}`: если в исходном названии есть слово (регистр не
  важен) — нода получает метку в тег. Первое совпадение по порядку;
- `domain_resolver_tag` (деф. `bootstrap`) — ставится в `domain_resolver` каждой ноды (кроме
  wireguard): какой DNS-сервер резолвит её домен. Тег должен быть в `dns.servers` базы.

**selector / urltest** — доп. поля на каждую группу соответствующего типа.

**emit:**
- `nodes_tester` (деф. `true`) — плоский тестовый селектор `nodes-tester` (все ноды прямыми
  членами; тестер выбирает ноду в нём, регион берёт из имени — `recognition: parse`);
- `global_failsafe` (деф. `false`) — `global-auto-out-failsafe` (urltest над региональными
  failsafe) и default селектора `global-auto-out`;
- `ensure_regions` (деф. `["eu","us","other"]`) — регионы, чей `{region}-auto-out` создаётся
  всегда: пустой заполняется всеми нодами (фолбэк), чтобы sing-box не падал по
  «outbound not found». `[]` — выключить.

**raw_user_nodes** (деф. `false`) — `true`: user_nodes копируются как есть, без
переименования/CRC/групп.

## Схема имён

```
{провайдер}-{протокол}-{страна}-out [метка] [CRC]
```
```
"🇫🇮 Finland | Reality"           →  LUNA-vless|reality-fi-out [b40c2d73]
"🇺🇸 USA | OpenAI (ws)"           →  LUNA-vmess|ws|tls-us-out [AI] [eebdd291]
"Server 5" (страна не распознана) →  LUNA-hy2-undef-out [5d8c097c]
```
- **Провайдер** — `tag` подписки из providers.json (не из названия ноды).
- **Протокол** — из выходных параметров ноды, `база|транспорт|маскировка`, дефолты опущены
  ([naming/protocol.py](../naming/protocol.py)): `vless|reality`, `vmess|ws|tls`, `hy2`, `wg`.
- **Страна** — из флаг-эмодзи; нет флага — по названию страны в тексте
  ([naming/regions.py](../naming/regions.py)); нет и его — подсказка `cc_hint` из raw (AWG:
  имя файла); иначе **`undef`**: нода остаётся в выводе, но **не попадает ни в одну группу**.
- **Метка** — из `rename.labels`, отдельная скобка перед `[CRC]`.
- **[CRC]** — CRC32 настроек ноды ([naming/crc.py](../naming/crc.py)): уникальность тегов,
  стабилен между прогонами, меняется только при смене настроек. Тег, метка и
  `domain_resolver` в CRC не входят.

Схема общая с тестером (пакет `naming`): тестер разбирает тег обратно тем же кодом.

## Группы

Ноды делятся на регионы по стране (`eu`, `us`, `ru`, `other` — `naming.coarse_region`).
Структура плоская: ноды региона — прямые члены `{region}-auto-out` (selector, default =
failsafe) и `{region}-auto-out-failsafe` (urltest). Сверху `global-auto-out`. WireGuard/AWG-ноды
пишутся в `endpoints[]` (в sing-box 1.14 wireguard-outbound удалён), группы ссылаются на них
по тегу как обычно.

## user_nodes.json

JSON-список sing-box outbound, добавляемых вручную. Копируются дословно, переписывается только
тег из формы `Provider-CC`:
```
"Sbercloud-RU"  →  Sbercloud-vless|reality-ru-out [..]
```
Тег не в форме `Provider-CC` — нода остаётся с исходным тегом и вне групп.

## Код

| файл | что |
|---|---|
| `build.py` | конвейер: raw → фильтры → rename → CRC → dedupe → группы; отчёт |
| `rename.py` | `group_meta` / `custom_rename` / `rename_user_node` |
| `groups.py` | selector/urltest-группы (параметры — аргументами, без глобалов) |
| `params.py` | загрузка/валидация groups_params (v1 принимается) |
| `migrate.py` | v1 → v2 (`split_v1` — ещё и для обёртки `python -m subscribe`) |
