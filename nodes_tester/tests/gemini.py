"""Gemini-тест: какой страной Google видит ноду и доступен ли ей Gemini.

Страница gemini.google.com содержит код страны, которым Google определил клиента, во
фрагменте `,2,1,200,"NLD"` (ISO alpha-3). Многие зарубежные ноды Google видит как RUS:
провайдер, база геолокации, выход через IPv6 Cloudflare WARP, который передаёт Google
реальное местоположение. Одна и та же нода может отвечать то NLD, то RUS — выход идёт то
по IPv4, то по IPv6, — поэтому запрос повторяется `attempts` раз, и нода проходит тест,
только если КАЖДАЯ попытка дала HTTP 200 и разрешённую страну. 403 (Google не пускает
клиента), обрыв и отсутствие кода страны — провал попытки.

Сетевой сбой (таймаут, обрыв) не говорит о стране. Если часть попыток дала разрешённую
страну, а остальные упали по сети, результат неопределённый (`inconclusive`): runner не
перезаписывает им прошлый вердикт. Все попытки упали по сети — провал: Google через ноду
недоступен.

Тест не входит в рейтинг: как heavy_download, он гоняется отдельной фазой для кандидатов
групп, где он обязателен (run.node_groups_specifics → required_tests), и решает, может ли
нода стать активной в такой группе.
"""

from __future__ import annotations

import re
from collections import Counter

from .base import BROWSER_HEADERS, BaseTest, TestContext, TestResult, register

DEFAULT_URL = "https://gemini.google.com/"
# Страны, где Gemini недоступен (ISO alpha-3). Переопределяется tests.gemini.forbidden_countries.
DEFAULT_FORBIDDEN = ["RUS", "BLR", "CHN", "HKG", "MAC", "IRN", "PRK", "CUB", "SYR"]
_COUNTRY_RE = re.compile(r',2,1,200,"([A-Z]{3})"')


def google_country(html: str) -> str | None:
    m = _COUNTRY_RE.search(html)
    return m.group(1) if m else None


@register
class GeminiTest(BaseTest):
    name = "gemini"
    default_url = DEFAULT_URL

    def run(self, ctx: TestContext) -> TestResult:
        url = self.url_for(ctx)
        attempts = max(1, int(self.options.get("attempts", 3)))
        forbidden = {c.upper() for c in self.options.get("forbidden_countries", DEFAULT_FORBIDDEN)}
        timeout = self.timeout(ctx)
        countries, codes, errors = [], [], []
        for _ in range(attempts):
            try:
                resp = ctx.session.get(url, headers=BROWSER_HEADERS, timeout=timeout)
                codes.append(resp.status_code)
                cc = google_country(resp.text) if resp.status_code == 200 else None
                countries.append(cc)
            except Exception as exc:  # noqa: BLE001 — любая сетевая ошибка = провал попытки
                codes.append(0)
                countries.append(None)
                errors.append(type(exc).__name__)
        seen = [c for c in countries if c]
        metrics = {"attempts": attempts, "http": codes, "countries": countries,
                   "country": Counter(seen).most_common(1)[0][0] if seen else None}
        bad = sorted({c for c in seen if c in forbidden})
        if bad:
            return TestResult(self.name, False, metrics,
                              error=f"Google видит страну {', '.join(bad)}", url=url)
        net_failed = codes.count(0)
        if net_failed and seen and len(seen) == attempts - net_failed:
            metrics["inconclusive"] = True
            return TestResult(self.name, False, metrics,
                              error=f"сеть: {net_failed}/{attempts} попыток без ответа "
                                    f"({', '.join(sorted(set(errors)))})", url=url)
        if any(code != 200 for code in codes):
            why = "403 — Google не пускает" if 403 in codes else \
                  f"HTTP {', '.join(str(c) for c in codes)}" + (f" ({errors[0]})" if errors else "")
            return TestResult(self.name, False, metrics, error=why, url=url)
        if len(seen) != attempts:
            return TestResult(self.name, False, metrics, error="страна не определена", url=url)
        return TestResult(self.name, True, metrics, url=url)
