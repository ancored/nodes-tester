"""Операции тестера над работающим sing-box через его API-сервис (sing-box ≥ 1.14).

Транспорт и кодек — `nodes_common.box_api`. Здесь — то, что нужно тестеру: члены и выбор
selector-групп, снимок всех outbounds и поток соединений.
"""

from __future__ import annotations

from nodes_common.box_api import BoxApi, BoxApiError

from .config import BoxApiConfig

ApiError = BoxApiError

_GROUP_TYPES = ("selector", "urltest", "loadbalance", "fallback")


class ApiClient:
    def __init__(self, cfg: BoxApiConfig):
        self._api = BoxApi(cfg.url, cfg.secret, cfg.timeout)
        self.base_url = self._api.url

    def ping(self) -> None:
        """Проверить доступность API (иначе — понятная ошибка)."""
        try:
            self._api.version()
        except ApiError as exc:
            raise ApiError(f"API-сервис sing-box ({self.base_url}): {exc}") from exc

    def _group(self, name: str) -> dict:
        for group in self._api.groups():
            if group["tag"] == name:
                return group
        raise ApiError(f"Группа '{name}' не найдена в API sing-box")

    def list_group_members(self, group: str) -> list[str]:
        g = self._group(group)
        if g["type"].lower() not in _GROUP_TYPES:
            raise ApiError(f"'{group}' имеет тип '{g['type']}', это не группа-селектор")
        return [item["tag"] for item in g["items"]]

    def current_selection(self, group: str) -> str:
        return self._group(group)["selected"]

    def select(self, group: str, node: str) -> None:
        try:
            self._api.select(group, node)
        except ApiError as exc:
            raise ApiError(f"Не удалось выбрать '{node}' в группе '{group}': {exc}") from exc

    def all_proxies(self) -> dict:
        """Все outbounds: tag → {type, all, now}; all/now есть только у групп."""
        out = {o["tag"]: {"type": o["type"]} for o in self._api.outbounds()}
        for g in self._api.groups():
            out[g["tag"]] = {"type": g["type"], "all": [i["tag"] for i in g["items"]],
                             "now": g["selected"]}
        return out

    def subscribe_connections(self, interval: float):
        return self._api.subscribe_connections(interval)
