"""Orchestrates the generator: build everything in memory, then write the source files."""
import csv
import json
import shutil
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from faker import Faker

from swiggy_ai.datagen import derived as dv
from swiggy_ai.datagen import reference as ref
from swiggy_ai.datagen.config import GenConfig
from swiggy_ai.datagen.defects import FilePlacement, place_in_files
from swiggy_ai.datagen.entities import (
    Customers,
    Menu,
    Partners,
    Restaurants,
    make_customers,
    make_menu,
    make_partners,
    make_restaurants,
)
from swiggy_ai.datagen.orders import (
    DELIVERED,
    REFUNDED,
    Items,
    Orders,
    Plan,
    generate_orders,
    make_plan,
)

# Everything the generator owns inside out_dir, so a re-run can wipe it cleanly.
OWNED = ["orders", "payments", "customers", "order_items", "refunds", "ratings",
         "restaurants.csv", "delivery_partners.csv", "menu_items.csv",
         "_defects.json", "_meta.json"]


def _clean(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for name in OWNED:
        target = out / name
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()


def _write_csv(path: Path, header: list[str], rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def _by_day(days: np.ndarray) -> dict[int, np.ndarray]:
    """Group row positions by day value, keeping original order inside each group."""
    order = np.argsort(days, kind="stable")
    uniq, start = np.unique(days[order], return_index=True)
    bounds = list(start[1:]) + [len(order)]
    return {int(d): order[s:e] for d, s, e in zip(uniq, start, bounds, strict=True)}


def _dated(cfg: GenConfig, folder: str, prefix: str, day: int, ext: str) -> Path:
    return cfg.out_dir / folder / f"{prefix}_{cfg.day(day).isoformat()}.{ext}"


# ---------------------------------------------------------------- reference tables ----------
def write_reference_tables(cfg: GenConfig, rest: Restaurants, part: Partners, menu: Menu) -> None:
    out = cfg.out_dir
    _write_csv(
        out / "restaurants.csv",
        ["restaurant_id", "name", "cuisine", "city", "rating", "onboarded_date", "is_active"],
        ([rest.ids[i], rest.name[i],
          "" if rest.blank_cuisine[i] else ref.CUISINE_NAMES[rest.cuisine[i]],
          ref.CITY_NAMES[rest.city[i]], float(rest.rating[i]),
          cfg.day(rest.onboard_off[i]).isoformat(), str(bool(rest.is_active[i])).lower()]
         for i in range(rest.n)))
    _write_csv(
        out / "delivery_partners.csv",
        ["partner_id", "name", "phone", "city", "joined_date"],
        ([part.ids[i], part.name[i], part.phone[i], ref.CITY_NAMES[part.city[i]],
          cfg.day(part.joined_off[i]).isoformat()] for i in range(part.n)))
    _write_csv(
        out / "menu_items.csv",
        ["item_id", "restaurant_id", "item_name", "category", "price"],
        ([menu.ids[i], rest.ids[menu.restaurant[i]], menu.name[i], menu.category[i],
          float(menu.price[i])] for i in range(menu.n)))


def write_customer_snapshots(cfg: GenConfig, c: Customers) -> dict:
    """Daily snapshot files hold customers who are new or changed that day (plus everyone on
    day 0). That keeps 365 files small while still giving SCD2 every change with its date."""
    events: dict[int, set[int]] = defaultdict(set)
    for i in range(c.n):
        events[max(int(c.signup_off[i]), 0)].add(i)
        for change in (int(c.city_change_day[i]), int(c.seg_change_day[i])):
            if change < cfg.days:
                events[change].add(i)

    header = ["snapshot_date", "customer_id", "name", "phone", "email", "address_line",
              "pincode", "city", "segment", "signup_date"]
    for day, members in events.items():
        rows = []
        for i in sorted(members):
            moved = day >= c.city_change_day[i]
            city = c.city_after[i] if moved else c.city_before[i]
            seg = c.seg_after[i] if day >= c.seg_change_day[i] else c.seg_before[i]
            rows.append([
                cfg.day(day).isoformat(), c.ids[i], c.name[i], c.phone[i], c.email[i],
                c.address_after[i] if moved else c.address_before[i],
                c.pincode_after[i] if moved else c.pincode_before[i],
                "" if c.null_city[i] else ref.CITY_NAMES[city], ref.SEGMENTS[seg],
                cfg.day(c.signup_off[i]).isoformat()])
        _write_csv(_dated(cfg, "customers", "customers_snapshot", day, "csv"), header, rows)


# ---------------------------------------------------------------- transactional files -------
def write_orders(cfg: GenConfig, o: Orders, rest: Restaurants, c: Customers, part: Partners,
                 place: FilePlacement) -> None:
    placed_ist = dv.ist_rel(o)
    order_ts = dv.fmt(cfg, placed_ist, utc=True)
    delivered_ts = dv.fmt(cfg, dv.delivered_rel(o).astype(np.int64), utc=True)
    has_delivery = (o.status == DELIVERED) | (o.status == REFUNDED)

    def record(i: int) -> str:
        m = o.delivery_min[i]
        return json.dumps({
            "order_id": f"ORD{i + 1:08d}", "customer_id": c.ids[o.cust[i]],
            "restaurant_id": rest.ids[o.rest[i]],
            "delivery_partner_id": None if o.part[i] < 0 else part.ids[o.part[i]],
            "city": ref.CITY_NAMES[rest.city[o.rest[i]]], "status": o.status_text[i],
            "platform": o.platform[i], "order_ts": order_ts[i],
            "delivered_ts": delivered_ts[i] if has_delivery[i] else None,
            "delivery_minutes": None if np.isnan(m) else int(m),
            "order_value": float(o.order_value[i]), "discount": float(o.discount[i]),
            "delivery_fee": float(o.delivery_fee[i]), "taxes": float(o.taxes[i]),
        }, separators=(",", ":"))

    originals = _by_day(place.file_day)
    copies = _by_day(place.dup_day) if len(place.dup_day) else {}
    for day in sorted(set(originals) | set(copies)):
        lines = [record(i) for i in originals.get(day, [])]
        lines += [record(place.dup_idx[j]) for j in copies.get(day, [])]  # copies go last
        path = _dated(cfg, "orders", "orders", day, "jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n")


def write_items(cfg: GenConfig, rng: np.random.Generator, it: Items, o: Orders, menu: Menu,
                place: FilePlacement) -> dict:
    n_rows = len(it.order_idx)
    rows = [[f"OI{k + 1:08d}", f"ORD{it.order_idx[k] + 1:08d}", menu.ids[it.menu_idx[k]],
             menu.name[it.menu_idx[k]], int(it.qty[k]), float(menu.price[it.menu_idx[k]])]
            for k in range(n_rows)]
    days = place.file_day[it.order_idx]

    # Defect: orphan rows pointing at an order that does not exist
    orphan_src = rng.choice(n_rows, round(0.002 * n_rows), replace=False)
    orphan_ids = [f"ORD9{k + 1:07d}" for k in range(len(orphan_src))]
    extra_days = []
    for k, src in enumerate(orphan_src):
        row = list(rows[src])
        row[0], row[1] = f"OI{n_rows + k + 1:08d}", orphan_ids[k]
        rows.append(row)
        extra_days.append(days[src])
    days = np.concatenate([days, np.array(extra_days, dtype=days.dtype)])

    header = ["order_item_id", "order_id", "item_id", "item_name", "qty", "unit_price"]
    for day, idx in _by_day(days).items():
        _write_csv(_dated(cfg, "order_items", "order_items", day, "csv"), header,
                   (rows[k] for k in idx))
    return {"rows": len(orphan_ids), "order_ids": orphan_ids}


def write_payments(cfg: GenConfig, p: dv.Payments, o: Orders, c: Customers) -> None:
    ts = dv.fmt(cfg, p.ist_sec, utc=False)  # IST, no timezone marker
    header = ["payment_id", "order_id", "payment_ts", "method", "status", "amount"]
    for day, idx in _by_day(p.day).items():
        _write_csv(_dated(cfg, "payments", "payments", day, "csv"), header,
                   ([p.ids[i], f"ORD{i + 1:08d}", ts[i], p.method[i], p.status[i],
                     float(p.amount[i])] for i in idx))


def write_refunds(cfg: GenConfig, r: dv.Refunds) -> None:
    ts = dv.fmt(cfg, r.utc_sec_ist_rel, utc=True)
    days = dv.utc_day(r.utc_sec_ist_rel)
    header = ["refund_id", "order_id", "refund_ts", "refund_amount", "reason_code"]
    for day, idx in _by_day(days).items():
        _write_csv(_dated(cfg, "refunds", "refunds", day, "csv"), header,
                   ([f"RFD{k + 1:07d}", f"ORD{r.order_idx[k] + 1:08d}", ts[k],
                     float(r.amount[k]), r.reason[k]] for k in idx))


def write_ratings(cfg: GenConfig, r: dv.Ratings) -> None:
    ts = dv.fmt(cfg, r.rel_sec, utc=True)
    days = dv.utc_day(r.rel_sec)
    header = ["order_id", "restaurant_rating", "delivery_rating", "rated_ts"]
    for day, idx in _by_day(days).items():
        _write_csv(_dated(cfg, "ratings", "ratings", day, "csv"), header,
                   ([f"ORD{r.order_idx[k] + 1:08d}", int(r.restaurant_rating[k]),
                     int(r.delivery_rating[k]), ts[k]] for k in idx))


# ---------------------------------------------------------------- bookkeeping ---------------
def build_defects(cfg: GenConfig, o: Orders, c: Customers, rest: Restaurants,
                  place: FilePlacement, orphans: dict) -> dict:
    canonical = dict.fromkeys(ref.STATUS_CANONICAL)
    mapping = {text: name for name, spells in ref.STATUS_SPELLINGS.items() for text, _ in spells}
    non_clean = [t for t in o.status_text if t not in canonical]
    moved_city, moved_seg = c.city_change_day < cfg.days, c.seg_change_day < cfg.days
    return {
        "duplicate_orders": {
            "extra_rows": int(len(place.dup_idx)),
            "same_file": int(place.dup_same_file.sum()),
            "next_day_file": int((~place.dup_same_file).sum()),
            "order_ids": [f"ORD{i + 1:08d}" for i in place.dup_idx],
        },
        "late_arriving_orders": {
            "orders": int(len(place.late_idx)),
            "one_day_late": int((place.late_shift == 1).sum()),
            "two_days_late": int((place.late_shift == 2).sum()),
        },
        "status_spelling_variants": {
            "rows_not_in_clean_form": len(non_clean),
            "counts_by_spelling": dict(sorted(Counter(o.status_text).items())),
            "mapping_to_canonical": mapping,
        },
        "mixed_time_zones": {
            "orders": "UTC, ISO-8601 with trailing Z",
            "payments": "IST (UTC+05:30), naive 'YYYY-MM-DD HH:MM:SS'",
            "refunds_and_ratings": "UTC, ISO-8601 with trailing Z",
            "ist_offset_minutes": 330,
        },
        "null_city_customers": {
            "customers": int(c.null_city.sum()),
            "customer_ids": [c.ids[i] for i in np.nonzero(c.null_city)[0]],
        },
        "blank_cuisine_restaurants": {
            "restaurants": int(rest.blank_cuisine.sum()),
            "restaurant_ids": [rest.ids[i] for i in np.nonzero(rest.blank_cuisine)[0]],
        },
        "delivery_minutes_outliers": {
            "rows": int(len(o.outlier_rows)),
            "order_ids": [f"ORD{i + 1:08d}" for i in o.outlier_rows],
            "valid_range_minutes": [1, 150],
        },
        "orphan_order_items": {"rows": orphans["rows"], "order_ids": orphans["order_ids"]},
        "scd2_changes": {
            "customers_changing_city": int(moved_city.sum()),
            "customers_changing_segment": int(moved_seg.sum()),
            "city_change_customer_ids": [c.ids[i] for i in np.nonzero(moved_city)[0]],
            "segment_change_customer_ids": [c.ids[i] for i in np.nonzero(moved_seg)[0]],
        },
        "pii_in_raw_files": {
            "customers/*.csv": ["name", "phone", "email", "address_line", "pincode"],
            "delivery_partners.csv": ["name", "phone"],
        },
    }


def build_meta(cfg: GenConfig, o: Orders, rest: Restaurants, plan: Plan, place: FilePlacement,
               n_item_rows: int) -> dict:
    return {
        "seed": cfg.seed, "start_date": cfg.start_date.isoformat(),
        "end_date": cfg.end_date.isoformat(), "days": cfg.days,
        "unique_orders": o.n, "order_rows_in_files": o.n + int(len(place.dup_idx)),
        "item_rows_in_files": n_item_rows,
        "status_counts": {name: int((o.status == k).sum())
                          for k, name in enumerate(ref.STATUS_CANONICAL)},
        "festival_dates": [cfg.day(d).isoformat() for d in plan.festival_days],
        "monsoon_window_mumbai": "06-20 to 09-10 (+12 min average delivery time)",
        "fastest_growing_cuisine": ref.FAST_GROWING_CUISINE,
        "refund_spike": {
            "city": "Mumbai", "month": plan.spike_month, "reason_code": ref.SPIKE_REASON,
            "restaurant_ids": [rest.ids[i] for i in plan.cluster],
            "note": "Ground truth for 'why did refunds go up in Mumbai?'. Used by tests/eval only.",
        },
    }


def generate(cfg: GenConfig) -> dict:
    """Generate all source files into cfg.out_dir. Same config -> byte-identical output."""
    started = time.time()
    rng = np.random.default_rng(cfg.seed)
    fake = Faker("en_IN")
    fake.seed_instance(cfg.seed)

    cust = make_customers(cfg, rng, fake)
    rest = make_restaurants(cfg, rng, fake)
    part = make_partners(cfg, rng, fake)
    menu = make_menu(cfg, rng, rest)
    plan = make_plan(cfg, rest, rng)
    orders, items = generate_orders(cfg, rng, cust, rest, part, menu, plan)
    pay = dv.make_payments(cfg, rng, orders)
    refunds = dv.make_refunds(cfg, rng, orders)
    ratings = dv.make_ratings(cfg, rng, orders)
    place = place_in_files(rng, dv.utc_day(dv.ist_rel(orders)), cfg.days - 1)

    _clean(cfg.out_dir)
    write_reference_tables(cfg, rest, part, menu)
    write_customer_snapshots(cfg, cust)
    write_orders(cfg, orders, rest, cust, part, place)
    orphans = write_items(cfg, rng, items, orders, menu, place)
    write_payments(cfg, pay, orders, cust)
    write_refunds(cfg, refunds)
    write_ratings(cfg, ratings)

    defects = build_defects(cfg, orders, cust, rest, place, orphans)
    meta = build_meta(cfg, orders, rest, plan, place, len(items.order_idx) + orphans["rows"])
    (cfg.out_dir / "_defects.json").write_text(json.dumps(defects, indent=1))
    (cfg.out_dir / "_meta.json").write_text(json.dumps(meta, indent=1))
    meta["seconds"] = round(time.time() - started, 1)
    return meta
