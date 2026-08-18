"""Общий помощник: тянуть данные из URL в течение заданного времени.

Многие speed-эндпоинты (напр. cloudflare `__down`) ограничивают размер одного
ответа. Чтобы держать поток дольше, перезапрашиваем порцию, когда ответ
естественно закончился. Реальный обрыв (RST/сеть) отличаем от «файл кончился»:
обрыв возвращается как error, конец файла — просто повод перезапросить.
"""

from __future__ import annotations

import time

import requests

CHUNK = 64 * 1024


def stream_for(session, url, duration, connect_timeout=10):
    """Тянуть данные из url в течение duration секунд.

    Возвращает (samples, total_bytes, error):
      samples     — [(t, cumulative_bytes)] по ходу скачивания,
      total_bytes — сколько всего скачали,
      error       — строка при реальном обрыве соединения, иначе None
                    (естественное завершение ответа обрывом НЕ считается).
    """
    start = time.monotonic()
    samples: list[tuple[float, int]] = [(0.0, 0)]
    total = 0
    try:
        while time.monotonic() - start < duration:
            remaining = duration - (time.monotonic() - start)
            reached_deadline = False
            with session.get(
                url, timeout=(connect_timeout, remaining + 5), stream=True
            ) as resp:
                resp.raise_for_status()
                for chunk in resp.iter_content(CHUNK):
                    total += len(chunk)
                    t = time.monotonic() - start
                    samples.append((t, total))
                    if t >= duration:
                        reached_deadline = True
                        break
            if reached_deadline:
                break
            # Ответ закончился раньше duration (лимит размера) — перезапросим.
    except requests.RequestException as exc:
        return samples, total, str(exc)
    return samples, total, None
