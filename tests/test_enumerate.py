"""Тестер перечисляет только настоящие leaf-ноды (с CRC). Члены без CRC —
под-селекторы/группы (напр. старый двухуровневый nodes-tester) — пропускаются,
а не тестируются как ноды."""

import unittest

from tests.helpers import make_runner, temp_dir


class EnumerateGuardTest(unittest.TestCase):
    def tearDown(self):
        self.r.storage.close()

    def test_skips_members_without_crc(self):
        tags = ["eu-nodes-tester", "us-nodes-tester", "other-nodes-tester",  # под-селекторы
                "LUNA-vless|reality-nl-out [abcd1234]"]                       # реальная нода
        self.r = make_runner(temp_dir(), tags=tags)
        nodes = self.r._enumerate_nodes()
        ids = [ident.node_id for _, ident in nodes]
        self.assertEqual(ids, ["abcd1234"])          # только нода с CRC

    def test_all_real_nodes_pass(self):
        tags = ["LUNA-vless|reality-nl-out [aaaa1111]",
                "VSPACE-vless|reality-us-out [bbbb2222]"]
        self.r = make_runner(temp_dir(), tags=tags)
        self.assertEqual(len(self.r._enumerate_nodes()), 2)


if __name__ == "__main__":
    unittest.main()
