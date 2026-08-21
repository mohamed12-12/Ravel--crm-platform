"""Maintained nationality/demonym and ISO 4217 currency-code reference lists.

Not the full ISO-3166 country set -- it covers the nationalities Ravel Traveler
customers overwhelmingly hold (Egypt, the wider Arab world, and major global
nationalities), in both English and Arabic demonym forms. Extend `_NATIONALITIES`
as new customer nationalities are seen in production; an unrecognized nationality
is rejected and re-asked rather than silently accepted as free text, so a gap in
this list shows up immediately as a support case instead of a silently corrupted
CRM record.
"""

from __future__ import annotations

import re

_NATIONALITIES: dict[str, str] = {
    # English demonyms / country names
    "egyptian": "Egyptian", "egypt": "Egyptian",
    "saudi": "Saudi", "saudi arabian": "Saudi", "saudi arabia": "Saudi", "ksa": "Saudi",
    "emirati": "Emirati", "uae": "Emirati", "united arab emirates": "Emirati",
    "kuwaiti": "Kuwaiti", "kuwait": "Kuwaiti",
    "qatari": "Qatari", "qatar": "Qatari",
    "bahraini": "Bahraini", "bahrain": "Bahraini",
    "omani": "Omani", "oman": "Omani",
    "jordanian": "Jordanian", "jordan": "Jordanian",
    "lebanese": "Lebanese", "lebanon": "Lebanese",
    "syrian": "Syrian", "syria": "Syrian",
    "iraqi": "Iraqi", "iraq": "Iraqi",
    "palestinian": "Palestinian", "palestine": "Palestinian",
    "yemeni": "Yemeni", "yemen": "Yemeni",
    "libyan": "Libyan", "libya": "Libyan",
    "tunisian": "Tunisian", "tunisia": "Tunisian",
    "algerian": "Algerian", "algeria": "Algerian",
    "moroccan": "Moroccan", "morocco": "Moroccan",
    "sudanese": "Sudanese", "sudan": "Sudanese",
    "somali": "Somali", "somalia": "Somali",
    "american": "American", "usa": "American", "united states": "American", "us": "American",
    "british": "British", "uk": "British", "united kingdom": "British", "english": "British",
    "canadian": "Canadian", "canada": "Canadian",
    "french": "French", "france": "French",
    "german": "German", "germany": "German",
    "italian": "Italian", "italy": "Italian",
    "spanish": "Spanish", "spain": "Spanish",
    "dutch": "Dutch", "netherlands": "Dutch",
    "belgian": "Belgian", "belgium": "Belgian",
    "swiss": "Swiss", "switzerland": "Swiss",
    "austrian": "Austrian", "austria": "Austrian",
    "swedish": "Swedish", "sweden": "Swedish",
    "norwegian": "Norwegian", "norway": "Norwegian",
    "danish": "Danish", "denmark": "Danish",
    "greek": "Greek", "greece": "Greek",
    "portuguese": "Portuguese", "portugal": "Portuguese",
    "polish": "Polish", "poland": "Polish",
    "ukrainian": "Ukrainian", "ukraine": "Ukrainian",
    "russian": "Russian", "russia": "Russian",
    "turkish": "Turkish", "turkey": "Turkish", "turkiye": "Turkish",
    "indian": "Indian", "india": "Indian",
    "pakistani": "Pakistani", "pakistan": "Pakistani",
    "bangladeshi": "Bangladeshi", "bangladesh": "Bangladeshi",
    "sri lankan": "Sri Lankan", "sri lanka": "Sri Lankan",
    "chinese": "Chinese", "china": "Chinese",
    "japanese": "Japanese", "japan": "Japanese",
    "korean": "Korean", "south korea": "Korean",
    "filipino": "Filipino", "philippines": "Filipino", "filipina": "Filipino",
    "indonesian": "Indonesian", "indonesia": "Indonesian",
    "malaysian": "Malaysian", "malaysia": "Malaysian",
    "nigerian": "Nigerian", "nigeria": "Nigerian",
    "south african": "South African", "south africa": "South African",
    "australian": "Australian", "australia": "Australian",
    "brazilian": "Brazilian", "brazil": "Brazilian",
    "mexican": "Mexican", "mexico": "Mexican",
    "eritrean": "Eritrean", "eritrea": "Eritrean",
    "ethiopian": "Ethiopian", "ethiopia": "Ethiopian",
    "comorian": "Comorian", "comoros": "Comorian",
    "djiboutian": "Djiboutian", "djibouti": "Djiboutian",
    "mauritanian": "Mauritanian", "mauritania": "Mauritanian",
    # Arabic demonym forms (masculine/feminine, and country name)
    "مصري": "Egyptian", "مصرية": "Egyptian", "مصر": "Egyptian",
    "سعودي": "Saudi", "سعودية": "Saudi",
    "اماراتي": "Emirati", "إماراتي": "Emirati", "اماراتية": "Emirati", "إماراتية": "Emirati",
    "كويتي": "Kuwaiti", "كويتية": "Kuwaiti",
    "قطري": "Qatari", "قطرية": "Qatari",
    "بحريني": "Bahraini", "بحرينية": "Bahraini",
    "عماني": "Omani", "عمانية": "Omani",
    "اردني": "Jordanian", "أردني": "Jordanian", "اردنية": "Jordanian", "أردنية": "Jordanian",
    "لبناني": "Lebanese", "لبنانية": "Lebanese",
    "سوري": "Syrian", "سورية": "Syrian",
    "عراقي": "Iraqi", "عراقية": "Iraqi",
    "فلسطيني": "Palestinian", "فلسطينية": "Palestinian",
    "يمني": "Yemeni", "يمنية": "Yemeni",
    "ليبي": "Libyan", "ليبية": "Libyan",
    "تونسي": "Tunisian", "تونسية": "Tunisian",
    "جزائري": "Algerian", "جزائرية": "Algerian",
    "مغربي": "Moroccan", "مغربية": "Moroccan",
    "سوداني": "Sudanese", "سودانية": "Sudanese",
    "صومالي": "Somali", "صومالية": "Somali",
    "امريكي": "American", "أمريكي": "American", "امريكية": "American", "أمريكية": "American",
    "بريطاني": "British", "بريطانية": "British", "انجليزي": "British", "إنجليزي": "British",
    "كندي": "Canadian", "كندية": "Canadian",
    "فرنسي": "French", "فرنسية": "French",
    "الماني": "German", "ألماني": "German", "المانية": "German", "ألمانية": "German",
    "ايطالي": "Italian", "إيطالي": "Italian", "ايطالية": "Italian", "إيطالية": "Italian",
    "تركي": "Turkish", "تركية": "Turkish",
    "هندي": "Indian", "هندية": "Indian",
    "باكستاني": "Pakistani", "باكستانية": "Pakistani",
    "صيني": "Chinese", "صينية": "Chinese",
    "ياباني": "Japanese", "يابانية": "Japanese",
    "فلبيني": "Filipino", "فلبينية": "Filipino",
    "اندونيسي": "Indonesian", "إندونيسي": "Indonesian",
    "ماليزي": "Malaysian", "ماليزية": "Malaysian",
    "نيجيري": "Nigerian", "نيجيرية": "Nigerian",
    "استرالي": "Australian", "أسترالي": "Australian", "استرالية": "Australian", "أسترالية": "Australian",
}

