"""Фоновый монитор активных боевых нод.

Крутится в отдельном потоке с коротким интервалом, независимо от замеров
тестера. По каждой активной ноде (из switcher):

  1. ДРЕЙФ ВЫБОРА — если боевые селекторы слетели (напр. после reload sing-box),
     возвращает выбор на активную ноду по цепочке (switcher.reassert_chain). В паузе
     после применения конфига и в общей аварии монитор делает только это.
  2. ОШИБКИ ПОЛЬЗОВАТЕЛЕЙ — строки лога sing-box «open connection … using
     outbound/selector[eu-auto-out]: <ошибка>». Для прокси-ноды ошибка соединения — это
     почти всегда сама нода: не соединиться с ней (connect), не ответила вовремя (timeout),
     оборвала (reset). Правило monitor.errors (или groups[].monitor.errors): за последнее окно
     ≥ min ошибок и за окно до него ≥ prev_min — EMERGENCY сразу, без зонда. Первые `fresh`
     после выбора ноды хватает fresh_min ошибок за окно. Ошибки соединений, начатых до смены
     ноды (время строки минус возраст соединения), не считаются — это хвост прежней ноды.
     Зонд одним соединением здесь не решает: мигающая нода часто его пропускает.
  3. ЗДОРОВЬЕ ПО ТРАФИКУ:
       • в логе за тик не меньше error_burst ошибок через группу → зонд сразу, даже если
         трафик идёт;
       • идёт трафик (≥ silence_floor за окно) → нода заведомо жива, не трогаем;
       • «тихо» дольше silence → ВНЕПЛАНОВЫЙ ЗОНД: качаем ~1 МБ через socks (prober), но
         только если за тишину были соединения или ошибки (silence_min_conns): группой не
         пользуются — проверять нечего. Провал (нет ответа ИЛИ скорость < probe_min_mbps —
         значит ТСПУ режет трафик, delay такое не ловит) — страйк и повтор зонда через retry;
         fails страйков подряд → EMERGENCY. Одиночный обрыв TLS после простоя бывает и у
         живой ноды: повтор его снимает.

Порог трафика приравнен к порогу зонда (min_mbps × window): пассивный трафик и
активный зонд меряют одно — «держит ли нода ≥ min_mbps». Тогда throttled-нода
(delay проходит, но трафик зарезан) не «зависает» до планового замера: как только
через неё перестаёт идти достаточный трафик, её догоняет зонд.

Если активной ноды больше нет в sing-box (регенерация конфига сменила теги) — монитор
её пропускает: замену выбирает переключатель, как только runner перечитает состав нод.
"""

from __future__ import annotations

import calendar
import collections
import json
import re
import shutil
import threading
import time

from nodes_config.groups import DIRECT, FAILSAFE

from .api import ApiError
from .identity import parse_node

# Строка ошибки соединения через селектор:
# "+0000 2026-10-21 19:07:01 ERROR [3774252907 5.0s] connection: open connection to X using
#  outbound/selector[eu-auto-out]: dial tcp 1.2.3.4:443: connect: connection refused".
# Метки времени нет, если в конфиге sing-box log.timestamp выключен.
_CONN_ERROR = re.compile(
    r"^(?:([+-]\d{4}) (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) )?\w+ (?:\[\d+ ([^\]]+)\] )?connection: "
    r"open connection to \S+ using outbound/selector\[([^\]]+)\]: (.*)$")
_GO_DURATION = re.compile(r"(\d+(?:\.\d+)?)(h|ms|µs|us|ns|m|s)")
_UNIT = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 1e-3, "µs": 1e-6, "us": 1e-6, "ns": 1e-9}
_CONNECT = re.compile(r"dial tcp \S+: (i/o timeout|connect: connection refused|connect: no route to host"
                      r"|connect: network is unreachable)")


def _go_seconds(text: str) -> float:
    """Длительность Go ("5.0s", "1m1s", "202ms") в секундах; не разобрать — 0."""
    return sum(float(n) * _UNIT[u] for n, u in _GO_DURATION.findall(text or ""))


