# Бэклог nodes-tester

Отложенные задачи и идеи. Приоритет сверху вниз внутри раздела.

---

## Активные

### 0. Актуализировать `score.md` (док-долг)

`score.md` описывает `score.csv` и отдельный тест `stability`, которых уже нет: рейтинг
живёт в таблице `scores` (SQLite), `stability` слит в единый `download`. Формулы, веса,
EWMA/gate/avail — верны. Нужно: заменить упоминания `score.csv` → таблица `scores`,
раздел про `stability` → показатели из `download` (`throttle_ratio`/`hold_ratio`),
блок «файл рейтинга» → строка таблицы `scores`. Низкий риск, чисто документация.

### 0b. Тесты падают на дефолтной Windows-консоли (cp1251) из-за Unicode в `print`

`python -m unittest discover -s tests` на cp1251-консоли даёт `errors=4` —
`UnicodeEncodeError` при печати `→`/`…` (SUT/тесты печатают в stdout, а
`_force_utf8_output` есть только в CLI-точках входа, не в тестах). Функционально 67/67
зелёные под `PYTHONUTF8=1`. Варианты: (а) задокументировать запуск с UTF-8 (сделано в README); (б) добавить в `tests/__init__` или общий `setUp` форс UTF-8 stdout; (в) убрать
печать из горячих путей под тестом. Приоритет низкий (грабли среды, не баг логики).

### Идеи из README «Дальше» (не начаты)

- **Многогрупповость `testing_groups`** — сейчас используется только первая группа
  (`Config.testing_group`). Гонять несколько тест-юнитов параллельно с раздельным стейтом.
- **Новые тесты:** upload speed, streaming-unlock (Netflix/YouTube premium), DNS-leak.
- **Слияние тестера и переименователя в один pipeline / один `nodes.json`.** Общий пакет
  `naming` (идентичность/CRC/протокол/регионы) уже выделен и используется обоими — база
  под слияние заложена; остаётся единый запуск/поток данных.

### 1. Дашборд «Результаты тестов»: фильтры по провайдеру / протоколу / стране

Добавить фильтры к таблице результатов (`renderResults` в `server.py`): по провайдеру,
протоколу и стране (cc). Данные уже есть в строках (`provider`/`protocol`/`country`).
Реализация — своим кодом (как region-табы/пагинация): селекторы/чипы над таблицей,
состояние в реестре (переживает refresh), фильтрация `X.rows` перед пагинацией; смена
фильтра сбрасывает страницу. Можно скомбинировать несколько фильтров (provider AND protocol
AND cc). Без библиотек.

### 2. Дашборд: сортировка колонок (опция, по желанию)

Пагинация и визуал сделаны своим кодом (см. архив). Осталась **опциональная** сортировка
по клику на заголовок (и, возможно, поиск). Можно добавить своим кодом (клик по `th` →
sort ключа, состояние в реестре как у пагинации/табов) — без gridjs. Низкий приоритет;
дашборд считаем готовым. Брать только если реально захочется.

### 3. Архитектурно правильные whitelist / backup списки нод

Сейчас whitelist/backup-наборы генерируются, но по сути «висят» рядом. Продумать модель,
где они **генерируются, но не используются и не тестируются**, пока не наступит одно из
условий: (а) пользователь явно выбрал `clash_mode = whitelist`; (б) тесты показали, что
умерли все обычные ноды — тогда автоматический переход на `backup`. Нужен «спящий» статус
набора (не грузим в тест-план, не тратим трафик), триггер активации и аккуратная стыковка
со switcher (какой набор сейчас «боевой»). Связано с двухуровневым тестированием и
garbage-нодами (см. активные задачи по тестеру).

### 4. Триггер обновления подписок при сильной деградации нод

Если деградация нод достигает большого порога (напр. доля мёртвых по региону/всего выше X%,
или все ноды региона garbage) — дать триггер на запуск обновления подписок
`sing-box-config/update-singbox-config.sh` (внешний скрипт-генератор). Продумать: порог,
антидребезг (не дёргать чаще раза в N часов), кто вызывает (тестер после прогона), и что
делать, если генерация не помогла. Связано с emergency-историей и garbage-нодами.

