"""Логика переключения активных нод по рейтингу (score.csv).

Лестница приоритетов на регион:
    1. EMERGENCY  — активная нода обнулилась (заблокирована) → немедленно
    2. QUALITY    — кандидат стабильно лучше активной (гистерезис Δ, M циклов)
    3. ROTATION   — вышел таймер принудительной ротации (размазать нагрузку)
    4. STAY       — иначе не трогаем

Действие переключения: выбранная нода делается активной ВО ВСЕХ боевых
selector-группах, где она прямой член (кроме тестовых nodes-tester/*-nodes-tester
и urltest — их не трогаем). Реализуется через Clash API.

Состояние по регионам (active/таймеры/recent) сохраняется в JSON, чтобы
переживать рестарт и корректно вести таймеры ротации/кулдауны.
"""

from __future__ import annotations

import json
import os
import random
import statistics
import threading
import time

from .clash_api import ClashApiError, ClashApiClient
from .identity import parse_node
from .scoreboard import Scoreboard


class Switcher:
    def __init__(self, cfg, clash: ClashApiClient, scoreboard: Scoreboard,
                 tester_group: str, traffic_provider=None, storage=None):
        self.cfg = cfg                      # SwitchingConfig
        self.clash = clash
        self.board = scoreboard
        self._tester_group = tester_group
        # Балансировка нагрузки в ротации (Вариант A): callable → {crc: bytes за окно}.
        # None → множитель недогруза = 1.0 (поведение как без балансировки).
        self._traffic_provider = traffic_provider
        # Хранилище для истории активаций (опц.); None → не пишем.
        self._storage = storage
        self.state: dict[str, dict] = _load_state(cfg.state_file)
        # Один RLock: switcher дёргают и поток прогона, и фоновый монитор.
        self._lock = threading.RLock()

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

    def evaluate_all(self) -> None:
        """Вызывать в конце прогона: обычная оценка (quality/rotation/init)."""
        if not self.cfg.enabled:
            return
        with self._lock:
            for region in self.board.regions():
                self.evaluate_region(region)
            _save_state(self.cfg.state_file, self.state)

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
            for sel, child in self._chain_pairs(node, proxies, skip):
                if str(proxies.get(sel, {}).get("now", "")) != child:
                    try:
                        self.clash.select(sel, child)
                        fixed += 1
                    except ClashApiError as exc:
                        print(f"  [monitor] {region}: ошибка в '{sel}': {exc}")
            if fixed:
                print(f"  [monitor] {region}: восстановлен выбор селекторов ({fixed})")
            return fixed

    # --- Ядро -----------------------------------------------------------

    def evaluate_region(self, region: str, emergency: bool = False) -> None:
        with self._lock:
            self._evaluate_region_locked(region, emergency)

    def _evaluate_region_locked(self, region: str, emergency: bool) -> None:
        st = self.state.setdefault(region, _new_region_state())
        cands = self.board.candidates(region)
        if not cands:
            # Нет здоровых нод — переключать не на что. Если это emergency (активная
            # заблокирована ТСПУ, замены нет) — фиксируем «застряли», чтобы факт
            # блокировки без восстановления остался в истории.
            if emergency:
                self._note_emergency_stuck(region, st, "no-candidates")
            return

        active = st.get("active")
        active_row = self.board.get(active) if active else None
        active_score = float(active_row["score"]) if active_row else 0.0
        now = time.time()

        # 1. Инициализация — активной ноды ещё нет: берём лучшую.
        if active_row is None:
            self._activate(cands[0], region, st, now, "init")
            return

        # 2. EMERGENCY — активная заблокирована/исчезла: немедленно на лучшую ДРУГУЮ.
        if emergency or active_score <= 0:
            others = [c for c in cands if c["node"] != active]
            if others:
                self._activate(others[0], region, st, now, "emergency")
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

    # --- Выбор кандидата для ротации -----------------------------------

    def _pick_rotation(self, cands, st) -> dict:
        """weighted-random из топ-K, исключая активную и последние avoid_recent."""
        active = st.get("active")
        recent = set(st.get("recent", [])[: self.cfg.rotation.avoid_recent])
        recent.add(active)
        filtered = [c for c in cands
                    if c["node"] not in recent
                    and float(c["score"]) >= self.cfg.rotation.min_score]
        pool = (filtered[: self.cfg.rotation.top_k]
                or [c for c in cands if c["node"] != active]
                or cands)
        dims, strengths = {}, {}
        lb = self.cfg.rotation.load_balance
        if lb.enabled and self._traffic_provider is not None:
            try:
                dims = self._traffic_provider() or {}
            except Exception as exc:  # noqa: BLE001 — балансировка не должна ронять ротацию
                print(f"  [switch] балансировка: не удалось получить трафик: {exc}")
            strengths = {"provider": lb.provider_strength,
                         "country": lb.country_strength,
                         "protocol": lb.protocol_strength}
        return _weighted_choice(pool, st.get("activations", {}), dims, strengths)

    def _activate(self, cand: dict, region: str, st: dict, now: float, reason: str) -> None:
        node = cand["node"]
        prev = st.get("active")             # предыдущая активная (для истории переходов)
        if node == st.get("active") and reason not in ("init",):
            return
        try:
            proxies = self.clash.all_proxies()
        except ClashApiError as exc:
            print(f"  [switch] {region}: не удалось получить прокси: {exc}")
            return
        skip = self._test_groups(proxies) | set(self.cfg.freeze_groups)
        pairs = self._chain_pairs(node, proxies, skip)
        if not pairs:
            print(f"  [switch] {region}: у '{node}' нет цепочки selector-групп — пропуск")
            return
        done = 0
        for sel, child in pairs:
            try:
                self.clash.select(sel, child)
                done += 1
            except ClashApiError as exc:
                print(f"  [switch] {region}: ошибка в группе '{sel}': {exc}")

        if done == 0:
            # Ни один PUT не прошёл — НЕ помечаем ноду активной и не заводим таймеры,
            # чтобы монитор/оператор не считали переключение состоявшимся.
            print(f"  [switch] {region}: переключение на '{node}' НЕ удалось (0 PUT) — состояние не меняем")
            return

        # Обновляем состояние.
        st["active"] = node
        st["last_switch"] = now
        st["quality_count"] = 0
        st.pop("emg_stuck", None)           # переключились — эпизод emergency закрыт
        st["rotate_deadline"] = now + self._rotate_delay()
        recent = [node] + [n for n in st.get("recent", []) if n != node]
        st["recent"] = recent[:10]
        st.setdefault("activations", {})
        st["activations"][node] = st["activations"].get(node, 0) + 1
        if self._storage is not None:
            prev_crc = parse_node(prev).node_id if prev else None
            self._storage.add_activation(
                region, cand.get("id") or parse_node(node).node_id,
                node, reason, cand.get("score"), prev_crc)
        self.board.set_active(region, node)
        partial = f", ЧАСТИЧНО {done}/{len(pairs)}" if done < len(pairs) else ""
        print(f"  [switch] {region}: {reason.upper()} → {node} "
              f"(score {cand['score']}, PUT {done}{partial})")

    def _chain_pairs(self, node: str, proxies: dict, skip: set) -> list[tuple[str, str]]:
        """Пары (селектор, желаемый_член) по ЦЕПОЧКЕ вверх от ноды.

        node → его leaf-группа (eu-LUNA-vless(ws)-out) → эта группа в родительском
        селекторе (eu-auto-out) → и так далее. Группы из skip (тестовые + freeze,
        напр. global-auto-out) не трогаем и выше них не поднимаемся.
        """
        selectors = {t: info for t, info in proxies.items()
                     if str(info.get("type", "")).lower() == "selector"}
        pairs: list[tuple[str, str]] = []
        queue = [node]
        seen = {node}
        while queue:
            child = queue.pop(0)
            for tag, info in selectors.items():
                if tag in skip or child not in (info.get("all") or []):
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
                groups.update(self.clash.list_group_members(self._tester_group))
            except ClashApiError:
                pass
        return groups

    def _rotate_delay(self) -> float:
        base = self.cfg.rotation.interval
        j = self.cfg.rotation.jitter
        return base + random.uniform(-j, j) if j else base


