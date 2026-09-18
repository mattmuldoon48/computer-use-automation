"""Hand-authored synthetic test fixtures, never discovered or production data."""

from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class Member:
    member_id: str
    display_name: str
    eligible_products: tuple[str, ...]


@dataclass(frozen=True)
class Product:
    code: str
    name: str
    monthly_fee_minor: int
    currency: str = "USD"

    @property
    def displayed_fee(self) -> str:
        return f"{self.monthly_fee_minor // 100}.{self.monthly_fee_minor % 100:02d}"


PRODUCTS = MappingProxyType(
    {
        "SAVINGS_BASIC": Product("SAVINGS_BASIC", "Basic savings", 250),
        "SAVINGS_PLUS": Product("SAVINGS_PLUS", "Plus savings", 700),
    }
)
MEMBERS = MappingProxyType(
    {
        "000042": Member("000042", "Avery Example", tuple(PRODUCTS)),
        "000099": Member("000099", "Jordan Sample", tuple(PRODUCTS)),
        "000777": Member("000777", "Taylor Fixture", ("SAVINGS_BASIC",)),
    }
)
SCENARIOS = frozenset(
    {
        "happy",
        "member_not_found",
        "product_ineligible",
        "validation_rejected",
        "slow_load",
        "known_interstitial",
        "permission_denied",
        "session_expired",
        "unknown_dialog",
        "duplicate_target",
        "wrong_member_review",
    }
)
