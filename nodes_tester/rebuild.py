"""Пересборка конфига роутера по деградации (pipeline.router.rebuild).

Перезапуск sing-box рвёт соединения пользователей, поэтому тестер просит конвейер собрать
конфиг заново только когда выполнены оба условия:

1. конфиг деградировал: группа без кандидатов (на failsafe-out, block-out или direct-out)
   дольше `degraded_for`, или кандидатов меньше резерва + 1 дольше `few_candidates_for`;
2. свежий опрос подписок это исправит: в raw есть доступы, которых нет в конфиге, а среди
   мёртвых нод конфига не меньше `gone_share` уже ушли из подписок.

Не чаще `min_interval` после прошлой сборки роутера (плановой, ручной или этой), не во время
другого прогона конвейера и не в окне применения (монитор на паузе).
"""

from __future__ import annotations

import json
import os
import time

from naming import parse_node
from nodes_common import raw as rawfmt


def _config_keys(nodes_file: str) -> set[str]:
    """Ключи нод действующего nodes.json."""
    with open(nodes_file, encoding="utf-8") as fh:
        config = json.load(fh)
    return {key for ob in config.get("outbounds", []) + config.get("endpoints", [])
            if (key := parse_node(str(ob.get("tag", ""))).node_id)}


def _raw_keys(raw_file: str, cfg) -> set[str]:
    """Ключи нод, которые сборка дала бы из последнего опроса (те же фильтры)."""
    from nodes_config import build
    fragment, _ = build.build([rawfmt.load(raw_file)], cfg, "router", log=lambda m: None)
    return {parse_node(tag).node_id for tag in build.tags(fragment)} - {""}


class RebuildWatch:
    def __init__(self, cfg, board, switcher, orchestrator, notifier, clock=time.time):
        self.cfg = cfg                    # Config целиком: сборке нужны подписки и группы
        self.board = board
        self.switcher = switcher
        self.orchestrator = orchestrator  # () -> Orchestrator | None (создаётся позже)
        self.notifier = notifier
        self.clock = clock
        self._since: dict[tuple[str, str], float] = {}   # (группа, признак) → с какого времени
        self._gain_cache: tuple = (None, None)           # (mtime raw и nodes.json, результат)

    def check(self) -> str:
        """Вызывается тиком монитора. Возвращает причину запущенной пересборки или ""."""
        rules = self.cfg.pipeline.router.rebuild
        now = self.clock()
        degraded = self._degraded(now, rules)
        orch = self.orchestrator()
        if not degraded or orch is None or not self.cfg.pipeline.router.enabled:
            return ""
        last = orch.last_started(("router",))
        if last is not None and now - last < rules["min_interval"]:
            return ""
        gain = self._gain(orch, rules)
        if not gain:
            return ""
        reason = f"{degraded}; {gain}"
        from nodes_admin.orchestrator import BusyError
        try:
            orch.start_run("router", False, trigger="rebuild")
        except BusyError:
            return ""
        print(f"  [rebuild] пересборка конфига роутера: {reason}")
        self.notifier.send("pipeline", f"Пересборка конфига роутера: {reason}.", key="rebuild")
        self._since.clear()
        return reason

    def _degraded(self, now: float, rules: dict) -> str:
        """Описание деградации, которая длится дольше порога, или ""."""
        found = set()
        for group in self.board.regions():
            st = self.switcher.state.get(group) or {}
            if st.get("failsafe") and not st.get("active"):
                found.add((group, "fallback"))
            else:
                policy = self.cfg.group(group)
                reserve = policy.reserve if policy else self.cfg.selection.reserve
                if len(self.board.candidates(group)) < reserve + 1:
                    found.add((group, "few"))
        for key in list(self._since):
            if key not in found:
                del self._since[key]
        for key in found:
            self._since.setdefault(key, now)
        limits = {"fallback": rules["degraded_for"], "few": rules["few_candidates_for"]}
        lasting = sorted(key for key in found if now - self._since[key] >= limits[key[1]])
        return ", ".join(f"группа {g} {'без кандидатов' if kind == 'fallback' else 'с малым числом кандидатов'}"
                         for g, kind in lasting)

    def _gain(self, orch, rules: dict) -> str:
        """Что даст сборка из последнего опроса, если это исправляет деградацию, иначе ""."""
        raw_file = os.path.join(str(orch.data_dir), "raw", "router.json")
        try:
            stamp = (os.path.getmtime(raw_file), os.path.getmtime(self.cfg.nodes_file))
        except OSError:
            return ""
        if self._gain_cache[0] == stamp:
            return self._gain_cache[1]
        try:
            fresh, current = _raw_keys(raw_file, self.cfg), _config_keys(self.cfg.nodes_file)
        except (OSError, ValueError) as exc:
            print(f"  [rebuild] сравнение с опросом не удалось: {exc}")
            return ""
        added, gone = fresh - current, current - fresh
        dead = current & self.board.dead_keys()
        share = len(gone & dead) / len(dead) if dead else 0.0
        result = ""
        if added and share >= rules["gone_share"]:
            result = (f"новых доступов в подписках: {len(added)}, ушли из подписок {len(gone & dead)} "
                      f"из {len(dead)} мёртвых нод конфига")
        self._gain_cache = (stamp, result)
        return result
