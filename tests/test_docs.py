"""Публичная документация на двух языках: имя.md (английский) и имя.ru.md (русский).

У каждого документа есть пара, в первой строке — переход на другой язык, относительные ссылки
ведут на существующие файлы и якоря, а ссылки на документы — на версию того же языка.
"""

import os
import re
import subprocess
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LINK = re.compile(r'(?:\]\(|(?:src|href|srcset)=")([^)"\s]+)')
_HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*$", re.M)
_CODE = re.compile(r"^```.*?^```", re.M | re.S)


def _docs():
    out = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "*.md"],
                                  cwd=_ROOT, text=True).split()
    return sorted(f for f in out if not f.startswith("tests/") and os.path.exists(os.path.join(_ROOT, f)))


def _slug(heading):
    """Якорь заголовка, как его строит GitHub: нижний регистр, без знаков, пробелы — дефисы."""
    return re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")


def _anchors(path):
    with open(path, encoding="utf-8") as fh:
        text = _CODE.sub("", fh.read())
    seen, out = {}, set()
    for heading in _HEADING.findall(text):
        slug = _slug(heading)
        n = seen.get(slug, 0)
        seen[slug] = n + 1
        out.add(slug if not n else f"{slug}-{n}")
    return out


class DocsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.docs = _docs()
        cls.english = [d for d in cls.docs if not d.endswith(".ru.md")]

    def test_every_document_has_both_languages(self):
        names = set(self.docs)
        for doc in self.english:
            with self.subTest(doc=doc):
                self.assertIn(doc[:-3] + ".ru.md", names)
        for doc in names - set(self.english):
            with self.subTest(doc=doc):
                self.assertIn(doc[:-6] + ".md", names)

    def test_language_switch_on_the_first_line(self):
        for doc in self.english:
            ru = doc[:-3] + ".ru.md"
            with self.subTest(doc=doc):
                with open(os.path.join(_ROOT, doc), encoding="utf-8") as fh:
                    self.assertEqual(fh.readline().strip(), f"**English** | [Русский]({os.path.basename(ru)})")
                with open(os.path.join(_ROOT, ru), encoding="utf-8") as fh:
                    self.assertEqual(fh.readline().strip(), f"[English]({os.path.basename(doc)}) | **Русский**")

    def test_links_resolve_and_stay_in_the_language(self):
        pairs = set(self.docs)
        for doc in self.docs:
            russian = doc.endswith(".ru.md")
            other = doc[:-6] + ".md" if russian else doc[:-3] + ".ru.md"     # переход на другой язык
            with open(os.path.join(_ROOT, doc), encoding="utf-8") as fh:
                text = _CODE.sub("", fh.read())
            for link in _LINK.findall(text):
                if re.match(r"[a-z]+:", link) or link.startswith("#") and not link[1:]:
                    continue
                target, _, anchor = link.partition("#")
                path = os.path.normpath(os.path.join(os.path.dirname(doc), target)) if target else doc
                with self.subTest(doc=doc, link=link):
                    self.assertTrue(os.path.exists(os.path.join(_ROOT, path)), "нет файла")
                    if path in pairs and path != other:
                        self.assertEqual(path.endswith(".ru.md"), russian, "ссылка на документ другого языка")
                        if anchor:
                            self.assertIn(anchor, _anchors(os.path.join(_ROOT, path)), "нет якоря")


if __name__ == "__main__":
    unittest.main()
