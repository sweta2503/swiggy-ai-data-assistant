"""Knobs for the generator. Defaults match the video; tests use smaller numbers."""
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path


def _yesterday() -> date:
    return date.today() - timedelta(days=1)


@dataclass(frozen=True)
class GenConfig:
    seed: int = 42
    end_date: date = field(default_factory=_yesterday)  # last day of data (IST)
    days: int = 365
    n_customers: int = 25_000
    n_restaurants: int = 1_800
    n_partners: int = 3_500
    n_orders: int = 150_000
    menu_per_restaurant: int = 40
    out_dir: Path = Path("data/raw")

    @property
    def start_date(self) -> date:
        return self.end_date - timedelta(days=self.days - 1)

    def day(self, offset: int) -> date:
        return self.start_date + timedelta(days=int(offset))