CURRENCY_CODES: frozenset[str] = frozenset(
    {
        "AED", "AFN", "ALL", "AMD", "ANG", "AOA", "ARS", "AUD", "AWG", "AZN",
        "BAM", "BBD", "BDT", "BGN", "BHD", "BIF", "BMD", "BND", "BOB", "BRL",
        "BSD", "BTN", "BWP", "BYN", "BZD", "CAD", "CDF", "CHF", "CLP", "CNY",
        "COP", "CRC", "CUP", "CVE", "CZK", "DJF", "DKK", "DOP", "DZD", "EGP",
        "ERN", "ETB", "EUR", "FJD", "FKP", "GBP", "GEL", "GHS", "GIP", "GMD",
        "GNF", "GTQ", "GYD", "HKD", "HNL", "HRK", "HTG", "HUF", "IDR", "ILS",
        "INR", "IQD", "IRR", "ISK", "JMD", "JOD", "JPY", "KES", "KGS", "KHR",
        "KMF", "KPW", "KRW", "KWD", "KYD", "KZT", "LAK", "LBP", "LKR", "LRD",
        "LSL", "LYD", "MAD", "MDL", "MGA", "MKD", "MMK", "MNT", "MOP", "MRU",
        "MUR", "MVR", "MWK", "MXN", "MYR", "MZN", "NAD", "NGN", "NIO", "NOK",
        "NPR", "NZD", "OMR", "PAB", "PEN", "PGK", "PHP", "PKR", "PLN", "PYG",
        "QAR", "RON", "RSD", "RUB", "RWF", "SAR", "SBD", "SCR", "SDG", "SEK",
        "SGD", "SHP", "SLE", "SOS", "SRD", "SSP", "STN", "SYP", "SZL", "THB",
        "TJS", "TMT", "TND", "TOP", "TRY", "TTD", "TWD", "TZS", "UAH", "UGX",
        "USD", "UYU", "UZS", "VES", "VND", "VUV", "WST", "XAF", "XCD", "XOF",
        "XPF", "YER", "ZAR", "ZMW", "ZWL",
    }
)