# Оси балансировки: (ключ поля кандидата, ключ агрегата в dims).
_LB_AXES = (("provider", "provider"), ("country", "country"), ("protocol", "protocol"))


def _weighted_choice(pool: list[dict], activations: dict,
                     dims: "dict | None" = None, strengths: "dict | None" = None) -> dict:
    """Взвешенный случайный выбор кандидата ротации.

    Базовый вес ~ score / (1 + число активаций). Балансировка трафика — множитель
    недогруза по ОСЯМ (провайдер/страна/протокол), перемножаются:
        underuse(axis) = scale / (scale + bytes),  scale = медиана байтов по пулу,
    где bytes — суммарный трафик за окно для значения оси у кандидата (напр. весь
    трафик провайдера этой ноды). Недогруженная ось → больший вес; перегруженная →
    меньший, но не 0. Масштаб адаптивный. Ось с strength<=0 или без данных не влияет.
    """
    dims = dims or {}
    strengths = strengths or {}
    # Предрасчёт медианного масштаба по каждой активной оси (по значениям в пуле).
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
            sc = scales.get(axis)
            if sc:
                b = (dims.get(axis) or {}).get(c.get(field), 0)
                w *= (sc / (sc + b)) ** strengths[axis]
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


def _load_state(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, ValueError):
        return {}


def _save_state(path: str, state: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
