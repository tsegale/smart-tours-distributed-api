"""Exact money handling. Amounts are Decimal in the domain and integer minor units at providers."""

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
MAX_AMOUNT = Decimal("99999999.99")  # NUMERIC(10, 2)

# ISO 4217 currencies without a minor unit, per Stripe's list. NAD and ZAR have two decimals.
ZERO_DECIMAL = frozenset(
    {"BIF", "CLP", "DJF", "GNF", "JPY", "KMF", "KRW", "MGA", "PYG", "RWF", "VND", "VUV", "XAF", "XOF", "XPF"}
)


def to_amount(value: Decimal | int | str) -> Decimal:
    """Quantise to cents. Rejects negative values and values that don't fit NUMERIC(10, 2)."""
    amount = Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)
    if amount < 0 or amount > MAX_AMOUNT:
        raise ValueError(f"Amount {value} is outside 0..{MAX_AMOUNT}")
    return amount


def to_minor_units(amount: Decimal, currency: str) -> int:
    if currency.upper() in ZERO_DECIMAL:
        return int(amount.quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return int((amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
