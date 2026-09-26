# OpenWrt-пакет nodes-tester

Пакет `nodes-tester` ставит на роутер все компоненты проекта: `nodes_fetch`,
`nodes_config`, `nodes_tester`, веб-админку `dashboard` и скрипты конвейера. Код — чистый
Python, поэтому пакет один на все архитектуры (`PKGARCH:=all`). Для OpenWrt 25.x он
собирается в `.apk`, для 24.10 и старше — в `.ipk`.

## Сборка

Нужен Docker. Сборка идёт в официальном OpenWrt SDK:

```bash
openwrt/build.sh                    # SDK x86-64-25.12.2 → dist/nodes-tester-<версия>.apk
openwrt/build.sh mediatek-filogic-24.10.4   # другой SDK (формат пакета — по версии OpenWrt)
```

Зависимости из фидов OpenWrt при сборке не компилируются — они записаны в метаданные пакета
(`EXTRA_DEPENDS`), и `apk` ставит готовые на роутере. Поэтому сборка занимает секунды; SDK
кешируется в docker-томе `nodes-tester-sdk-<тег>` (сбросить: `docker volume rm …`).

## Установка

```bash
scp -O dist/nodes-tester-*.apk root@<роутер>:/tmp/
ssh root@<роутер> apk add --allow-untrusted /tmp/nodes-tester-*.apk
```

`--allow-untrusted` нужен, пока пакет не подписан ключом своего репозитория. Зависимости
(`python3-light`, `python3-requests`, `python3-jsonschema`, `python3-ruamel-yaml` и др.)
`apk` подтянет из фидов OpenWrt сам. PySocks в фидах нет — он вложен в пакет.

## Что где лежит

| Путь | Что | При обновлении пакета |
|---|---|---|
| `/usr/lib/nodes-tester/` | код (Python-пакеты, `scripts/router/`) | заменяется |
| `/etc/config/nodes-tester` | UCI: пути, включение сервиса | сохраняется |
| `/etc/nodes-tester/config.json` | конфиг тестера и админки | сохраняется |
| `/etc/nodes-tester/config.schema.json` | JSON Schema конфига | заменяется |
| `/etc/nodes-tester/config-main/`, `config-wh/` | `providers.json`, `groups_params.json`, `user_nodes.json` для конвейера | сохраняются |
| `/opt/nodes-tester/` (`data_dir`) | `results/stats.db`, raw подписок, кандидаты конфига sing-box | не трогается |
| `/usr/share/nodes-tester/examples/` | шаблоны конфигов | заменяются |

`data_dir` должен быть на постоянном носителе: `/tmp` и `/var` на OpenWrt находятся в RAM.
На роутерах с маленьким флешем его лучше вынести на USB (`uci set nodes-tester.main.data_dir=…`).

## Первый запуск

При первой установке создаются `config.json` (со случайным `dashboard.token`),
`config-main/` и `config-wh/` из шаблонов. Дальше:

1. Настроить sing-box (SOCKS5 inbound, селектор `nodes-tester`, route-правило) — см.
   [README](../README.md#1-настройка-sing-box-один-раз).
2. В `/etc/nodes-tester/config.json` вписать `clash_api.secret`, при необходимости
   включить `dashboard.enabled`; в `config-main/providers.json` — подписки.
3. Включить и запустить тестер:
   ```sh
   uci set nodes-tester.tester.enabled=1 && uci commit nodes-tester
   /etc/init.d/nodes-tester start
   ```

Веб-админка встроена в тестер: включается блоком `dashboard` в `config.json`
(`enabled`, `host`, `port`, `token`), отдельного сервиса нет.

## Команда `nodes-tester`

Запускает компоненты с путями из UCI (рабочий каталог — `data_dir`):

```sh
nodes-tester fetch -p /etc/nodes-tester/config-main/providers.json --dry-run --json
nodes-tester config --raw /opt/nodes-tester/raw/main.json --groups … -o … --check
nodes-tester tester --list-tests
nodes-tester vacuum
nodes-tester pipeline router --dry-run
```

## cron

Пакет не добавляет задания в cron: конвейер `pipeline.sh` применяет конфиг к sing-box и
перезапускает его, это решение пользователя. Пример для `/etc/crontabs/root`:

```
0 4 1 * * nodes-tester vacuum
0 3 * * * nodes-tester pipeline router >> /var/log/nodes-pipeline.log 2>&1
```

`pipeline router` использует `update-rules.sh` и `apply-nodes.sh`: базовый конфиг
`/etc/sing-box/base.json` и репозиторий правил (`REPO_DIR`, по умолчанию `/root/singbox-repo`).