---

## Реализовано (архив — краткий журнал)

- **subscribe — поддержка happ://crypt5 (RSA + ChaCha20-Poly1305):** дореализован формат
  crypt5 в `subscribe/happ_decode.py` (порт hpwnr): `block_pair_swap` payload → 8-символьный
  маркер выбирает RSA-ключ PKCS#8 (36 ключей в `subscribe/happ_keys_crypt5.py`, RSA-4096) →
  RSA/PKCS#1 v1.5 даёт 32-байтный ChaCha-ключ → тело расшифровывается ChaCha20-Poly1305. Две
  раскладки тела (legacy / salted+XOR), верную выбирает Poly1305-тег. Добавлен чистый-Python
  ChaCha20-Poly1305 `subscribe/_chacha.py` (RFC 8439, без внешних зависимостей — работает на
  роутере; проверен официальными тест-векторами RFC). `happ.py`/`main.py` уже маршрутизировали
  любой `happ://crypt*` через `decode_link`, так что подписки crypt5 подхватываются
  автоматически. Проверено RFC-векторами и round-trip'ом (encrypt↔decode, обе раскладки).
- **Фикс распознавания страны AWG-нод (`subscribe/parsers/awg.py` + `tool.group_meta`):** у
  .conf нет ни флага, ни названия страны — cc только в имени файла; старый хардкод-словарь
  `_COUNTRY` на 6 стран давал остальным тег `"CH | AWG"` без флага → `group_meta` → `undef`.
  Ввёл чёткий приоритет определения страны в `group_meta`: **флаг в подписке → `country_from_text`
  → `_file_cc`** (подсказка из имени файла для folder-парсеров) → `undef`. `awg.py` ставит
  `node['_file_cc']=stem` (без синтетических флагов), `main` чистит `_file_cc` перед выводом.
  CRC не меняется (`_`-поля/tag не входят в payload) → история нод сохраняется.
- **Дашборд — жизненный цикл и деградация нод (Phase 2):** новые секции поверх БД-слоя:
  «Мусорные / деградирующие ноды» — фильтр-чипы provider/cc/protocol (как в трафике), поля
  в-конфиге-с/статус(backoff|карантин)/провалов/×в-мусоре/посл.мусор/время-в-мусоре/until/
  ● удалена (по `nodes.present`); «Динамика выбытия» — inline-SVG график появилось/выбыло по
  дням (`node_events`); «Долгожители» (живые по возрасту ↓) и «Быстро выпадающие» (по сроку
  жизни до 1-го garbage ↑); спарклайн истории рейтинга (`score_history`) колонкой в «Рейтинге».
  `dashboard/data.py`: `_event_stats/_garbage_table/_degradation/_attrition/_score_spark`;
  `server.py`: `filtered()`/`plain()`/`attritionChart()`/`spark()`. Всё stdlib + inline-SVG.
- **Всё состояние — в SQLite (убран зоопарк csv/json):** `score.csv` → таблица `scores`;
  `switch_state.json` → `switch_state`/`switch_recent`/`switch_activations` (нормализованно);
  файловый Reporter (`results.*`) убран — сырьё уже в таблице `results`; `report` слит до
  `{console}`. Новые таблицы `score_history` (score/s_run/gate — каждая нода каждый проход) и
  `node_events` (пер-нодный журнал: `added`/`removed` — появление/выбытие из подписки на КАЖДОМ
  переходе, флаг `nodes.present`; `backoff`/`garbage`/`recovered` — здоровье; `reconcile_presence`
  каждый прогон) — фундамент под историю рейтинга и деградацию/выбытие нод в дашборде.
  `Scoreboard`/`Switcher` работают через `Storage`; `scoring`/
  `switching` теперь требуют `storage.enabled`; при старте — однократная миграция старых файлов
  из каталога БД (`migrate_legacy`, идемпотентно). Дашборд (`data.py`) и тесты читают из БД.
  Каскад `cleanup()` по crc и возрастной кап расширены на новые таблицы. Убраны поля
  `scoring.file`/`switching.state_file`/секция `report`-файла из конфига и схемы.
