"""HTTP-загрузка подписок."""

import requests

# Браузерный User-Agent по умолчанию: часть панелей отдаёт подписку только «браузеру».
DEFAULT_UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 '
              '(KHTML, like Gecko) Version/16.5 Safari/605.1.15')
# connect/read, сек — роутер тоннелирует DNS/connect, нужен запас
DEFAULT_TIMEOUT = (30, 120)


def http_get(url, user_agent=None, timeout=DEFAULT_TIMEOUT, proxies=None, headers=None):
    """GET подписки. Ответ 200 → requests.Response, иначе (ошибка/не-200) → None.
    headers — дополнительные заголовки (устройство для панелей с лимитом устройств)."""
    headers = {**(headers or {}), 'User-Agent': user_agent or DEFAULT_UA}
    try:
        response = requests.get(url, headers=headers, timeout=timeout, proxies=proxies)
    except requests.RequestException:
        return None
    return response if response.status_code == 200 else None
