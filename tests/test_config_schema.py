"""config.schema.json — подсказки редактора — совпадает с загрузчиком config.json.

Поля загрузчика берутся из его же ошибки на неизвестном поле («есть: a, b, c»), поэтому новое
поле, добавленное только в код или только в схему, роняет тест.
"""

import json
import os
import re
import unittest

from nodes_tester.config import ConfigError, parse_config

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA = os.path.join(_ROOT, "config", "config.schema.json")
EXAMPLES = ("config/config.example.json", "tests/fixtures/golden/synthetic/config/config.json",
            "tests/fixtures/golden/synthetic-wh/config/config.json")


def _load(path):
    with open(os.path.join(_ROOT, path), encoding="utf-8") as fh:
        return json.load(fh)


def _nest(path, leaf):
    """{"a": {"b": [leaf]}} по пути ("a", "b", 0)."""
    for key in reversed(path):
        leaf = [leaf] if key == 0 else {key: leaf}
    return leaf


def _loader_fields(path):
    try:
        parse_config(_nest(path, {"zz_unknown": 1}))
    except ConfigError as exc:
        found = re.search(r"zz_unknown: неизвестное поле \(есть: (.*)\)", str(exc))
        if found:
            return set(found.group(1).split(", "))
        raise AssertionError(f"{path}: {exc}")
    raise AssertionError(f"{path}: неизвестное поле прошло")


def _objects(schema, path=()):
    """Закрытые объекты схемы (additionalProperties: false) с путём; $ref не раскрываются."""
    if schema.get("type") == "array" and isinstance(schema.get("items"), dict):
        yield from _objects(schema["items"], path + (0,))
    if schema.get("type") == "object" and schema.get("additionalProperties") is False:
        yield path, schema["properties"]
        for key, sub in schema["properties"].items():
            yield from _objects(sub, path + (key,))


class ConfigSchemaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = _load(SCHEMA)

    def test_schema_fields_match_loader(self):
        for path, props in _objects(self.schema):
            if not path:
                continue          # корень: "$schema" и разделы — ниже отдельной проверкой
            with self.subTest(path=".".join(map(str, path))):
                self.assertEqual(set(props), _loader_fields(path))

    def test_root_sections_match_loader(self):
        try:
            parse_config({"zz_unknown": 1})
        except ConfigError as exc:
            sections = set(re.search(r"есть: (.*)\)", str(exc)).group(1).split(", "))
        self.assertEqual(set(self.schema["properties"]), sections)

    def test_examples_valid(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("нет jsonschema")
        validator = jsonschema.Draft7Validator(self.schema)
        for path in EXAMPLES:
            with self.subTest(example=path):
                data = _load(path)
                self.assertEqual([e.message for e in validator.iter_errors(data)], [])
                parse_config(data)
        bad = {"groups": [{"tag": "ai", "require": {"gemeni": {}}}], "selection": {"cooldown": 300}}
        self.assertEqual(len(list(validator.iter_errors(bad))), 2)


if __name__ == "__main__":
    unittest.main()
