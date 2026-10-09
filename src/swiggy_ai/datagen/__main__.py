"""`python -m swiggy_ai.datagen --seed 42` (or `make data`)."""
import argparse
from datetime import date, timedelta
from pathlib import Path

from swiggy_ai.datagen.config import GenConfig
from swiggy_ai.datagen.generate import generate


def main() -> None:
    p = argparse.ArgumentParser(description="Generate deterministic Swiggy-style sample data.")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--end-date", type=date.fromisoformat,
                   default=date.today() - timedelta(days=1), help="last day of data (YYYY-MM-DD)")
    p.add_argument("--orders", type=int, default=150_000)
    p.add_argument("--customers", type=int, default=25_000)
    p.add_argument("--restaurants", type=int, default=1_800)
    p.add_argument("--partners", type=int, default=3_500)
    p.add_argument("--out", type=Path, default=Path("data/raw"))
    a = p.parse_args()

    meta = generate(GenConfig(seed=a.seed, end_date=a.end_date, n_orders=a.orders,
                              n_customers=a.customers, n_restaurants=a.restaurants,
                              n_partners=a.partners, out_dir=a.out))
    print(f"Generated {meta['unique_orders']:,} orders ({meta['order_rows_in_files']:,} rows "
          f"incl. duplicates) for {meta['start_date']} .. {meta['end_date']} "
          f"in {meta['seconds']}s -> {a.out}")
    print(f"Status counts: {meta['status_counts']}")
    print("Injected defects recorded in", a.out / "_defects.json")


if __name__ == "__main__":
    main()
