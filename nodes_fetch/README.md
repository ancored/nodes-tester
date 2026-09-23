# nodes_fetch — подписки → raw_nodes.json

Первая стадия конвейера (см. [REFACTOR-MODULES.md](../REFACTOR-MODULES.md)). «Честное зеркало»
подписок: скачивает, парсит в sing-box outbounds и пишет `raw_nodes.json`. Ничего не фильтрует
и не переименовывает — это задача `nodes_config`. От `naming` не зависит.

```bash
python -m nodes_fetch -p config/providers.json -o data/raw/main.json          # записать
python -m nodes_fetch -p config/providers.json --dry-run --only LUNA --json   # проверить подписку
python -m nodes_fetch -p … -o … --only MEDVED   # обновить одну подписку, остальные — из прошлого raw
```

Лог — stderr; `--json` — сводка в stdout (число нод, по подпискам: ok/stale/ошибка/типы).
Выход: `0` записано / dry-run, `2` сработал guard (файл не тронут), `1` ошибка.
Запись атомарная, с lock-файлом `<output>.lock`.

## providers.json

Формат v1 (нынешний) принимается как есть; ключи стадии переименования (`save_config_path`,
`exclude_*`, `labels`, `domain_resolver_tag`) fetch игнорирует. Новая секция `fetch`:

| ключ | деф. | смысл |
|---|---|---|
| `timeout` | 120 | таймаут чтения, сек (connect — 30) |
| `retries` | 3 | повторы HTTP при отсутствии ответа |
| `proxy` | null | качать подписки через прокси (`socks5://127.0.0.1:2080`), в т.ч. happ |
| `stale_max_hours` | 48 | упавшая подписка берёт свои ноды из прошлого raw, пока они моложе |
| `min_ratio` | 0.5 | guard: нод стало меньше доли от прошлого → не перезаписывать (`--force`) |

Подписка: `tag` (уникален), `enabled`, и одно из `url` (https / `sub://` / `happ://cryptN/…` /
сама ссылка), `file` (.txt или clash .yaml, путь от providers.json), `type: "folder"` + `path`
(+ `format`, `ext`; AWG). `user_agent` (или старое `User-Agent`), `happ_headers`.
Упавшая или пустая подписка не валит остальные. Схема — `schemas/providers.schema.json`.

## Контракт выхода

`nodes_common/raw.py` и `schemas/raw_nodes.schema.json`: конверт `{version, generated_at,
providers, nodes_hash, sources[], nodes[]}`, узел — `{provider, title, cc_hint, outbound}`
(outbound без `tag` и `_`-полей; исходное имя — в `title`).