- **Монитор: трафик-гейт + внеплановый зонд (анти-throttle ТСПУ):** delay-проба заменена на
  логику по САМОМУ надёжному сигналу — идёт ли через активную ноду боевой трафик. Идёт
  (≥ `silence_floor` за окно, из /connections, свой трафик тестера исключён) → нода жива, не
  трогаем. «Тихо» дольше `silence_window` (деф.5 мин) → внеплановый зонд: закачка ~`probe_bytes`
  (деф.1 МБ) через socks реюзом `download`-теста. Провал — нет ответа ИЛИ `speed < probe_min_mbps`
  (throttle, delay такое не ловит) — `fails` раз подряд → EMERGENCY; 429/limited/http-ошибка =
  туннель жив, не throttle → без страйка. `silence_floor` авто = `min_mbps × window` (≈37.5 МБ):
  пассивный трафик и зонд меряют одно. `runner._probe_node` (плоский `select(nodes-tester,leaf)`),
  общий `_tester_lock` сериализует селектор/socks между лёгкой/тяжёлой фазами прогона и зондом.
  `MonitorConfig` расширен (+схема, +config.json); загрузка monitor через `_filtered`.
- **Фикс AWG-нод в дашборде (`storage.load_nodes` читает endpoints):** sing-box 1.11+ вынес
  wireguard/amneziawg из `outbounds` в отдельный `endpoints` — `load_nodes` читал только
  `outbounds`, поэтому AWG-ноды не попадали в таблицу `nodes`, и `LEFT JOIN nodes` в дашборде
  (`data.py`) давал пустые provider/protocol при живом CRC (из traffic/results). Теперь читаются
  оба массива → AWG-строки (provider=AWG, protocol=wg) появляются, имена в дашборде восстановлены
  (ретроспективно — join идёт по CRC на существующие traffic/results).
- **Фильтр `exclude_node_protocols` (исключение протоколов из генерации для клиентов):**
  новый ключ providers.json — список токенов дескриптора протокола (`naming.node_protocol`,
  пайп-токены), ноды с любым из них выкидываются целиком в `main.finalize_nodes` (там же, где
  дропаются «голые» vless/vmess из `groups.UNGROUPED_PROTOCOLS`; фильтруются и sub-, и
  user-ноды). В отличие от `exclude_protocol` (работает в `get_parser` по базовой share-ссылке
  vless/trojan/…), ловит транспорт-уровневые протоколы: `"xhttp"` матчит `vless|xhttp|tls`,
  `"wg"` — wireguard/awg-эндпоинты. Хелпер `main._protocol_excluded(node, tokens)` — пересечение
  `node_protocol.split('|')` с набором. Прописан в `config_whitelist/providers.json`
  (`["xhttp","wg"]`) — из этого набора берутся ноды для клиентов на ванильном sing-box, не
  понимающих xhttp/amneziawg. В `config/providers.json` (роутер, форк lx) ключа нет — там всё
  как было. Парсинг xhttp/awg не тронут (тесты `test_xhttp.py` зелёные) — только отсев на выходе.
- **AWG-парсер + тип подписки `folder` (форк sing-box-lx, тег `with_awg`):** новый
  `subscribe/parsers/awg.py` (`parse_file`/`load_dir`) разбирает `.conf` (WireGuard-INI с
  AWG3-полями: Jc/S1-4/H1-4/HeaderProtectionKey/тайминги-диапазоны/ContentPaddingAddition/I1
  дословно) в узел `type:"wireguard"`, страна — из имени файла (de/ee/fi/fr/pl/se → флаг в
  теге). В `providers.json` — новый тип подписки `folder` (каталог отдельных файлов, ключи
  `path`/`format`/`ext`); `main.get_nodes_from_folder` диспетчит по `format` на `parse_file`,
  ноды идут через общий `finalize_nodes` (rename → `AWG-wg-<cc>-out [CRC]`, дедуп, группы).
  Ассемблер: WG-узлы выделяются в `endpoints[]` (в 1.14 wireguard-outbound удалён), теги
  остаются ссылаемыми селекторами; `add_domain_resolver` пропускает wireguard (peer — IP).
