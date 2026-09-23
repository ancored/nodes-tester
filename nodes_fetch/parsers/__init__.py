"""Парсеры share-ссылок → узлы sing-box. Имя модуля = схема ссылки (vless, vmess, hysteria2…);
folder-форматы (awg) дают parse_file(path). Модули грузятся лениво, один раз."""

import importlib
import pkgutil

_modules = None


def modules():
    global _modules
    if _modules is None:
        _modules = {m.name: importlib.import_module(f"{__name__}.{m.name}")
                    for m in pkgutil.iter_modules(__path__)}
    return _modules


def get(name):
    return modules().get(name) if name else None
