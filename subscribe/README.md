# subscribe — переименователь нод из подписок

Часть репозитория **nodes-tester**. Читает подписки, парсит каждую ноду в формат
sing-box outbound, переименовывает по единой схеме (общий пакет [`naming`](../naming)),
строит selector/urltest-группы и пишет фрагмент конфига `{"outbounds": [...]}`.

Форк-минимализация [Toperlock/sing-box-subscribe](https://github.com/Toperlock/sing-box-subscribe):
без веб-сервера, Vercel/Docker и шаблонов.

Результат мёржится в базовый конфиг sing-box:
```
sing-box merge <output.json> -c config.json -c nodes.json
```

## Запуск

```bash
python -m subscribe
```
Из корня репозитория (там на пути общий пакет `naming`). Зависимости — в общем
`requirements.txt` (`PyYAML`, `ruamel.yaml`).

## Конфиги (папка `config/`)

- **`config/providers.json`** — подписки и параметры.
- **`config/user_nodes.json`** — ноды, добавляемые вручную.
- **`config/groups_params.json`** — параметры групп + флаг эмиссии тестовых селекторов.

### providers.json

```json
{
  "subscribes": [
    { "url": "https://example.com/api/v1/client/subscribe?token=xx",
      "tag": "LUNA", "User-Agent": "curl" }
  ],
  "save_config_path": "/etc/sing-box-subscribe/nodes.json",
  "exclude_protocol": "ssr",
  "exclude_countries": ["cn", "ua"],
  "domain_resolver_tag": "bootstrap",
  "labels": { "AI": ["Gemini", "OpenAI", "Claude"], "Media": ["Netflix"] }
}
```

- `url` — **обязателен**. V2 (base64), Clash, sing-box подписка или локальный файл.
- `tag` — **обязателен**. Имя **провайдера** в переименованных нодах и группах
  (`tag: "LUNA"` → `LUNA-…-out`). Даёт осмысленный tag каждой подписке.
- `User-Agent` — опц. UA при запросе подписки (некоторые провайдеры отдают разный
  формат под разный UA).
- `save_config_path` — **обязателен**. Куда пишется результат.
- `exclude_protocol` — опц. протоколы через запятую, которые пропускать (`ssr,vmess`).
- `exclude_countries` — опц. список 2-буквенных кодов стран, чьи ноды выкидываются.
- `domain_resolver_tag` — опц. (по умолчанию `bootstrap`). Ставится в `domain_resolver`
  каждой ноды — какой DNS-сервер (по тегу) резолвит её домен. Тег должен быть в
  `dns.servers` базового конфига.
- **`labels`** — опц. `{метка: [ключевые слова]}`. Если в исходном названии ноды есть
  ключевое слово — нода получает эту **метку** в тег (см. ниже). Первое совпадение
  по порядку в объекте.

Прочие поля (читаются, но по умолчанию отсутствуют): `enabled`, `subgroup`, `prefix`,
`ex-node-name`, `auto_backup`. **Не включайте `emoji`** — он вставляет флаг в начало
названия, что ломает определение страны.

## Схема имён

Тег ноды собирается как:
```
{провайдер}-{протокол}-{страна}-out [метка] [CRC]
```
Примеры:
```
"🇫🇮 Finland | Reality"           →  LUNA-vless|reality-fi-out [b40c2d73]
"🇺🇸 USA | OpenAI (ws)"           →  LUNA-vmess|ws|tls-us-out [AI] [eebdd291]
"Server 5" (страна не распознана) →  LUNA-hy2-undef-out [5d8c097c]
```

- **Провайдер** — `tag` подписки (не из названия ноды). Может содержать дефис.
- **Протокол** — из ВЫХОДНЫХ параметров ноды (не из названия), формат
  `база|транспорт|маскировка`, дефолты опущены (см. [naming/protocol.py](../naming/protocol.py)):
  `vless|reality`, `vless|grpc|reality`, `vmess|ws|tls`, `trojan|tls`, `hy2`, `ss`, `wg`.
- **Страна** — 2-буквенный код из флаг-эмодзи; если флага нет — по полному названию
  страны в тексте ([naming/regions.py](../naming/regions.py)); если не определилась —
  **`undef`** (нода всё равно переименовывается и группируется, регион `other`).
- **Метка** (опц.) — из `providers.json.labels` по ключевым словам в исходном названии.
  В теге — отдельная скобка **перед** `[CRC]`.
- **[CRC]** — 8-hex CRC32 отпечаток настроек ноды ([naming/crc.py](../naming/crc.py)):
  делает теги уникальными, стабилен между прогонами, меняется только при смене
  настроек ноды. Метка и `domain_resolver` в CRC НЕ входят.

Схема имён — **общая с тестером** (пакет `naming`): тег, собранный здесь, тестер
разбирает обратно тем же кодом.

## Группы (`groups.py`)

После сборки нод `groups.build` добавляет selector/urltest-группы. Ноды делятся на
регионы по стране (`ru`, `us`, `eu`, `other`) — через общий `naming.coarse_region`.
Структура **плоская**: ноды каждого региона — прямые члены `{region}-auto-out`
(selector, default = failsafe) и `{region}-auto-out-failsafe` (urltest); промежуточных
провайдер/протокол-групп между `-auto-out` и нодами нет. Сверху `global-auto-out`
собирает регионы (+ опц. `global-auto-out-failsafe` при `emit.global_failsafe`).

**`config/groups_params.json`** — параметры групп и что генерировать:
```json
{
  "selector": { "interrupt_exist_connections": true },
  "urltest":  { "interval": "5m", "tolerance": 100, "idle_timeout": "5m", "interrupt_exist_connections": true },
  "emit": { "nodes_tester": true }
}
```
- `selector`/`urltest` — доп. поля на каждую группу соответствующего типа.
- **`emit.nodes_tester`** — создавать ли плоский тестовый селектор `nodes-tester`
  (все ноды прямыми членами; тестер выбирает ноду прямо в нём, регион берёт из имени
  ноды — `recognition: parse`). `false` — не создавать (напр. для клиентских конфигов).
- **`emit.ensure_regions`** — список регионов (деф. `["eu","us","other"]`), для которых
  `{region}-auto-out` создаётся всегда: пустой регион заполняется всеми нодами (фолбэк),
  чтобы основной конфиг sing-box не падал по «outbound not found». `[]` — выключить.

## user_nodes.json

Опц. JSON-список sing-box outbound-объектов, добавляемых вручную. Копируются в вывод
**дословно** (server/uuid/tls не трогаются), переписывается только тег из `Provider-CC`:
```
"Sbercloud-RU"  →  Sbercloud-vless|reality-ru-out [..]
```

## Поддерживаемые протоколы

`http`, `socks5`, `shadowsocks`, `shadowsocksR`, `vmess`, `trojan`, `vless`, `tuic`,
`hysteria`, `hysteria2`, `wireguard`, `anytls`. Парсеры — в `subscribe/parsers/`
(файл `<protocol>.py` с функцией `parse`).

## Credits

- [sing-box](https://github.com/SagerNet/sing-box)
- Upstream: [Toperlock/sing-box-subscribe](https://github.com/Toperlock/sing-box-subscribe)
