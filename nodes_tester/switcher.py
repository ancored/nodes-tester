"""Логика переключения активных нод по рейтингу (score.csv).

Лестница приоритетов на регион:
    1. EMERGENCY  — активная нода обнулилась (заблокирована) → немедленно
    2. QUALITY    — кандидат стабильно лучше активной (гистерезис Δ, M циклов)
    3. ROTATION   — вышел таймер принудительной ротации (размазать нагрузку)
    4. STAY       — иначе не трогаем

Действие переключения: выбранная нода делается активной ВО ВСЕХ боевых
selector-группах, где она прямой член (кроме тестовых nodes-tester/*-nodes-tester
и urltest — их не трогаем). Реализуется через API-сервис sing-box.

Состояние по регионам (active/таймеры/recent/activations) хранится в SQLite
(Storage), чтобы переживать рестарт и корректно вести таймеры ротации/кулдауны.
"""

from __future__ import annotations

import random
import statistics
import threading
import time

from .api import ApiClient, ApiError
from .identity import parse_node
from .scoreboard import Scoreboard


class Switcher:
    def __init__(self, cfg, api: ApiClient, scoreboard: Scoreboard,
                 tester_group: str, traffic_provider=None, storage=None):
        self.cfg = cfg                      # SwitchingConfig
        self.api = api
        self.board = scoreboard
        self._tester_group = tester_group
        # Балансировка нагрузки в ротации (Вариант A): callable → {crc: bytes за окно}.
        # None → множитель недогруза = 1.0 (поведение как без балансировки).
        self._traffic_provider = traffic_provider
        # Хранилище для истории активаций (опц.); None → не пишем.
        self._storage = storage
        # Наблюдатель записей истории (уведомления): on_activation(group, reason, node, prev).
        self.on_activation = None
        self.state: dict[str, dict] = storage.load_switch_state() if storage else {}
        # Один RLock: switcher дёргают и поток прогона, и фоновый монитор.
        self._lock = threading.RLock()
        # Нода может быть активной в нескольких группах: рейтингу нужен полный набор.
        for group, st in self.state.items():
            if st.get("active"):
                self.board.set_active(group, st["active"])
        self._align_loaded()

    # --- Публичное ------------------------------------------------------

    def notify_score(self, region: str, node: str, blocked: bool) -> None:
        """Вызывать сразу после теста ноды: если активная заблокирована — emergency."""
        if not self.cfg.enabled or not blocked:
            return
        with self._lock:
            st = self.state.get(region)
            if st and st.get("active") == node:
                print(f"  [switch] EMERGENCY: активная нода региона {region} заблокирована")
                self.evaluate_region(region, emergency=True)

    def evaluate_all(self, groups=None) -> None:
        """Вызывать в конце прогона: обычная оценка (quality/rotation/init).
        groups — оценить только эти группы (частичный прогон); None — все."""
        if not self.cfg.enabled:
            return
        with self._lock:
            for region in self.board.regions():
                if groups is None or region in groups:
                    self._evaluate_region_locked(region, emergency=False)
            self._persist()

    def prune_groups(self, groups) -> None:
        """Забыть состояние групп, которых больше нет в sing-box (группу выключили в
        groups_params): иначе монитор и ротация продолжали бы обслуживать её ноду."""
        with self._lock:
            gone = [g for g in self.state if g not in groups]
            if not gone:
                return
            for g in gone:
                del self.state[g]
                self.board.set_active(g, None)
            if self._storage is not None:
                self._storage.set_active_crcs(
                    {parse_node(s["active"]).node_id for s in self.state.values() if s.get("active")})
            self._persist()
            print(f"  [switch] группы исчезли из sing-box, состояние сброшено: {', '.join(gone)}")

    def active_regions(self) -> list[str]:
        with self._lock:
            return [r for r, st in self.state.items() if st.get("active")]

    def active_node(self, region: str) -> "str | None":
        with self._lock:
            st = self.state.get(region)
            return st.get("active") if st else None

    def next_rotate_deadline(self) -> "float | None":
        """Ближайший срок ротации среди активных регионов (для rotation_bound-режима
        тестера). None — если ни один регион ещё не активирован."""
        with self._lock:
            ds = [st.get("rotate_deadline") for st in self.state.values()
                  if st.get("active") and st.get("rotate_deadline")]
        return min(ds) if ds else None

    def rotation_pool(self, region: str) -> list[str]:
        """Ноды, из которых ближайший evaluate_all выберет новую активную: пул ротации
        (тот же, что в _pick_rotation), а без активной — первая по score (init)."""
        with self._lock:
            cands = self.board.candidates(region)
            if not cands:
                return []
            st = self.state.get(region) or {}
            if not st.get("active"):
                return [cands[0]["node"]]
            return [c["node"] for c in self._rotation_pool(cands, st)]

    def rotation_due(self, region: str) -> bool:
        """Ближайший evaluate_all сменит активную ноду региона по ротации (или выберет
        первую, если активной нет). Условия — как в _evaluate_region_locked."""
        if not self.cfg.enabled:
            return False
        with self._lock:
            st = self.state.get(region) or {}
            if not st.get("active"):
                return True
            now = time.time()
            return (self.cfg.rotation.enabled
                    and now >= st.get("rotate_deadline", 0)
                    and now - st.get("last_switch", 0) >= self.cfg.rotation.min_dwell)

    def force_activate(self, region: str, node: str) -> bool:
        """Ручное форс-переключение активной ноды региона (из админки).

        Нода должна быть здоровым кандидатом региона (score > 0) в Scoreboard.
        Возвращает True, если нода реально стала активной (PUT по цепочке прошёл);
        False — если switching выключен, нода не кандидат, или переключение не удалось.
        """
        if not self.cfg.enabled:
            return False
        with self._lock:
            st = self.state.setdefault(region, _new_region_state())
            # Используем тот же список кандидатов, что автоматический switcher: это
            # одновременно проверяет регион, score/gate и актуальный heavy-veto.
            cand = next((c for c in self.board.candidates(region)
                         if c.get("node") == node), None)
            if cand is None:
                return False
            ok = self._activate(cand, region, st, time.time(), "manual")
            self._persist()
            return ok

    def reassert_chain(self, region: str, proxies: dict) -> int:
        """Если выбор в боевых селекторах слетел — вернуть активную ноду по цепочке.

        Вызывается фоновым монитором. Возвращает число исправленных селекторов.
        """
        with self._lock:
            st = self.state.get(region)
            node = st.get("active") if st else None
            if not node or node not in proxies:
                return 0
            skip = self._test_groups(proxies) | set(self.cfg.freeze_groups)
            fixed = 0
            for sel, child in self._chain_pairs(node, proxies, skip, region):
                if str(proxies.get(sel, {}).get("now", "")) != child:
                    try:
                        self.api.select(sel, child)
                        fixed += 1
                    except ApiError as exc:
                        print(f"  [monitor] {region}: ошибка в '{sel}': {exc}")
            if fixed:
                print(f"  [monitor] {region}: восстановлен выбор селекторов ({fixed})")
            return fixed

    # --- Ядро -----------------------------------------------------------

    def evaluate_region(self, region: str, emergency: bool = False) -> None:
        # Сохраняем state СРАЗУ после изменения: emergency/emg_stuck от монитора между
        # прогонами (в rotation_bound ожидание — часы) иначе теряются при рестарте до
        # следующего evaluate_all (см. review.md P1).
        with self._lock:
            self._evaluate_region_locked(region, emergency)
            self._persist()

    def _persist(self) -> None:
        """Сохранить состояние регионов в БД (если storage подключён)."""
        if self._storage is not None:
            self._storage.save_switch_state(self.state)

    def _evaluate_region_locked(self, region: str, emergency: bool) -> None:
        st = self.state.setdefault(region, _new_region_state())
        cands = self.board.candidates(region)
        required = self.board.required(region)
        if not cands:
            # У группы с обязательными тестами (gemini) никто их не прошёл — активной
            # ноды быть не может: группа уходит на свой failsafe (urltest).
            if required:
                self._to_failsafe(region, st)
                return
            # Нет здоровых нод — переключать не на что. Если это emergency (активная
            # заблокирована ТСПУ, замены нет) — фиксируем «застряли», чтобы факт
            # блокировки без восстановления остался в истории.
            if emergency:
                self._note_emergency_stuck(region, st, "no-candidates")
            return

        active = st.get("active")
        active_row = self.board.get(active) if active else None
        if required and active_row is not None and active not in {c["node"] for c in cands}:
            # Активная больше не проходит обязательный тест (провал или истёк срок) —
            # оставаться на ней нельзя: немедленно на другую прошедшую.
            print(f"  [switch] {region}: активная не проходит {', '.join(required)} — замена")
            emergency = True
        if active_row is not None and self.board.is_restricted(active):
            # Активную поставили на паузу/в карантин/забанили — оставаться на ней нельзя.
            print(f"  [switch] {region}: активная нода ограничена — замена")
            emergency = True
        active_score = float(active_row["score"]) if active_row else 0.0
        now = time.time()

        # 1. Инициализация — активной ноды ещё нет: берём лучшую.
        if active_row is None:
            self._activate(cands[0], region, st, now, "init")
            return

        # 2. EMERGENCY — активная заблокирована/исчезла: немедленно на ДРУГУЮ, но
        # БАЛАНСИРОВАННО (не всегда топ-score → раньше 25% переключений лили на одного
        # провайдера мимо балансировки) и уводя от провайдера упавшей ноды.
        if emergency or active_score <= 0:
            others = [c for c in cands if c["node"] != active]
            if others:
                self._activate(self._pick_emergency(others, st, active),
                               region, st, now, "emergency")
            else:
                self._note_emergency_stuck(region, st, "no-other")  # замены нет
            return
        st.pop("emg_stuck", None)     # активная жива — вышли из залипшего emergency

        # 3. Принудительная ротация по таймеру.
        if (self.cfg.rotation.enabled
                and now >= st.get("rotate_deadline", 0)
                and now - st.get("last_switch", 0) >= self.cfg.rotation.min_dwell
                and len(cands) >= 2):
            self._activate(self._pick_rotation(cands, st), region, st, now, "rotation")
            return

        # 4. Quality-переключение с гистерезисом.
        best = cands[0]
        margin = 0.0 if active_score < self.cfg.comfort_floor else self.cfg.quality_margin
        if best["node"] != active and float(best["score"]) > active_score + margin:
            if now - st.get("last_switch", 0) < self.cfg.cooldown:
                return  # антифлаппинг
            st["quality_count"] = st.get("quality_count", 0) + 1
            if st["quality_count"] >= self.cfg.confirm_cycles:
                self._activate(best, region, st, now, "quality")
            return
        st["quality_count"] = 0

    def _to_failsafe(self, region: str, st: dict) -> None:
        """Выбрать в {group}-auto-out член {group}-auto-out-failsafe (urltest) и снять
        активную ноду. Повторно не делаем, пока группа уже на failsafe."""
        if st.get("failsafe") and not st.get("active"):
            return
        sel, fs = f"{region}-auto-out", f"{region}-auto-out-failsafe"
        try:
            self.api.select(sel, fs)
        except ApiError as exc:
            print(f"  [switch] {region}: не удалось переключить на failsafe: {exc}")
            return
        prev = st.get("active")
        st["active"] = None
        st["failsafe"] = True
        self.board.set_active(region, None)
        print(f"  [switch] {region}: ни одна нода не прошла {', '.join(self.board.required(region))} "
              f"— группа на {fs}")
        if self._storage is not None:
            self._storage.add_activation(region, "", fs, "failsafe", 0.0,
                                         parse_node(prev).node_id if prev else None)
        self._emit(region, "failsafe", fs, prev)
        if self._storage is not None:
            self._storage.set_active_crcs(
                {parse_node(s["active"]).node_id for s in self.state.values() if s.get("active")})

    def _note_emergency_stuck(self, region: str, st: dict, detail: str) -> None:
        """Emergency без замены (ТСПУ заблокировал активную, здоровых кандидатов нет).
        Пишем в историю ОДИН раз за эпизод (флаг emg_stuck), чтобы не спамить каждый
        прогон; сбрасывается, когда активная снова ожила или произошло переключение."""
        if st.get("emg_stuck"):
            return
        st["emg_stuck"] = True
        active = st.get("active")
        print(f"  [switch] EMERGENCY {region}: замены нет ({detail}) — застряли на "
              f"заблокированной ноде")
        if self._storage is not None:
            crc = parse_node(active).node_id if active else ""
            self._storage.add_activation(region, crc, active or "", "emergency-stuck",
                                         0.0, active)
        self._emit(region, "emergency-stuck", active or "", active)

    def _emit(self, region: str, reason: str, node: str, prev) -> None:
        if self.on_activation is not None:
            try:
                self.on_activation(region, reason, node, prev)
            except Exception as exc:  # noqa: BLE001 — уведомление не мешает переключению
                print(f"  [switch] уведомление не отправлено: {exc}")

    # --- Выбор кандидата для ротации -----------------------------------

    def _pick_rotation(self, cands, st) -> dict:
        """weighted-random из топ-K, исключая активную и последние avoid_recent."""
        return self._balanced_choice(self._rotation_pool(cands, st), st)

    def _rotation_pool(self, cands, st) -> list:
        active = st.get("active")
        recent = set(st.get("recent", [])[: self.cfg.rotation.avoid_recent])
        recent.add(active)
        filtered = [c for c in cands
                    if c["node"] not in recent
                    and float(c["score"]) >= self.cfg.rotation.min_score]
        return (filtered[: self.cfg.rotation.top_k]
                or [c for c in cands if c["node"] != active]
                or cands)

    def _balanced_choice(self, pool: list, st: dict) -> dict:
        """Взвешенный выбор из pool: fair-share по числу нод оси + underuse по трафику.
        Общий для ротации и emergency. strengths ставим при lb.enabled даже без трафика —
        fair-share (равномерность по провайдеру/стране) работает и без данных о трафике."""
        dims, strengths = {}, {}
        lb = self.cfg.rotation.load_balance
        if lb.enabled:
            strengths = {"provider": lb.provider_strength,
                         "country": lb.country_strength,
                         "protocol": lb.protocol_strength}
            if self._traffic_provider is not None:
                try:
                    dims = self._traffic_provider() or {}
                except Exception as exc:  # noqa: BLE001 — балансировка не должна ронять выбор
                    print(f"  [switch] балансировка: не удалось получить трафик: {exc}")
        return _weighted_choice(pool, st.get("activations", {}), dims, strengths)

    def _pick_emergency(self, others: list, st: dict, blocked: str) -> dict:
        """Аварийный выбор: уводим от ПРОВАЙДЕРА упавшей ноды (ТСПУ часто блокирует
        провайдера/подсеть целиком), затем балансированный взвешенный выбор среди здоровых."""
        bp = parse_node(blocked).provider if blocked else None
        diff = [c for c in others if c.get("provider") != bp] if bp else others
        return self._balanced_choice(diff or others, st)

    def _activate(self, cand: dict, region: str, st: dict, now: float, reason: str) -> bool:
        node = cand["node"]
        prev = st.get("active")             # предыдущая активная (для истории переходов)
        if node == st.get("active") and reason not in ("init",):
            return False
        try:
            proxies = self.api.all_proxies()
        except ApiError as exc:
            print(f"  [switch] {region}: не удалось получить прокси: {exc}")
            return False
        skip = self._test_groups(proxies) | set(self.cfg.freeze_groups)
        pairs = self._chain_pairs(node, proxies, skip, region)
        if not pairs:
            print(f"  [switch] {region}: у '{node}' нет цепочки selector-групп — пропуск")
            return False
        done = 0
        for sel, child in pairs:
            try:
                self.api.select(sel, child)
                done += 1
            except ApiError as exc:
                print(f"  [switch] {region}: ошибка в группе '{sel}': {exc}")

        if done != len(pairs):
            # Частичный PUT не означает, что production-цепочка реально ведёт на node.
            # Не публикуем ложное active-состояние: монитор/оператор должны видеть отказ.
            print(f"  [switch] {region}: переключение на '{node}' НЕ завершено "
                  f"(PUT {done}/{len(pairs)}) — состояние не меняем")
            return False

        # Обновляем состояние только после успеха ВСЕЙ цепочки.
        st["active"] = node
        st["last_switch"] = now
        st["quality_count"] = 0
        st.pop("emg_stuck", None)           # переключились — эпизод emergency закрыт
        st.pop("failsafe", None)
        st["rotate_deadline"] = self._next_deadline(now)
        recent = [node] + [n for n in st.get("recent", []) if n != node]
        st["recent"] = recent[:10]
        st.setdefault("activations", {})
        st["activations"][node] = st["activations"].get(node, 0) + 1
        crc = cand.get("id") or parse_node(node).node_id
        if self._storage is not None:
            prev_crc = parse_node(prev).node_id if prev else None
            self._storage.add_activation(
                region, crc, node, reason, cand.get("score"), prev_crc)
        self._emit(region, reason, node, prev)
        if self._storage is not None:
            # Держим scores.active в БД в синхроне с activations: save_scores переписывает
            # scores лишь раз в прогон, а переключение может произойти между прогонами.
            self._storage.set_active_crcs(
                {parse_node(s["active"]).node_id for s in self.state.values() if s.get("active")})
        self.board.set_active(region, node)
        partial = f", ЧАСТИЧНО {done}/{len(pairs)}" if done < len(pairs) else ""
        print(f"  [switch] {region}: {reason.upper()} → {node} "
              f"(score {cand['score']}, PUT {done}{partial})")
        return True

    def _chain_pairs(self, node: str, proxies: dict, skip: set,
                     region: str) -> list[tuple[str, str]]:
        """Пары (селектор, желаемый_член) по ЦЕПОЧКЕ вверх от ноды.

        node → {region}-auto-out → его родители. Группы из skip (тестовые + freeze,
        напр. global-auto-out) не трогаем. ЧУЖИЕ auto-селекторы групп ({G}-auto-out для
        G != region) пропускаем: нода может входить в несколько групп (eu и ai, фолбэк),
        но переключение группы X не должно менять выбор в группе Y (см. review.md P1).
        """
        selectors = {t: info for t, info in proxies.items()
                     if str(info.get("type", "")).lower() == "selector"}
        groups = set(self.board.regions()) | _COARSE_REGIONS
        pairs: list[tuple[str, str]] = []
        queue = [node]
        seen = {node}
        while queue:
            child = queue.pop(0)
            for tag, info in selectors.items():
                if (tag in skip or _foreign_group_auto(tag, region, groups)
                        or child not in (info.get("all") or [])):
                    continue
                pairs.append((tag, child))
                if tag not in seen:
                    seen.add(tag)
                    queue.append(tag)   # подняться выше: активировать tag в его родителях
        return pairs

    # --- Прочее ---------------------------------------------------------

    def _test_groups(self, proxies: dict | None = None) -> set[str]:
        """Тестовые группы, которые не трогаем: nodes-tester и *-nodes-tester."""
        if not self.cfg.exclude_test_groups:
            return set()
        groups = {self._tester_group}
        if proxies is not None:
            groups.update(proxies.get(self._tester_group, {}).get("all") or [])
        else:
            try:
                groups.update(self.api.list_group_members(self._tester_group))
            except ApiError:
                pass
        return groups

    def _rotate_delay(self) -> float:
        base = self.cfg.rotation.interval
        j = self.cfg.rotation.jitter
        return base + random.uniform(-j, j) if j else base

    # --- Общая сетка сроков ротации (rotation.align) ----------------------
    # Слот k: k*interval от эпохи + jitter, общий для всех групп и стабильный между
    # рестартами (зерно — номер слота). Сроки групп совпадают → тестер делает один
    # прогон на слот вместо прогона к сроку каждой группы.

    def _slot(self, k: int) -> float:
        rot = self.cfg.rotation
        j = min(float(rot.jitter or 0), float(rot.interval) / 4)   # слоты не перехлёстываются
        return k * float(rot.interval) + (random.Random(k).uniform(-j, j) if j else 0.0)

    def _slot_at_or_after(self, t: float) -> float:
        k = int(t // float(self.cfg.rotation.interval)) - 1
        while self._slot(k) < t:
            k += 1
        return self._slot(k)

    def _next_deadline(self, now: float) -> float:
        """Срок следующей ротации после переключения в момент now."""
        rot = self.cfg.rotation
        if not rot.align or float(rot.interval) <= 0:
            return now + self._rotate_delay()
        return self._slot_at_or_after(now + float(rot.min_dwell))

    def _align_loaded(self) -> None:
        """Сроки, сохранённые до включения align, — на ближайший слот сетки (не раньше
        last_switch + min_dwell). Наступившие сроки не трогаем."""
        rot = self.cfg.rotation
        if not rot.align or float(rot.interval) <= 0:
            return
        now = time.time()
        for st in self.state.values():
            old = float(st.get("rotate_deadline") or 0)
            if not st.get("active") or old <= now:
                continue
            new = self._slot_at_or_after(old - float(rot.interval) / 2)
            earliest = float(st.get("last_switch") or 0) + float(rot.min_dwell)
            if new < earliest:
                new = self._slot_at_or_after(earliest)
            st["rotate_deadline"] = new


# Встроенные группы (префикс тега {group}-auto-out) — известны и без данных sing-box.
_COARSE_REGIONS = {"eu", "us", "ru", "other"}


def _foreign_group_auto(tag: str, group: str, groups: set) -> bool:
    """tag = '{G}-auto-out' известной группы G != group → чужой auto-селектор."""
    if not tag.endswith("-auto-out"):
        return False
    g = tag[:-len("-auto-out")]
    return g in groups and g != group


# Оси балансировки: (ключ поля кандидата, ключ агрегата в dims).
_LB_AXES = (("provider", "provider"), ("country", "country"), ("protocol", "protocol"))


def _weighted_choice(pool: list[dict], activations: dict,
                     dims: "dict | None" = None, strengths: "dict | None" = None) -> dict:
    """Взвешенный случайный выбор кандидата (ротация и emergency).

    Базовый вес ~ score / (1 + число активаций). По каждой оси (провайдер/страна/
    протокол) с strength>0 — ДВА множителя:
      1) fair-share = (1 / число_нод_оси_в_пуле)^strength — суммарный вес провайдера
         НЕ зависит от числа его нод: 100-нодовый и 10-нодовый получают равную
         вероятность (это и есть «равномерно по провайдеру»);
      2) underuse = (scale / (scale + bytes))^strength — дополнительно придавливает
         перегруженную по ТРАФИКУ ось (scale = медиана байтов по пулу).
    strength=0 → ось не влияет (пропорционально числу нод). fair-share работает и без
    данных о трафике.
    """
    dims = dims or {}
    strengths = strengths or {}
    # Число нод пула по каждому значению оси (для fair-share: провайдер со 100 нодами
    # не должен получать в 10 раз больше веса, чем провайдер с 10).
    counts: dict[str, dict] = {axis: {} for axis, _ in _LB_AXES}
    for axis, field in _LB_AXES:
        for c in pool:
            v = c.get(field)
            counts[axis][v] = counts[axis].get(v, 0) + 1
    # Предрасчёт медианного масштаба байтов по каждой активной оси (для underuse).
    scales: dict[str, float] = {}
    for axis, field in _LB_AXES:
        s = strengths.get(axis, 0.0)
        if s and s > 0:
            amap = dims.get(axis) or {}
            vals = [amap.get(c.get(field), 0) for c in pool]
            sc = statistics.median(vals) if vals else 0.0
            if sc > 0:
                scales[axis] = sc
    weights = []
    for c in pool:
        acts = activations.get(c["node"], 0)
        w = max(0.01, float(c["score"])) / (1.0 + acts)
        for axis, field in _LB_AXES:
            s = strengths.get(axis, 0.0)
            if not (s and s > 0):
                continue
            cnt = counts[axis].get(c.get(field), 1) or 1     # 1) fair-share по числу нод
            w *= (1.0 / cnt) ** s
            sc = scales.get(axis)                            # 2) underuse по трафику
            if sc:
                b = (dims.get(axis) or {}).get(c.get(field), 0)
                w *= (sc / (sc + b)) ** s
        weights.append(w)
    total = sum(weights)
    if total <= 0:
        return pool[0]
    r = random.uniform(0, total)
    acc = 0.0
    for c, w in zip(pool, weights):
        acc += w
        if r <= acc:
            return c
    return pool[-1]


def _new_region_state() -> dict:
    return {"active": None, "last_switch": 0, "rotate_deadline": 0,
            "quality_count": 0, "recent": [], "activations": {}}
