# subscribe — совместимая обёртка (до Ф6 рефактора)

Бывший переименователь (форк [Toperlock/sing-box-subscribe](https://github.com/Toperlock/sing-box-subscribe))
разобран на две стадии конвейера:

- [`nodes_fetch`](../nodes_fetch/README.md) — подписки → `raw_nodes.json` (парсеры, happ, AWG);
- [`nodes_config`](../nodes_config/README.md) — raw → `nodes.json` (фильтры, имена, CRC, группы).

Здесь остался только прежний интерфейс для роутерных скриптов, на конфигах **v1**:

```bash
python -m subscribe                      # config/providers.json (+groups_params, user_nodes)
python -m subscribe --config-dir DIR     # другой набор, напр. config_whitelist
```

v1 (общий `providers.json` с `save_config_path`, фильтрами, labels) переводится в v2 на лету
(`nodes_config.migrate.split_v1`), результат — в `save_config_path`. Результат совпадает с
прежним генератором (golden-тест, `tests/test_golden.py`). Удаляется на Ф6, когда скрипты
перейдут на `python -m nodes_fetch` + `python -m nodes_config`.
