"""Golden-инвариант рефактора (REFACTOR-MODULES.md, D8): генератор nodes.json на
записанных ответах подписок выдаёт эталон — JSON-равенство (порядок ключей внутри объекта
не важен, порядок элементов списков — важен). Теги/CRC не должны меняться.

Каждый набор проверяется двумя путями: прежний интерфейс `python -m subscribe` (конфиги v1,
обёртка) и новый конвейер `migrate → python -m nodes_fetch → python -m nodes_config`.

Наборы:
- tests/fixtures/golden/*   — синтетические (в git), см. tests/golden/make_synthetic.py;
- tests/fixtures/local/*    — реальные подписки, записанные на роутере (вне git, секреты);
                              тест пропускается, если их нет.
Пересоздать эталон (только осознанно!): python tests/golden/harness.py golden --project . --set <SET>
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tests", "golden"))
import harness  # noqa: E402


def _sets(kind):
    base = os.path.join(_ROOT, "tests", "fixtures", kind)
    if not os.path.isdir(base):
        return []
    return sorted(os.path.join(base, d) for d in os.listdir(base)
                  if os.path.isfile(os.path.join(base, d, "golden.json")))


class GoldenTest(unittest.TestCase):
    def _check(self, set_dir, runner=harness.replay):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "nodes.json")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                runner(_ROOT, set_dir, out)
            with open(out, encoding="utf-8") as f:
                got = json.load(f)
        with open(os.path.join(set_dir, "golden.json"), encoding="utf-8") as f:
            want = json.load(f)
        tags = lambda c: [o["tag"] for o in c.get("outbounds", []) + c.get("endpoints", [])]
        self.assertEqual(tags(got), tags(want), f"теги разошлись с эталоном: {set_dir}")
        self.assertEqual(got, want, f"nodes.json разошёлся с эталоном: {set_dir}")

    def _check_all(self, sets):
        for s in sets:
            for runner in (harness.replay, harness.replay_pipeline):
                with self.subTest(set=os.path.basename(s), path=runner.__name__):
                    self._check(s, runner)

    def test_synthetic_sets(self):
        sets = _sets("golden")
        self.assertGreaterEqual(len(sets), 2)
        self._check_all(sets)

    def test_local_real_sets(self):
        sets = _sets("local")
        if not sets:
            self.skipTest("нет tests/fixtures/local (реальные подписки записываются на роутере)")
        self._check_all(sets)


if __name__ == "__main__":
    unittest.main()