- **XHTTP-транспорт в парсере подписок (форк sing-box-lx, тег `with_xhttp`):** ветка
  `type=xhttp` в `subscribe/parsers/{vless,trojan}.py` → `tool.xhttp_transport(netquery)`
  (общий хелпер в `tool.py` + `tool.xhttp_range` нормализует диапазоны в `"min-max"`).
  Эмитим только пришедшие поля; `extra` (urlencoded-JSON) мёржится поверх query с
  приоритетом (fallback на `unquote` при двойном энкоде, битый `extra` не роняет парсер);
  `path` срезает `?`-суффикс, хвостовой слэш сохраняется (`stream-one`); camelCase→snake_case;
  `sc_*`/`no_grpc_header`/`x_padding_*`; `xmux` вложенным объектом (ключи xmux тоже маппятся
  camelCase→snake_case — MEDVED/puxvpn шлют `cMaxReuseTimes` и пр., иначе sing-box падает
  `unknown field`; `h_keep_alive_period` — int64, из диапазона `"0-0"` берём первое число,
  `-1`=выкл сохраняется как есть). XHTTP-нода теперь даёт
  протокол `vless|xhttp|tls` (не «голый» vless) → не отбрасывается фильтром
  `groups.UNGROUPED_PROTOCOLS`. Заодно закрыты пробелы `vless.py`: парсинг `alpn` (split по
  запятой), `utls`-отпечаток из `fp` и вне reality, `flow` не выставляется для xhttp,
  passthrough `encryption` (VLESS PQ, фича 012) на верхний уровень аутбаунда. Тесты —
  `tests/test_xhttp.py` (18 шт., включая живой минимальный кейс провайдера и эталонный
  packet-up с `extra`). Инструкция-источник: `sing-box-config/XHTTP-PARSER-INSTRUCTION.md`.
- **Юнит-тесты (`tests/`, stdlib unittest):** 67 тестов, `python -m unittest discover` из корня.
  Покрывают временной backoff, P0-retention, ошибки Clash API, host-обход+зазор, heavy-veto,
  pass_label через полночь, уплощённые группы, качество измерений и блок 8–13.
- **Качество измерений (review.md P2 «тестовая механика»):**
  - `download`/`_stream`: классификация HTTP — 429/5xx = `limited` (сервер занят, нода жива),
    прочие 4xx = `http_error` (битый url/блок/auth — не успех и не обрыв транспорта); упор в
    `duration` при живом потоке = `duration_reached`-успех (медленную СТАБИЛЬНУЮ ноду не
    заваливаем); валидация duration/window_bytes/connect_timeout > 0.
  - `scoring`: `http_error` нейтрален (сервер ответил) — пропускную/hold не штрафуем.
  - `reachability`: «достигнута» = 2xx/3xx (не любой <500); кворум `min_reached` (деф.1);
    `ok_status_below` настраивается; валидация workers > 0.
  - `connectivity`: `ok` требует распознанный `exit_ip` (иначе инъекция/captive); `transport_ok`
    отдельной метрикой.
  - `jitter`: валидация samples/interval.
  - Staged short-circuit: провал `connectivity` → остальные тесты ноды не гоняем (трафик).
- **Блок 8–13 review.md:**
  8. **Switcher region-aware** (`_chain_pairs(..., region)`): активация региона X не трогает
     чужие `{Y}-auto-out` (фолбэк-членство при малом числе нод больше не «загрязняет» соседей).
  9. **Валидация на загрузке**: варнинг, если эффективные `tests_enabled` региона без scoring-
     теста (напр. только connectivity) → score 0 и ноды не станут кандидатами.
  10. **Возрастной кап сырья**: `cleanup()` режет results/traffic/activations/endpoints старше
      `retention_days` и у ЖИВЫХ нод — БД/дашборд не пухнут на роутере.
  12. **Traffic flush транзакционен** (`add_traffic_batch` — один commit) + атомарный своп
      буферов под lock; при ошибке записи дельты возвращаются в буфер (не теряются).
  13. **Scoreboard под RLock**: `record/set_heavy/set_active/write/candidates/get/regions`
      защищены (прогон-поток пишет, монитор читает); наружу отдаём копии строк.
