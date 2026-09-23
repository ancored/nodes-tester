# Отчёт ревью (2026-09-21)

Дата: 2026-09-21  
Приоритет ревью: работоспособность; информационная безопасность — вторична, но критичная утечка локальных секретов устранена.

## Объём проверки

Проверены `nodes_tester` (lifecycle, потоки, storage, switcher), HTTP/API/SQLite/config,
Vue SPA и собранная статика. Выполнены полный `unittest`, `compileall`, Vite build,
проверка diff и реальный HTTP-smoke на временном порту.

## Исправленные дефекты

### Critical

1. **Windows path traversal при раздаче статики** (`dashboard/webapp.py`).
   Обратный слэш не учитывался POSIX-нормализацией, после чего Windows мог разрешить путь
   за пределами `dashboard/static`. Исправлено нормализацией обоих разделителей и проверкой
   итогового `realpath` через `commonpath`. Регрессионный тест добавлен.

### High

2. **Повреждение lifecycle `Runner` при ошибке ранней инициализации** (`runner.py`).
   Перехват stdout/stderr и `_running=True` устанавливались до защищающего `try/finally`.
   Теперь весь этап инициализации защищён: потоки восстанавливаются, storage закрывается,
   selector возвращается, `_running` сбрасывается.

3. **Ban превращался в ложное исчезновение ноды и мог удалиться retention-очисткой**
   (`runner.py`, `storage.py`). Физическое присутствие теперь учитывается до фильтрации ban:
   banned-нода не тестируется, но получает `present=1` и свежий `last_seen`.

4. **Частичное переключение selector-цепочки считалось успешным** (`switcher.py`).
   `active`, история и таймеры меняются только при успешном PUT всей цепочки. Ручное
   переключение также принимает только кандидата нужного региона из `board.candidates()`
   (включая heavy-veto).

5. **Редактор менял соседний `config.json`, а не реально загруженный custom config**
   (`api_config.py`). GET/PUT теперь используют точный `cfg.path`.

6. **JSON Schema можно было отключить отсутствующим `$schema` или отсутствием библиотеки**
   (`config.py`, `requirements.txt`). Явно указанный отсутствующий schema-файл — ошибка;
   `jsonschema` стала обязательной зависимостью. Добавлена семантическая проверка параметров
   dashboard, используемых сервером и таймерами.

7. **`providers.json` принимал структуру, которая затем роняла генератор** (`api_config.py`).
   До замены проверяются обязательные `subscribes` (list) и `save_config_path` (непустая строка).

8. **Фоновые worker могли жить после `stop()`, пока Runner уже закрывал SQLite**
   (`monitor.py`, `traffic.py`). Shutdown теперь дожидается фактического завершения worker;
   traffic flush выполняется до закрытия Storage.

9. **Token flow в открытом Vue-разделе не обновлялся** (`api.js`, `App.vue`, `Control.vue`,
   `Config.vue`). Admin token перенесён в общий реактивный `ref`; ввод/очистка немедленно
   включает или блокирует действия без reload/remount.

10. **Перекрывающиеся polling-запросы могли записать устаревший snapshot поверх нового**
    (`store.js`). Введён единый in-flight Promise; ручное и периодическое обновление
    сериализованы.

### Medium / Low

11. Control API валидирует CRC, наличие ноды, тип JSON и числовые query-параметры; вместо
    ложного `200`/orphan-row/`500` возвращаются `400` или `404`.
12. SQLite read-only URI строится через `Path.as_uri()`, поэтому путь с `#` работает.
13. Некорректный/отрицательный/слишком большой `Content-Length` даёт `400`; body ограничен 2 МиБ.
14. Существующий API-путь с неверным методом даёт `405`, а не `404`.
15. Atomic config write дополнен `flush()` + `fsync()` перед `os.replace()`.
16. Отсутствующий JS/CSS теперь даёт 404, а не HTML SPA с кодом 200; Vite использует `base: '/'`,
    поэтому deep URL с завершающим `/` загружает ассеты корректно.
17. Запрос внепланового прогона, пришедший во время последнего прохода при `loop=false`, больше
    не теряется: выполняется ещё один проход.
18. Read-only экран «Прогоны» прекращает polling после первого `409 tester_not_running`.
19. Пагинация не сбрасывается на первую страницу при каждом snapshot refresh.
20. При временной ошибке polling последний валидный snapshot остаётся видимым.
21. Фильтр «в карантине» не смешивает `garbage` и обычный `backoff`.
22. Ошибки загрузки config/providers показываются пользователю, а не маскируются пустым редактором.
23. KPI общего трафика включает direct/other через новый `traffic_total`.
24. Неизвестный hash-route перенаправляется на обзор.
25. Исправлена мобильная компоновка sidebar/topbar/KPI/token input.
26. Тестовые helpers закрывают файлы и копируют schema рядом с временным config.

## Регрессионное покрытие

Добавлены/расширены тесты для:

- lifecycle Runner при ошибке до запуска прохода;
- сохранности ban/present/last_seen;
- partial selector-chain и чужого региона;
- path traversal Windows;
- SQLite-пути с `#`;
- custom config filename;
- schema/config/providers validation;
- неверных JSON/query и неизвестного CRC.

Итог: **97 тестов, OK** (до ревью было 82).

## Реальные проверки

- `PYTHONUTF8=1 PYTHONIOENCODING=utf-8 python -m unittest discover -s tests -v`
  → `Ran 97 tests in 4.585s`, `OK`.
- `python -m compileall -q nodes_tester dashboard naming subscribe` → `COMPILE_OK`.
- `npm run build` → 43 модуля, успешная production-сборка в `dashboard/static`.
- HTTP-smoke реального `ThreadingHTTPServer` на случайном localhost-порту:
  `spa_root=200`, `api_data=200`, `readonly_status=409`, `missing_asset=404`,
  `spa_deep_slash=200`, `malformed_content_length=400`.
- `git diff --check` → ошибок whitespace нет; есть только предупреждения Git о будущей
  конвертации LF→CRLF в уже существующем Windows working tree.

## Оставшиеся риски / отложенные улучшения

Эти пункты не блокируют текущую работоспособность и занесены в `BACKLOG.md`:

1. `dashboard.data.collect()` выполняет разделы через отдельные read-only соединения, поэтому
   очень редкий ответ может смешать соседние SQLite commits. Для строгого snapshot нужно одно
   соединение и одна read transaction.
2. Параллельные PUT config не имеют revision/ETag (last writer wins).
3. `LogRing` может склеить фрагменты двух потоков, если запись текста и `\n` перемежаются.
4. Ответ 405 пока не формирует заголовок `Allow`.
5. Оставшиеся `ResourceWarning` идут из старых `test_retention.py` и
   `test_review_block813.py`; на корректность тестов не влияют, но файлы стоит закрыть.

## Вывод

Критичные и высокие дефекты, влияющие на запуск, сохранность состояния, достоверность
переключения, редактирование конфигурации, shutdown и основной UI-flow, исправлены и покрыты
регрессиями. Проект проходит полный suite, компиляцию, production-сборку и реальный HTTP-smoke.
