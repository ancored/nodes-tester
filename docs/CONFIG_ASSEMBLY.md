# Как собирается конфиг sing-box

[О проекте](../README.md) · [Руководство](USAGE.md) · [Скрипты роутера](../scripts/router/README.md)

Итоговый `/etc/sing-box/config.json` на роутере собирается из трёх цепочек: ноды из
подписок, база с наборами правил и пресеты правил. Все три сходятся в `apply-nodes.sh`.

<p align="center"><img alt="Схема сборки итогового конфига sing-box" src="config-assembly.svg" width="680"></p>

## Входы

| Что | Где лежит | Кто обрабатывает | Результат |
|---|---|---|---|
| Подписки | `config-main/providers.json` | `nodes_fetch` — загрузка подписок | `raw/main.json` — сырые ноды |
| Рецепт сборки нод | `config-main/groups_params.json`, `user_nodes.json` | `nodes_config` — фильтры, имена, группы | `/etc/sing-box-subscribe/nodes.json` — ноды и группы `{group}-auto-out`, `{group}-auto-out-failsafe`, `global-auto-out`, `nodes-tester` |
| База | `singbox/base.json` | `nodes_admin.rules` — копирует | `/etc/sing-box/base.json` |
| Источники наборов правил | `singbox/rules.json` | `nodes_admin.rules` — загрузка правил | `/etc/sing-box/rules/*` |
| Пресеты правил | `singbox/presets/*.json` | `nodes_admin.presets` — удаляет блок `_preset { }`, сортирует по приоритету | `/tmp/tmp.XXXXXX/30-preset-***.json` |

`singbox/` — каталог `/etc/nodes-tester/singbox/` пакетной установки; эти файлы правятся в
админке («Файлы sing-box», «Правила»). `config-main/` — «Подписки и сборка».

База содержит то, что нужно всегда: inbound'ы, общие DNS-серверы, `final`, API-сервис.
Правила маршрутизации и DNS, а также DNS-серверы и наборы правил, нужные только им, лежат
в пресетах. Пресет — фрагмент конфига sing-box с метаданными `_preset` (название, включён
ли, приоритет, нужные группы).

## Склейка

`apply-nodes.sh` кладёт входы в один временный каталог (`mktemp -d`, например
`/tmp/tmp.aB3dEf/`) и склеивает их через `sing-box merge`:

```text
10-nodes.json        ← /etc/sing-box-subscribe/nodes.json
20-base.json         ← /etc/sing-box/base.json
30-preset-000.json   ← включённые пресеты: правила маршрута — по priority,
30-preset-001.json     правила DNS — по dns_priority
…
```

`sing-box merge` склеивает входы в порядке **имён файлов**, а не аргументов `-c`; массивы
(`route.rules`, `dns.rules`, `dns.servers`, `route.rule_set`, `outbounds`) дописываются в
этом порядке. Поэтому порядок задают префиксы `10-`, `20-`, `30-`. Файл пресета как есть
`sing-box merge` не примет (`unknown field "_preset"`) — блок удаляется перед склейкой.

Одинаковые элементы массивов не схлопываются: DNS-сервер или `rule_set`, описанный в двух
файлах, попадёт в конфиг дважды, и `sing-box check` такой дубль пропускает. Поэтому объект с
`tag` описывается ровно в одном входе, остальные ссылаются на него по тегу. Скаляры
(`route.final`, `dns.strategy`) повторять можно, но при разных значениях остаётся значение из
**первого** по имени файла: пресет не переопределит скаляр базы.

Дальше:

1. Проверка повторяющихся тегов (`inbounds`, `outbounds`+`endpoints`, `dns.servers`,
   `route.rule_set`, `services`) и `sing-box check` итогового файла; ошибка — работающий
   конфиг не трогается. Админка делает ту же проверку при сохранении базы и пресетов.
2. Сравнение с работающим `/etc/sing-box/config.json`; совпадает — ничего не делается.
3. Замена конфига и перезапуск sing-box.
4. URL-тест группы `global-auto-out` через API-сервис sing-box; нет связности — возврат
   `config.json.prev` и перезапуск на нём.

Сохранение базы или пресета в админке прогоняет склейку и `sing-box check` так же, во
временном каталоге, и не записывает файл, который sing-box не примет.

## Кто запускает

| Команда | Что делает |
|---|---|
| `nodes-tester pipeline router` | все три цепочки: подписки, сборка нод, база и правила, применение; по расписанию или из «Конвейера» |
| `nodes-tester pipeline apply` | база и пресеты к текущему `nodes.json`, без загрузки подписок; кнопка «Применить» в «Правилах» |
| `--dry-run` у обеих | собирает кандидата из редактируемых файлов и проверяет его, ничего не меняя |

## После сборки

`nodes-tester` работает с запущенным sing-box через API-сервис (`services[type=api]`):
читает группы и выбирает в них ноды, получает поток соединений для учёта трафика. Тесты
идут через отдельный inbound `nodes-tester-in` и плоский selector `nodes-tester`.

Клиентская ветка (`pipeline clients`) устроена отдельно: `config-wh/` → `whnodes.json`,
склейка с `singbox/clients/base_<имя>.json` через `merge-configs.py` → `/etc/sing-box-clients/`.
Роутерный sing-box она не трогает.