- **Hardening по review.md (блок 1–7):**
  1. **Cooldown → единая ВРЕМЕННАЯ модель** (`storage.garbage` + `streak`, методы
     `load/set/clear_backoff`): backoff `base_seconds·2^(n-1)` с потолком `garbage_hours`
     (потолок = карантин «мусорной»), персистентно. Убрало залипание при `rotation_bound`
     (мало проходов/сутки + суточный сброс `pass_no`). Загрузка `cooldown` терпима к
     старым ключам (`_filtered`). Мигр. ALTER `garbage.streak`.
  2. **`last_seen`** обновляется для всех нод, присутствующих в selector (`touch_seen`) —
     `cleanup()` больше не удаляет активно тестируемую ноду со всей историей, если
     `nodes.json` давно не менялся (P0).
  3. **Нормализация ошибок Clash API** (`_ok_json`): HTTP 5xx и битый JSON → `ClashApiError`
     во всех методах (`ping/get_proxy/all_proxies/connections`), а не голый `HTTPError`,
     роняющий процесс.
  4. **Изоляция исключений тестов**: `test.run()` в try (сбой одного теста → `ok:false`,
     не рушит проход/ноду), session закрывается в `finally`. Лёгкая и тяжёлая фазы.
  5. **Нет busy-loop**: пустой/сорванный проход → retry-пауза `_EMPTY_PASS_RETRY`; варнинг,
     если `loop` без `rotation_bound` и `pass_pause<=0` (проходы вплотную).
  6. **Дашборд `pass_label`**: `pass_no` берётся из строки с максимальным `ts` (не
     независимый max) — через полночь не склеивает свежую дату со старым номером.
  7. **Switch state сохраняется сразу** после мутации в `evaluate_region` (emergency/emg_stuck
     от монитора между прогонами не теряются при рестарте).


Старые пункты 1–7 бэклога + последующие доработки, все выкачены одним циклом правок
(детали — в git-истории и README):

- **Транспорт-тест (download ⊇ stability):** одна одиночная закачка даёт
  `speed_mbps` + `throttle_ratio` (оконный, по байтам) + `hold_ratio` + `limited`/обрыв;
  единый код для RU (selectel/10MB) и остальных зон; порционная (chunks) модель отменена.
- **Классификация 429 vs ТСПУ:** `HTTPError` (сервер ответил) ≠ обрыв транспорта; 429 не FAIL.
- **Витрина трафика:** 3 таблицы (провайдеры / страны / ноды) + `parse_group` + разметка
  leaf / direct / `unspecified` (failsafe-трафик) / other.
- **Connectivity:** выбор `service` (cloudflare / curlmyip) вместо url + нормализованные метрики.
- **Балансировка трафика в ротации:** множитель недогруза в весе ротации. Сначала по-нодовая
  (`traffic_by_crc`), затем заменена на **по осям** — провайдер + страна (полный вес) + протокол
  (мягко, 0.3), мультипликативно; `traffic_by_dims` (агрегаты по провайдеру/стране/протоколу,
  failsafe-трафик → провайдер через `parse_group`); `load_balance.{provider,country,protocol}_strength`.
- **Провайдер-фильтр `ru`** (`exclude_countries`), выпил `ru-predef-out` из генератора,
  `undef`-ноды не попадают в группу `other`.
- **БД:** таблица `activations` (история переключений); единый `cleanup()` (каскад по crc +
  возрастной кап не-нодовых строк, без сирот); отдельный `vacuum()` под cron (`--vacuum`).
