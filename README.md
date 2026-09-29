# nodes-tester

Управление прокси-нодами sing-box на OpenWrt: загрузка подписок, сборка нод и групп, проверки качества, автоматическое переключение и веб-админка.

Проект работает с уже установленным sing-box. Он не устанавливает прокси-ядро и не настраивает маршрутизацию роутера за пользователя. Для первого запуска нужны SSH-доступ и знание основ конфигурации sing-box.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/overview.png">
  <source media="(prefers-color-scheme: light)" srcset="docs/screenshots/overview-light.png">
  <img alt="Обзор состояния nodes-tester в веб-админке" src="docs/screenshots/overview-light.png">
</picture>

<p align="center">
  Обзор системы, ноды, результаты проверок и управление в одном интерфейсе.
  <a href="docs/screenshots/README.md">Все экраны</a>
</p>

## Начать

1. Скачать пакет из [релизов](https://github.com/andreydyadyk/nodes-tester/releases).
2. Пройти [установку и первый запуск на OpenWrt](openwrt/README.md).
3. Открыть [руководство по админке и повседневной работе](docs/USAGE.md).

Готовый релиз `v0.1.5` содержит `.apk` для OpenWrt 25.x. Для OpenWrt 24.10 нужен отдельно собранный `.ipk`. Пакет пока не подписан; порядок установки и ограничения описаны в инструкции.

В исходниках готовится версия 0.2.0 с управлением конвейером из админки и локальными файлами sing-box. Она ещё не выпущена и не установлена на роутер.

## Что важно знать

- Парсеры понимают XHTTP и AmneziaWG, но обычный sing-box их не принимает. Для этих нод
  нужен `sing-box-lx`; с обычным ядром их следует исключить на стадии `nodes_config`.
  См. [совместимость с ядрами sing-box](docs/SING_BOX_COMPATIBILITY.md).
- **Сохранить подписки**, **применить новые ноды к sing-box** и **проверить текущие ноды** — разные операции. Сохранение в админке не запускает обновление sing-box.
- Проверки создают сетевой трафик и скачивают тестовые файлы. Автоматическое переключение может менять рабочие селекторы; в шаблоне оно включено. Для знакомства с системой его можно отключить.
- Админка встроена в тестер. После установки сервис и веб-интерфейс выключены, пока вы их не настроите и не включите.
- Рабочие конфиги содержат секреты. Не публикуйте URL подписок, токены, базу статистики или полный диагностический вывод. Не выставляйте админку напрямую в интернет.

## Документация модулей

Основной README оставляет только маршрут первого запуска. Устройство компонентов описано
рядом с их кодом:

| Компонент | Описание |
|---|---|
| [`nodes_fetch`](nodes_fetch/README.md) | источники, парсеры, last-good и формат `raw_nodes.json` |
| [`nodes_config`](nodes_config/README.md) | фильтры, имена, CRC, группы и `nodes.json` |
| [`nodes_tester`](nodes_tester/README.md) | проверки, рейтинг, переключение, монитор и SQLite |
| [`nodes_admin`](nodes_admin/README.md) | конвейер, расписание, правила и миграция в 0.2.0 |
| [`dashboard`](dashboard/README.md) | режимы HTTP-сервера, API, доступ и работа с файлами |
| [`web`](web/README.md) | исходники Vue, сборка и правила интерфейса |
| [`nodes_common`](nodes_common/README.md) | контракт raw, атомарная запись и блокировки |
| [`naming`](naming/README.md) | идентичность, протоколы, страны, регионы и CRC |
| [`scripts/router`](scripts/router/README.md) | применение, проверка, откат и клиентский конвейер |
| [`subscribe`](subscribe/README.md) | совместимость со старым общим конфигом |

Отдельно: [совместимость sing-box](docs/SING_BOX_COMPATIBILITY.md),
[повседневная работа](docs/USAGE.md) и [пакет OpenWrt](openwrt/README.md).

## Если устанавливаете из исходников

Нужен Python 3.8 или новее. Из корня репозитория выполните `python3 -m pip install -r requirements.txt`, создайте рабочие конфиги из [шаблонов](config/), подключите sing-box по той же схеме, что в инструкции OpenWrt, и запускайте `python3 -m nodes_tester -c config/config.json`. Вместо пакетной команды `nodes-tester fetch/config` используются `python3 -m nodes_fetch` и `python3 -m nodes_config`.

Готовая веб-статика включена в репозиторий; Node.js для запуска не нужен. Для сборки OpenWrt-пакета есть [скрипт SDK](openwrt/build.sh), для параметров команд — `--help`, для доступных настроек — [схемы](schemas/) и [шаблон конфига](config/config.example.json).

Парсеры подписок основаны на [Toperlock/sing-box-subscribe](https://github.com/Toperlock/sing-box-subscribe).
