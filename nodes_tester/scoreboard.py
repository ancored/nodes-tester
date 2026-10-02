"""Рейтинг нод в SQLite (таблица scores): обновляется каждый прогон, исчезнувшие
ноды удаляются, активные помечаются флагом. Используется switcher'ом для выбора.

В памяти держим снимок (dict по raw-тегу ноды) — как раньше при csv; персистентность
идёт через Storage (scores + score_history). Строка хранит идентичность, итоговый
score (S_final), последние под-скоры и EWMA-состояние (продолжение между прогонами и
после рестарта). score_history копит точку (score/s_run/gate) на каждую ноду каждый
прогон — для динамики рейтинга в дашборде.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

from . import scoring
from .identity import NodeIdentity


class Scoreboard:
    def __init__(self, storage, scoring_cfg):
        self.storage = storage
        self.cfg = scoring_cfg
        self.rows: dict[str, dict] = storage.load_scores()
        self._seen: set[str] = set()
        self._history: list[tuple] = []       # буфер точек истории за прогон
        self._heavy_veto_secs = 0.0           # 0 = veto не истекает по времени
        # Группы переключения ({name}-auto-out в sing-box) → теги нод-членов. Нода может
        # быть в нескольких группах. None — состав неизвестен: группа = регион ноды.
        self._members: dict[str, set[str]] | None = None
        self._active: dict[str, str] = {}     # группа → активная нода
        # Обязательные тесты групп: группа → {тест: срок годности результата, с}.
        self._required: dict[str, dict[str, float]] = {}
        # CRC нод с действующей паузой/карантином/баном: строка рейтинга у них остаётся
        # (board.keep), но выбирать их нельзя — ни ротацией, ни emergency, ни вручную.
        self._restricted: set[str] = set()
        # Прогон-поток пишет (record/set_heavy/end_pass/write), фоновый монитор читает
        # (candidates/get/regions) и ставит active — защищаем составные операции.
        self._lock = threading.RLock()

    # --- Прогон --------------------------------------------------------

    def begin_pass(self) -> None:
        with self._lock:
            self._seen = set()
            self._history = []

    def keep(self, tag: str) -> None:
        """Пометить ноду «встреченной» без нового замера — чтобы end_pass её не
        удалил (для backoff-пропуска: строка и её последний score сохраняются)."""
        with self._lock:
            self._seen.add(tag)

    def record(self, ident: NodeIdentity, region: str, tests: dict) -> tuple[float, float, bool]:
        """Обновить рейтинг ноды по её тестам.

        Возвращает (S_final, S_run, gate): S_final — сглаженный рейтинг (для
        рейтинга/качества), S_run/gate — мгновенный результат этого прогона
        (для emergency: gate=False значит нода заблокирована прямо сейчас).
        """
        s_run, gate, comps = scoring.instant_score(
            tests, self.cfg.thresholds, self.cfg.weights
        )
        with self._lock:
            return self._record_locked(ident, region, s_run, gate, comps)

    def _record_locked(self, ident, region, s_run, gate, comps):
        prev = self.rows.get(ident.raw)
        prev_state = None
        if prev is not None:
            prev_state = {
                "score_ewma": prev.get("score_ewma", s_run),
                "flap": prev.get("flap", 0.0),
                "avail": prev.get("avail", 1.0 if gate else 0.0),
                "samples": prev.get("samples", 0),
            }
        agg = scoring.aggregate(prev_state, s_run, gate, self.cfg)

        row = {
            "node": ident.raw,
            "provider": ident.provider,
            "protocol": ident.protocol,
            "region": region,
            "country": ident.country,
            "label": ident.label,
            "id": ident.node_id,
            "active": (prev.get("active", 0) if prev else 0),
            "last_seen": self._pass_no,
            # veto тяжёлого download не относится к лёгкому прогону — переносим как есть
            "heavy_ok": (prev.get("heavy_ok", "") if prev else ""),
            "heavy_ts": (prev.get("heavy_ts", 0) if prev else 0),
            # обязательные тесты групп идут отдельной фазой — переносим как есть
            "gemini_ok": (prev.get("gemini_ok", "") if prev else ""),
            "gemini_ts": (prev.get("gemini_ts", 0) if prev else 0),
            "gemini_cc": (prev.get("gemini_cc", "") if prev else ""),
        }
        for comp in scoring.COMPONENTS:
            v = comps.get(comp)
            row[comp] = round(v, 3) if v is not None else ""
        row.update(agg)
        self.rows[ident.raw] = row
        self._seen.add(ident.raw)
        # Точка истории рейтинга этой ноды за прогон (для динамики в дашборде).
        self._history.append((ident.node_id, region, float(agg["score"]), s_run, gate))
        return float(agg["score"]), s_run, gate

    def end_pass(self, pass_no: int) -> None:
        """Удалить исчезнувшие ноды (не встреченные в этом прогоне), записать снимок
        рейтинга и дописать точки истории за прогон."""
        with self._lock:
            for tag in list(self.rows):
                if tag not in self._seen:
                    del self.rows[tag]
            self.storage.save_scores(list(self.rows.values()))
            if self._history:
                self.storage.add_score_history(int(time.time()), self._history)
                self._history = []

    @property
    def _pass_no(self):
        return getattr(self, "_current_pass", 0)

    def set_pass(self, pass_no: int) -> None:
        self._current_pass = pass_no

    # --- Доступ для switcher (читается из фонового монитора — снимаем снимок) ---
    # Монитор-поток читает рейтинг, пока прогон-поток его пишет. Снимок через
    # list(...) атомарен под GIL и исключает "dict changed size during iteration".

    def get(self, node: str) -> Optional[dict]:
        with self._lock:
            r = self.rows.get(node)
            return dict(r) if r is not None else None   # копия наружу

    # --- Тяжёлый download как veto (двухуровневое тестирование) ---------
    # heavy_ok=="0" (нода провалила sustained heavy-закачку) исключает её из кандидатов —
    # но лишь при наличии не-vetoed альтернатив: при малом числе нод выбираем из
    # имеющихся. Veto протухает по TTL, тогда нода снова попадёт на тяжёлую пробу.

    def set_heavy_veto_ttl(self, seconds: float) -> None:
        self._heavy_veto_secs = max(0.0, float(seconds or 0))

    def set_heavy(self, node: str, ok: bool) -> None:
        """Зафиксировать результат тяжёлого download для ноды (veto-фильтр)."""
        with self._lock:
            r = self.rows.get(node)
            if r is not None:
                r["heavy_ok"] = "1" if ok else "0"
                r["heavy_ts"] = int(time.time())

    def _vetoed(self, r: dict) -> bool:
        if str(r.get("heavy_ok", "")) != "0":
            return False
        if self._heavy_veto_secs <= 0:
            return True                       # veto без TTL — действует, пока не пере-проверят
        age = time.time() - int(r.get("heavy_ts", 0) or 0)
        return age < self._heavy_veto_secs    # свежий veto действует, протухший — нет

    def set_groups(self, members: dict[str, set[str]]) -> None:
        """Состав групп переключения по данным sing-box (группа → теги нод)."""
        with self._lock:
            # Групп нет (нестандартный конфиг) — прежняя схема: группа = регион ноды.
            self._members = {g: set(tags) for g, tags in members.items() if tags} or None

    def node_groups(self, node: str) -> list[str]:
        """Группы, в которые входит нода (порядок — как в sing-box)."""
        with self._lock:
            if self._members is None:
                r = self.rows.get(node)
                return [r["region"]] if r and r.get("region") else []
            return [g for g, tags in self._members.items() if node in tags]

    def group_nodes(self, group: str) -> list[str]:
        """Ноды группы (без данных sing-box — ноды региона)."""
        with self._lock:
            if self._members is None:
                return [n for n, r in self.rows.items() if r.get("region") == group]
            return list(self._members.get(group, ()))

    def _in_group(self, r: dict, group: str) -> bool:
        if self._members is None:
            return r["region"] == group
        return r["node"] in self._members.get(group, ())

    # --- Обязательные тесты групп (gemini): решающие, в скоринг не входят ---------

    def set_required(self, required: dict[str, dict[str, float]]) -> None:
        """Группа → {тест: срок годности результата, с}."""
        with self._lock:
            self._required = {g: dict(t) for g, t in required.items() if t}

    def required(self, group: str) -> dict[str, float]:
        with self._lock:
            return dict(self._required.get(group, {}))

    def set_required_result(self, node: str, test: str, ok: bool, detail: str = "") -> None:
        with self._lock:
            r = self.rows.get(node)
            if r is not None:
                r[f"{test}_ok"] = "1" if ok else "0"
                r[f"{test}_ts"] = int(time.time())
                r[f"{test}_cc"] = detail or ""

    @staticmethod
    def _fresh(r: dict, test: str, ttl: float) -> bool:
        return time.time() - int(r.get(f"{test}_ts", 0) or 0) < ttl

    def needs_test(self, node: str, test: str, ttl: float) -> bool:
        """Нет свежего результата обязательного теста."""
        with self._lock:
            r = self.rows.get(node)
            return r is not None and (str(r.get(f"{test}_ok", "")) == "" or not self._fresh(r, test, ttl))

    def _passed(self, r: dict, group: str) -> bool:
        return all(str(r.get(f"{t}_ok", "")) == "1" and self._fresh(r, t, ttl)
                   for t, ttl in self._required.get(group, {}).items())

    def _failed(self, r: dict, group: str) -> bool:
        return any(str(r.get(f"{t}_ok", "")) == "0" and self._fresh(r, t, ttl)
                   for t, ttl in self._required.get(group, {}).items())

    def _healthy(self, region: str) -> list[dict]:
        """Здоровые ноды группы (score > 0), по убыванию score. Свежий heavy-veto
        исключается — но если не-vetoed не осталось, берём vetoed (мало нод → выбираем
        из имеющихся, конструкция не разваливается)."""
        healthy = [r for r in self.rows.values()
                   if self._in_group(r, region) and float(r.get("score", 0)) > 0
                   and r.get("id") not in self._restricted]
        non_veto = [r for r in healthy if not self._vetoed(r)]
        return sorted(non_veto or healthy, key=lambda r: float(r["score"]), reverse=True)

    def candidates(self, region: str) -> list[dict]:
        """Кандидаты группы: здоровые ноды; если у группы есть обязательные тесты —
        только прошедшие их со свежим результатом (решающее условие, без поблажек)."""
        with self._lock:
            chosen = [r for r in self._healthy(region) if self._passed(r, region)]
            return [dict(r) for r in chosen]               # копии наружу (монитор-поток)

    def probe_pool(self, region: str) -> list[str]:
        """Ноды, которые стоит проверять обязательными тестами: здоровые, без свежего
        провала, по убыванию score."""
        with self._lock:
            return [r["node"] for r in self._healthy(region) if not self._failed(r, region)]

    def set_restricted(self, crcs) -> None:
        """Полный набор ограниченных нод (снимок на прогон)."""
        with self._lock:
            self._restricted = {c for c in crcs if c}

    def restrict(self, crc: str, on: bool) -> None:
        """Ограничение одной ноды поставлено/снято (карантин, бан, снятие)."""
        if not crc:
            return
        with self._lock:
            if on:
                self._restricted.add(crc)
            else:
                self._restricted.discard(crc)

    def is_restricted(self, node: str) -> bool:
        with self._lock:
            r = self.rows.get(node)
            return bool(r and r.get("id") in self._restricted)

    def regions(self) -> list[str]:
        """Группы переключения (без данных sing-box — регионы нод)."""
        with self._lock:
            if self._members is not None:
                return list(self._members)
            return sorted({r["region"] for r in self.rows.values() if r["region"]})

    def set_active(self, region: str, node: str) -> None:
        """Активная нода группы; флаг active у ноды — активна хотя бы в одной группе."""
        with self._lock:
            if node:
                self._active[region] = node
            else:
                self._active.pop(region, None)
            # По всем строкам: при старте группы восстанавливаются раньше, чем известен их
            # состав, а нода группы (ai) может иметь другой регион (eu) — фильтр по региону
            # терял её флаг до следующего переключения.
            actives = set(self._active.values())
            for r in self.rows.values():
                r["active"] = 1 if r["node"] in actives else 0

    # --- Запись --------------------------------------------------------

    def write(self) -> None:
        with self._lock:
            self.storage.save_scores(list(self.rows.values()))
