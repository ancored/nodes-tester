[English](README.md) | **Русский**

# scripts/router

Скрипты связывают Python-стадии с рабочей конфигурацией sing-box на роутере. Они отвечают
за расписание, правила, merge, проверку кандидата, перезапуск и откат.

Пакет OpenWrt предоставляет обёртку `nodes-tester`, поэтому вручную вызывать файлы обычно
не требуется.

## Основной конвейер

```sh
nodes-tester pipeline router --dry-run
nodes-tester pipeline router
```

Режим `router` выполняет:

```text
nodes_fetch                       (все подписки обеих ветвей)
    -> raw/router.json            (pipeline.sh fetch — только этот шаг)
    -> nodes_config
    -> nodes.json
    -> update-rules.sh
    -> apply-nodes.sh
```

Если guard загрузки вернул код `2`, pipeline сохраняет прошлый raw и продолжает сборку на
нём. Другие ошибки fetch или config останавливают процесс до изменения рабочего sing-box.

`--dry-run` всё равно загружает источники и пишет промежуточный raw, но итоговый фрагмент
идёт в отдельный файл. Правила в этом режиме не обновляются. `apply-nodes.sh` объединяет
фрагмент с базой и запускает `sing-box check`, не заменяя рабочий конфиг.

Именно dry-run следует использовать для проверки XHTTP и AWG. Обычный sing-box не примет
расширения `sing-box-lx`, если они не удалены фильтром. Подробности:
[`docs/SING_BOX_COMPATIBILITY.md`](../../docs/SING_BOX_COMPATIBILITY.ru.md).

## Пресеты правил и режим `apply`

Схема всей сборки: [`docs/CONFIG_ASSEMBLY.md`](../../docs/CONFIG_ASSEMBLY.ru.md).

Правила маршрутизации и DNS можно вынести из `base.json` в пресеты — отдельные JSON-файлы
`singbox/presets/*.json`. Пресет — фрагмент конфига sing-box (любые разделы) с метаданными
в ключе `_preset`: `title`, `description`, `enabled`, `priority`, `priorities` и
`requires.groups`.

