"""What a question cost, from the tokens it used and the prices you entered in .env.

Prices are NOT built in: model prices change, so you copy them from the provider's pricing page
(US dollars per 1,000,000 tokens). If a price is missing the cost is "not computed", never a guess.
"""
from dataclasses import dataclass
from decimal import Decimal

from swiggy_ai.agents.state import Usage
from swiggy_ai.config import Settings

NOT_COMPUTED = "cost not computed (prices not set)"
TIERS = ("low", "medium", "high")


@dataclass(frozen=True)
class CostEstimate:
    usd: Decimal | None  # None means "could not be computed"
    note: str = ""

    @property
    def known(self) -> bool:
        return self.usd is not None

    def __str__(self) -> str:
        return f"${self.usd:.6f}" if self.usd is not None else NOT_COMPUTED


def price_for(settings: Settings, tier: str) -> tuple[float, float] | None:
    """(input, output) dollars per million tokens for this tier, or None if not entered."""
    pair = (getattr(settings, f"price_input_per_1m_{tier}", None),
            getattr(settings, f"price_output_per_1m_{tier}", None))
    if None in pair:
        pair = (settings.price_input_per_1m, settings.price_output_per_1m)
    return None if None in pair else (float(pair[0]), float(pair[1]))


def estimate_cost(usage: Usage, settings: Settings) -> CostEstimate:
    """Add up tokens x price for each tier that was used. A question that made no model call
    cost nothing (0), whether or not prices are set."""
    if not usage.by_tier:
        return CostEstimate(Decimal("0"))
    total = Decimal("0")
    for tier, row in usage.by_tier.items():
        price = price_for(settings, tier)
        if price is None:
            return CostEstimate(None, f"no price set for the '{tier}' model tier")
        total += (Decimal(str(price[0])) * row["tokens_in"]
                  + Decimal(str(price[1])) * row["tokens_out"]) / Decimal(1_000_000)
    return CostEstimate(total.quantize(Decimal("0.000001")))