- **Cooldown + экспоненциальный backoff** по провалу gate (пропуск 1/2/4/8/…/32, сброс на успехе).
- **Дашборд:** «История переключений» (из `activations`, наверх), «Качество провайдеров»,
  `#`/`pass_no` в результатах (лимит 10), скрыт тестовый источник `127.0.0.1`.
- **Дашборд — качество провайдеров:** «доля мёртвых» (%, красным при >50%), «ср. рейтинг живых».
- **Дашборд — CSS-полировка:** карточки-секции, цветовые токены (тёмная/светлая), зебра, hover,
  аккуратные заголовки/шапка; без зависимостей, self-contained.
- **Дашборд — единый «Трафик» с переключателем** (провайдеры / страны / протоколы / ноды);
  добавлен агрегат `traffic_protocols`; состояние таба переживает авто-refresh.
- **Дашборд — результаты тестов:** колонки провайдер / протокол / cc / crc (полное имя ноды
  убрано как дублирующее).
- **Дашборд — переключатель по региону** (все/eu/us/other) в «История переключений», «Рейтинг
  нод», «Качество провайдеров»; те же провайдер/протокол/cc/crc-колонки в истории и рейтинге.
- **Дашборд — fix:** активная строка не подсвечивалась на чётных позициях (зебра перебивала
  `tr.active` по специфичности) → селектор усилен; время везде в 24h.
- **Дашборд — пагинация** (свой код, без библиотек): рейтинг/история/качество (внутри
  region-табов), результаты, топ назначений; по 15 строк, prev/next, состояние переживает
  refresh; смена региона сбрасывает страницу.
- **Дашборд — визуальный рефреш** в духе Tabler/Grid.js (чистый CSS): pill-табы, скруглённые
  карточки с тенью, refined палитра (тёмная/светлая), разделители строк вместо зебры,
  аккуратный пейджер; self-contained, без зависимостей.
- **subscribe — подписка из файла:** в `providers.json` поддержан ключ `file` (путь к .txt со
  share-links или clash .yaml) наравне с `url`; `get_nodes_from_file`, graceful-skip при
  отсутствии/битом файле (не роняет генерацию). Добавлена запись `MEDVED`.
- **subscribe — `--config-dir <path>`:** независимая генерация из чужого набора конфигов
  (`providers.json`+`user_nodes.json`+`groups_params.json` из указанной папки); вывод — по
  `save_config_path` их providers. `groups.load_params()` для перезагрузки group-параметров.
  Дефолтный запуск без аргументов не изменился.
- **subscribe — `groups_params.raw_user_nodes`** (дефолт false): при true ноды из
  `user_nodes.json` копируются в вывод как есть (без rename/группировки/CRC/exclude — нет
  `_meta`, groups.build их не трогает). `groups.raw_user_nodes()` + параметр `group_user_nodes`
  у `finalize_nodes`.
- **subscribe — `emit.global_failsafe`** (в groups_params, дефолт false): при true создаётся
  `global-auto-out-failsafe` (urltest над региональными `*-auto-out-failsafe`) и ставится
  default'ом в селектор `global-auto-out` — авто-фейловер на лучший регион по латентности.
- **Посуточная нумерация прогонов:** pass_no сбрасывается в полночь, переживает рестарт
  внутри суток (meta `pass_day`/`pass_no`); в дашборде «DD/MM-NNN» (дата из ts результата).
- **rotation_bound-режим** (`run.default.rotation_bound`, дефолт true): в непрерывном режиме
  со включённой ротацией тестер не крутит прогоны без остановки, а спит до ближайшего
  `rotate_deadline` switcher'а (`switcher.next_rotate_deadline`), делает один прогон и снова
  спит; emergency между прогонами ловит монитор. Опрос сна короткими шагами (реакция на
  сдвиг дедлайна/Ctrl+C). При loop=false или выкл. switching/rotation — обычный pass_pause.
