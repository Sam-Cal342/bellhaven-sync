"""Normalisation helpers so formatting noise (Rd vs Road, & vs and) never looks like a real difference."""
from __future__ import annotations

import re
from difflib import SequenceMatcher

STREET_TOKENS = {
    "street": "st", "str": "st", "road": "rd", "avenue": "ave", "av": "ave", "boulevard": "blvd",
    "drive": "dr", "lane": "ln", "court": "ct", "place": "pl", "parkway": "pkwy", "highway": "hwy",
    "pk": "pike", "pke": "pike", "circle": "cir", "terrace": "ter", "trail": "trl", "square": "sq",
    "north": "n", "south": "s", "east": "e", "west": "w",
    "northwest": "nw", "northeast": "ne", "southwest": "sw", "southeast": "se",
    "suite": "ste", "saint": "st",
}

NAME_TOKENS = {
    "&": "and", "rehab": "rehabilitation", "centre": "center", "ctr": "center",
    "healthcare": "health care", "snr": "senior",
}
NAME_STOPWORDS = {"the", "of", "at", "a", "-"}


def clean(s: str | None) -> str:
    s = (s or "").lower().replace("&", " & ")
    s = re.sub(r"[^\w&\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def norm_street(s: str | None) -> str:
    return " ".join(STREET_TOKENS.get(t, t) for t in clean(s).split())


def street_number(s: str | None) -> str | None:
    m = re.match(r"\s*(\d+)", s or "")
    return m.group(1) if m else None


def is_po_box(s: str | None) -> bool:
    return bool(re.match(r"\s*p\.?\s*o\.?\s*box\b", s or "", re.I))


def norm_city(s: str | None) -> str:
    return clean(s)


def norm_zip(s: str | None) -> str:
    return re.sub(r"\D", "", s or "")[:5]


def norm_phone(s: str | None) -> str:
    d = re.sub(r"\D", "", s or "")
    return d[-10:] if len(d) >= 10 else d


def norm_name(s: str | None) -> str:
    words = []
    for t in clean(s).split():
        t = NAME_TOKENS.get(t, t)
        words.extend(t.split())
    return " ".join(w for w in words if w not in NAME_STOPWORDS)


def name_similarity(a: str | None, b: str | None) -> float:
    a, b = norm_name(a), norm_name(b)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def same_address(street_a, city_a, state_a, zip_a, street_b, city_b, state_b, zip_b) -> bool:
    return (norm_street(street_a) == norm_street(street_b) and norm_city(city_a) == norm_city(city_b)
            and (state_a or "").upper() == (state_b or "").upper() and norm_zip(zip_a) == norm_zip(zip_b))
