"""Единый транспорт-тест: одна закачка файла даёт скорость, троттлинг и устойчивость.

Раньше было два теста (download + stability), качавших по 10 МБ одно и то же. Слито
в один — все метрики снимаем с ОДНОЙ закачки:
  - speed_mbps     — общая скорость (весь объём / время);
  - throttle_ratio — скорость последнего байтового окна / первого (замедление ТСПУ,
    сравнение внутри одной закачки — не зависит от абс. скорости ноды);
  - hold_ratio     — доля скачанного от Content-Length (обрыв → < 1);
  - обрыв транспорта (RST/timeout) = срез → ok=False;
  - отказ сервера (429/5xx) = НЕ вина ноды → ok=True, метка `limited`.

Один GET файла — работает и с cloudflare `__down?bytes=N`, и с фикс-файлами
(speedtest.selectel.ru/10MB): различие только в url (`url_by_region`). Один запрос
→ 429 не провоцируется.
"""

from __future__ import annotations

from ._stream import stream_single, time_at_bytes
from .base import BaseTest, TestContext, TestResult, register

DEFAULT_URL = "https://speed.cloudflare.com/__down?bytes=10000000"


@register
class DownloadTest(BaseTest):
    name = "download"
    default_url = DEFAULT_URL

    def run(self, ctx: TestContext) -> TestResult:
        url = self.url_for(ctx)
        duration = float(self.options.get("duration", 20))
        window_bytes = int(self.options.get("window_bytes", 2_000_000))
        connect_timeout = float(self.options.get("connect_timeout", 10))

        r = stream_single(ctx.session, url, duration, connect_timeout)
        total, expected, samples, secs = r["total"], r["expected"], r["samples"], r["seconds"]

        metrics: dict = {"downloaded": total, "seconds": secs}
        if secs > 0 and total > 0:
            metrics["speed_mbps"] = round(total * 8 / secs / 1_000_000, 2)
        if expected:
            metrics["expected"] = expected
            metrics["hold_ratio"] = round(min(1.0, total / expected), 3)

        # throttle_ratio: скорость последнего окна window_bytes / первого (по байтам).
        if total >= 2 * window_bytes:
            t_first = time_at_bytes(samples, window_bytes) - time_at_bytes(samples, 0)
            t_last = time_at_bytes(samples, total) - time_at_bytes(samples, total - window_bytes)
            if t_first > 0 and t_last > 0:
                first_bps, last_bps = window_bytes / t_first, window_bytes / t_last
                metrics["throttle_ratio"] = round(last_bps / first_bps, 2)
                metrics["first_mbps"] = round(first_bps * 8 / 1_000_000, 2)
                metrics["last_mbps"] = round(last_bps * 8 / 1_000_000, 2)

        if r["server_limit"]:
            # Сервер отбил запрос (429/5xx) — туннель жив, это НЕ FAIL ноды.
            metrics["limited"] = True
            return TestResult(self.name, ok=True, metrics=metrics, url=url)
        if r["broken"]:
            # Реальный обрыв транспорта — нестабильность.
            return TestResult(self.name, ok=False, metrics=metrics,
                              error=r["error"], url=url)
        # Завершилось без обрыва: ok если скачали (почти) весь файл.
        ok = (total >= expected * 0.95) if expected else (not r["capped"] and total > 0)
        return TestResult(self.name, ok=ok, metrics=metrics,
                          error=None if ok else "неполная закачка", url=url)


HEAVY_URL = "https://speed.cloudflare.com/__down?bytes=50000000"   # 50 МБ


@register
class HeavyDownloadTest(DownloadTest):
    """Тяжёлый sustained-download (50 МБ) — ТОЛЬКО финальный veto-фильтр кандидатов
    в двухуровневом тестировании. В скоринг НЕ входит: гоняется отдельной фазой
    прогона по heavy_candidates нод/регион, результат — pass/fail. Логика замера
    та же, что у лёгкого download; отличается объёмом (url) и назначением.

    url/длительность настраиваются в config.tests.heavy_download (url_by_region —
    напр. фикс-файл 50МБ для RU-зоны вместо cloudflare)."""
    name = "heavy_download"
    default_url = HEAVY_URL