- **Двухуровневое тестирование:** лёгкие тесты (вкл. 10МБ `download`) скорят ВСЕ ноды как
  прежде; новый тяжёлый `heavy_download` (50МБ, класс-наследник DownloadTest) гоняется
  отдельной ФАЗОЙ прогона только по `heavy_candidates` лучшим нодам региона (+активная) как
  pass/fail **veto** — в скоринг НЕ входит. Veto хранится в score.csv (`heavy_ok`/`heavy_ts`),
  протухает по `heavy_veto_hours` (деф.6). `Scoreboard.candidates` исключает свежий veto, но
  при отсутствии не-vetoed — возвращает vetoed (мало нод → выбираем из имеющихся).
- **Устойчивость к малому числу нод:** в `subscribe/groups.py` требуемые регионы
  (`emit.ensure_regions`, деф. eu/us/other) всегда получают `{region}-auto-out`; пустой регион
  заполняется кросс-региональным фолбэком (все leaf-группы) — конфиг sing-box не падает по
  «outbound not found». Тестер/switcher уже дефенсивны к пустым регионам.
- **Emergency-история:** emergency без замены (активная заблокирована, здоровых кандидатов
  нет) фиксируется в `activations` (reason `emergency-stuck`, один раз за эпизод — флаг
  `emg_stuck`). Дашборд «История переключений»: новая колонка «причина» с бейджем EMERGENCY /
  ⚠ EMERGENCY (stuck). Emergency-переключения и раньше писались с reason `emergency`.
- **subscribe — подписки Happ (`happ://crypt…crypt4`):** вендорный чистый-Python декодер
  (`subscribe/happ_decode.py`, RSA/PKCS#1 v1.5, без внешних зависимостей — работает на роутере)
  + `subscribe/happ.py` (заголовки Happ, gzip, base64). В `providers.json` ключ `url` вида
  `happ://crypt4/…` расшифровывается в реальный URL и качается с заголовками клиента Happ;
  опц. ключ подписки `happ_headers` — оверрайд (напр. X-Hwid). Падение одной подписки не
  роняет генерацию.
- **Уплощение групп (`groups.build`):** убраны промежуточные провайдер/протокол-leaf-группы.
  Теперь на регион ровно две группы, ноды — прямые члены: `{region}-auto-out` (selector,
  default=failsafe) + `{region}-auto-out-failsafe` (urltest, тот же список нод). Сверху
  `global-auto-out` (+ опц. `global-auto-out-failsafe`). Тестовый селектор `nodes-tester` —
  плоский список всех нод (без `{region}-nodes-tester`). Убрано деление eu/other-по-протоколу.
  Меньше urltest-проб, короче цепочка switcher'а. Конфиги sing-box ссылаются только на
  верхнеуровневые группы — срез чистый.
- **Тестер на `recognition=parse`, удалён `by_selector`:** тестер выбирает ноду прямо в плоском
  `nodes-tester` (регион из имени ноды). Ветка `by_selector` и переключение регионального
  селектора вырезаны из `runner`/`config`/схемы. `_load_run` теперь терпит неизвестные ключи
  (удалённый `group_pause` не роняет загрузку старых конфигов).
- **Host-aware обход (анти-ТСПУ):** очередь нод раскладывается round-robin по хостам (`server`
  из storage; фолбэк — провайдер/CRC), одинаковые хосты максимально далеко. Зазор
  `run.min_host_gap` (деф.120с) — минимум времени между обращениями к ОДНОМУ хосту с РАЗНЫМ
  (порт/протокол); тот же порт/протокол (лёгкий→тяжёлый одной ноды) ожидания не ждёт. Общий
  трекинг на лёгкую и тяжёлую фазы. Убраны `group_pause` и `pass_pause` (в rotation_bound
  интервал задаёт срок ротации). `storage.endpoints_by_crc()` даёт `crc→(server, port)`.
- **Garbage-ноды:** нода, дошедшая до `cooldown.max_skip` backoff (или провалившая пробу
  после карантина), помечается мусорной в БД (таблица `garbage`, переживает рестарт) и
  исключается из тестов на `cooldown.garbage_hours` (деф.72) — чтобы не теребить
  заблокированные ТСПУ ноды. После истечения — одна проба; провал → снова карантин, успех →
  снятие. Истёкшие строки чистятся в `cleanup()`.
