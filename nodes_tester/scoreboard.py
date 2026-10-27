"""Рейтинг нод в SQLite (таблица scores): строка ноды пересчитывается и пишется после
каждого её замера, ушедшие из sing-box ноды удаляются, активные помечаются флагом.
Используется switcher'ом для выбора.

В памяти держим снимок (dict по raw-тегу ноды); персистентность идёт через Storage
(scores + score_history). Строка хранит идентичность, итоговый score (S_final), последние
под-скоры и EWMA-состояние (продолжение между замерами и после рестарта). score_history
копит точку (score/s_run/gate) на каждый замер ноды — для динамики рейтинга в дашборде.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

from . import scoring
from .config import region_of
from .freshness import expires, fresh
from .identity import NodeIdentity
from .ipinfo import db_country_mismatch, kind

# Проверки, которые резерв догоняет сам (по одной на ноду); прочие дают замеры тестера.
RUNNABLE_CHECKS = ("gemini", "openai", "anthropic", "upstream", "heavy")
_DEFAULT_KINDS = ("residential", "mobile")


class Scoreboard:
    def __init__(self, storage, scoring_cfg):
        self.storage = storage
        self.cfg = scoring_cfg
        self.rows: dict[str, dict] = storage.load_scores()
        storage.set_reserve_marks({})         # резерв после старта собирается заново
        self._heavy_ttl = (0.0, 0.0)          # срок годности heavy и разброс, с; 0 — бессрочно
        self.tier_floor = 0.0                 # selection.tier_floor: ниже — ярус после всех
        # Группы переключения ({tag}-auto-out в sing-box) → теги нод-членов. Пока состав
        # из sing-box не получен, групп нет: выбирать не из чего.
        self._members: dict[str, set[str]] = {}
        self._active: dict[str, str] = {}     # группа → активная нода
        # Политика групп (config.groups): обязательные и приоритетные проверки.
        self._policy: dict = {}
        self._regions: dict[str, list[str]] = {}
        # Резерв групп (nodes_tester.reserve): группа → проверенные кандидаты на замену.
        self._reserve: dict[str, list[str]] = {}
        # Сведения о выходных IP (кэш ip_info): страна по базе, тип IP.
        self._ip_info: dict[str, dict] = {}
        # Множитель боевой надёжности по ключу ноды (selection.stability); нет — 1.
        self._stability: dict[str, float] = {}
        # CRC нод с действующей паузой/карантином/баном: строка рейтинга у них остаётся,
        # но выбирать их нельзя — ни ротацией, ни emergency, ни вручную.
        self._restricted: set[str] = set()
        # CRC нод, с которых сняли ограничение: следующий замер начинает сглаженный рейтинг
        # заново, иначе память провалов сразу вернёт выздоровевшую ноду в карантин.
        self._fresh: set[str] = set()
        # Тестер и резерв пишут (record/set_heavy/prune), фоновый монитор читает
        # (candidates/get/regions) и ставит active — защищаем составные операции.
        self._lock = threading.RLock()

    # --- Замеры --------------------------------------------------------

    def record(self, ident: NodeIdentity, region: str, tests: dict) -> tuple[float, float, bool]:
        """Обновить рейтинг ноды по последнему результату каждого её теста.

        Возвращает (S_final, S_run, gate): S_final — сглаженный рейтинг (для
        рейтинга/качества), S_run/gate — мгновенный результат этого замера
        (для emergency: gate=False значит нода заблокирована прямо сейчас).
        """
        s_run, gate, comps = scoring.instant_score(
            tests, self.cfg.thresholds, self.cfg.weights
        )
        conn = tests.get("connectivity") or {}
        with self._lock:
            return self._record_locked(ident, region, s_run, gate, comps, conn)

    def _record_locked(self, ident, region, s_run, gate, comps, conn):
        # Новый доступ известной ноды (провайдер перевыпустил учётные данные) наследует
        # её состояние от лучшего соседнего доступа; gate этого замера — свой.
        own = self.rows.get(ident.raw)
        prev = own or self._best_sibling(ident) or {}
        prev_state = None
        if ident.node_id in self._fresh:
            self._fresh.discard(ident.node_id)
        elif prev:
            prev_state = {
                "score_ewma": prev.get("score_ewma", s_run),
                "flap": prev.get("flap", 0.0),
                "avail": prev.get("avail", 1.0 if gate else 0.0),
                "samples": prev.get("samples", 0),
            }
        agg = scoring.aggregate(prev_state, s_run, gate, self.cfg, self._stability.get(ident.node_id, 1.0))

        row = {
            "node": ident.raw,
            "provider": ident.provider,
            "protocol": ident.protocol,
            "region": region,
            "id": ident.node_id,
            "active": (own.get("active", 0) if own else 0),
            # heavy и обязательные тесты групп гоняет резерв — переносим как есть
            "heavy_ok": prev.get("heavy_ok", ""),
            "heavy_ts": prev.get("heavy_ts", 0),
            "required": dict(prev.get("required") or {}),
            "exit_ip": prev.get("exit_ip", ""),
            "exit_cc": prev.get("exit_cc", ""),
            "country_ts": prev.get("country_ts", 0),
        }
        if conn.get("exit_ip"):               # без ответа connectivity — прежний выход
            row["exit_ip"] = conn["exit_ip"]
            row["exit_cc"] = (conn.get("country") or "").upper()
            row["country_ts"] = int(time.time())
        row["country"] = row["exit_cc"].lower()   # страна ноды — измеренная, не из имени
        for comp in scoring.COMPONENTS:
            v = comps.get(comp)
            row[comp] = round(v, 3) if v is not None else ""
        row.update(agg)
        self.rows[ident.raw] = row
        self._persist(row)
        self.storage.add_score_history(int(time.time()), [(ident.node_id, region,
                                                           float(agg["score"]), s_run, gate)])
        return float(agg["score"]), s_run, gate

    def set_stability(self, stats: dict[str, list], cfg) -> None:
        """Пересчитать множители надёжности (selection.stability) по боевой статистике
        {crc: [часы активной, аварий]} и рейтинг нод с ними. Частота поставщика — по его
        нодам; поставщик с малой историей (provider_min_hours) — средняя частота всех."""
        with self._lock:
            provider_of = {r.get("id"): r.get("provider") or "" for r in self.rows.values()}
            totals: dict[str, list] = {}
            for crc, (hours, fails) in stats.items():
                t = totals.setdefault(provider_of.get(crc, ""), [0.0, 0])
                t[0] += hours
                t[1] += fails
            all_hours = sum(t[0] for t in totals.values())
            overall = sum(t[1] for t in totals.values()) / all_hours if all_hours else 0.0
            rates = {p: (f / h if h >= cfg.provider_min_hours else overall) for p, (h, f) in totals.items()}
            self._stability = {}
            changed = []
            for row in self.rows.values():
                hours, fails = stats.get(row.get("id"), (0.0, 0))
                factor = scoring.stability_factor(fails, hours, rates.get(row.get("provider") or "", overall), cfg)
                self._stability[row.get("id")] = factor
                if float(row.get("score") or 0) <= 0:
                    continue                          # провал gate — рейтинг 0 и так
                score = round(scoring.final_score(float(row["score_ewma"]), float(row["avail"]),
                                                  float(row["flap"]), self.cfg) * factor, 1)
                if score != row["score"]:
                    row["score"] = score
                    changed.append(row)
            if changed:
                marks = self._reserve_marks()
                self.storage.upsert_scores([{**r, "reserve": marks.get(r["node"], "")} for r in changed])

    def stability(self, node: str) -> float:
        """Множитель надёжности ноды (1 — нет истории или учёт выключен)."""
        with self._lock:
            r = self.rows.get(node)
            return self._stability.get(r.get("id"), 1.0) if r else 1.0

    def _persist(self, row: dict) -> None:
        """Записать строку ноды в scores. У дублей с тем же ключом пишется предпочтительная."""
        same = [r for r in self.rows.values() if r.get("id") == row.get("id")]
        marks = self._reserve_marks()
        self.storage.upsert_scores([{**r, "reserve": marks.get(r["node"], "")} for r in same])

    def prune(self, nodes) -> None:
        """Убрать строки нод, которых больше нет среди тестируемых (ушли из sing-box, бан)."""
        with self._lock:
            gone = [tag for tag in self.rows if tag not in nodes]
            if not gone:
                return
            crcs = {self.rows.pop(tag).get("id") for tag in gone}
            crcs -= {r.get("id") for r in self.rows.values()}
        crcs.discard(None)
        if crcs:
            self.storage.delete_scores(crcs)

    def _best_sibling(self, ident) -> Optional[dict]:
        """Строка лучшего (по рейтингу) другого доступа той же ноды (node_crc)."""
        prefix = ident.node_crc + "."
        siblings = [r for r in self.rows.values()
                    if r["node"] != ident.raw and str(r.get("id", "")).startswith(prefix)]
        return max(siblings, key=lambda r: float(r.get("score") or 0), default=None)

    def country(self, ident) -> str:
        """Измеренная страна ноды (ISO, нижний регистр); "" — ещё не измерена."""
        with self._lock:
            row = self.rows.get(ident.raw) or self._best_sibling(ident)
            return (row or {}).get("country") or ""

    # --- Доступ для switcher (читается из фонового монитора — снимаем снимок) ---
    # Монитор-поток читает рейтинг, пока тестер его пишет. Снимок через
    # list(...) атомарен под GIL и исключает "dict changed size during iteration".

    def get(self, node: str) -> Optional[dict]:
        with self._lock:
            r = self.rows.get(node)
            return dict(r) if r is not None else None   # копия наружу

    # --- Тяжёлый download (veto) --------------------------------------------
    # heavy_ok=="0" (нода провалила sustained heavy-закачку) исключает её из кандидатов —
    # но лишь при наличии не-vetoed альтернатив: при малом числе нод выбираем из
    # имеющихся. Результат (успех и veto) живёт heavy_ttl, потом нода проверяется снова.

    def set_heavy_ttl(self, seconds: float, jitter: float = 0.0) -> None:
        self._heavy_ttl = (max(0.0, float(seconds or 0)), max(0.0, float(jitter or 0)))

    def set_heavy(self, node: str, ok: bool) -> None:
        """Зафиксировать результат тяжёлого download для ноды (veto-фильтр)."""
        with self._lock:
            r = self.rows.get(node)
            if r is not None:
                r["heavy_ok"] = "1" if ok else "0"
                r["heavy_ts"] = int(time.time())
                self._persist(r)

    def _heavy(self, r: dict) -> str:
        """Свежий результат heavy: "1"/"0", "" — нет или просрочен."""
        ttl, jitter = self._heavy_ttl
        if not fresh(r.get("heavy_ts"), ttl, jitter, f"{r.get('id')}/heavy"):
            return ""
        return str(r.get("heavy_ok", ""))

    def _vetoed(self, r: dict) -> bool:
        return self._heavy(r) == "0"

    def set_groups(self, members: dict[str, set[str]]) -> None:
        """Состав групп переключения по данным sing-box (группа → теги нод)."""
        with self._lock:
            self._members = {g: set(tags) for g, tags in members.items() if tags}

    def node_groups(self, node: str) -> list[str]:
        """Группы, в которые входит нода (порядок — как в sing-box)."""
        with self._lock:
            return [g for g, tags in self._members.items() if node in tags]

    def group_nodes(self, group: str) -> list[str]:
        """Ноды группы."""
        with self._lock:
            return list(self._members.get(group, ()))

    def _in_group(self, r: dict, group: str) -> bool:
        return r["node"] in self._members.get(group, ())

    # --- Политика групп: проверки ------------------------------------------
    # Вердикт проверки: "1" — свежий успех, "0" — свежий провал, "" — неизвестно (нет
    # замера или срок ttl истёк). Обязательные (require) — все "1", иначе нода не кандидат;
    # приоритетные (prefer) — порядок: меньше непройденных — раньше.

    def set_policy(self, groups, regions: dict) -> None:
        """Группы из config.groups и именованные регионы стран."""
        with self._lock:
            self._policy = {g.tag: g for g in groups}
            self._regions = dict(regions)

    def policy(self, group: str):
        with self._lock:
            return self._policy.get(group)

    def requirements(self, group: str) -> list[str]:
        """Обязательные проверки группы (для журнала)."""
        g = self.policy(group)
        return list(g.require) if g else []

    def set_required_result(self, node: str, test: str, ok: bool, detail: str = "") -> None:
        """Вердикт проверки, которую гоняет резерв (gemini, openai, anthropic, upstream)."""
        with self._lock:
            r = self.rows.get(node)
            if r is not None:
                r.setdefault("required", {})[test] = {"ok": "1" if ok else "0",
                                                      "ts": int(time.time()), "cc": detail or ""}
                self._persist(r)

    def set_ip_info(self, infos: dict) -> None:
        """Сведения о выходных IP (кэш ip_info): {ip: {country, mobile, proxy, hosting, …}}."""
        with self._lock:
            self._ip_info = dict(infos or {})

    def _exit_info(self, r: dict) -> "dict | None":
        return self._ip_info.get(r.get("exit_ip") or "")

    def _verdict(self, r: dict, check: str, params: dict) -> str:
        ttl, jitter = float(params.get("ttl") or 0), float(params.get("ttl_jitter") or 0)
        key = f"{r.get('id')}/{check}"

        def current(ts) -> bool:
            return fresh(ts, ttl, jitter, key)

        if check == "country":
            if not r.get("exit_cc") or not current(r.get("country_ts")):
                return ""
            return "1" if self._country_fits(r["exit_cc"].lower(), params) else "0"
        if check in ("ip_type", "geo_consistent"):
            info = self._exit_info(r)
            if not info or not r.get("exit_cc"):
                return ""
            if check == "geo_consistent":
                return "0" if db_country_mismatch(info, r["exit_cc"]) else "1"
            # Признак proxy ip-api ставит почти всем выходным IP нод: тип он не отменяет, а
            # "proxy" в kinds пропускает любой IP с этим признаком.
            kinds = params.get("kinds") or _DEFAULT_KINDS
            return "1" if kind(info) in kinds or ("proxy" in kinds and info.get("proxy")) else "0"
        if check == "heavy":
            return str(r.get("heavy_ok", "")) if current(r.get("heavy_ts")) else ""
        result = (r.get("required") or {}).get(check) or {}
        return str(result.get("ok", "")) if current(result.get("ts")) else ""

    def _country_fits(self, cc: str, params: dict) -> bool:
        regions = region_of(cc, self._regions)
        for key, have in (("countries", {cc}), ("regions", regions)):
            want = set(params.get(key) or ())
            if want and not want & have:
                return False
            if set(params.get("exclude_" + key) or ()) & have:
                return False
        return True

    def _checks(self, r: dict, group: str, part: str) -> dict[str, str]:
        g = self._policy.get(group)
        return {c: self._verdict(r, c, p) for c, p in (getattr(g, part) if g else {}).items()}

    def _passed(self, r: dict, group: str) -> bool:
        return all(v == "1" for v in self._checks(r, group, "require").values())

    def _failed(self, r: dict, group: str) -> bool:
        return any(v == "0" for v in self._checks(r, group, "require").values())

    def _tier(self, r: dict, group: str) -> int:
        """Ярус кандидата: сколько приоритетных проверок группы не пройдено. Рейтинг ниже
        tier_floor — ярус после всех остальных: слабая нода, прошедшая приоритетные проверки,
        не обходит сильные, которые их не прошли."""
        prefer = self._checks(r, group, "prefer")
        missed = sum(v != "1" for v in prefer.values())
        g = self._policy.get(group)
        floor = g.tier_floor if g is not None and g.tier_floor is not None else self.tier_floor
        if float(r.get("score") or 0) < floor:
            missed += len(prefer) + 1
        return missed

    def tier(self, node: str, group: str) -> int:
        with self._lock:
            r = self.rows.get(node)
            g = self._policy.get(group)
            return self._tier(r, group) if r is not None else len(g.prefer if g else ())

    def _check_ts(self, r: dict, check: str) -> "int | None":
        """Когда получен результат проверки (для возраста в админке)."""
        if check == "country":
            ts = r.get("country_ts")
        elif check in ("ip_type", "geo_consistent"):
            ts = (self._exit_info(r) or {}).get("ts")
        elif check == "heavy":
            ts = r.get("heavy_ts")
        else:
            ts = ((r.get("required") or {}).get(check) or {}).get("ts")
        return int(ts) if ts else None

    def check_report(self, node: str, group: str) -> list[dict]:
        """Проверки группы для ноды (админка): обязательные и желательные — вердикт
        "1"/"0"/"" и время результата."""
        with self._lock:
            r, g = self.rows.get(node), self._policy.get(group)
            if r is None or g is None:
                return []
            return [{"check": check, "part": part, "verdict": self._verdict(r, check, params),
                     "ts": self._check_ts(r, check)}
                    for part in ("require", "prefer") for check, params in getattr(g, part).items()]

    def fitting(self, group, providers) -> list[str]:
        """Ноды подписок providers, прошедшие обязательные проверки группы group (GroupConfig вне
        политики роутера — клиентская группа): рейтинг выше 0, без ограничений; лучшие первыми."""
        with self._lock:
            rows = [r for r in self.rows.values()
                    if r.get("provider") in providers and float(r.get("score") or 0) > 0
                    and r.get("id") not in self._restricted
                    and all(self._verdict(r, c, p) == "1" for c, p in group.require.items())]
            return [r["node"] for r in sorted(rows, key=lambda r: float(r["score"]), reverse=True)]

    def useless(self, node: str, client_groups) -> bool:
        """Нода не нужна ни одной группе: в каждой группе роутера, куда она входит, и в каждой
        клиентской группе её подписки (client_groups — [(GroupConfig, подписки)]) у неё свежий
        провал обязательной проверки, чаще всего страны выхода. Ни одной такой группы или о
        ноде ничего не известно — нужна."""
        with self._lock:
            r = self.rows.get(node)
            if r is None:
                return False
            failed = [self._failed(r, g) for g, tags in self._members.items()
                      if node in tags and g in self._policy]
            failed += [any(self._verdict(r, c, p) == "0" for c, p in g.require.items())
                       for g, providers in client_groups if r.get("provider") in providers]
            return bool(failed) and all(failed)

    def node_by_key(self, key: str) -> "str | None":
        """Тег ноды по ключу node_crc.cred_crc."""
        with self._lock:
            return next((n for n, r in self.rows.items() if r.get("id") == key), None)

    def fits(self, node: str, group: str) -> bool:
        """Нода проходит все обязательные проверки группы (свежие успехи)."""
        with self._lock:
            r = self.rows.get(node)
            return r is not None and self._passed(r, group)

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
        """Кандидаты группы: здоровые ноды со свежими успехами всех обязательных проверок.
        Порядок: сначала меньше непройденных приоритетных проверок (ярус), внутри яруса —
        по рейтингу."""
        with self._lock:
            chosen = [dict(r, tier=self._tier(r, region)) for r in self._healthy(region)
                      if self._passed(r, region)]
            chosen.sort(key=lambda c: c["tier"])           # внутри яруса — по рейтингу
            return chosen                                  # копии наружу (монитор-поток)

    # --- Резерв группы (nodes_tester.reserve) -----------------------------------

    def reserve_pool(self, group: str, min_score: float = 0.0) -> list[str]:
        """Претенденты в резерв: здоровые ноды группы без свежего провала обязательных
        проверок и heavy. Порядок: ярус, затем рейтинг не ниже min_score, затем рейтинг."""
        with self._lock:
            pool = [r for r in self.rows.values() if self._reserve_fit(r, group)]
            pool.sort(key=lambda r: (self._tier(r, group),
                                     float(r["score"]) < min_score, -float(r["score"])))
            return [r["node"] for r in pool]

    def reserve_missing(self, node: str, group: str) -> "list[str] | None":
        """Что осталось проверить, чтобы нода годилась в резерв группы: обязательные
        проверки без свежего результата, которые резерв гоняет сам, и heavy. None — нода
        не годится (нет в группе, рейтинг 0, ограничена, свежий провал, или неизвестна
        проверка из замеров тестера — страна, тип IP: её даст следующий замер)."""
        with self._lock:
            r = self.rows.get(node)
            if r is None or not self._reserve_fit(r, group):
                return None
            missing = []
            for check, verdict in self._checks(r, group, "require").items():
                if verdict == "":
                    if check not in RUNNABLE_CHECKS:
                        return None
                    missing.append(check)
            if "heavy" not in missing and self._heavy(r) != "1":
                missing.append("heavy")
            return missing

    def prefer_missing(self, node: str, group: str) -> list[str]:
        """Желательные проверки группы без свежего результата, которые гоняет резерв."""
        with self._lock:
            r = self.rows.get(node)
            if r is None:
                return []
            return [check for check, verdict in self._checks(r, group, "prefer").items()
                    if verdict == "" and check in RUNNABLE_CHECKS]

    def due_checks(self, node: str, group: str, ahead: float) -> list[str]:
        """Обязательные проверки группы, которые гоняет резерв, с результатом, истёкшим или
        истекающим в ближайшие ahead секунд (для активной ноды: резерв её не проверяет)."""
        with self._lock:
            r, g = self.rows.get(node), self._policy.get(group)
            if r is None or g is None:
                return []
            soon = time.time() + ahead
            due = []
            for check, params in g.require.items():
                if check not in RUNNABLE_CHECKS:
                    continue
                until = expires(self._check_ts(r, check), float(params.get("ttl") or 0),
                                float(params.get("ttl_jitter") or 0), f"{r.get('id')}/{check}")
                if until <= soon:
                    due.append(check)
            return due

    def may_fit(self, node: str, group: str) -> bool:
        """Может ли ограниченная нода пройти обязательные проверки группы: известная страна
        выхода подходит (даже давний замер страны лучше, чем снять заведомо чужую ноду),
        свежих провалов нет. О ноде ничего не известно — может."""
        with self._lock:
            r = self.rows.get(node)
            if r is None:
                return True
            g = self._policy.get(group)
            country = g.require.get("country") if g is not None else None
            cc = (r.get("exit_cc") or "").lower()
            if country is not None and cc and not self._country_fits(cc, country):
                return False
            return not self._failed(r, group)

    def _reserve_fit(self, r: dict, group: str) -> bool:
        return (self._in_group(r, group) and float(r.get("score", 0)) > 0
                and r.get("id") not in self._restricted and not self._vetoed(r)
                and not self._failed(r, group))

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
        """Полный набор ограниченных нод."""
        with self._lock:
            self._restricted = {c for c in crcs if c}

    def forget_history(self, crc: str) -> None:
        """Ограничение снято (вручную, резервом, истёк карантин): следующий замер ноды
        начинает сглаженный рейтинг (EWMA, доступность, флаппинг) с себя. История замеров и
        аварийность в боевой работе остаются."""
        with self._lock:
            self._fresh.add(crc)

    def restrict(self, crc: str, on: bool) -> None:
        """Ограничение одной ноды поставлено/снято (карантин, бан, снятие)."""
        if not crc:
            return
        with self._lock:
            if on:
                self._restricted.add(crc)
            else:
                self._restricted.discard(crc)

    def dead_keys(self) -> set[str]:
        """Ключи измеренных нод с нулевым рейтингом или под паузой, карантином, баном.
        Ноды без замеров сюда не входят: о них ещё ничего не известно."""
        with self._lock:
            return {r["id"] for r in self.rows.values()
                    if r.get("id") and (float(r.get("score", 0)) <= 0 or r["id"] in self._restricted)}

    def is_restricted(self, node: str) -> bool:
        with self._lock:
            r = self.rows.get(node)
            return bool(r and r.get("id") in self._restricted)

    def regions(self) -> list[str]:
        """Группы переключения в порядке sing-box."""
        with self._lock:
            return list(self._members)

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
