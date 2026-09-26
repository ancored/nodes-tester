# nodes_fetch: подписки в `raw_nodes.json`

`nodes_fetch` - первая стадия конвейера. Пакет загружает подписки, преобразует записи в outbounds sing-box и пишет `raw_nodes.json`. Он сохраняет состав подписки без фильтрации и переименования. Эти операции выполняет [`nodes_config`](../nodes_config/README.md).

Вход: `providers.json` и доступные источники подписок. Выход: версия сырых нод с отчётом по источникам. Полная схема конвейера приведена в [REFACTOR-MODULES.md](../REFACTOR-MODULES.md).

## Запуск

```bash
python -m nodes_fetch -p config/providers.json -o data/raw/main.json          # записать
python -m nodes_fetch -p config/providers.json --dry-run --only LUNA --json   # проверить подписку
python -m nodes_fetch -p … -o … --only MEDVED   # обновить одну подписку, остальные - из прошлого raw
```

Лог пишется в stderr. Флаг `--json` выводит в stdout число нод и состояние каждой подписки. Код `0` означает успешную запись или проверку, `2` - отказ защитной проверки без изменения файла, `1` - ошибку. Запись атомарная; параллельную запись блокирует файл `<output>.lock`.

## providers.json

Формат v1 (нынешний) принимается как есть; ключи стадии переименования (`save_config_path`,
`exclude_*`, `labels`, `domain_resolver_tag`) fetch игнорирует. Новая секция `fetch`:

| ключ | деф. | смысл |
|---|---|---|
| `timeout` | 120 | таймаут чтения, сек (connect - 30) |
| `retries` | 3 | повторы HTTP при отсутствии ответа |
| `proxy` | null | качать подписки через прокси (`socks5://127.0.0.1:2080`), в т.ч. happ |
| `stale_max_hours` | 48 | упавшая подписка берёт свои ноды из прошлого raw, пока они моложе |
| `min_ratio` | 0.5 | если доля нод относительно прошлого файла ниже порога, не перезаписывать его (`--force` отключает проверку) |

Подписка: `tag` (уникален), `enabled`, и одно из `url` (https / `sub://` / `happ://cryptN/…` /
сама ссылка), `file` (.txt или clash .yaml, путь от providers.json) либо `type: "folder"` с `path`
и необязательными `format`, `ext` для AWG. Также поддерживаются `user_agent` (или старое `User-Agent`) и `happ_headers`.
Упавшая или пустая подписка не валит остальные. Схема - `schemas/providers.schema.json`.

## Парсеры и upstream

Парсеры (`parsers/<схема>.py` с `parse`; folder-форматы - `parse_file`) - форк
[Toperlock/sing-box-subscribe](https://github.com/Toperlock/sing-box-subscribe), синхронизирован
по коммит `558731c` (2026-09-23). Протоколы: `http`, `socks5`, `shadowsocks`, `shadowsocksR`,
`vmess`, `trojan`, `vless`, `tuic`, `hysteria`, `hysteria2`, `wireguard`, `anytls`, AWG (folder).
Намеренные отличия от upstream: `xhttp` (vless/trojan), `alpn`/`fp`/`encryption` в vless,
`ast.literal_eval` вместо `eval` в ss, `packet_encoding: xudp` по умолчанию в vless/vmess (поле
входит в CRC - upstream его убрал). Следующая сверка: `git diff 558731c HEAD -- parsers` в клоне
upstream; локальный импорт `import tool` заменён на `from .. import util`.

## Контракт выхода

`nodes_common/raw.py` и `schemas/raw_nodes.schema.json`: конверт `{version, generated_at,
providers, nodes_hash, sources[], nodes[]}`, узел - `{provider, title, cc_hint, outbound}`
(outbound без `tag` и `_`-полей; исходное имя - в `title`).