def error_class(error: str) -> "str | None":
    """Класс ошибки соединения через ноду (config.ERROR_CLASSES) или None — не про ноду:
    группа на block-out («operation not permitted»), не резолвится домен самой ноды
    («lookup …» — отказ bootstrap, 16.10 так падали все ноды разом) и прочее."""
    if "operation not permitted" in error or error.startswith("lookup "):
        return None
    if _CONNECT.match(error):
        return "connect"
    if "context deadline exceeded" in error:
        return "timeout"
    if "EOF" in error or "reset by peer" in error:
        return "reset"
    if "operation was canceled" in error or error.startswith("context canceled"):
        return "canceled"
    return None


def parse_error(line: str, now: float) -> "tuple | None":
    """(тег селектора, текст ошибки, когда записана, когда началось соединение) или None.
    Без метки времени в строке — записана сейчас; без возраста — началась тогда же."""
    m = _CONN_ERROR.match(line)
    if not m:
        return None
    offset, stamp, age, tag, error = m.groups()
    logged = now
    if stamp:
        utc = calendar.timegm(time.strptime(stamp, "%Y-%m-%d %H:%M:%S"))
        sign = -1 if offset[0] == "-" else 1
        logged = utc - sign * (int(offset[1:3]) * 3600 + int(offset[3:5]) * 60)
    return tag, error, logged, logged - _go_seconds(age)


class _LogTail:
    """Новые строки лога sing-box с прошлого чтения. Первое чтение — с конца файла;
    файл стал короче (обрезали) — с начала. Перерос max_bytes (0 — без предела) — копия в
    `.1` и очистка: лог на OpenWrt лежит в RAM, а sing-box пишет с O_APPEND и продолжит с
    начала. Переименовать нельзя: sing-box писал бы в старый файл."""

    def __init__(self, path: str, max_bytes: int = 0):
        self.path = path
        self.max_bytes = max_bytes
        self.pos: int | None = None

    def lines(self) -> list[str]:
        try:
            with open(self.path, "rb") as fh:
                size = fh.seek(0, 2)
                if self.pos is None:
                    self.pos = size
                elif size < self.pos:
                    self.pos = 0
                fh.seek(self.pos)
                data = fh.read()
        except OSError:
            return []
        end = data.rfind(b"\n") + 1                  # недописанную строку — в следующий раз
        self.pos += end
        if self.max_bytes and size > self.max_bytes:
            self._rotate()
        return data[:end].decode("utf-8", "replace").splitlines()

    def _rotate(self) -> None:
        try:
            shutil.copyfile(self.path, self.path + ".1")
            with open(self.path, "r+b") as fh:
                fh.truncate(0)
        except OSError as exc:
            print(f"  [monitor] лог sing-box не очищен: {exc}")
            return
        self.pos = 0


def singbox_log_path(config_path: str) -> str:
    """Файл лога из конфига sing-box (log.output); пусто — лог не в файле."""
    try:
        with open(config_path, encoding="utf-8") as fh:
            return str((json.load(fh).get("log") or {}).get("output") or "")
    except (OSError, ValueError):
        return ""


