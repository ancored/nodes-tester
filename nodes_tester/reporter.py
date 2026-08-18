"""Запись результатов прогона в файл (jsonl / json / csv).

Новые результаты добавляются В НАЧАЛО файла (newest-first), старое содержимое
сохраняется. Запись — по прогонам: runner вызывает flush() после каждого полного
прогона, тогда буфер прогона дописывается поверх уже накопленного файла.
Запись атомарна (temp + os.replace), чтобы прерывание не билo файл.

Формат — обогащённый: Провайдер · Протокол · Регион · ID · url теста · результат.
- jsonl/json: запись по ноде (identity + tests{name: {url, ...}}).
- csv: по строке на пару (нода, тест) с колонками из требований.
"""

from __future__ import annotations

import csv
import io
import json
import os
from datetime import datetime
from typing import Any

from .config import ReportConfig

# Служебные ключи в результате теста, не относящиеся к «метрикам».
_SERVICE_KEYS = {"ok", "url", "error"}

_CSV_COLUMNS = [
    "timestamp", "round", "region_group",
    "provider", "protocol", "region", "id", "node",
    "test", "url", "ok", "result", "error",
]
_CSV_HEADER = ",".join(_CSV_COLUMNS)


class Reporter:
    def __init__(self, cfg: ReportConfig, run_ts: str):
        self.cfg = cfg
        os.makedirs(cfg.dir, exist_ok=True)
        filename = cfg.filename or f"results.{cfg.format}"
        filename = filename.replace("{ts}", run_ts)
        self.path = os.path.join(cfg.dir, filename)
        self._buffer: list[dict[str, Any]] = []   # записи текущего прогона (в порядке теста)

    def add(self, record: dict[str, Any]) -> None:
        self._buffer.append(record)

    def flush(self) -> None:
        """Дописать буфер прогона в НАЧАЛО файла, сохранив старое содержимое."""
        if not self._buffer:
            return
        if self.cfg.format == "jsonl":
            self._flush_jsonl()
        elif self.cfg.format == "json":
            self._flush_json()
        elif self.cfg.format == "csv":
            self._flush_csv()
        self._buffer.clear()

    def close(self) -> None:
        self.flush()

    # --- Форматы -------------------------------------------------------

    def _flush_jsonl(self) -> None:
        new = "".join(
            json.dumps(r, ensure_ascii=False) + "\n" for r in self._buffer
        )
        self._atomic_write(new + _read_text(self.path))

    def _flush_json(self) -> None:
        existing = []
        old = _read_text(self.path)
        if old.strip():
            try:
                existing = json.loads(old)
                if not isinstance(existing, list):
                    existing = []
            except ValueError:
                existing = []
        combined = list(self._buffer) + existing
        self._atomic_write(json.dumps(combined, ensure_ascii=False, indent=2))

    def _flush_csv(self) -> None:
        # Новые строки (без заголовка).
        buf = io.StringIO()
        writer = csv.DictWriter(
            buf, fieldnames=_CSV_COLUMNS, extrasaction="ignore", lineterminator="\n"
        )
        for record in self._buffer:
            for row in _explode_csv(record):
                writer.writerow(row)
        new_rows = buf.getvalue()

        # Старые строки данных (без повторного заголовка).
        existing_lines = _read_text(self.path).splitlines()
        if existing_lines and existing_lines[0] == _CSV_HEADER:
            existing_lines = existing_lines[1:]
        old_rows = ("\n".join(existing_lines) + "\n") if existing_lines else ""

        self._atomic_write(_CSV_HEADER + "\n" + new_rows + old_rows)

    # --- Атомарная запись ----------------------------------------------

    def _atomic_write(self, text: str) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.replace(tmp, self.path)


def _read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        return ""


def _explode_csv(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Развернуть запись ноды в строки CSV — по одной на тест."""
    identity = {
        "timestamp": record.get("timestamp"),
        "round": record.get("round"),
        "region_group": record.get("region_group"),
        "provider": record.get("provider"),
        "protocol": record.get("protocol"),
        "region": record.get("region"),
        "id": record.get("id"),
        "node": record.get("node"),
    }
    rows = []
    for test_name, res in (record.get("tests") or {}).items():
        row = dict(identity)
        row["test"] = test_name
        row["url"] = res.get("url", "")
        row["ok"] = res.get("ok")
        row["error"] = res.get("error", "")
        row["result"] = _result_str(res)
        rows.append(row)
    return rows


def _result_str(res: dict[str, Any]) -> str:
    """Человекочитаемая сводка метрик теста: 'ttfb_ms=83.2 status=200'."""
    parts = [f"{k}={v}" for k, v in res.items() if k not in _SERVICE_KEYS]
    return " ".join(parts)


def run_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")
