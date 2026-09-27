# nodes_fetch

`nodes_fetch` загружает источники нод и преобразует их в промежуточный
`raw_nodes.json`. Это первая стадия конвейера. Она не переименовывает ноды, не строит
группы и не решает, поддерживает ли их установленный sing-box.

```text
providers.json + URL, файлы, happ, папки AWG
    -> nodes_fetch
    -> raw_nodes.json
```

Следующая стадия, [`nodes_config`](../nodes_config/README.md), применяет фильтры и создаёт
фрагмент конфигурации sing-box.

## Запуск

```sh
python3 -m nodes_fetch \
  -p config/providers.json \
  -o data/raw/main.json

python3 -m nodes_fetch \
  -p config/providers.json \
  -o data/raw/main.json \
  --only LUNA --json

python3 -m nodes_fetch \
  -p config/providers.json \
  --dry-run --json
```

Параметры:

| Параметр | Назначение |
|---|---|
| `-p`, `--providers` | входной `providers.json` |
| `-o`, `--output` | выходной `raw_nodes.json` |
| `--only TAG` | обновить указанный источник, остальные взять из прошлого raw |
| `--dry-run` | загрузить и разобрать источники без записи файла |
| `--force` | записать результат, даже если сработал защитный порог |
| `--json` | вывести машинную сводку в stdout; обычный журнал остаётся в stderr |

Коды завершения: `0` — успех, `2` — защитная проверка отказалась заменять прошлый raw,
`1` — ошибка. Запись атомарная и защищена lock-файлом.

## Источники

Каждая запись `subscribes` содержит уникальный `tag` и один источник:

- `url`: HTTP(S), отдельная share-ссылка, `sub://` или `happ://`;
- `file`: локальная текстовая подписка или Clash YAML;
- `type: "folder"`: каталог файлов, сейчас используется для AWG `.conf`.

Поддерживаются `enabled`, `user_agent` или совместимое поле `User-Agent`, а также
`happ_headers`. Относительные пути считаются от каталога `providers.json`.

Секция `fetch` управляет загрузкой:

| Поле | По умолчанию | Назначение |
|---|---:|---|
| `timeout` | 120 | общий таймаут чтения в секундах |
| `retries` | 3 | повторные HTTP-попытки |
| `proxy` | `null` | прокси для загрузки, например `socks5://127.0.0.1:2080` |
| `stale_max_hours` | 48 | сколько можно использовать last-good данные упавшего источника |
| `min_ratio` | 0.5 | минимальная доля нод относительно прошлого raw |

Ошибка одного источника не останавливает остальные. Если есть пригодный прошлый raw,
источник может перейти в состояние `stale`. После сборки guard сравнивает общее число нод
с прошлым файлом и при резком падении оставляет старый файл целым.

Полная схема входа: [`schemas/providers.schema.json`](../schemas/providers.schema.json).

## Парсеры

Парсеры находятся в `nodes_fetch/parsers`. Обычные share-схемы реализуют `parse`, а
форматы папок — `parse_file`.

Поддерживаются HTTP, SOCKS, Shadowsocks, ShadowsocksR, VMess, Trojan, VLESS, TUIC,
Hysteria, Hysteria2, WireGuard, AnyTLS и AWG. Happ-ссылки сначала раскрываются до обычной
подписки.

Парсеры основаны на
[Toperlock/sing-box-subscribe](https://github.com/Toperlock/sing-box-subscribe), но проект
содержит собственные изменения: XHTTP для VLESS и Trojan, дополнительные поля VLESS,
безопасный разбор Shadowsocks, AWG и сохранение `packet_encoding: xudp`.

## XHTTP и AWG

`nodes_fetch` намеренно разбирает XHTTP и AWG, даже если на машине установлен обычный
sing-box:

- XHTTP становится транспортом `type: "xhttp"` у VLESS или Trojan;
- каждый AWG `.conf` становится WireGuard endpoint с расширенными полями AmneziaWG;
- имя AWG-файла из двух букв используется как `cc_hint`, например `de.conf` даёт `de`.

Эти расширения предназначены для `sing-box-lx`. Обычный sing-box их не принимает.
Парсер не отбрасывает их сам, потому что raw может использоваться несколькими сборками.

Для обычного sing-box задайте в `groups_params.json` следующей стадии:

```json
{
  "filters": {
    "exclude_protocols": ["xhttp", "wg"]
  }
}
```

Подробности, включая различие AWG и обычного WireGuard, приведены в
[руководстве по совместимости](../docs/SING_BOX_COMPATIBILITY.md).

## Формат `raw_nodes.json`

Контракт находится в [`nodes_common/raw.py`](../nodes_common/raw.py) и
[`schemas/raw_nodes.schema.json`](../schemas/raw_nodes.schema.json).

```json
{
  "version": 1,
  "generated_at": "2026-09-27T12:00:00Z",
  "providers": "providers-main",
  "nodes_hash": "sha256:...",
  "sources": [],
  "nodes": [
    {
      "provider": "LUNA",
      "title": "Finland Reality",
      "cc_hint": null,
      "outbound": {"type": "vless"}
    }
  ]
}
```

`outbound` не содержит `tag` и служебные поля с `_`. Исходное имя хранится в `title`.
`nodes_hash` считается от канонического JSON массива `nodes` и меняется только при смене
его содержимого.

## Карта кода

| Файл | Ответственность |
|---|---|
| `__main__.py` | CLI, lock, guard и атомарная запись |
| `fetch.py` | загрузка providers, last-good и итоговая сводка |
| `sources.py` | URL, файл, папка и happ как источники |
| `util.py` | общие функции парсеров, HTTP и XHTTP |
| `happ.py`, `happ_decode.py` | раскрытие happ-подписок |
| `parsers/` | преобразование протоколов в объекты sing-box |
