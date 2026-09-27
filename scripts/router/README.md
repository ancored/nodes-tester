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
nodes_fetch
    -> raw/main.json
    -> nodes_config
    -> nodes.json
    -> update-rules.sh
    -> apply-nodes.sh
```

Если guard загрузки вернул код `2`, pipeline сохраняет прошлый raw и продолжает сборку на
нём. Другие ошибки fetch или config останавливают процесс до изменения рабочего sing-box.

`--dry-run` всё равно загружает источники и пишет промежуточный raw, но итоговый фрагмент
идёт в отдельный файл. `apply-nodes.sh` объединяет его с базой и запускает `sing-box check`,
не заменяя рабочий конфиг.

Именно dry-run следует использовать для проверки XHTTP и AWG. Обычный sing-box не примет
расширения `sing-box-lx`, если они не удалены фильтром. Подробности:
[`docs/SING_BOX_COMPATIBILITY.md`](../../docs/SING_BOX_COMPATIBILITY.md).

## Применение и откат

`apply-nodes.sh` выполняет следующую последовательность:

1. `sing-box merge` базового конфига и `nodes.json` во временный файл.
2. `sing-box check` тем бинарником, который установлен на роутере.
3. Сравнение списка тегов и итогового файла.
4. Сохранение предыдущего `config.json`.
5. Замена конфига и перезапуск только при изменениях.
6. Проверка реального запроса через Clash API и боевую группу.
7. Автоматический возврат предыдущего конфига при потере связности.

Невалидный кандидат не заменяет рабочий файл. Перезапуск может кратко оборвать сеть и SSH,
если доступ идёт через этот же прокси.

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

## Клиентская ветка

```sh
nodes-tester pipeline clients --dry-run
nodes-tester pipeline clients
```

Она использует `config-wh`, создаёт `whnodes.json` и передаёт его в `build-clients.sh`.
Нужны собственные базовые конфиги клиентов, `clients.list` и инструменты в `REPO_DIR`.
Установка пакета их не создаёт.

## Файлы

| Скрипт | Ответственность |
|---|---|
| `pipeline.sh` | последовательность fetch, config и применения для router или clients |
| `apply-nodes.sh` | merge, check, diff, перезапуск, health-check и откат |
| `update-rules.sh` | обновление rule-set перед сборкой рабочего конфига |
| `build-clients.sh` | сборка клиентских конфигураций |
| `backup-nodes-tester.sh` | снимок пакета, конфигов, данных и БД перед обновлением |
| `rollback-nodes-tester.sh` | откат пакета и данных на снимок |
| `shadow-pipeline.sh` | теневой прогон без применения |
| `switch-to-pipeline.sh` | однократный переход старого cron на новый pipeline |

## Переменные окружения

`pipeline.sh` использует:

- `PROJECT_DIR` — корень установленного кода;
- `DATA` — каталог raw и кандидатов;
- `CFG_ROOT` — каталог `config-main` и `config-wh`;
- `REPO_DIR` — база sing-box, правила и клиентская инфраструктура.

Пакетная команда заполняет основные пути из UCI. Для ручного запуска указывайте их явно,
если расположение отличается от старых `/root/...` значений.
