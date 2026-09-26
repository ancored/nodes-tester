# subscribe: временная обёртка совместимости

> Статус: устаревающий интерфейс. Используйте `nodes_fetch` и `nodes_config` для новых настроек. Обёртка останется до фазы Ф6 рефакторинга.

Бывший переименователь (форк [Toperlock/sing-box-subscribe](https://github.com/Toperlock/sing-box-subscribe))
разобран на две стадии конвейера:

- [`nodes_fetch`](../nodes_fetch/README.md) - преобразует подписки в `raw_nodes.json` (парсеры, happ, AWG);
- [`nodes_config`](../nodes_config/README.md) - преобразует raw в `nodes.json` (фильтры, имена, CRC, группы).

Здесь сохранён прежний интерфейс для роутерных скриптов и конфигов v1:

```bash
python -m subscribe                      # config/providers.json (+groups_params, user_nodes)
python -m subscribe --config-dir DIR     # другой набор, напр. config_whitelist
```

Обёртка переводит общий `providers.json` v1 в формат v2 через `nodes_config.migrate.split_v1` и пишет результат в `save_config_path`. Совместимость с прежним генератором проверяет `tests/test_golden.py`.
