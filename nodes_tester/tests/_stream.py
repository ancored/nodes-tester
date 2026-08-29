"""Одиночная потоковая закачка для теста stability (единая для всех зон).

Один GET файла, семплим накопленные байты по времени. Троттлинг считаем по
БАЙТОВЫМ окнам внутри этой закачки (скорость последнего окна / первого) — не
зависит от скорости ноды и работает как с cloudflare `__down?bytes=N`, так и с
фикс-файлами (напр. speedtest.selectel.ru/10MB). Один запрос → нет частых
повторов → 429 не провоцируется.

Классификация исхода (важно под РФ):
  - сервер ОТВЕТИЛ статусом (429/5xx) → `HTTPError`: туннель жив (TLS+запрос
    прошли, ТСПУ так не умеет — только reset/drop/timeout) → `server_limit`, не FAIL;
  - транспорт порвался (RST/timeout) → `ConnectionError`/`Timeout` → `broken`
    (сигнал нестабильности/ТСПУ).
"""

from __future__ import annotations

import time

import requests

CHUNK = 64 * 1024


def stream_single(session, url, duration, connect_timeout=10):
    """Один потоковый GET url; семплим (t, накопленные_байты) до EOF или duration.

    Возвращает dict:
      samples      — [(t, cumulative_bytes)] по ходу закачки,
      total        — сколько всего скачали,
      expected     — Content-Length (ожидаемый размер) или None,
      seconds      — суммарное время,
      capped       — True, если упёрлись в duration (не докачали),
      broken       — True при реальном обрыве транспорта (нестабильность),
      server_limit — True при отказе сервера статусом (429/5xx; НЕ вина ноды),
      error        — строка ошибки или None.
    """
    start = time.monotonic()
    samples: list[tuple[float, int]] = [(0.0, 0)]
    total = 0
    expected = None
    capped = broken = server_limit = False
    error = None
    try:
        with session.get(url, timeout=(connect_timeout, duration + 5),
                         stream=True) as resp:
            resp.raise_for_status()
            cl = resp.headers.get("Content-Length")
            if cl and cl.isdigit():
                expected = int(cl)
            for part in resp.iter_content(CHUNK):
                total += len(part)
                t = time.monotonic() - start
                samples.append((t, total))
                if t >= duration:
                    capped = True
                    break
    except requests.exceptions.HTTPError as exc:
        server_limit = True                    # сервер ответил статусом — туннель жив
        error = str(exc)
    except requests.RequestException as exc:
        broken = True                          # обрыв транспорта — нестабильность
        error = str(exc)
    return {
        "samples": samples,
        "total": total,
        "expected": expected,
        "seconds": round(time.monotonic() - start, 2),
        "capped": capped,
        "broken": broken,
        "server_limit": server_limit,
        "error": error,
    }


def time_at_bytes(samples: list[tuple[float, int]], target: float) -> float:
    """Линейно интерполировать момент времени, когда накопилось target байт."""
    prev_t, prev_b = samples[0]
    for t, b in samples:
        if b >= target:
            if b == prev_b:
                return t
            frac = (target - prev_b) / (b - prev_b)
            return prev_t + (t - prev_t) * frac
        prev_t, prev_b = t, b
    return samples[-1][0]
