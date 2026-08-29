"""CLI-точка входа: python -m nodes_tester [--config config.json]."""

from __future__ import annotations

import argparse
import sys

from .config import load_config
from .runner import Runner
from .tests import known_tests


def _force_utf8_output() -> None:
    """Вывод в UTF-8 с заменой непредставимых символов.

    Роутер обычно UTF-8, но на Windows-консоли (cp1251) кириллица и символы
    оформления иначе роняют print. errors='replace' страхует любой терминал.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def main(argv=None) -> int:
    _force_utf8_output()
    parser = argparse.ArgumentParser(
        prog="nodes_tester",
        description="Тестер leaf-нод sing-box через SOCKS5 + Clash API",
    )
    parser.add_argument(
        "-c", "--config", default="config/config.json",
        help="путь к config.json (по умолчанию: ./config/config.json)",
    )
    parser.add_argument(
        "--list-tests", action="store_true",
        help="показать доступные тесты и выйти",
    )
    parser.add_argument(
        "--vacuum", action="store_true",
        help="выполнить VACUUM БД и выйти (тяжёлый, для cron раз в месяц)",
    )
    args = parser.parse_args(argv)

    if args.list_tests:
        print("Доступные тесты:", ", ".join(known_tests()))
        return 0

    try:
        cfg = load_config(args.config)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 2

    if args.vacuum:
        from .storage import Storage
        st = Storage(cfg.storage)
        try:
            st.vacuum()
            print("VACUUM выполнен.")
        except Exception as exc:  # noqa: BLE001 — БД занята/ошибка: не роняем cron трейсбеком
            print(f"VACUUM не выполнен: {exc}", file=sys.stderr)
            return 1
        finally:
            st.close()
        return 0

    try:
        path = Runner(cfg).run()
    except KeyboardInterrupt:
        print("\nПрервано пользователем.", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 — верхнеуровневый перехват для CLI
        print(f"Ошибка прогона: {exc}", file=sys.stderr)
        return 1

    print(f"\nГотово. Результаты: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
