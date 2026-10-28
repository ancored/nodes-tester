"""google_country: какой страной Google видит выход ноды.

Сервер вариаций Chrome отдаёт в заголовке `X-Country` страну, которой Google определил
клиента; по ней Chrome включает свои функции (например, Gemini в Chrome). Эта страна не
всегда совпадает ни со страной по Cloudflare (`connectivity`), ни со страной на странице
Gemini (тест `gemini`): у одной ноды бывает PL, GBR и RU сразу. Запрос HEAD — без тела.

На рейтинг тест не влияет; группа может требовать или предпочитать ноды со страной вне
списка (проверка `google_country`, вердикт считает scoreboard). ok — страна получена.
"""

from __future__ import annotations

from naming.regions import alpha3_to_alpha2

from .base import BROWSER_HEADERS, BaseTest, TestContext, TestResult, register
from .gemini import DEFAULT_FORBIDDEN

DEFAULT_URL = "https://clientservices.googleapis.com/chrome-variations/seed"
# Страны, где Google AI недоступен (alpha-2, нижний регистр): по умолчанию для проверки группы.
GOOGLE_AI_FORBIDDEN = frozenset(alpha3_to_alpha2(c).lower() for c in DEFAULT_FORBIDDEN)


@register
class GoogleCountryTest(BaseTest):
    name = "google_country"
    default_url = DEFAULT_URL

    def run(self, ctx: TestContext) -> TestResult:
        url = self.url_for(ctx)
        try:
            resp = ctx.session.head(url, headers=BROWSER_HEADERS, timeout=self.timeout(ctx),
                                    allow_redirects=False)
        except Exception as exc:  # noqa: BLE001 — любая сетевая ошибка = провал замера
            return TestResult(self.name, False, error=f"{type(exc).__name__}: {exc}", url=url)
        if resp.status_code != 200:
            return TestResult(self.name, False, {"http": resp.status_code},
                              error=f"HTTP {resp.status_code}", url=url)
        country = (resp.headers.get("X-Country") or "").strip().upper()
        if not country:
            return TestResult(self.name, False, {"http": resp.status_code},
                              error="нет заголовка X-Country", url=url)
        return TestResult(self.name, True, {"country": country}, url=url)
