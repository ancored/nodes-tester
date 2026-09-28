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
    # heavy_ok=="0" (нода провалила sustained 50MB) исключает её из кандидатов —
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

    def candidates(self, region: str) -> list[dict]:
        """Здоровые ноды региона (score > 0), по убыванию score. Свежий heavy-veto
        исключается — но если не-vetoed не осталось, возвращаем vetoed (мало нод →
        выбираем из имеющихся, конструкция не разваливается)."""
        with self._lock:
            healthy = [r for r in self.rows.values()
                       if r["region"] == region and float(r.get("score", 0)) > 0]
            non_veto = [r for r in healthy if not self._vetoed(r)]
            chosen = non_veto or healthy
            return [dict(r) for r in                       # копии наружу (монитор-поток)
                    sorted(chosen, key=lambda r: float(r["score"]), reverse=True)]

    def regions(self) -> list[str]:
        with self._lock:
            return sorted({r["region"] for r in self.rows.values() if r["region"]})

    def set_active(self, region: str, node: str) -> None:
        with self._lock:
            for r in self.rows.values():
                if r["region"] == region:
                    r["active"] = 1 if r["node"] == node else 0

    # --- Запись --------------------------------------------------------

    def write(self) -> None:
        with self._lock:
            self.storage.save_scores(list(self.rows.values()))
