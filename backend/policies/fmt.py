"""Human-readable amounts and addresses for finding texts."""

from decimal import Decimal
from fractions import Fraction


def over_pct(part: int, whole: int, limit_pct: float) -> bool:
    """Exact `part > limit_pct % of whole` (floats lose precision at wei scale)."""
    return Fraction(part) * 100 > Fraction(str(limit_pct)) * whole


def short(addr: str) -> str:
    return f"{addr[:6]}..{addr[-4:]}" if addr and len(addr) == 42 else str(addr)


def amount(asset: str, value: int) -> str:
    units = Decimal(value) / Decimal(10**18)
    text = f"{units:,.6f}".rstrip("0").rstrip(".") if value else "0"
    return f"{text} ETH" if asset == "native" else f"{text} tokens of {short(asset)}"


def pct(part: int, whole: int) -> str:
    return f"{Decimal(part) * 100 / Decimal(whole):.1f}%" if whole else "100%"
