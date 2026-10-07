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

Поддерживаются `enabled`, `user_agent` или совместимое поле `User-Agent`, `happ_headers` и
`send_device`. Относительные пути считаются от каталога `providers.json`.

### Устройство и заголовки панелей

Панели с лимитом устройств (Marzban, Remnawave и другие) считают устройством каждый новый
`X-Hwid`. У установки свой HWID в `device.json`: рядом с `providers.json`, а для раскладки
`config-main/`, `config-wh/` — уровнем выше, общий для обоих наборов (`--device` задаёт путь
явно). Файл создаётся при первой загрузке случайным.

happ-подписки отправляют HWID и описание устройства всегда; `happ_headers` подписки
переопределяет любой заголовок, включая `X-Hwid`. URL-подписки отправляют их только при
`send_device: true`: часть панелей меняет ответ или занимает слот устройства.

Из ответа сохраняются метаданные панели — в `sources[].meta` raw: `Subscription-Userinfo`
(`upload`, `download`, `total`, `expire`), `Profile-Title` (`title`), `Announce`
(`announce`), `Support-Url`, `Profile-Web-Page-Url`, `Profile-Update-Interval`. Значения
`base64:…` декодируются. При сбое загрузки остаются метаданные последней удачной.

Сеть, тайм-аут, HTTP 429 и 5xx повторяются с паузами 2, 10 и 30 с: разовый сбой панели не
должен оставлять подписку без обновления до следующего прогона.

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

Парсеры находятся в `nodes_fetch/parsers`: `parse_link` разбирает строку подписки,
`FOLDER_FORMATS` — каталоги файлов (AWG), `clash.convert` — записи `proxies` Clash/mihomo,
`xray_json` — JSON-подписка Xray.

Поддерживаются VLESS, VMess, Trojan, Shadowsocks (SIP002 и прежний формат), Hysteria,
Hysteria2, TUIC, AnyTLS, SOCKS, HTTP/HTTPS, WireGuard и AWG. Содержимое подписки
распознаётся само: share-ссылки построчно, base64 от них, Clash YAML, JSON sing-box или
JSON-подписка Xray. ShadowsocksR и другие типы, которых нет в sing-box, пропускаются.

JSON-подписку Xray (массив полных конфигов клиента) отдают панели вроде Remnawave, когда
User-Agent не похож на sing-box или Clash. По конфигу на пункт меню, имя ноды — `remarks`.
Ноды, которые повторяются в нескольких пунктах («Авто», пункты с резервным сервером),
берутся один раз с именем пункта, где нода стоит одна. Mux Xray (mux.cool) не переносится.
Часть панелей выдаёт случайный `short_id` Reality из нескольких допустимых: от загрузки к
загрузке у такой ноды меняется CRC, а с ним и история в тестере.

Нераспознанная или битая строка пропускается с записью в журнал, подписка из-за неё не
падает. Если в ссылке нет имени, тегом становится `тип адрес:порт`.

## Подписки Happ

Ссылка `happ://crypt…/…` содержит зашифрованный адрес подписки. Ключи расшифровки
принадлежат приложению Happ и в nodes-tester не входят. Они скачиваются из
`fetch.happ_keys_url` providers.json — URL или путь к файлу `keys.rs` либо JSON
`{"crypt": [...], "crypt5": {...}}`. По умолчанию это `src/keys.rs` проекта
[Omegaplexx/hpwnr](https://github.com/Omegaplexx/hpwnr). Источник меняется в админке на
вкладке подписок.

Ключи кэшируются в `happ-keys.json` рядом с `device.json`. Кэш обновляется, если его нет
или для ссылки не нашлось ключа, но не чаще раза в 6 часов. Подписка скачивается с
заголовками мобильного Happ; их можно переопределить в `happ_headers`.

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
| `util.py` | HTTP-загрузка подписок |
| `happ.py` | раскрытие и загрузка happ-подписок |
| `happ_crypto.py`, `_chacha.py` | расшифровка `happ://crypt…` (RSA, ChaCha20-Poly1305) |
| `happ_keys.py` | источник и кэш ключей Happ |
| `parsers/` | share-ссылки, Clash и JSON Xray → объекты sing-box; `_common.py` — адрес, TLS, транспорты, XHTTP |
