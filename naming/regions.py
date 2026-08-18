"""Страны и коарс-регионы — общий модуль.

- Определение страны при переименовании (флаг-эмодзи → ISO alpha-2, либо полное
  название страны в тексте) — используется переименователем.
- Коарс-регион (eu/us/ru/other) по стране — используется тестером (recognition=parse)
  и группировкой переименователя.
"""

from __future__ import annotations

import re

# Пара regional indicator symbols (флаг-эмодзи).
_flag_regex = re.compile(r"[\U0001F1E6-\U0001F1FF]{2}")


def flag_to_code(flag: str) -> str:
    """Флаг-эмодзи → ISO alpha-2 (каждый символ-индикатор → одна ASCII-буква)."""
    return "".join(chr(ord(c) - 0x1F1E6 + ord("a")) for c in flag)


# Полные названия стран (ru/en) -> ISO alpha-2. Второй слой определения страны,
# когда во флага в имени нет. Двухбуквенные коды НАМЕРЕННО не включены, чтобы
# слова вроде "AUTO"(->au) / "PLUS"(->us) не давали ложных срабатываний.
_COUNTRY_NAMES = {
    "ru": ["россия", "russia"],
    "us": ["сша", "usa", "america", "united states"],
    "gb": ["британия", "великобритания", "англия", "britain", "england", "united kingdom"],
    "de": ["германия", "germany"],
    "nl": ["нидерланды", "голландия", "netherlands", "holland"],
    "fr": ["франция", "france"],
    "fi": ["финляндия", "finland"],
    "se": ["швеция", "sweden"],
    "no": ["норвегия", "norway"],
    "dk": ["дания", "denmark"],
    "lt": ["литва", "lithuania"],
    "lv": ["латвия", "latvia"],
    "ee": ["эстония", "estonia"],
    "pl": ["польша", "poland"],
    "cz": ["чехия", "czech", "czechia"],
    "at": ["австрия", "austria"],
    "ch": ["швейцария", "switzerland"],
    "it": ["италия", "italy"],
    "es": ["испания", "spain"],
    "pt": ["португалия", "portugal"],
    "ie": ["ирландия", "ireland"],
    "ro": ["румыния", "romania"],
    "md": ["молдова", "молдавия", "moldova"],
    "ua": ["украина", "ukraine"],
    "by": ["беларусь", "белоруссия", "belarus"],
    "tr": ["турция", "turkey", "türkiye"],
    "ca": ["канада", "canada"],
    "jp": ["япония", "japan"],
    "sg": ["сингапур", "singapore"],
    "hk": ["гонконг", "hong kong", "hongkong"],
    "tw": ["тайвань", "taiwan"],
    "kr": ["корея", "korea"],
    "cn": ["китай", "china"],
    "in": ["индия", "india"],
    "th": ["таиланд", "thailand"],
    "kz": ["казахстан", "kazakhstan"],
    "ae": ["оаэ", "эмираты", "emirates", "dubai", "дубай"],
    "au": ["австралия", "australia"],
    "nz": ["новая зеландия", "new zealand"],
    "ar": ["аргентина", "argentina"],
    "br": ["бразилия", "brazil"],
    "il": ["израиль", "israel"],
    "hu": ["венгрия", "hungary"],
    "gr": ["греция", "greece"],
    "bg": ["болгария", "bulgaria"],
    "rs": ["сербия", "serbia"],
    "is": ["исландия", "iceland"],
    "lu": ["люксембург", "luxembourg"],
    "be": ["бельгия", "belgium"],
}
_COUNTRY_NAME_RE = [
    (cc, re.compile(r"\b(?:%s)\b" % "|".join(map(re.escape, names)), re.IGNORECASE))
    for cc, names in _COUNTRY_NAMES.items()
]


def country_from_text(text: str):
    """Полное название страны в тексте → ISO alpha-2, либо None."""
    for cc, pattern in _COUNTRY_NAME_RE:
        if pattern.search(text):
            return cc
    return None


# Европейские страны (ISO alpha-2) — для коарс-региона eu.
_EUROPE = {
    "al", "ad", "am", "at", "az", "by", "be", "ba", "bg", "hr", "cy", "cz",
    "dk", "ee", "fi", "fr", "ge", "de", "gr", "hu", "is", "ie", "it", "xk",
    "lv", "li", "lt", "lu", "mt", "md", "mc", "me", "nl", "mk", "no", "pl",
    "pt", "ro", "sm", "rs", "sk", "si", "es", "se", "ch", "tr", "ua", "gb",
    "uk", "va",
}


def coarse_region(country: str) -> str:
    """Страна → коарс-регион eu/us/ru/other."""
    cc = (country or "").split("(")[0].lower()   # 'nl(n)' -> 'nl'
    if cc == "ru":
        return "ru"
    if cc == "us":
        return "us"
    if cc in _EUROPE:
        return "eu"
    return "other"
