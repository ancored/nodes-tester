"""Пакет тестов. Импорт модулей регистрирует их в реестре.

Чтобы добавить новый тест: создать модуль с классом-наследником BaseTest,
пометить @register, добавить импорт сюда и секцию в config.yaml.
"""

from .base import (  # noqa: F401
    BaseTest,
    TestContext,
    TestResult,
    get_test_class,
    known_tests,
    register,
)

# Регистрация встроенных тестов (порядок импорта не важен).
from . import connectivity  # noqa: F401,E402
from . import latency       # noqa: F401,E402
from . import jitter        # noqa: F401,E402
from . import download      # noqa: F401,E402
from . import stability     # noqa: F401,E402
from . import reachability  # noqa: F401,E402
