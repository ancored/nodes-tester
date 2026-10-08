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
from .ipinfo import db_country_mismatch, preferred


class Scoreboard:
    def __init__(self, storage, scoring_cfg):
        self.storage = storage
        self.cfg = scoring_cfg
        self.rows: dict[str, dict] = storage.load_scores()
        storage.set_reserve_marks({})         # резерв после старта собирается заново
        self._seen: set[str] = set()
        self._history: list[tuple] = []       # буфер точек истории за прогон
        self._heavy_ttl = 0.0                 # срок годности heavy, с; 0 — бессрочно
        # Группы переключения ({name}-auto-out в sing-box) → теги нод-членов. Нода может
        # быть в нескольких группах. None — состав неизвестен: группа = регион ноды.
        self._members: dict[str, set[str]] | None = None
        self._active: dict[str, str] = {}     # группа → активная нода
        # Обязательные тесты групп: группа → {тест: срок годности результата, с}.
        self._required: dict[str, dict[str, float]] = {}
        # Резерв групп (nodes_tester.reserve): группа → проверенные кандидаты на замену.
        self._reserve: dict[str, list[str]] = {}
        # Группы со строгим выходным IP (run.*.strict_exit_ip) и сведения о выходных IP.
        self._strict: set[str] = set()
        self._ip_info: dict[str, dict] = {}
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
        conn = tests.get("connectivity") or {}
        with self._lock:
            return self._record_locked(ident, region, s_run, gate, comps, conn)

    def _record_locked(self, ident, region, s_run, gate, comps, conn):
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
            "required": dict(prev.get("required") or {}) if prev else {},
            "exit_ip": (prev.get("exit_ip", "") if prev else ""),
            "exit_cc": (prev.get("exit_cc", "") if prev else ""),
        }
        if conn.get("exit_ip"):               # без ответа connectivity — прежний выход
            row["exit_ip"] = conn["exit_ip"]
            row["exit_cc"] = (conn.get("country") or "").upper()
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
            self.storage.save_scores(self._rows_for_save())
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

    # --- Тяжёлый download (veto) --------------------------------------------
    # heavy_ok=="0" (нода провалила sustained heavy-закачку) исключает её из кандидатов —
    # но лишь при наличии не-vetoed альтернатив: при малом числе нод выбираем из
    # имеющихся. Результат (успех и veto) живёт heavy_ttl, потом нода проверяется снова.

    def set_heavy_ttl(self, seconds: float) -> None:
        self._heavy_ttl = max(0.0, float(seconds or 0))

    def set_heavy(self, node: str, ok: bool) -> None:
        """Зафиксировать результат тяжёлого download для ноды (veto-фильтр)."""
        with self._lock:
            r = self.rows.get(node)
            if r is not None:
                r["heavy_ok"] = "1" if ok else "0"
                r["heavy_ts"] = int(time.time())

    def _heavy(self, r: dict) -> str:
        """Свежий результат heavy: "1"/"0", "" — нет или просрочен."""
        verdict = str(r.get("heavy_ok", ""))
        if self._heavy_ttl > 0 and time.time() - int(r.get("heavy_ts", 0) or 0) >= self._heavy_ttl:
            return ""
        return verdict

    def _vetoed(self, r: dict) -> bool:
        return self._heavy(r) == "0"

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
                r.setdefault("required", {})[test] = {"ok": "1" if ok else "0",
                                                      "ts": int(time.time()), "cc": detail or ""}

    @staticmethod
    def _result(r: dict, test: str) -> dict:
        return (r.get("required") or {}).get(test) or {}

    def _verdict(self, r: dict, test: str, ttl: float) -> str:
        """Свежий результат обязательного теста: "1"/"0", "" — нет или просрочен."""
        res = self._result(r, test)
        fresh = time.time() - int(res.get("ts", 0) or 0) < ttl
        return str(res.get("ok", "")) if fresh else ""

    def _passed(self, r: dict, group: str) -> bool:
        return all(self._verdict(r, t, ttl) == "1" for t, ttl in self._required.get(group, {}).items())

    def _failed(self, r: dict, group: str) -> bool:
        return any(self._verdict(r, t, ttl) == "0" for t, ttl in self._required.get(group, {}).items())

    # --- Строгий выходной IP (strict_exit_ip): страна по базе и тип IP -----------

    def set_strict(self, groups) -> None:
        with self._lock:
            self._strict = set(groups)

    def set_ip_info(self, infos: dict) -> None:
        """Сведения о выходных IP (кэш ip_info): {ip: {country, mobile, proxy, hosting, …}}."""
        with self._lock:
            self._ip_info = dict(infos or {})

    def _exit_info(self, r: dict) -> "dict | None":
        return self._ip_info.get(r.get("exit_ip") or "")

    def _geo_bad(self, r: dict, group: str) -> bool:
        """База относит выходной IP к другой стране, чем connectivity («по базе XX»)."""
        return group in self._strict and bool(db_country_mismatch(self._exit_info(r), r.get("exit_cc") or ""))

    def _healthy(self, region: str) -> list[dict]:
        """Здоровые ноды группы (score > 0), по убыванию score. Свежий heavy-veto
        исключается — но если не-vetoed не осталось, берём vetoed (мало нод → выбираем
        из имеющихся, конструкция не разваливается)."""
        healthy = [r for r in self.rows.values()
                   if self._in_group(r, region) and float(r.get("score", 0)) > 0
                   and r.get("id") not in self._restricted]
        non_veto = [r for r in healthy if not self._vetoed(r)]
        return sorted(non_veto or healthy, key=lambda r: float(r["score"]), reverse=True)

    def _tier(self, r: dict, group: str) -> int:
        """Ярус выходного IP: 0 — домашний или мобильный (или группа не строгая),
        1 — дата-центр, прокси, IP без сведений."""
        return int(group in self._strict and not preferred(self._exit_info(r)))

    def tier(self, node: str, group: str) -> int:
        with self._lock:
            r = self.rows.get(node)
            return self._tier(r, group) if r is not None else 1

    def candidates(self, region: str) -> list[dict]:
        """Кандидаты группы: здоровые ноды; если у группы есть обязательные тесты —
        только прошедшие их со свежим результатом (решающее условие, без поблажек).
        Строгая группа: без расхождения страны по базе; ярус выходного IP (`tier`) —
        приоритет, а не фильтр: сначала домашние и мобильные, затем остальные."""
        with self._lock:
            chosen = [dict(r, tier=self._tier(r, region)) for r in self._healthy(region)
                      if self._passed(r, region) and not self._geo_bad(r, region)]
            chosen.sort(key=lambda c: c["tier"])           # внутри яруса — по рейтингу
            return chosen                                  # копии наружу (монитор-поток)

    # --- Резерв группы (nodes_tester.reserve) -----------------------------------

    def reserve_pool(self, group: str, min_score: float = 0.0) -> list[str]:
        """Претенденты в резерв: здоровые ноды группы без свежего провала обязательных
        тестов и heavy, без расхождения страны по базе. Порядок: в строгой группе сначала
        домашние и мобильные IP, затем рейтинг не ниже min_score, затем по рейтингу."""
        with self._lock:
            pool = [r for r in self.rows.values() if self._reserve_fit(r, group)]
            pool.sort(key=lambda r: (self._tier(r, group),
                                     float(r["score"]) < min_score, -float(r["score"])))
            return [r["node"] for r in pool]

    def reserve_missing(self, node: str, group: str) -> "list[str] | None":
        """Что осталось проверить, чтобы нода годилась в резерв группы: обязательные тесты
        без свежего результата и "heavy". None — нода не годится (нет в группе, рейтинг 0,
        ограничена, свежий провал теста или heavy, расхождение страны по базе)."""
        with self._lock:
            r = self.rows.get(node)
            if r is None or not self._reserve_fit(r, group):
                return None
            missing = [t for t, ttl in self._required.get(group, {}).items()
                       if self._verdict(r, t, ttl) == ""]
            if self._heavy(r) != "1":
                missing.append("heavy")
            return missing

    def _reserve_fit(self, r: dict, group: str) -> bool:
        return (self._in_group(r, group) and float(r.get("score", 0)) > 0
                and r.get("id") not in self._restricted and not self._vetoed(r)
                and not self._failed(r, group) and not self._geo_bad(r, group))

    def reserve(self, group: str) -> list[str]:
        with self._lock:
            return list(self._reserve.get(group, ()))

    def set_reserve(self, group: str, nodes: list[str]) -> None:
        """Состав резерва группы; флаг reserve в scores (БД) обновляется сразу."""
        with self._lock:
            if list(self._reserve.get(group, ())) == list(nodes):
                return
            self._reserve[group] = list(nodes)
            marks = self._reserve_marks()
            crcs = {n: r.get("id") for n, r in self.rows.items()}
        self.storage.set_reserve_marks({crcs[n]: g for n, g in marks.items() if crcs.get(n)})

    def _reserve_marks(self) -> dict[str, str]:
        """Нода → группы резерва через запятую."""
        marks: dict[str, list[str]] = {}
        for group, nodes in self._reserve.items():
            for node in nodes:
                marks.setdefault(node, []).append(group)
        return {n: ",".join(sorted(gs)) for n, gs in marks.items()}

    def _rows_for_save(self) -> list[dict]:
        marks = self._reserve_marks()
        return [{**r, "reserve": marks.get(r["node"], "")} for r in self.rows.values()]

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
            self.storage.save_scores(self._rows_for_save())
