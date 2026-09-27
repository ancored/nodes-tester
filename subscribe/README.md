# subscribe

`subscribe` — совместимая обёртка старого интерфейса. Новые конфигурации должны запускать
[`nodes_fetch`](../nodes_fetch/README.md) и [`nodes_config`](../nodes_config/README.md)
раздельно.

```sh
python3 -m subscribe
python3 -m subscribe --config-dir config_whitelist
```

Обёртка читает общий `providers.json` старого формата, временно разделяет его на настройки
загрузки и сборки, запускает обе стадии и пишет файл из `save_config_path`.

Старые поля `exclude_protocol`, `exclude_node_protocols`, `exclude_countries`, `labels` и
`domain_resolver_tag` переводятся в `groups_params` через `nodes_config.migrate.split_v1`.
В частности, прежнее:

```json
{
  "exclude_node_protocols": ["xhttp", "wg"]
}
```

соответствует новому:

```json
{
  "filters": {
    "exclude_protocols": ["xhttp", "wg"]
  }
}
```

Сам `nodes_fetch` старые поля фильтрации игнорирует. Не переносите их в новый
`providers.json` в надежде отфильтровать несовместимые ноды.

Совместимость обёртки проверяется golden-тестами. Она нужна для постепенной миграции, но
скрывает границу между загрузкой и сборкой, поэтому не подходит как основа новых сценариев.