`apply-nodes.sh` склеивает базу, `nodes.json` и включённые пресеты (`PRESETS_DIR`) своим
склейщиком (`python3 -m nodes_admin.presets assemble`): база выше всех, пресеты — по
`priority`, а для путей из `priorities` — по нему; внутри пресета порядок не меняется.
Одинаковые элементы схлопываются, тот же тег с другим содержимым — ошибка. Подробности —
[`docs/CONFIG_ASSEMBLY.md`](../../docs/CONFIG_ASSEMBLY.ru.md#склейка).

```sh
nodes-tester pipeline apply --dry-run
nodes-tester pipeline apply
```

Режим `apply` не загружает подписки: он копирует `singbox/base.json` и применяет базу и
пресеты к текущему `nodes.json`. `--dry-run` собирает кандидата из редактируемой базы и
пресетов и выполняет `sing-box check`, ничего не меняя. Этот же режим запускает страница
«Правила» в админке. Примеры пресетов: `/usr/share/nodes-tester/examples/singbox/presets/`.

## Применение и откат

`apply-nodes.sh` выполняет следующую последовательность:

1. Склейка базы, `nodes.json` и включённых пресетов во временный файл (`nodes_admin.merge`).
2. `sing-box check` тем бинарником, который установлен на роутере.
3. Сравнение списка тегов и итогового файла.
4. Сохранение предыдущего `config.json`.
5. Замена конфига и перезапуск только при изменениях итогового конфига. Обновлённые `.srs` и
   source-наборы в `/etc/sing-box/rules/` sing-box версии 1.10 и новее перечитывает без перезапуска.
6. Проверка связности: URL-тест `failsafe-out` (все ноды, `HEALTH_GROUP`) через API-сервис sing-box
   (`services[type=api]`); от выбора нод в группах она не зависит.
7. Автоматический возврат предыдущего конфига при потере связности.

Если sing-box остановлен из админки (есть флаг `STOP_FLAG`, по умолчанию
`/var/run/nodes-tester/singbox-stopped`), применение завершается ошибкой без замены конфига и
без запуска sing-box.

Невалидный кандидат не заменяет рабочий файл. Перезапуск может кратко оборвать сеть и SSH,
если доступ идёт через этот же прокси.

С момента сохранения `config.json.prev` скрипт игнорирует TERM, INT и HUP до завершения
проверки связности или отката. Оркестратор при таймауте ждёт всю группу процессов до
300 секунд после TERM и только затем посылает SIGKILL оставшимся процессам. Окно
применения закрывается после завершения всей группы. Перед установкой на роутер проверьте
это на тестовой конфигурации: запустите применение с заведомо неработающим кандидатом,
во время проверки связности отправьте TERM группе конвейера и убедитесь, что скрипт
вернул прежний `config.json`, перезапустил sing-box и сохранил связность. Автоматический
локальный тест этой секции не запускается: в скрипте указан абсолютный путь
`/etc/init.d/sing-box`, а `pidof sing-box` и API-сервис проверяют реальный сервис.

## Резервная копия и откат

```sh
nodes-tester backup [--stable] [--pkg FILE]
nodes-tester rollback [-y] [--keep-db] [--check] [SNAPSHOT_DIR]
```

`backup-nodes-tester.sh` читает `config_dir` и `data_dir` из `/etc/config/nodes-tester` и
создаёт `/root/backups/nodes-tester-<дата>/`. Сервис не останавливается.

| Файл снимка | Содержимое |
|---|---|
| `files.tar.gz` | `config_dir`, `/etc/config/nodes-tester`, `data_dir` без БД, `/etc/sing-box` |
| `code.tar.gz` | файлы установленного пакета без конфигов |
| `stats.db.gz` | копия через SQLite backup API с `integrity_check` |
| `nodes-tester-<версия>.apk` | пакет из `--pkg` или `/root/packages/` |
| `crontab.txt`, `enabled.txt` | задачи nodes-tester и автозапуск |
| `MANIFEST`, `SHA256SUMS`, `rollback.sh` | описание, контрольные суммы и скрипт отката |

`--stable` переводит ссылку `/root/backups/nodes-tester-stable` на новый снимок. Её по
умолчанию использует откат.

`rollback-nodes-tester.sh` проверяет контрольные суммы и архивы. `--check` на этом
останавливается. Полный откат:

1. Останавливает сервис и сохраняет текущие файлы, crontab и версию в
   `/root/backups/failed-<дата>/`.
2. Переустанавливает пакет из снимка. Без файла пакета возвращает код из `code.tar.gz`,
   но пакетный менеджер продолжит показывать новую версию.
3. Возвращает конфиги, данные и, без `--keep-db`, БД снимка.
4. Заменяет в crontab только строки nodes-tester и восстанавливает автозапуск.
5. Возвращает конфиг sing-box, если он изменился и проходит `sing-box check`; иначе sing-box
   не трогает.
6. Запускает сервис.

Старые снимки не удаляются автоматически.

<a id="clients"></a>
## Клиентская ветвь

```sh
nodes-tester pipeline clients --dry-run
nodes-tester pipeline clients
```

Клиентская ветвь подписки не загружает: их все загружает конвейер роутера, а тестер проверяет и ноды клиентских подписок (в sing-box
роутера они есть только в селекторе `nodes-tester`). Режим `clients` выполняет:

```text
nodes.json + client-candidates.json     (тестер раз в 5 минут пишет ноды каждой клиентской группы)
    -> nodes_config --branch clients    (клиентские фильтры и domain_resolver; группа без нод или
                                         данные старше 30 минут останавливают запуск)
    -> whnodes.json                     (urltest {группа}-auto-out, selector global-auto-out)
    -> build-clients.sh
```

`build-clients.sh` берёт клиентов из раздела `clients` в `config.json` (только включённых). Для каждого он склеивает базу
`singbox/clients/base_<имя>.json` с `whnodes.json` и включёнными пресетами `singbox/clients/presets/*.json` (локальные наборы правил
в них заменяются на `remote` с адресом из `singbox/clients/settings.json`) или берёт готовый файл `singbox/clients/publish/<имя>.json`
как есть. Каждый конфиг проходит `sing-box check`; не прошедший остаётся прежним. Готовый конфиг попадает в `<output>/<имя>.json` и
`<output>/by-token/<токен>.json` (`pipeline.clients.output`, по умолчанию `/etc/sing-box-clients`). Файлы в `by-token/` удалённых и
выключенных клиентов и старых токенов удаляются: их ссылки перестают работать. Пробный запуск собирает в `$DATA/clients-dry` и
сравнивает с опубликованными конфигами.

Веб-сервер раздаёт только `by-token/`, имя файла — токен. Пример для Caddy:

```caddyfile
router.example:8443 {
    @client path_regexp client ^/([A-Za-z0-9_-]{16,})$
    handle @client {
        root * /etc/sing-box-clients/by-token
        rewrite * /{re.client.1}.json
        file_server
    }
    handle {
        respond 404
    }
}
```

Новый клиент или смена токена не требуют перезагрузки веб-сервера. Журнал доступа не включайте: в него попадут токены.

Режим `apply-clients` пересобирает клиентов по текущему `whnodes.json`:

```sh
nodes-tester pipeline apply-clients --dry-run
nodes-tester pipeline apply-clients
```

## Файлы

| Скрипт | Ответственность |
|---|---|
| `pipeline.sh` | последовательность fetch, config и применения роутера; сборка клиентов из проверенных нод |
| `apply-nodes.sh` | merge, check, diff, перезапуск, health-check и откат |
| `update-rules.sh` | обновление rule-set перед сборкой рабочего конфига |
| `build-clients.sh` | сборка клиентских конфигураций |
| `backup-nodes-tester.sh` | снимок пакета, конфигов, данных и БД перед обновлением |
| `rollback-nodes-tester.sh` | откат пакета и данных на снимок |

## Переменные окружения

`pipeline.sh` использует:

- `PROJECT_DIR`: корень установленного кода;
- `DATA`: каталог raw и кандидатов;
- `CFG_ROOT`: каталог `config.json` и `singbox/`;
- `CONFIG_DIR`: база sing-box, правила и клиентские источники в `singbox/`;
- `SINGBOX`: каталог исходников sing-box (по умолчанию `$CFG_ROOT/singbox`), пресеты — в `presets/`.

Пакетная команда заполняет основные пути из UCI. Без неё действуют пути пакета:
`DATA=/opt/nodes-tester`, `CFG_ROOT=/etc/nodes-tester`.
