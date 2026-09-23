"""Golden-инвариант рефактора (REFACTOR-MODULES.md, D8): генератор nodes.json на
записанных ответах подписок выдаёт ПОБАЙТНО эталон. Теги/CRC не должны меняться.

Наборы:
- tests/fixtures/golden/*   — синтетические (в git), см. tests/golden/make_synthetic.py;
- tests/fixtures/local/*    — реальные подписки, записанные на роутере (вне git, секреты);
                              тест пропускается, если их нет.
Пересоздать эталон (только осознанно!): python tests/golden/harness.py golden --project . --set <SET>
"""

import contextlib
import io
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
    def _check(self, set_dir):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "nodes.json")
            with contextlib.redirect_stdout(io.StringIO()):
                harness.replay(_ROOT, set_dir, out)
            with open(out, "rb") as f:
                got = f.read()
        with open(os.path.join(set_dir, "golden.json"), "rb") as f:
            want = f.read()
        self.assertEqual(got, want, f"nodes.json разошёлся с эталоном: {set_dir}")

    def test_synthetic_sets(self):
        sets = _sets("golden")
        self.assertGreaterEqual(len(sets), 2)
        for s in sets:
            with self.subTest(set=os.path.basename(s)):
                self._check(s)

    def test_local_real_sets(self):
        sets = _sets("local")
        if not sets:
            self.skipTest("нет tests/fixtures/local (реальные подписки записываются на роутере)")
        for s in sets:
            with self.subTest(set=os.path.basename(s)):
                self._check(s)


if __name__ == "__main__":
    unittest.main()
