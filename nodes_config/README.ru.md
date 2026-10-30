[English](README.md) | **Русский**

# nodes_config

`nodes_config` превращает `raw_nodes.json` в готовый фрагмент конфигурации sing-box роутера, а проверенные тестером ноды
роутера — в клиентский фрагмент. Это вторая стадия конвейера.

```text
raw_nodes.json + config.json (pipeline.router, groups, subscriptions[].exclude_names)
    -> фильтры против падения sing-box
    -> имена и ключи [node_crc.cred_crc]
    -> группы из всех нод
    -> nodes.json

nodes.json + client-candidates.json (тестер) + config.json (pipeline.clients)
    -> клиентские фильтры и domain_resolver
    -> urltest клиентских групп, global-auto-out
    -> whnodes.json
```

Выход содержит `outbounds` и, при наличии WireGuard или AWG, `endpoints`. Фрагмент нужно
объединить с базовой конфигурацией и проверить тем sing-box, который будет его запускать.

## Запуск

```sh
python3 -m nodes_config -c config.json \
  --raw data/raw/router.json -o data/nodes.json --json
```

`--raw` можно повторять. Флаг `--check` собирает результат и сравнивает его с текущим файлом,
но ничего не записывает. Клиентский фрагмент:

```sh
python3 -m nodes_config -c config.json --branch clients \
  --nodes data/nodes.json --candidates data/client-candidates.json -o data/whnodes.json
```

Файл заменяется только при изменении JSON. Запись атомарная и защищена lock-файлом. Сводка
`--json` содержит `changed`, `written`, добавленные и удалённые теги, хеши raw и число
отброшенных нод по причинам.

## Порядок обработки

1. Ноды извлекаются из raw с исходным названием и провайдером.
2. Применяются `exclude_types` ветви и `exclude_names` подписки; нода, у которой сервер — сам роутер
   (`0.0.0.0`, loopback, link-local, multicast, `localhost`), отбрасывается.
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
selector `{tag}-auto-out`, в котором **все** ноды подписок `router` и `both`; какую активировать, решает тестер по
проверкам группы. Кроме нод в селекторе запасные члены:

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
  зондирует запасной выход групп. Ноды подписок `clients` есть только здесь: тестер их проверяет, трафик роутера через них
  не идёт.

Поля `selector` и `urltest` ветви добавляются ко всем selector-ам и к `failsafe-out`.

Ветвь `clients` берёт ноды из `nodes.json`: ноды подписок `clients` и `both`, которые тестер назвал для клиентской группы
(`pipeline.clients.groups`), и ноды, на которые указывает их `detour`. Поверх — фильтры и `domain_resolver` ветви `clients`.
Каждая клиентская группа — urltest `{tag}-auto-out`; группы с `in_global` образуют селектор `global-auto-out`, на который
ссылаются клиентские конфиги. Группа без нод, группа, которую тестер не назвал, и кандидаты старше 30 минут — ошибка:
`whnodes.json` остаётся прежним.

## Карта кода

| Файл | Ответственность |
|---|---|
| `__main__.py` | CLI, сравнение и атомарная запись |
| `build.py` | фильтры, имена, схлопывание доступов, сборка фрагмента |
| `groups.py` | selector и urltest групп |
