"""Минимальный клиент Clash API (совместим с sing-box experimental.clash_api)."""

from __future__ import annotations

from urllib.parse import quote

import requests

from .config import ClashApiConfig


class ClashApiError(RuntimeError):
    pass


class ClashApiClient:
    def __init__(self, cfg: ClashApiConfig):
        self.base_url = cfg.base_url.rstrip("/")
        self.timeout = cfg.timeout
        self._session = requests.Session()
        if cfg.secret:
            self._session.headers["Authorization"] = f"Bearer {cfg.secret}"
        # Трафик к самому API идёт напрямую, не через ноду.
        self._session.trust_env = False

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def ping(self) -> None:
        """Проверить доступность API (иначе — понятная ошибка)."""
        try:
            resp = self._session.get(self._url("/version"), timeout=self.timeout)
        except requests.RequestException as exc:
            raise ClashApiError(
                f"Не удалось подключиться к Clash API ({self.base_url}): {exc}"
            ) from exc
        if resp.status_code == 401:
            raise ClashApiError("Clash API вернул 401 — неверный secret")
        resp.raise_for_status()

    def _get(self, path: str):
        """GET с конвертацией сетевых ошибок в ClashApiError.

        Важно, когда sing-box перезапускается (регенерация конфига): API
        временно недоступен → ConnectionError. Возвращаем ClashApiError,
        чтобы главный цикл пережил перезапуск, а не упал.
        """
        try:
            return self._session.get(self._url(path), timeout=self.timeout)
        except requests.RequestException as exc:
            raise ClashApiError(
                f"Clash API недоступен ({self.base_url}): {exc}"
            ) from exc

    def _put(self, path: str, payload: dict):
        try:
            return self._session.put(self._url(path), json=payload, timeout=self.timeout)
        except requests.RequestException as exc:
            raise ClashApiError(
                f"Clash API недоступен ({self.base_url}): {exc}"
            ) from exc

    def get_proxy(self, name: str) -> dict:
        """GET /proxies/{name} — инфо о ноде/группе (all, now, type ...)."""
        resp = self._get(f"/proxies/{quote(name, safe='')}")
        if resp.status_code == 404:
            raise ClashApiError(f"Прокси/группа '{name}' не найдена в Clash API")
        resp.raise_for_status()
        return resp.json()

    def list_group_members(self, group: str) -> list[str]:
        """Члены selector-группы (поле 'all')."""
        data = self.get_proxy(group)
        gtype = str(data.get("type", "")).lower()
        if gtype not in ("selector", "urltest", "loadbalance", "fallback"):
            raise ClashApiError(
                f"'{group}' имеет тип '{data.get('type')}', это не группа-селектор"
            )
        return list(data.get("all", []))

    def current_selection(self, group: str) -> str:
        """Текущая выбранная нода в группе (поле 'now')."""
        return str(self.get_proxy(group).get("now", ""))

    def select(self, group: str, node: str) -> None:
        """PUT /proxies/{group} {name: node} — переключить selector на node."""
        resp = self._put(f"/proxies/{quote(group, safe='')}", {"name": node})
        if resp.status_code == 404:
            raise ClashApiError(f"Группа '{group}' не найдена")
        if resp.status_code >= 400:
            raise ClashApiError(
                f"Не удалось выбрать '{node}' в группе '{group}': "
                f"HTTP {resp.status_code} {resp.text}"
            )

    def all_proxies(self) -> dict:
        """GET /proxies — все прокси/группы (name -> {type, all, now, ...})."""
        resp = self._get("/proxies")
        resp.raise_for_status()
        return resp.json().get("proxies", {})

    def connections(self) -> list[dict]:
        """GET /connections — активные соединения (байты, метадата, chains)."""
        resp = self._get("/connections")
        resp.raise_for_status()
        return resp.json().get("connections") or []

    def delay(self, node: str, url: str, timeout_ms: int) -> "int | None":
        """GET /proxies/{node}/delay — sing-box сам дозванивается до ноды.

        Возвращает задержку в мс (нода жива) или None (таймаут/недоступна).
        При недоступности самого API бросает ClashApiError (это НЕ смерть ноды).
        """
        q = f"url={quote(url, safe='')}&timeout={int(timeout_ms)}"
        resp = self._get(f"/proxies/{quote(node, safe='')}/delay?{q}")
        if resp.status_code != 200:
            return None
        try:
            return resp.json().get("delay")
        except ValueError:
            return None

    def selector_groups_with_member(
        self, node: str, exclude: set[str] | None = None
    ) -> list[str]:
        """Теги selector-групп, где node — прямой член (кроме exclude)."""
        exclude = exclude or set()
        groups = []
        for tag, info in self.all_proxies().items():
            if tag in exclude:
                continue
            if str(info.get("type", "")).lower() != "selector":
                continue
            if node in (info.get("all") or []):
                groups.append(tag)
        return groups
