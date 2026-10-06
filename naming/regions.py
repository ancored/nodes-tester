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
EUROPE = frozenset(_EUROPE)


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


# ISO 3166-1 alpha-3 → alpha-2 (Google отдаёт страну клиента в alpha-3).
_ALPHA3 = dict(zip(*[iter("""
AFG AF ALA AX ALB AL DZA DZ ASM AS AND AD AGO AO AIA AI ATA AQ ATG AG ARG AR ARM AM ABW AW
AUS AU AUT AT AZE AZ BHS BS BHR BH BGD BD BRB BB BLR BY BEL BE BLZ BZ BEN BJ BMU BM BTN BT
BOL BO BES BQ BIH BA BWA BW BVT BV BRA BR IOT IO BRN BN BGR BG BFA BF BDI BI CPV CV KHM KH
CMR CM CAN CA CYM KY CAF CF TCD TD CHL CL CHN CN CXR CX CCK CC COL CO COM KM COG CG COD CD
COK CK CRI CR CIV CI HRV HR CUB CU CUW CW CYP CY CZE CZ DNK DK DJI DJ DMA DM DOM DO ECU EC
EGY EG SLV SV GNQ GQ ERI ER EST EE SWZ SZ ETH ET FLK FK FRO FO FJI FJ FIN FI FRA FR GUF GF
PYF PF ATF TF GAB GA GMB GM GEO GE DEU DE GHA GH GIB GI GRC GR GRL GL GRD GD GLP GP GUM GU
GTM GT GGY GG GIN GN GNB GW GUY GY HTI HT HMD HM VAT VA HND HN HKG HK HUN HU ISL IS IND IN
IDN ID IRN IR IRQ IQ IRL IE IMN IM ISR IL ITA IT JAM JM JPN JP JEY JE JOR JO KAZ KZ KEN KE
KIR KI PRK KP KOR KR KWT KW KGZ KG LAO LA LVA LV LBN LB LSO LS LBR LR LBY LY LIE LI LTU LT
LUX LU MAC MO MDG MG MWI MW MYS MY MDV MV MLI ML MLT MT MHL MH MTQ MQ MRT MR MUS MU MYT YT
MEX MX FSM FM MDA MD MCO MC MNG MN MNE ME MSR MS MAR MA MOZ MZ MMR MM NAM NA NRU NR NPL NP
NLD NL NCL NC NZL NZ NIC NI NER NE NGA NG NIU NU NFK NF MKD MK MNP MP NOR NO OMN OM PAK PK
PLW PW PSE PS PAN PA PNG PG PRY PY PER PE PHL PH PCN PN POL PL PRT PT PRI PR QAT QA REU RE
ROU RO RUS RU RWA RW BLM BL SHN SH KNA KN LCA LC MAF MF SPM PM VCT VC WSM WS SMR SM STP ST
SAU SA SEN SN SRB RS SYC SC SLE SL SGP SG SXM SX SVK SK SVN SI SLB SB SOM SO ZAF ZA SGS GS
SSD SS ESP ES LKA LK SDN SD SUR SR SJM SJ SWE SE CHE CH SYR SY TWN TW TJK TJ TZA TZ THA TH
TLS TL TGO TG TKL TK TON TO TTO TT TUN TN TUR TR TKM TM TCA TC TUV TV UGA UG UKR UA ARE AE
GBR GB USA US UMI UM URY UY UZB UZ VUT VU VEN VE VNM VN VGB VG VIR VI WLF WF ESH EH YEM YE
ZMB ZM ZWE ZW XKX XK
""".split())] * 2))


def alpha3_to_alpha2(code: str) -> str:
    """NLD → NL; неизвестный код — пустая строка."""
    return _ALPHA3.get((code or "").upper(), "")
