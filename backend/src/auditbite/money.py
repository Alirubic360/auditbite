"""All money math uses Decimal. Never float."""
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

CENT = Decimal("0.01")


def D(x) -> Decimal:
    """Coerce AI/DB values to Decimal via str() so float noise never enters. None/'' -> 0."""
    if x is None or x == "":
        return Decimal(0)
    if isinstance(x, Decimal):
        return x
    try:
        return Decimal(str(x))
    except InvalidOperation as e:
        raise ValueError(f"not a number: {x!r}") from e


def D_or_none(x):
    return None if x is None or x == "" else D(x)


def cents(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)
