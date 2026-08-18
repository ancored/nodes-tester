"""Stability: за ОДНУ длительную закачку снимаем две метрики устойчивости под РФ:

  - survive_seconds — сколько секунд поток продержался (RST-resistance ТСПУ);
    реальный обрыв раньше времени = нода режется.
  - throttle_ratio  — скорость последнего окна / первого (замедление ТСПУ).

Объединяет прежние long_hold и throttle: один поток, два показателя — не гоняем
трафик дважды. Тяжёлый тест, поэтому обычно запускается раз в N прогонов
(опция `every` в конфиге).
"""

from __future__ import annotations

from ._stream import stream_for
from .base import BaseTest, TestContext, TestResult, register

DEFAULT_URL = "https://speed.cloudflare.com/__down?bytes=10000000"


@register
class StabilityTest(BaseTest):
    name = "stability"
    default_url = DEFAULT_URL

    def run(self, ctx: TestContext) -> TestResult:
        url = self.url_for(ctx)
        duration = float(self.options.get("duration", 20))
        window = float(self.options.get("window", 5))
        connect_timeout = float(self.options.get("connect_timeout", 10))

        samples, total, error = stream_for(ctx.session, url, duration, connect_timeout)
        t_end = samples[-1][0]
        survive = min(t_end, duration)
        # ok = продержались всю длительность без обрыва.
        held = error is None and total > 0 and survive >= duration * 0.95

        metrics: dict = {
            "survive_seconds": round(survive, 1),
            "target": duration,
            "bytes": total,
            "seconds": round(t_end, 1),
        }
        # throttle-ratio считаем, если хватает данных (даже при обрыве — покажет просадку).
        if total > 0 and t_end >= window * 1.5:
            first_bps = (_bytes_at(samples, window) - _bytes_at(samples, 0)) / window
            last_bps = (_bytes_at(samples, t_end) - _bytes_at(samples, t_end - window)) / window
            if first_bps > 0:
                metrics["throttle_ratio"] = round(last_bps / first_bps, 2)
                metrics["first_mbps"] = round(first_bps * 8 / 1_000_000, 2)
                metrics["last_mbps"] = round(last_bps * 8 / 1_000_000, 2)

        return TestResult(self.name, ok=held, metrics=metrics, error=error, url=url)


def _bytes_at(samples: list[tuple[float, int]], tq: float) -> float:
    """Линейно интерполировать накопленные байты в момент tq."""
    prev = samples[0]
    for t, b in samples:
        if t >= tq:
            if t == prev[0]:
                return float(b)
            frac = (tq - prev[0]) / (t - prev[0])
            return prev[1] + (b - prev[1]) * frac
        prev = (t, b)
    return float(samples[-1][1])
