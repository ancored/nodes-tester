# nodes_config

`nodes_config` превращает один или несколько `raw_nodes.json` в готовый фрагмент
конфигурации sing-box. Это вторая стадия конвейера.

```text
raw_nodes.json + groups_params.json + user_nodes.json
    -> фильтры
    -> имена, регионы и CRC
    -> selector и urltest группы
    -> nodes.json
```

Выход содержит `outbounds` и, при наличии WireGuard или AWG, `endpoints`. Фрагмент нужно
объединить с базовой конфигурацией и проверить тем sing-box, который будет его запускать.

## Запуск

```sh
python3 -m nodes_config \
  --raw data/raw/main.json \
  --groups config/groups_params.json \
  --user-nodes config/user_nodes.json \
  -o data/nodes.json \
  --json
```

`--raw` можно повторять. `--groups` и `--user-nodes` необязательны. Флаг `--check`
собирает результат и сравнивает его с текущим файлом, но ничего не записывает.

Файл заменяется только при изменении JSON. Запись атомарная и защищена lock-файлом.
Сводка `--json` содержит `changed`, `written`, добавленные и удалённые теги, хеши raw и
число отброшенных нод по причинам.

Старые конфиги можно разделить на стадии командой:

```sh
python3 -m nodes_config migrate --from config-v1/ --to config-v2/
```

Исходный каталог не меняется. `--force` разрешает заменять уже существующие файлы в
каталоге назначения.

## Порядок обработки

Порядок важен для стабильности имён и CRC:

1. Ноды извлекаются из raw с исходными `title`, `provider` и `cc_hint`.
2. Применяются `exclude_types` и `exclude_names`.
3. Применяются `exclude_protocols`; голые VLESS и VMess без распознанного транспорта или
   Reality отбрасываются.
4. Определяются метки, страна и регион; создаётся новое имя.
5. Добавляются `domain_resolver`, фильтр стран и CRC.
6. Дубликаты схлопываются, ссылки `detour` переводятся на новые имена.
7. Строятся региональные selector и urltest группы.
8. WireGuard endpoints отделяются от обычных outbounds.

## `groups_params.json`

Схема: [`schemas/groups_params.schema.json`](../schemas/groups_params.schema.json).

```json
{
  "filters": {
    "exclude_types": ["shadowsocksr"],
    "exclude_protocols": ["xhttp", "wg"],
    "exclude_countries": ["cn", "ru"],
    "exclude_names": {
      "LUNA": ["promo"],
      "*": []
    }
  },
  "rename": {
    "labels": {
      "AI": ["OpenAI", "Claude"]
    },
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
    "global_failsafe": false,
    "ensure_regions": ["eu", "us", "other"]
  },
  "raw_user_nodes": false
}
```

### Фильтры

- `exclude_types` сравнивается с `type` outbound, например `shadowsocksr`.
- `exclude_protocols` сравнивается с токенами дескриптора протокола. `xhttp` ловит
  `vless|xhttp|tls` и `trojan|xhttp|tls`; `wg` ловит WireGuard и AWG.
- `exclude_countries` содержит двухбуквенные коды стран.
- `exclude_names` ищет подстроки в исходном названии. Ключ — тег провайдера или `*`.

`nodes_fetch` не применяет эти фильтры. Они работают только во время `nodes_config`.

### Совместимость ядра

XHTTP и AWG предназначены для `sing-box-lx`. Для обычного sing-box используйте:

```json
{
  "filters": {
    "exclude_protocols": ["xhttp", "wg"]
  }
}
```

Токен `wg` удаляет и AWG, и обычные WireGuard endpoints. Если обычный WireGuard нужен,
не подключайте источник папки AWG и проверьте поддержку endpoints в своей версии ядра.

Подробное объяснение и проверка кандидата находятся в
[`docs/SING_BOX_COMPATIBILITY.md`](../docs/SING_BOX_COMPATIBILITY.md).

### Имена и DNS

