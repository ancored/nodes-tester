[English](README.md) | **Русский**

# nodes_config

`nodes_config` превращает `raw_nodes.json` ветви в готовый фрагмент конфигурации sing-box. Это
вторая стадия конвейера.

```text
raw_nodes.json + config.json (pipeline.<ветвь>, groups, subscriptions[].exclude_names)
    -> фильтры против падения sing-box
    -> имена и ключи [node_crc.cred_crc]
    -> группы из всех нод
    -> nodes.json (ветвь router) или whnodes.json (ветвь clients)
```

Выход содержит `outbounds` и, при наличии WireGuard или AWG, `endpoints`. Фрагмент нужно
объединить с базовой конфигурацией и проверить тем sing-box, который будет его запускать.

## Запуск

```sh
python3 -m nodes_config -c config.json --branch router \
  --raw data/raw/router.json -o data/nodes.json --json
```

`--raw` можно повторять. Флаг `--check` собирает результат и сравнивает его с текущим файлом,
но ничего не записывает.

Файл заменяется только при изменении JSON. Запись атомарная и защищена lock-файлом. Сводка
`--json` содержит `changed`, `written`, добавленные и удалённые теги, хеши raw и число
отброшенных нод по причинам.

## Порядок обработки

1. Ноды извлекаются из raw с исходным названием и провайдером.
2. Применяются `exclude_types` ветви и `exclude_names` подписки.
3. Голые VLESS и VMess без распознанного транспорта или Reality отбрасываются; применяются
   `exclude_protocols` ветви.
4. Нода получает имя, `domain_resolver` и ключ.
5. Одинаковые доступы схлопываются, ссылки `detour` переводятся на новые имена.
6. Строятся группы.
7. WireGuard endpoints отделяются от обычных outbounds.

Страна нигде не определяется и не фильтруется: её измеряет тестер, и он же решает, какая нода
подходит группе.

## Фильтры

Настройки — в разделе `pipeline.router` или `pipeline.clients` файла
[`config.json`](../docs/CONFIG.ru.md#pipeline):

- `exclude_types` сравнивается с `type` outbound, например `shadowsocksr`;
- `exclude_protocols` сравнивается с токенами дескриптора протокола: `xhttp` ловит
  `vless|xhttp|tls` и `trojan|xhttp|tls`, `wg` — WireGuard и AWG;
- `exclude_names` у подписки ищет подстроки в исходном названии ноды.

`nodes_fetch` эти фильтры не применяет.

### Совместимость ядра

XHTTP и AWG предназначены для `sing-box-lx`. Для обычного sing-box:

```json
"pipeline": { "router": { "exclude_protocols": ["xhttp", "wg"] } }
```

Токен `wg` удаляет и AWG, и обычные WireGuard endpoints. Подробности и проверка кандидата — в
[`docs/SING_BOX_COMPATIBILITY.md`](../docs/SING_BOX_COMPATIBILITY.ru.md).

## Имена и DNS

`domain_resolver` ветви задаёт DNS-сервер для резолва домена ноды; тег должен существовать в
`dns.servers` базового конфига. Для WireGuard поле не добавляется.

```text
{подписка}-{протокол}-out [node_crc.cred_crc]

LUNA-vless|reality-out [b40c2d73.0be1f7a2]
LUNA-vmess|ws|tls-out [eebdd291.5a1c03de]
AWG-wg-out [61df49c0.9d2e4b17]
```

Страны и подписей в имени нет. Свои ноды (подписка `"type": "inline"`) называются так же, по
тегу подписки.

Ключ в квадратных скобках — CRC32 настроек сервера и CRC32 учётных данных (см.
[`naming`](../naming/README.ru.md#ключ-ноды)). Новые учётные данные от провайдера меняют только
вторую часть. Одинаковый доступ из двух подписок (тот же ключ) остаётся одной нодой.

## Группы

Ветвь `router` строит группы из раздела [`groups`](../docs/CONFIG.ru.md#groups). Каждая группа —
selector `{tag}-auto-out`, в котором **все** ноды; какую активировать, решает тестер по проверкам
группы. Кроме нод в селекторе запасные члены:

| Группа | Члены сверх нод | По умолчанию |
|---|---|---|
| нестрогая | `failsafe-out` | `failsafe-out` |
| нестрогая, `direct_fallback` | `failsafe-out`, `direct-out` | `failsafe-out` |
| строгая (`strict`) | `block-out` | `block-out` |
| строгая, `direct_fallback` | `block-out`, `direct-out` | `direct-out` |

- `failsafe-out` — один urltest по всем нодам для всех нестрогих групп; без нод — `direct-out`.
- `block-out` и `direct-out` берутся из базового конфига sing-box (`base.json`), например
  `{"type": "block", "tag": "block-out"}`.
- `global-auto-out` — selector групп с `in_global`.
- `nodes-tester` — плоский selector всех нод для тестера и `failsafe-out`: через него монитор
  зондирует запасной выход групп.

Поля `selector` и `urltest` ветви добавляются ко всем selector-ам и к `failsafe-out`.

Ветвь `clients` строит одну группу: `global-auto-out` со всеми нодами (по умолчанию
`failsafe-out`) и `failsafe-out` — клиентские конфиги ссылаются только на неё.

## Карта кода

| Файл | Ответственность |
|---|---|
| `__main__.py` | CLI, сравнение и атомарная запись |
| `build.py` | фильтры, имена, схлопывание доступов, сборка фрагмента |
| `groups.py` | selector и urltest групп |
