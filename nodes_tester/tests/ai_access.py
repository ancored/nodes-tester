"""Доступность OpenAI и Claude через ноду: решение самого сервиса, а не база геолокации.

Страну оба сервиса берут у Cloudflare — её уже показывает connectivity, поэтому тесты
проверяют только вердикт сервиса:
  openai    — api.openai.com/compliance/cookie_requirements: в запрещённом регионе JSON
              с ошибкой `unsupported_country…`, в разрешённом — `cookie_consent_required`;
  anthropic — api.anthropic.com/v1/models без ключа: в запрещённом регионе 403 с ошибкой
              `forbidden`, в разрешённом — 401 `authentication_error`. claude.ai не годится:
              IP дата-центров там получают проверку Cloudflare (403, `cf-mitigated: challenge`),
              которую браузер проходит, а тестер — нет.

Как и gemini, запрос повторяется `attempts` раз (выход ноды может чередовать IPv4 и
IPv6): нода проходит, только если КАЖДАЯ попытка дала «доступен». Нераспознанный ответ
(страница проверки Cloudflare, 403, 5xx) — провал. Часть попыток упала по сети, остальные
«доступен» — неопределённо (`inconclusive`), runner сохраняет прошлый вердикт.

Тесты обязательные (run.node_groups_specifics → required_tests), в рейтинг не входят.
"""

from __future__ import annotations

from .base import BROWSER_HEADERS, BaseTest, TestContext, TestResult, register

ALLOWED, BLOCKED = "allowed", "blocked"


class _AccessTest(BaseTest):
    """Попытки и итоговый вердикт; наследник реализует probe() → (код, вердикт|None)."""

    def probe(self, session, url: str, timeout: float) -> tuple[int, "str | None"]:
        raise NotImplementedError

    def run(self, ctx: TestContext) -> TestResult:
        url = self.url_for(ctx)
        attempts = max(1, int(self.options.get("attempts", 3)))
        timeout = self.timeout(ctx)
        codes, verdicts, errors = [], [], []
        for _ in range(attempts):
            try:
                code, verdict = self.probe(ctx.session, url, timeout)
            except Exception as exc:  # noqa: BLE001 — любая сетевая ошибка = провал попытки
                code, verdict = 0, None
                errors.append(type(exc).__name__)
            codes.append(code)
            verdicts.append(verdict)
        metrics = {"attempts": attempts, "http": codes, "verdicts": verdicts}
        if BLOCKED in verdicts:
            return TestResult(self.name, False, metrics, error="сервис недоступен в регионе", url=url)
        net_failed = codes.count(0)
        allowed = verdicts.count(ALLOWED)
        if net_failed and allowed and allowed == attempts - net_failed:
            metrics["inconclusive"] = True
            return TestResult(self.name, False, metrics,
                              error=f"сеть: {net_failed}/{attempts} попыток без ответа "
                                    f"({', '.join(sorted(set(errors)))})", url=url)
        if allowed != attempts:
            seen = [str(c) for c in codes if c]
            if seen:
                why = f"ответ не распознан (HTTP {', '.join(seen)})"
            else:
                why = f"нет ответа ({', '.join(sorted(set(errors)))})"
            return TestResult(self.name, False, metrics, error=why, url=url)
        return TestResult(self.name, True, metrics, url=url)


def _json(resp) -> dict:
    """JSON-объект ответа; не JSON или не объект — {}."""
    try:
        data = resp.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _error_field(data: dict, key: str) -> str:
    """Поле ошибки API ({"error": {key: …}}), иначе ""."""
    err = data.get("error")
    return str(err.get(key) or "") if isinstance(err, dict) else ""


@register
class OpenAITest(_AccessTest):
    name = "openai"
    default_url = "https://api.openai.com/compliance/cookie_requirements"

    def probe(self, session, url, timeout):
        resp = session.get(url, headers=BROWSER_HEADERS, timeout=timeout)
        data = _json(resp)
        if _error_field(data, "code").startswith("unsupported_country"):
            return resp.status_code, BLOCKED
        if "cookie_consent_required" in data:
            return resp.status_code, ALLOWED
        return resp.status_code, None


@register
class AnthropicTest(_AccessTest):
    name = "anthropic"
    default_url = "https://api.anthropic.com/v1/models"

    def probe(self, session, url, timeout):
        resp = session.get(url, headers=BROWSER_HEADERS, timeout=timeout)
        kind = _error_field(_json(resp), "type")
        if kind == "forbidden":
            return resp.status_code, BLOCKED
        if kind == "authentication_error":
            return resp.status_code, ALLOWED
        return resp.status_code, None