`rename.labels` добавляет метку по первому совпавшему ключевому слову исходного имени.
`rename.domain_resolver_tag` задаёт DNS-сервер для резолвинга домена ноды. Указанный тег
должен существовать в `dns.servers` базового конфига. Для WireGuard поле не добавляется.

Имя ноды:

```text
{провайдер}-{протокол}-{страна}-out [метка] [CRC]
```

Примеры:

```text
LUNA-vless|reality-fi-out [b40c2d73]
LUNA-vmess|ws|tls-us-out [AI] [eebdd291]
AWG-wg-de-out [61df49c0]
```

Страна определяется по флагу, полному названию страны или `cc_hint`. Для AWG подсказкой
служит имя файла. Нода со страной `undef` остаётся в выводе, но не входит в региональные
группы.

CRC32 считается от настроек ноды без `tag`, `domain_resolver` и служебных полей. Он
стабилен, пока настройки ноды не меняются, и используется тестером как идентификатор.

### Группы

Группы задаются списком `groups`. Для каждой включённой группы создаются:

- `{name}-auto-out` — selector;
- `{name}-auto-out-failsafe` — urltest с теми же leaf-нодами.

```json
{
  "regions": { "asia": ["jp", "sg", "kr"] },
  "groups": [
    { "name": "eu", "match": { "regions": ["eu"] } },
    { "name": "us", "match": { "regions": ["us"] } },
    { "name": "other", "enabled": false, "match": { "regions": ["other"] } },
    { "name": "ai", "in_global": false, "match": { "labels": ["AI"], "exclude_countries": ["tr"] } }
  ]
}
```

- `enabled` (по умолчанию `true`) — создавать ли группу;
- `in_global` (`true`) — входит ли selector группы в `global-auto-out`;
- `fallback` (`true`) — если под условия не попала ни одна нода, заполнить группу всеми
  нодами, чтобы ссылки базового конфига не стали недействительными;
- `match` — условия отбора: `countries`, `regions`, `labels` и их исключающие варианты
  `exclude_countries`, `exclude_regions`, `exclude_labels`. Условия объединяются через И,
  значения внутри условия — через ИЛИ. Без условий в группу попадают все ноды.

Нода может входить в несколько групп: например, в `eu` и в `ai`. Тестер выбирает активную
ноду в каждой группе отдельно. Состав групп он берёт из работающего sing-box, отдельной
настройки групп в тестере нет.

Регион — именованный список стран. Встроенные: `eu` (европейские страны), `us`, `ru` и
`other` — страны вне `eu`, `us` и `ru`. Встроенный регион можно переопределить в `regions`,
там же добавляются свои.

`global-auto-out` объединяет selector групп с `in_global`. При `emit.nodes_tester: true`
создаётся плоский selector `nodes-tester` из нод, попавших хотя бы в одну группу; из него
тестер выбирает leaf-ноды напрямую.

Если `groups` не задан, действует прежняя схема: группы `eu`, `us`, `ru`, `other` по
регионам нод, а `emit.ensure_regions` (по умолчанию `eu`, `us`, `other`) задаёт группы,
которые создаются даже без собственных нод.

Группы и регионы можно настроить в админке: «Подписки и сборка» → «Параметры сборки».

## `user_nodes.json`

Это JSON-массив вручную заданных outbounds. При `raw_user_nodes: false` они проходят
переименование и группировку. При `raw_user_nodes: true` копируются дословно, без фильтров,
CRC и добавления в автоматически построенные группы.

`raw_user_nodes: true` обходит в том числе фильтры совместимости. Ответственность за поля
таких нод лежит на авторе файла.

## Карта кода

| Файл | Ответственность |
|---|---|
| `__main__.py` | CLI, сравнение и атомарная запись |
| `build.py` | полный конвейер фильтрации и сборки |
| `params.py` | дефолты и проверка `groups_params.json` |
| `rename.py` | метаданные, страны и имена |
| `groups.py` | selector и urltest группы |
| `migrate.py` | переход со старого общего конфига на две стадии |
