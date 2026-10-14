"""Резерв групп: фоновый процесс держит в каждой группе переключения проверенных
кандидатов на замену активной ноды.

Замеры тестера только рейтингуют ноды (лёгкие тесты). Резерв добирается по рейтингу
независимо от них: нода проходит обязательные тесты группы (gemini, openai,
anthropic — свежий результат не повторяется), затем тяжёлый download (тоже, если не
свежий), и встаёт в резерв. Ротация, авария и переключение по качеству берут замену
только из резерва (Switcher), после смены активной резерв сразу добирается.

У членов резерва следим за сроками: истёк срок теста — тест повторяется. Кандидаты по
рейтингу кончились — снимаем ограничение с ноды группы, которая под ним дольше всех,
и проверяем её (Runner.release_for_reserve). Не набрали резерв и снимать некого —
следующая попытка добора через _RETRY_SHORT или при появлении новых нод. Набранный резерв
улучшается: после каждого замера тестера нода заметно лучше худшего кандидата занимает его место.
"""

from __future__ import annotations

import threading
import time

from .api import ApiError
from .identity import parse_node

# Как часто просыпаться без событий: проверить сроки тестов у членов резерва.
_TICK = 60.0
# Резерв не набран, а кандидаты и карантин исчерпаны — следующая попытка не раньше.
_RETRY_SHORT = 900.0
# Обязательные проверки активной ноды повторяются за столько до истечения результата.
_ACTIVE_AHEAD = 5 * _TICK


class ReserveKeeper:
    def __init__(self, runner):
        self.runner = runner
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self._retry_at: dict[str, float] = {}     # группа → не добирать раньше (monotonic)
        self._tried: dict[str, float] = {}        # не прошедший претендент → не пробовать раньше

    # --- Поток ---------------------------------------------------------

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="reserve", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def wake(self, ratings_changed: bool = False) -> None:
        """Разбудить добор: сменилась активная или состав нод (ratings_changed — появились
        новые ноды: отложенные из-за нехватки кандидатов группы пробуются сразу)."""
        if ratings_changed:
            self._retry_at.clear()
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(_TICK)
            self._wake.clear()
            if self._stop.is_set():
                return
            try:
                self.maintain_all()
            except ApiError as exc:          # sing-box перезапускается — попробуем позже
                print(f"  [reserve] API sing-box недоступен: {exc}")
            except Exception as exc:  # noqa: BLE001 — сбой одного круга не останавливает резерв
                print(f"  [reserve] ошибка: {type(exc).__name__}: {exc}")

    # --- Добор ---------------------------------------------------------

    def maintain_all(self) -> None:
        runner = self.runner
        if not runner._node_by_raw:
            return                            # состав нод ещё неизвестен (до первой сверки)
        for group in runner.board.regions():
            if runner._apply_paused():
                return                        # конвейер перезапускает sing-box
            self.maintain(group)

    def maintain(self, group: str) -> None:
        runner, board = self.runner, self.runner.board
        size = runner.reserve_size(group)
        excluded = runner.switcher.excluded(group)
        members = [n for n in board.reserve(group)[:size]
                   if n not in excluded and self._verified(n, group)]
        board.set_reserve(group, members)
        if len(members) < size and time.monotonic() >= self._retry_at.get(group, 0.0):
            self._fill(group, members, size, excluded)
        elif members and len(members) >= size:
            self._displace(group, members, excluded)
        active = runner.switcher.active_node(group)
        if active:
            self._refresh_active(group, active)
        elif members:
            runner.switcher.evaluate_region(group)   # init или выход из failsafe

    def _refresh_active(self, group: str, active: str) -> None:
        """Повторить обязательные проверки активной ноды до истечения результата. Не прошла
        или результат истёк — оценка группы сразу заменит её нодой из резерва."""
        runner = self.runner
        due = runner.board.due_checks(active, group, _ACTIVE_AHEAD)
        if not due:
            return
        for check in due:
            if runner._apply_paused():
                return
            runner.check_node(active, check)
        if not runner.board.fits(active, group):
            runner.switcher.evaluate_region(group)

    def _fits(self, group: str, node: str, others: list, excluded: set) -> bool:
        """Нода годится в резерв рядом с others. Доступы одного сервера падают вместе: в
        резерве по одному на сервер, и не тот сервер, что у активной ноды."""
        servers = {parse_node(n).node_crc for n in others}
        active = self.runner.switcher.active_node(group)
        if active:
            servers.add(parse_node(active).node_crc)
        return (node not in excluded and parse_node(node).node_crc not in servers
                and self._verified(node, group))

    def _displace(self, group: str, members: list, excluded: set) -> None:
        """Резерв набран: нода вне резерва, которая лучше худшего члена (ярус выше или рейтинг
        больше на selection.reserve_margin), занимает его место. За раз — одна попытка:
        претендент сначала проходит проверки, провал оставляет резерв как был, и сам
        претендент не пробуется _RETRY_SHORT."""
        board = self.runner.board
        margin = float(self.runner.cfg.selection.reserve_margin)

        def score(node: str) -> float:
            return float((board.get(node) or {}).get("score") or 0)

        worst = max(members, key=lambda n: (board.tier(n, group), -score(n)))
        worst_tier = board.tier(worst, group)
        others = [n for n in members if n != worst]
        for node in board.reserve_pool(group):
            if (node in members or node in excluded
                    or time.monotonic() < self._tried.get(node, 0.0)):
                continue
            tier = board.tier(node, group)
            if tier > worst_tier or (tier == worst_tier and score(node) < score(worst) + margin):
                continue
            if self._fits(group, node, others, excluded):
                members[members.index(worst)] = node
                board.set_reserve(group, members)
                print(f"  [reserve] {group}: {parse_node(node).short()} вместо "
                      f"{parse_node(worst).short()} — лучше по рейтингу или ярусу")
            else:
                self._tried[node] = time.monotonic() + _RETRY_SHORT
            return

    def _fill(self, group: str, members: list, size: int, excluded: set) -> None:
        board = self.runner.board
        min_score = float(self.runner.cfg.selection.rotation.min_score)
        for node in board.reserve_pool(group, min_score):
            if len(members) >= size:
                return
            if self._fits(group, node, members, excluded):
                members.append(node)
                board.set_reserve(group, members)
        released: set = set()
        while len(members) < size:
            node = self.runner.release_for_reserve(group, released)
            if node is None:
                break
            released.add(node)
            if self._fits(group, node, members, excluded):
                members.append(node)
                board.set_reserve(group, members)
        if len(members) < size:
            self._retry_at[group] = time.monotonic() + _RETRY_SHORT
            print(f"  [reserve] {group}: в резерве {len(members)} из {size}, кандидатов больше "
                  f"нет — повтор через {_RETRY_SHORT / 60:.0f} мин или при новых нодах")

    def _verified(self, node: str, group: str) -> bool:
        """Догнать недостающие проверки ноды (обязательные тесты, затем heavy) и сказать,
        годится ли она в резерв. Провал выбивает её сразу, без остальных тестов. Годной
        ноде — и желательные проверки: их результат влияет только на ярус."""
        board = self.runner.board
        missing = board.reserve_missing(node, group)
        while missing:
            if self.runner._apply_paused():
                return False
            self.runner.check_node(node, missing[0])
            after = board.reserve_missing(node, group)
            if after is not None and after[:1] == missing[:1]:
                return False                  # тест не дал вердикта (сеть) — не сейчас
            missing = after
        if missing is None:
            return False
        for check in board.prefer_missing(node, group):
            if self.runner._apply_paused():
                break
            self.runner.check_node(node, check)
        return True
