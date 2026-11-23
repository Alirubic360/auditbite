import re

_ALIASES = {
    "LBS": "LB", "POUND": "LB", "POUNDS": "LB", "#": "LB",
    "CASE": "CS", "CASES": "CS", "CTN": "CS", "CARTON": "CS",
    "EACH": "EA", "PC": "EA", "PCS": "EA", "UNIT": "EA",
    "KGS": "KG", "KILO": "KG", "KILOGRAM": "KG",
    "DOZ": "DZ", "DOZEN": "DZ",
    "GAL": "GAL", "GALLON": "GAL", "GALLONS": "GAL",
}


def norm_unit(u: str | None) -> str | None:
    if u is None:
        return None
    s = re.sub(r"[\s.]", "", u).upper()
    return _ALIASES.get(s, s) or None


_PACK = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s*([A-Za-z#]+)\s*$")


def pack_factor(pack_description: str | None, target_unit: str | None):
    """'4/5 LB' with target LB -> Decimal(20). None unless the pack text is exactly COUNT/SIZE UNIT and UNIT == target."""
    from decimal import Decimal
    if not pack_description or not target_unit:
        return None
    m = _PACK.match(pack_description)
    if not m or norm_unit(m.group(3)) != norm_unit(target_unit):
        return None
    f = Decimal(m.group(1)) * Decimal(m.group(2))
    return f if f > 0 else None
