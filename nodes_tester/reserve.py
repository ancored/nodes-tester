"""Резерв групп: фоновый процесс держит в каждой группе переключения проверенных
кандидатов на замену активной ноды.

Проход тестера только рейтингует ноды (лёгкие тесты). Резерв добирается по рейтингу
независимо от проходов: нода проходит обязательные тесты группы (gemini, openai,
anthropic — свежий результат не повторяется), затем тяжёлый download (тоже, если не
свежий), и встаёт в резерв. Ротация, авария и переключение по качеству берут замену
только из резерва (Switcher), после смены активной резерв сразу добирается.

У членов резерва следим за сроками: истёк срок теста — тест повторяется. Кандидаты по
рейтингу кончились — снимаем ограничение с ноды группы, которая под ним дольше всех,
и проверяем её (Runner.release_for_reserve). Не набрали резерв и снимать некого —
следующая попытка добора через _RETRY_SHORT или после нового прохода.
"""

from __future__ import annotations

import threading
import time

from .api import ApiError

# Как часто просыпаться без событий: проверить сроки тестов у членов резерва.
_TICK = 60.0
# Резерв не набран, а кандидаты и карантин исчерпаны — следующая попытка не раньше.
_RETRY_SHORT = 900.0


class ReserveKeeper:
    def __init__(self, runner):
        self.runner = runner
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self._retry_at: dict[str, float] = {}     # группа → не добирать раньше (monotonic)

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
        """Разбудить добор: сменилась активная, новый состав нод или рейтинг (тогда
        отложенные из-за нехватки кандидатов группы пробуются сразу)."""
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
        if runner.board is None or runner.switcher is None or not runner._node_by_raw:
            return                            # состав нод ещё неизвестен (до первого прохода)
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
        if members and not runner.switcher.active_node(group):
            runner.switcher.evaluate_region(group)   # init или выход из failsafe

    def _fill(self, group: str, members: list, size: int, excluded: set) -> None:
        board = self.runner.board
        min_score = float(self.runner.cfg.switching.rotation.min_score)
        for node in board.reserve_pool(group, min_score):
            if len(members) >= size:
                return
            if node not in members and node not in excluded and self._verified(node, group):
                members.append(node)
                board.set_reserve(group, members)
        released: set = set()
        while len(members) < size:
            node = self.runner.release_for_reserve(group, released)
            if node is None:
                break
            released.add(node)
            if node not in excluded and self._verified(node, group):
                members.append(node)
                board.set_reserve(group, members)
        if len(members) < size:
            self._retry_at[group] = time.monotonic() + _RETRY_SHORT
            print(f"  [reserve] {group}: в резерве {len(members)} из {size}, кандидатов больше "
                  f"нет — повтор через {_RETRY_SHORT / 60:.0f} мин или после прохода")

    def _verified(self, node: str, group: str) -> bool:
        """Догнать недостающие проверки ноды (обязательные тесты, затем heavy) и сказать,
        годится ли она в резерв. Провал выбивает её сразу, без остальных тестов."""
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
        return missing is not None
