"""Phase 1: the generator is deterministic and every promised defect is really in the files.

Uses a smaller dataset than `make data` so the whole file runs in seconds.
"""
import csv
import hashlib
import json
import re
from datetime import date
from pathlib import Path

import pytest

from swiggy_ai.datagen.config import GenConfig
from swiggy_ai.datagen.generate import generate

END = date(2026, 9, 30)
SMALL = dict(end_date=END, n_orders=8_000, n_customers=1_500, n_restaurants=150, n_partners=200)


def tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(root)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def read_csv(pattern: str, root: Path) -> list[dict]:
    rows = []
    for f in sorted(root.glob(pattern)):
        with f.open(newline="") as fh:
            rows += list(csv.DictReader(fh))
    return rows


def read_orders(root: Path) -> list[dict]:
    rows = []
    for f in sorted((root / "orders").glob("*.jsonl")):
        rows += [{**json.loads(line), "_file": f.name} for line in f.read_text().splitlines()]
    return rows


@pytest.fixture(scope="module")
def raw(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("raw")
    generate(GenConfig(seed=42, out_dir=out, **SMALL))
    return out


@pytest.fixture(scope="module")
def defects(raw) -> dict:
    return json.loads((raw / "_defects.json").read_text())


@pytest.fixture(scope="module")
def orders(raw) -> list[dict]:
    return read_orders(raw)


# --- determinism ----------------------------------------------------------------------------
def test_same_seed_gives_identical_files(raw, tmp_path):
    generate(GenConfig(seed=42, out_dir=tmp_path, **SMALL))
    assert tree_hash(tmp_path) == tree_hash(raw)


def test_different_seed_gives_different_files(raw, tmp_path):
    generate(GenConfig(seed=7, out_dir=tmp_path, **SMALL))
    assert tree_hash(tmp_path) != tree_hash(raw)


def test_rerun_into_same_folder_is_idempotent(tmp_path):
    cfg = GenConfig(seed=42, out_dir=tmp_path, **SMALL)
    generate(cfg)
    first = tree_hash(tmp_path)
    generate(cfg)  # must wipe its own old files, not pile up
    assert tree_hash(tmp_path) == first


# --- volumes --------------------------------------------------------------------------------
def test_row_counts_are_in_range(raw, orders):
    unique = {o["order_id"] for o in orders}
    assert len(unique) == SMALL["n_orders"]
    items = read_csv("order_items/*.csv", raw)
    assert 2.8 <= len(items) / len(unique) <= 3.6  # about 3.2 items per order
    assert len(read_csv("payments/*.csv", raw)) == len(unique)  # one payment per order
    assert len(read_csv("restaurants.csv", raw)) == SMALL["n_restaurants"]
    assert len(read_csv("delivery_partners.csv", raw)) == SMALL["n_partners"]
    assert len(read_csv("menu_items.csv", raw)) / SMALL["n_restaurants"] > 30


def test_status_mix_and_refund_rate(raw, orders):
    canon = {"dlvrd": "delivered", "canceled": "cancelled"}
    status = {o["order_id"]: canon.get(o["status"].lower(), o["status"].lower()) for o in orders}
    n = len(status)
    delivered = sum(s == "delivered" for s in status.values())
    cancelled = sum(s == "cancelled" for s in status.values())
    assert 0.85 <= delivered / n <= 0.91
    assert 0.06 <= cancelled / n <= 0.10
    refunds = read_csv("refunds/*.csv", raw)
    assert 0.025 <= len(refunds) / delivered <= 0.06  # about 4% of delivered orders


def test_all_six_cities_present(orders):
    cities = {o["city"] for o in orders}
    assert cities == {"Bengaluru", "Mumbai", "Delhi NCR", "Hyderabad", "Chennai", "Pune"}


# --- defects: each one is in the files AND the recorded count is true ------------------------
def test_defect_1_duplicates(orders, defects):
    ids = [o["order_id"] for o in orders]
    extra = len(ids) - len(set(ids))
    assert extra > 0 and extra == defects["duplicate_orders"]["extra_rows"]
    assert 0.01 <= extra / SMALL["n_orders"] <= 0.02
    assert defects["duplicate_orders"]["next_day_file"] > 0  # some copies are in a second file


def test_defect_2_late_arrivals(orders, defects):
    first_file: dict[str, str] = {}
    for o in orders:  # files are read in date order, so the first sighting is the earliest
        first_file.setdefault(o["order_id"], o["_file"][7:17])
    placed = {o["order_id"]: o["order_ts"][:10] for o in orders}
    late = [i for i, f in first_file.items() if f > placed[i]]
    assert len(late) == defects["late_arriving_orders"]["orders"] > 0


def test_defect_3_status_spellings(orders, defects):
    clean = {"delivered", "cancelled", "refunded", "failed"}
    unique = {o["order_id"]: o["status"] for o in orders}  # counts are per unique order
    messy = [s for s in unique.values() if s not in clean]
    assert len(messy) == defects["status_spelling_variants"]["rows_not_in_clean_form"] > 0
    assert {"DLVRD", "DELIVERED", "Delivered", "CANCELED"} <= {o["status"] for o in orders}
    mapping = defects["status_spelling_variants"]["mapping_to_canonical"]
    assert {o["status"] for o in orders} <= set(mapping)


def test_defect_4_mixed_time_zones(raw, orders):
    assert all(o["order_ts"].endswith("Z") for o in orders)
    pay = read_csv("payments/*.csv", raw)
    assert all(re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d", p["payment_ts"]) for p in pay)
    # payment is 5-90s after the order, but only once IST is converted to UTC
    from datetime import datetime, timedelta
    by_id = {o["order_id"]: o for o in orders}
    for p in pay[:200]:
        order_utc = datetime.strptime(by_id[p["order_id"]]["order_ts"], "%Y-%m-%dT%H:%M:%SZ")
        pay_utc = datetime.strptime(p["payment_ts"], "%Y-%m-%d %H:%M:%S") - timedelta(minutes=330)
        assert 0 <= (pay_utc - order_utc).total_seconds() <= 91


def test_defect_5_nulls(raw, defects):
    first_day = read_csv("customers/customers_snapshot_*.csv", raw)
    blank_city = {r["customer_id"] for r in first_day if r["city"] == ""}
    assert blank_city == set(defects["null_city_customers"]["customer_ids"])
    assert len(blank_city) > 0
    restaurants = read_csv("restaurants.csv", raw)
    blank_cuisine = [r["restaurant_id"] for r in restaurants if not r["cuisine"]]
    assert sorted(blank_cuisine) == sorted(defects["blank_cuisine_restaurants"]["restaurant_ids"])
    assert blank_cuisine


def test_defect_6_delivery_minute_outliers(orders, defects):
    bad = {o["order_id"] for o in orders
           if o["delivery_minutes"] is not None and not 1 <= o["delivery_minutes"] <= 150}
    assert bad == set(defects["delivery_minutes_outliers"]["order_ids"]) and bad
    assert any(o["delivery_minutes"] < 0 for o in orders if o["delivery_minutes"] is not None)


def test_defect_7_orphan_items(raw, orders, defects):
    known = {o["order_id"] for o in orders}
    items = read_csv("order_items/*.csv", raw)
    orphans = {i["order_id"] for i in items if i["order_id"] not in known}
    assert orphans == set(defects["orphan_order_items"]["order_ids"]) and orphans


def test_defect_8_scd2_changes_visible_in_snapshots(raw, defects):
    history: dict[str, list[tuple[str, str]]] = {}
    for r in read_csv("customers/customers_snapshot_*.csv", raw):
        history.setdefault(r["customer_id"], []).append((r["city"], r["segment"]))
    city_changed = {c for c, h in history.items() if len({x[0] for x in h if x[0]}) > 1}
    seg_changed = {c for c, h in history.items() if len({x[1] for x in h}) > 1}
    scd2 = defects["scd2_changes"]
    assert city_changed == set(scd2["city_change_customer_ids"]) and city_changed
    assert seg_changed == set(scd2["segment_change_customer_ids"]) and seg_changed
    assert 0.02 <= len(city_changed) / SMALL["n_customers"] <= 0.04
    assert 0.06 <= len(seg_changed) / SMALL["n_customers"] <= 0.10


def test_defect_9_pii_present_in_raw(raw):
    rows = read_csv("customers/customers_snapshot_*.csv", raw)
    assert all(re.fullmatch(r"[6-9]\d{9}", r["phone"]) for r in rows)
    assert all("@" in r["email"] for r in rows)
    assert all(r["address_line"] for r in rows)


# --- the story in the data ---------------------------------------------------------------------
def test_refund_spike_ground_truth_is_recorded(raw):
    meta = json.loads((raw / "_meta.json").read_text())
    spike = meta["refund_spike"]
    assert spike["city"] == "Mumbai" and spike["month"] == "2026-09"  # last full month before END
    assert len(spike["restaurant_ids"]) >= 5
    assert meta["fastest_growing_cuisine"] == "Healthy"
    assert len(meta["festival_dates"]) == 2


def test_customers_only_order_after_signing_up(raw, orders):
    signup = {r["customer_id"]: r["signup_date"] for r in read_csv("customers/*.csv", raw)}
    assert all(o["order_ts"][:10] >= signup[o["customer_id"]] for o in orders)
