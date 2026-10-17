[English](SING_BOX_COMPATIBILITY.md) | **Русский**

# Совместимость с sing-box

[О проекте](../README.ru.md) · [`nodes_fetch`](../nodes_fetch/README.ru.md) · [`nodes_config`](../nodes_config/README.ru.md)

## Главное

Успешный разбор подписки не означает, что полученную ноду примет установленное ядро.
`nodes_fetch` умеет разбирать XHTTP и AmneziaWG (AWG), но эти расширения рассчитаны на
[Leadaxe/sing-box-lx](https://github.com/Leadaxe/sing-box-lx). Обычный sing-box их не
поддерживает.

Если на роутере установлен обычный sing-box, исключите XHTTP и AWG до создания
`nodes.json`. Иначе итоговый конфиг может не пройти `sing-box check`, а sing-box не примет
его при перезапуске.

| Нода | Что создаёт парсер | Требуемое ядро |
|---|---|---|
| VLESS или Trojan с XHTTP | `transport.type: "xhttp"` | `sing-box-lx` |
| AmneziaWG из `.conf` | endpoint `type: "wireguard"` с полями AWG | `sing-box-lx` |
| Обычный WireGuard | endpoint `type: "wireguard"` без полей AWG | зависит от версии обычного sing-box |

Версию и возможности ядра всё равно нужно проверять командой `sing-box version` и на
итоговом объединённом конфиге. Эта таблица описывает расширения, которые добавлены именно
для `sing-box-lx`.

## Версия ядра для тестера

Тестер управляет sing-box через API-сервис (`services[].type = "api"`), который появился в
sing-box 1.14. Нужна версия 1.14 или новее с включённым сервисом:

```json
"services": [
  { "type": "api", "tag": "api", "listen": "127.0.0.1", "listen_port": 9090,
    "secret": "<секрет>", "dashboard": true }
]
```

Через этот сервис тестер выбирает ноды в selector-группах, проверяет связность после
применения конфига и получает поток событий соединений для точного учёта трафика. Clash API
(`experimental.clash_api`) не используется. Сервис отдаёт и официальную веб-панель sing-box
(`dashboard`). Режимы `clash_mode` в правилах работают и без Clash API; после перезапуска
действует режим `Rule`, пока его не переключат в панели.

## Где возникает несовместимость

Конвейер разделён на стадии:

```text
config.json (subscriptions)
    -> nodes_fetch
    -> raw_nodes.json
    -> nodes_config
    -> nodes.json
    -> склейка с базой и пресетами, sing-box check
```

`nodes_fetch` сохраняет всё, что смог разобрать. Он не знает, какое ядро установлено на
роутере, и не применяет фильтры сборки. Благодаря этому один raw-файл можно
использовать для разных целей.

Совместимость с целевым ядром задаёт `nodes_config`. Он вычисляет дескриптор протокола:

- XHTTP содержит токен `xhttp`, например `vless|xhttp|tls`;
- WireGuard и AmneziaWG содержат токен `wg`.

Затем `exclude_protocols` ветви (`pipeline.router` или `pipeline.clients` в `config.json`)
удаляет ноды с указанными токенами до переименования, группировки и записи `nodes.json`.

## Обычный sing-box: безопасная настройка

В `config.json` добавьте фильтр ветви:

```json
"pipeline": {
  "router": { "exclude_protocols": ["xhttp", "wg"] },
  "clients": { "exclude_protocols": ["xhttp", "wg"] }
}
```

Токен `wg` исключает все WireGuard endpoints, включая обычный WireGuard. Если обычный
WireGuard нужен, а AWG нет, уберите из `subscriptions` источник папки с
`type: "folder", format: "awg"`. Тогда AWG не попадёт в raw.
После этого решите отдельно, поддерживает ли ваша версия ядра обычные WireGuard endpoints.

Фильтр задаётся только в разделе `pipeline`: `nodes_fetch` фильтров не применяет.

## sing-box-lx: оставить XHTTP и AWG

Если установлен `sing-box-lx`, не добавляйте `xhttp` и `wg` в `exclude_protocols`. Для AWG
добавьте в `subscriptions` источник папки:

```json
{
  "type": "folder",
  "path": "awg",
  "format": "awg",
  "ext": ".conf",
  "tag": "AWG"
}
```

Каждый `.conf` становится отдельным endpoint. Страну ноды измеряет тестер, имя файла на
неё не влияет. DNS из файла не переносится,
потому что DNS настраивается централизованно в sing-box.

XHTTP извлекается из VLESS и Trojan share-ссылок с `type=xhttp`. Параметры транспорта и
`extra` переводятся в поля `sing-box-lx`; camelCase ключи XMUX нормализуются в snake_case.

Версии форка и формата конфигурации должны совпадать. Перед применением всегда проверяйте
кандидат тем бинарником, который реально запускается на роутере.

## Перечитывание локальных правил

После обновления `.srs` и JSON source rule-set в `/etc/sing-box/rules/` sing-box версии 1.10 и новее перечитывает изменённые файлы без перезапуска. Поэтому конвейер перезапускает sing-box только при изменении итогового конфига, `base.json` или при явном принудительном применении. На более старом ядре изменённые локальные правила начнут действовать при следующем перезапуске. Перед настройкой правил проверьте установленную версию командой `sing-box version`.

## Проверка перед применением

Для пакетной установки на OpenWrt:

```sh
nodes-tester pipeline router --dry-run
```

Этот режим скачивает подписки и собирает кандидат, затем склеивает его с базой и пресетами и выполняет
`sing-box check`, но не заменяет рабочий конфиг и не перезапускает sing-box. При ошибке
прочитайте вывод `sing-box check`: неизвестный транспорт XHTTP или неизвестные поля AWG
означают, что фильтр не включён либо используется неподходящее ядро.

Для ручного запуска можно отдельно проверить сборку:

```sh
python3 -m nodes_fetch -c config/config.json --branch router -o data/raw/router.json --json
python3 -m nodes_config -c config/config.json --branch router --raw data/raw/router.json \
  -o data/nodes.json --check --json
```

`nodes_config --check` проверяет только сборку фрагмента. Окончательную совместимость
подтверждает `sing-box check` после объединения фрагмента с базовым конфигом.