# Arabic orthographic variation that carries no meaning: a tied taa marbuta
# typed as a plain haa, alef with any hamza form typed bare, alef maqsura typed
# as a plain yaa. Live transcript (2026-08-20): a customer answered "مصريه" --
# the dominant Egyptian typing habit for "مصرية" -- and was told
# "مش قادر أتعرف على الجنسية دي", because the lookup below is an exact
# whole-string dict match and casefold() is a no-op for Arabic. Every Arabic
# feminine row in _NATIONALITIES had the same hole, and the hamza forms were
# only partly covered row by row (e.g. "اماراتي"/"إماراتي" both listed, but
# "سورية" only in one spelling). Folding once here fixes the whole table
# instead of adding spelling variants per row forever.
#
# Same substitutions as tool_calling_runtime.py's
# _ARABIC_TRIP_REFERENCE_TRANSLATION, which already normalizes trip and person
# names this way -- it is deliberately duplicated rather than imported, because
# this module is a leaf reference table and must not import the 7k-line runtime
# (which imports this module).
_ARABIC_FOLD_TRANSLATION = str.maketrans(
    {"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه", "ؤ": "و", "ئ": "ي"}
)


def _fold_arabic(value: str) -> str:
    return value.translate(_ARABIC_FOLD_TRANSLATION)


# Built once at import from _NATIONALITIES itself, so it can never drift out of
# sync with the canonical table. First writer wins on a collision (two spellings
# folding to the same key always map to the same canonical nationality in
# practice, and preferring the earlier row keeps the table's own ordering
# authoritative).
_FOLDED_NATIONALITIES: dict[str, str] = {}
for _form, _canonical in _NATIONALITIES.items():
    _FOLDED_NATIONALITIES.setdefault(_fold_arabic(_form), _canonical)


def _without_definite_article(value: str) -> str:
    """Strip a leading Arabic definite article, or return "" if there is none.

    Live 2026-08-20 transcript: a customer answered "العراقيه" and was told
    "مش قادر أتعرف على الجنسية دي"; "عراقي" on the next turn worked. Answering
    with the article is completely ordinary Arabic ("أنا العراقية"), and it
    broke every row in the table, not just this one -- "المصريه",
    "السعوديه" and "الاماراتيه" all failed the same way.
    """

    if len(value) > 4 and value.startswith("ال"):
        return value[2:]
    return ""


def resolve_nationality(text: str) -> str:
    """Return the canonical nationality for a recognized demonym/country name, or ''."""
    normalized = " ".join(str(text or "").strip().casefold().split())
    exact = _NATIONALITIES.get(normalized, "")
    if exact:
        return exact
    # Only reached when the literal spelling is unknown, so an unrecognized
    # nationality still fails closed exactly as before -- folding widens what
    # counts as the SAME word, it never invents a new nationality.
    folded = _FOLDED_NATIONALITIES.get(_fold_arabic(normalized), "")
    if folded:
        return folded
    # Tried LAST so a nationality whose own spelling begins with "ال"
    # ("الماني") is always matched as itself first, and stripping can never
    # shadow a real entry.
    bare = _without_definite_article(normalized)
    if not bare:
        return ""
    return _NATIONALITIES.get(bare, "") or _FOLDED_NATIONALITIES.get(_fold_arabic(bare), "")


def looks_like_currency_code(text: str) -> bool:
    """True if the whole answer is (only) a 3-letter ISO 4217 currency code."""
    token = re.sub(r"[^A-Za-z]", "", str(text or "")).upper()
    return token in CURRENCY_CODES