class ProductionMonitor:
    def __init__(self, cfg, api, switcher, prober, traffic, pause_check=None,
                 on_tick=None, singbox_log="", conns=None, group_rules=None, log_max=0):
        self.cfg = cfg                 # MonitorConfig
        self.api = api
        self.sw = switcher
        # prober(region, leaf) -> (ok: bool, mbps: float, inconclusive: bool, error: str).
        # inconclusive — туннель жив, но замер не показателен (429/limited, http-ошибка):
        # это НЕ throttle → сбрасываем страйки. ApiError prober пробрасывает наверх.
        self.prober = prober
        # traffic() -> {crc: накопительные байты боевого трафика} (TrafficCollector.user_bytes);
        # conns() -> {crc: накопительное число боевых соединений} (TrafficCollector.user_conns).
        self.traffic = traffic
        self.conns = conns or dict
        # Правило аварии по ошибкам у групп со своим groups[].monitor.errors.
        self.group_rules = group_rules or {}
        # pause_check — пауза применения конфига или общая авария: зондов и замен нет, только
        # восстановление выбора (проверка связности конвейера идёт через failsafe-out и от
        # выбора групп не зависит).
        self.pause_check = pause_check or (lambda: False)
        # Доп. проверка на каждом тике вне окна применения (пересборка по деградации).
        self.on_tick = on_tick
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # Состояние по региону: тихий таймер, накопленные байты и соединения, страйки зонда,
        # ошибки соединений активной ноды [(когда записана, класс)].
        self._state: dict[str, dict] = {}
        # Счётчики traffic()/conns() на прошлом тике — для дельты между тиками.
        self._last_bytes: dict[str, int] | None = None
        self._last_conns: dict[str, int] | None = None
        # Наблюдение за failsafe-out (общее для групп без кандидатов); None — не нужно.
        self._failsafe: dict | None = None
        self._log = _LogTail(singbox_log, log_max) if singbox_log else None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._loop, name="prod-monitor", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            # Runner сразу после этого закрывает Storage и восстанавливает selector;
            # поэтому нельзя возвращаться с живым monitor worker.
            self._thread.join()
            self._thread = None

    def is_alive(self) -> bool:
        """Жив ли фоновый поток монитора (для /api/status админки)."""
        return self._thread is not None and self._thread.is_alive()

    # --- Внутреннее -----------------------------------------------------

    def _loop(self) -> None:
        # wait() возвращает True при stop() — тогда выходим; иначе тикаем по интервалу.
        while not self._stop.wait(self.cfg.interval):
            try:
                self._tick()
                if self.on_tick is not None and not self.pause_check():
                    self.on_tick()
            except Exception as exc:  # noqa: BLE001 — монитор не должен ронять процесс
                print(f"  [monitor] ошибка: {exc}")

    def reassert(self) -> None:
        """Вернуть выбор боевых селекторов на активные ноды — после перезапуска sing-box они
        на default (failsafe-out, block-out). Ноды, ушедшей из конфига, здесь не заменяем."""
        if not self.cfg.reassert_drift:
            return
        try:
            proxies = self.api.all_proxies()
        except ApiError:
            return
        for region in self.sw.active_regions():
            self.sw.reassert_chain(region, proxies)

    def _tick(self) -> None:
        if self.pause_check():
            self.reassert()
            if self._log is not None:
                self._log.lines()               # ошибки паузы — не про ноды, пропускаем
            self._state.clear()
            return
        try:
            proxies = self.api.all_proxies()
        except ApiError:
            return  # API недоступен (sing-box рестартует) — пропускаем тик

        # Боевой трафик и соединения по CRC ноды за этот тик (свои тестера/зонда исключены).
        node_bytes, self._last_bytes = _delta(self.traffic(), self._last_bytes)
        node_conns, self._last_conns = _delta(self.conns(), self._last_conns)
        regions = self.sw.active_regions()
        errors = self._conn_errors(proxies, regions)
        now = time.monotonic()
        wall = time.time()

        for region in regions:
            node = self.sw.active_node(region)
            if not node or node not in proxies:
                # Активной ноды нет/исчезла — замену выберет переключатель.
                self._state.pop(region, None)
                continue

            if self.cfg.reassert_drift:
                self.sw.reassert_chain(region, proxies)

            st = self._state.get(region)
            if st is None or st["node"] != node:
                st = self._state[region] = {"node": node, "quiet_since": now, "quiet_bytes": 0,
                                            "quiet_conns": 0, "strikes": 0, "next_probe": 0.0,
                                            "errors": collections.deque()}
            crc = parse_node(node).node_id or node
            st["quiet_bytes"] += node_bytes.get(crc, 0)
            region_errors = errors.get(region, ())
            counted = self._add_errors(region, region_errors, st)
            st["quiet_conns"] += node_conns.get(crc, 0) + counted

            if self._errors_rule(region, node, st, wall):
                continue
            if st["strikes"]:
                # Прошлый зонд провален — повтор через retry, трафик его не отменяет.
                if now >= st["next_probe"]:
                    self._probe_region(region, node, st, now)
                continue
            burst = len(region_errors)
            if self.cfg.error_burst and burst >= self.cfg.error_burst and now >= st["next_probe"]:
                print(f"  [monitor] {region}: ошибок соединений за тик: {burst} — зонд")
                self._probe_region(region, node, st, now)
                continue
            if st["quiet_bytes"] >= self.cfg.silence_floor_bytes:
                # Нода пассивно доказала пропускную способность — жива.
                st.update(quiet_since=now, quiet_bytes=0, quiet_conns=0, strikes=0)
                continue
            if now - st["quiet_since"] < self.cfg.silence:
                continue  # ещё недостаточно тихо
            if st["quiet_conns"] < self.cfg.silence_min_conns:
                # Группой не пользуются: соединений не было — проверять нечего.
                st.update(quiet_since=now, quiet_bytes=0, quiet_conns=0)
                continue

            self._probe_region(region, node, st, now)

        self._watch_failsafe(proxies, node_bytes, now)

    def _rule(self, region: str):
        return self.group_rules.get(region) or self.cfg.errors

    def _add_errors(self, region: str, events, st: dict) -> int:
        """Добавить в окно ошибки активной ноды: соединения, начатые до её выбора, — хвост
        прежней ноды, их не считаем. Возвращает, сколько ошибок засчитано ноде."""
        since = self.sw.last_switch(region)
        rule = self._rule(region)
        counted = 0
        for logged, started, cls in events:
            if started < since:
                continue
            counted += 1
            if cls in rule.classes:
                st["errors"].append((logged, cls))
        return counted

    def _errors_rule(self, region: str, node: str, st: dict, now: float) -> bool:
        """Правило аварии по ошибкам (monitor.errors). True — тик для группы обработан: авария
        или подтверждающий зонд (probe_confirm), который ноду оставил."""
        rule = self._rule(region)
        errs = st["errors"]
        while errs and errs[0][0] < now - 2 * rule.window:
            errs.popleft()
        if not rule.enabled or not errs:
            return False
        current = sum(1 for t, _ in errs if t >= now - rule.window)
        previous = len(errs) - current
        if now - self.sw.last_switch(region) < rule.fresh:
            hit = current >= rule.fresh_min
        else:
            hit = current >= rule.min and previous >= rule.prev_min
        if not hit:
            return False
        kinds = ", ".join(f"{k} {n}" for k, n in collections.Counter(c for _, c in errs).most_common())
        why = f"ошибок соединений за {rule.window:.0f} с: {current}, за окно до него: {previous} ({kinds})"
        if rule.probe_confirm:
            try:
                ok, mbps, inconclusive, error = self.prober(region, node)
            except ApiError:
                return False
            if inconclusive or (ok and mbps >= self.cfg.probe_min_mbps):
                print(f"  [monitor] {region}: {why}, но зонд прошёл — нода остаётся")
                errs.clear()
                st["next_probe"] = time.monotonic() + 2 * self.cfg.retry
                return True
        print(f"  [monitor] {region}: {why} — EMERGENCY")
        self.sw.penalize(node)
        self.sw.evaluate_region(region, emergency=True)
        errs.clear()
        return True

    def _watch_failsafe(self, proxies: dict, node_bytes: dict, now: float) -> None:
        """failsafe-out групп без кандидатов наблюдается как нода: трафик через выбранную им
        ноду — жив, тишина дольше silence — один зонд через тестовый селектор на все группы.
        Провал fails раз подряд — группы с direct_fallback уходят на direct-out; ответ —
        возвращаются на failsafe-out."""
        groups = {r: t for r, t in self.sw.fallback_regions() if t in (FAILSAFE, DIRECT)}
        if not groups or FAILSAFE not in proxies:
            self._failsafe = None
            return
        st = self._failsafe = self._failsafe or {"quiet_since": now, "quiet_bytes": 0, "strikes": 0}
        if FAILSAFE in groups.values():
            leaf = proxies[FAILSAFE].get("now") or ""
            st["quiet_bytes"] += node_bytes.get(parse_node(leaf).node_id or leaf, 0)
            if st["quiet_bytes"] >= self.cfg.silence_floor_bytes:
                st.update(quiet_since=now, quiet_bytes=0, strikes=0)
                return
        if now - st["quiet_since"] < self.cfg.silence:
            return
        st.update(quiet_since=now, quiet_bytes=0)
        region = next(iter(groups))
        try:
            ok, mbps, inconclusive, error = self.prober(region, FAILSAFE)
            if not ok and not inconclusive:          # одиночный обрыв — повтор, как у ноды
                ok, mbps, inconclusive, error = self.prober(region, FAILSAFE)
        except ApiError:
            return
        if inconclusive or (ok and mbps >= self.cfg.probe_min_mbps):
            st["strikes"] = 0
            for region, target in groups.items():
                if target == DIRECT:
                    self.sw.failsafe_verdict(region, alive=True)
            return
        st["strikes"] += 1
        print(f"  [monitor] зонд failsafe-out провален ({error or f'{mbps:.2f} Мбит/с'}) "
              f"{st['strikes']}/{self.cfg.fails}")
        if st["strikes"] >= self.cfg.fails:
            st["strikes"] = 0
            for region, target in groups.items():
                if target == FAILSAFE:
                    self.sw.failsafe_verdict(region, alive=False)

    def _probe_region(self, region: str, node: str, st: dict, now: float) -> None:
        """Внеплановый зонд активной ноды: ~1 МБ через socks. Провал → страйк и повтор
        через retry; fails страйков подряд → emergency. Следующий зонд по всплеску ошибок —
        не раньше чем через 2 × retry после удачного: ошибки до мёртвых адресов не должны
        гонять зонд каждый тик."""
        st.update(quiet_since=now, quiet_bytes=0, quiet_conns=0)
        try:
            ok, mbps, inconclusive, error = self.prober(region, node)
        except ApiError:
            return  # API/переключение недоступно — это НЕ смерть ноды, без страйка

        if inconclusive or (ok and mbps >= self.cfg.probe_min_mbps):
            st.update(strikes=0, next_probe=now + 2 * self.cfg.retry)
            return

        st["strikes"] += 1
        st["next_probe"] = now + self.cfg.retry
        why = f"нет ответа: {error or '?'}" if not ok else f"throttle {mbps:.2f} Мбит/с"
        print(f"  [monitor] {region}: зонд провален ({why}) "
              f"{st['strikes']}/{self.cfg.fails}")
        if st["strikes"] >= self.cfg.fails:
            print(f"  [monitor] {region}: активная нода не тянет трафик — EMERGENCY")
            self.sw.penalize(node)
            self.sw.evaluate_region(region, emergency=True)
            st.update(strikes=0, next_probe=now)

    def _conn_errors(self, proxies: dict, regions: list[str]) -> dict[str, list]:
        """Ошибки соединений через группы из новых строк лога sing-box: группа → [(когда
        записана, когда началось соединение, класс)]. Селектор выше группы (global-auto-out) —
        по его выбору вниз до селектора группы; ошибки не про ноду (block-out группы без
        кандидатов) не в счёт."""
        out: dict[str, list] = collections.defaultdict(list)
        if self._log is None:
            return out
        by_tag = {f"{r}-auto-out": r for r in regions}
        now = time.time()
        for line in self._log.lines():
            parsed = parse_error(line, now)
            if parsed is None:
                continue
            tag, error, logged, started = parsed
            cls = error_class(error)
            if cls is None:
                continue
            seen = set()
            while tag not in by_tag and tag not in seen and proxies.get(tag, {}).get("type") == "selector":
                seen.add(tag)
                tag = proxies[tag].get("now") or ""
            if tag in by_tag:
                out[by_tag[tag]].append((logged, started, cls))
        return out


def _delta(now: dict, last: "dict | None") -> tuple[dict, dict]:
    """Прирост накопительных счётчиков по CRC с прошлого тика и новая точка отсчёта.
    Первый тик — только точка отсчёта."""
    if last is None:
        return {}, now
    return {crc: n - last.get(crc, 0) for crc, n in now.items() if n > last.get(crc, 0)}, now
