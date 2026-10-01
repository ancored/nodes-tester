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

DEFAULT_URL = "https://speed.cloudflare.com/__down?bytes=5000000"


@register
class DownloadTest(BaseTest):
    name = "download"
    default_url = DEFAULT_URL

    def run(self, ctx: TestContext) -> TestResult:
        url = self.url_for(ctx)
        duration = max(1.0, float(self.options.get("duration", 20)))       # >0 (валидация)
        window_bytes = max(1, int(self.options.get("window_bytes", 2_000_000)))
        connect_timeout = max(1.0, float(self.options.get("connect_timeout", 10)))

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
            # Сервер занят (429/5xx) — туннель жив, это НЕ FAIL ноды.
            metrics["limited"] = True
            return TestResult(self.name, ok=True, metrics=metrics, url=url)
        if r.get("http_error"):
            # Клиентская ошибка (401/403/404): сервер ответил, но замер не состоялся —
            # это проблема url/блока/auth, НЕ обрыв транспорта ноды. Отдельный исход:
            # ok=false, но скоринг не штрафует пропускную (см. scoring: http_error).
            metrics["http_error"] = True
            metrics["http_status"] = r.get("http_status")
            return TestResult(self.name, ok=False, metrics=metrics,
                              error=f"HTTP {r.get('http_status')}", url=url)
        if r["broken"]:
            # Реальный обрыв транспорта — нестабильность.
            return TestResult(self.name, ok=False, metrics=metrics,
                              error=r["error"], url=url)
        # Упёрлись в duration, но поток шёл без обрыва — sustained-успех (медленную, но
        # СТАБИЛЬНУЮ ноду не заваливаем неполной закачкой). Иначе — докачали ли файл.
        if r["capped"]:
            metrics["duration_reached"] = True
            ok = total > 0
        else:
            ok = (total >= expected * 0.95) if expected else (total > 0)
        return TestResult(self.name, ok=ok, metrics=metrics,
                          error=None if ok else "неполная закачка", url=url)


HEAVY_URL = "https://speed.cloudflare.com/__down?bytes=20000000"   # 20 МБ


@register
class HeavyDownloadTest(DownloadTest):
    """Тяжёлый sustained-download (20 МБ) — ТОЛЬКО финальный veto-фильтр кандидатов
    в двухуровневом тестировании. В скоринг НЕ входит: гоняется отдельной фазой
    прогона по heavy_candidates нод/регион, результат — pass/fail. Логика замера
    та же, что у лёгкого download; отличается объёмом (url) и назначением.

    url/длительность настраиваются в config.tests.heavy_download (url_by_region —
    напр. фикс-файл для RU-зоны вместо cloudflare)."""
    name = "heavy_download"
    default_url = HEAVY_URL
