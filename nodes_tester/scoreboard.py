"""Файл рейтинга нод score.csv: обновляется каждый прогон, исчезнувшие ноды
удаляются, активные помечаются флагом. Используется switcher'ом для выбора.

Строка на ноду. Хранит идентичность, итоговый score (S_final), последние
под-скоры (для наблюдаемости) и EWMA-состояние (для продолжения между
прогонами и после рестарта). Запись атомарна.
"""

from __future__ import annotations

import csv
import os
from typing import Optional

from . import scoring
from .identity import NodeIdentity

_COLUMNS = [
    "node", "provider", "protocol", "region", "country", "id",
    "active", "score",
    "reliability", "consistency", "throttle", "jitter", "latency", "throughput",
    "score_ewma", "avail", "flap", "samples", "last_seen",
]
_FLOAT_COLS = {"score", "score_ewma", "avail", "flap",
               *scoring.COMPONENTS}
_INT_COLS = {"active", "samples"}


class Scoreboard:
    def __init__(self, path: str, scoring_cfg):
        self.path = path
        self.cfg = scoring_cfg
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.rows: dict[str, dict] = _load(path)
        self._seen: set[str] = set()

    # --- Прогон --------------------------------------------------------

    def begin_pass(self) -> None:
        self._seen = set()

    def record(self, ident: NodeIdentity, region: str, tests: dict) -> tuple[float, float, bool]:
        """Обновить рейтинг ноды по её тестам.

        Возвращает (S_final, S_run, gate): S_final — сглаженный рейтинг (для
        рейтинга/качества), S_run/gate — мгновенный результат этого прогона
        (для emergency: gate=False значит нода заблокирована прямо сейчас).
        """
        s_run, gate, comps = scoring.instant_score(
            tests, self.cfg.thresholds, self.cfg.weights
        )
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
            "id": ident.node_id,
            "active": (prev.get("active", 0) if prev else 0),
            "last_seen": self._pass_no,
        }
        for comp in scoring.COMPONENTS:
            v = comps.get(comp)
            row[comp] = round(v, 3) if v is not None else ""
        row.update(agg)
        self.rows[ident.raw] = row
        self._seen.add(ident.raw)
        return float(agg["score"]), s_run, gate

    def end_pass(self, pass_no: int) -> None:
        """Удалить исчезнувшие ноды (не встреченные в этом прогоне) и записать."""
        for tag in list(self.rows):
            if tag not in self._seen:
                del self.rows[tag]
        self.write()

    @property
    def _pass_no(self):
        return getattr(self, "_current_pass", 0)

    def set_pass(self, pass_no: int) -> None:
        self._current_pass = pass_no

    # --- Доступ для switcher (читается из фонового монитора — снимаем снимок) ---
    # Монитор-поток читает рейтинг, пока прогон-поток его пишет. Снимок через
    # list(...) атомарен под GIL и исключает "dict changed size during iteration".

    def get(self, node: str) -> Optional[dict]:
        return self.rows.get(node)

    def candidates(self, region: str) -> list[dict]:
        """Здоровые ноды региона (score > 0), по убыванию score."""
        rows = [r for r in list(self.rows.values())
                if r["region"] == region and float(r.get("score", 0)) > 0]
        return sorted(rows, key=lambda r: float(r["score"]), reverse=True)

    def regions(self) -> list[str]:
        return sorted({r["region"] for r in list(self.rows.values()) if r["region"]})

    def set_active(self, region: str, node: str) -> None:
        for r in list(self.rows.values()):
            if r["region"] == region:
                r["active"] = 1 if r["node"] == node else 0

    # --- Запись --------------------------------------------------------

    def write(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=_COLUMNS, extrasaction="ignore",
                                    lineterminator="\n")
            writer.writeheader()
            # Активные сверху, далее по убыванию score.
            for r in sorted(self.rows.values(),
                            key=lambda r: (int(r.get("active", 0)), float(r.get("score", 0))),
                            reverse=True):
                writer.writerow(r)
        os.replace(tmp, self.path)


def _load(path: str) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    try:
        with open(path, "r", encoding="utf-8", newline="") as fh:
            for raw in csv.DictReader(fh):
                row = dict(raw)
                for k in list(row):
                    if k in _FLOAT_COLS:
                        row[k] = _to_float(row[k])
                    elif k in _INT_COLS:
                        row[k] = _to_int(row[k])
                if row.get("node"):
                    rows[row["node"]] = row
    except FileNotFoundError:
        pass
    return rows


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _to_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0
