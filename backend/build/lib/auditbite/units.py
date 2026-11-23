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
