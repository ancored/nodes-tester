# web

`web` содержит исходники Vue 3 интерфейса. Python-бэкенд находится в
[`dashboard`](../dashboard/README.md), а пользовательское руководство — в
[`docs/USAGE.md`](../docs/USAGE.md).

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="../docs/screenshots/mobile-overview.png">
    <source media="(prefers-color-scheme: light)" srcset="../docs/screenshots/mobile-overview-light.png">
    <img alt="Мобильный обзор nodes-tester" src="../docs/screenshots/mobile-overview-light.png" width="360">
  </picture>
  &nbsp;
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="../docs/screenshots/mobile-nodes.png">
    <source media="(prefers-color-scheme: light)" srcset="../docs/screenshots/mobile-nodes-light.png">
    <img alt="Список нод на мобильном экране" src="../docs/screenshots/mobile-nodes-light.png" width="360">
  </picture>
</p>

Остальные экраны: [`docs/screenshots`](../docs/screenshots/README.md).

## Разработка

```sh
cd web
npm install
npm run dev
```

Vite перенаправляет `/api` на локальный Python-сервер согласно `vite.config.js`. Для
полноценной проверки действий нужен встроенный dashboard с живым Runner; отдельный сервер
подходит для просмотра и работы с файлами.

## Сборка

```sh
cd web
npm run test
npm run build
```

`npm run test` запускает unit-тесты Node и проверку рендера. `npm run build` пишет файлы
не в `web/dist`, а сразу в `dashboard/static`. После изменения интерфейса нужно коммитить
исходники и обновлённую статику вместе.

## Устройство

| Путь | Назначение |
|---|---|
| `src/router.js` | разделы, вложенная навигация по нодам и карточка ноды |
| `src/store.js` | общий снимок данных и автообновление |
| `src/api.js` | HTTP, токен, сессия и обработка ошибок |
| `src/query.js` | состояние фильтров, сортировки и страницы в URL |
| `src/status.js` | единый словарь статусов ноды |
| `src/ux.js` | общие пользовательские проверки и сообщения |
| `src/components/` | таблица, легенда и действия над нодой |
| `src/views/` | страницы админки |

Router использует hash-history, поэтому статический сервер не должен знать маршруты SPA.
Фильтры и сортировка хранятся в URL и переживают автообновление данных.

## Правила интерфейса

- отсутствие данных не следует выдавать за нулевое значение;
- рейтинг и физические метрики показываются отдельно;
- опасные действия требуют подтверждения и объясняют последствия;
- интерфейс различает отдельный dashboard и встроенный Runner;
- токен администратора не подставляется в URL и может храниться только по явному выбору;
- сохранение providers не изображается как применение новых нод к sing-box.
