"""Customers, restaurants, delivery partners and menus.

Everything is stored as parallel numpy arrays (one array per column) because the order
generator needs to index them thousands of times per day.

Day numbers: "offset 0" is the first day of the window; negative offsets are before it.
"""
import re
from dataclasses import dataclass

import numpy as np
from faker import Faker

from swiggy_ai.datagen import reference as ref
from swiggy_ai.datagen.config import GenConfig

NEVER = 10**6  # an offset that is always in the future ("this change never happens")
EMAIL_DOMAINS = ["gmail.com", "yahoo.in", "outlook.com", "rediffmail.com", "hotmail.com"]


def _slug(name: str) -> str:
    return re.sub(r"[^a-z]", "", name.lower()) or "user"


def _pincodes(rng: np.random.Generator, cities: np.ndarray) -> list[str]:
    tails = rng.integers(0, 1000, len(cities))
    return [f"{ref.CITY_PIN_PREFIX[c]}{t:03d}" for c, t in zip(cities, tails, strict=True)]


@dataclass
class Customers:
    n: int
    ids: list[str]
    signup_off: np.ndarray  # sorted ascending, so index order == signup order
    city_before: np.ndarray
    city_after: np.ndarray  # == city_before when the customer never moves
    city_change_day: np.ndarray
    seg_before: np.ndarray
    seg_after: np.ndarray
    seg_change_day: np.ndarray
    null_city: np.ndarray  # bool: city is missing in the source system
    name: list[str]
    phone: list[str]
    email: list[str]
    address_before: list[str]
    address_after: list[str]
    pincode_before: list[str]
    pincode_after: list[str]

    def true_city_on(self, idx: np.ndarray, day: int) -> np.ndarray:
        """Where the customer really lives on `day` (used to pick restaurants nearby)."""
        moved = day >= self.city_change_day[idx]
        return np.where(moved, self.city_after[idx], self.city_before[idx])


def make_customers(cfg: GenConfig, rng: np.random.Generator, fake: Faker) -> Customers:
    n, days = cfg.n_customers, cfg.days

    # 30% already customers before the window; the rest sign up with growth (density ~ x)
    before = rng.random(n) < 0.30
    in_window = np.minimum(np.floor(days * np.sqrt(rng.random(n))).astype(int), days - 1)
    signup = np.where(before, -rng.integers(1, 400, n), in_window)
    signup.sort()

    city_before = rng.choice(len(ref.CITY_NAMES), n, p=ref.CITY_SHARES)
    seg_before = np.where(
        signup < 0, rng.choice([1, 2, 3], n, p=[0.55, 0.30, 0.15]), 0
    )  # new signups start as "new"

    # Defect pools. Exact counts (not coin flips) so the recorded numbers are exact.
    null_city = np.zeros(n, dtype=bool)
    null_city[rng.choice(n, round(0.005 * n), replace=False)] = True
    can_change = np.nonzero((signup < days - 45) & ~null_city)[0]  # needs room for a change

    city_after = city_before.copy()
    city_change_day = np.full(n, NEVER)
    movers = rng.choice(can_change, min(round(0.03 * n), len(can_change)), replace=False)
    for i in movers:
        others = [c for c in range(len(ref.CITY_NAMES)) if c != city_before[i]]
        city_after[i] = rng.choice(others)
    city_change_day[movers] = rng.integers(np.maximum(signup[movers], 0) + 14, days - 10)

    seg_after = seg_before.copy()
    seg_change_day = np.full(n, NEVER)
    upgraders = rng.choice(can_change, min(round(0.08 * n), len(can_change)), replace=False)
    seg_after[upgraders] = np.where(seg_before[upgraders] < 3, seg_before[upgraders] + 1, 2)
    seg_change_day[upgraders] = rng.integers(np.maximum(signup[upgraders], 0) + 14, days - 10)

    # Personal data (this is the PII the assistant must never see)
    names = [fake.name() for _ in range(n)]
    phones = [f"{p:010d}" for p in (rng.integers(6, 10, n) * 10**9 + rng.integers(0, 10**9, n))]
    emails = [
        f"{_slug(nm)}{i + 1}@{EMAIL_DOMAINS[d]}"
        for i, (nm, d) in enumerate(zip(names, rng.integers(0, len(EMAIL_DOMAINS), n), strict=True))
    ]
    addr_before = [fake.street_address() for _ in range(n)]
    addr_after = list(addr_before)
    for i in movers:  # moving house means a new address
        addr_after[i] = fake.street_address()

    return Customers(
        n=n, ids=[f"CUST{i + 1:06d}" for i in range(n)], signup_off=signup,
        city_before=city_before, city_after=city_after, city_change_day=city_change_day,
        seg_before=seg_before, seg_after=seg_after, seg_change_day=seg_change_day,
        null_city=null_city, name=names, phone=phones, email=emails,
        address_before=addr_before, address_after=addr_after,
        pincode_before=_pincodes(rng, city_before), pincode_after=_pincodes(rng, city_after),
    )


@dataclass
class Restaurants:
    n: int
    ids: list[str]
    name: list[str]
    city: np.ndarray
    cuisine: np.ndarray  # index into CUISINE_NAMES (kept even when the source field is blank)
    blank_cuisine: np.ndarray  # bool
    rating: np.ndarray
    onboard_off: np.ndarray
    is_active: np.ndarray
    until_off: np.ndarray  # last day (exclusive) it takes orders; inactive ones closed mid-window
    weight: np.ndarray  # popularity
    by_city: list[np.ndarray]  # restaurant indices per city


def make_restaurants(cfg: GenConfig, rng: np.random.Generator, fake: Faker) -> Restaurants:
    n, days = cfg.n_restaurants, cfg.days
    city = rng.choice(len(ref.CITY_NAMES), n, p=ref.CITY_SHARES)
    shares = np.array([c[0] for c in ref.CUISINES.values()])
    cuisine = rng.choice(len(ref.CUISINE_NAMES), n, p=shares / shares.sum())

    pre_window = rng.random(n) < 0.85
    onboard = np.where(pre_window, -rng.integers(30, 1500, n), rng.integers(0, days - 20, n))
    is_active = rng.random(n) < 0.92
    until = np.where(is_active, NEVER, rng.integers(days // 4, days, n))
    until = np.maximum(until, onboard + 30)

    blank = np.zeros(n, dtype=bool)
    blank[rng.choice(n, min(5, n), replace=False)] = True

    return Restaurants(
        n=n, ids=[f"REST{i + 1:05d}" for i in range(n)],
        name=[f"{fake.last_name()} {ref.RESTAURANT_SUFFIXES[s]}"
              for s in rng.integers(0, len(ref.RESTAURANT_SUFFIXES), n)],
        city=city, cuisine=cuisine, blank_cuisine=blank,
        rating=np.round(np.clip(rng.normal(4.0, 0.4, n), 2.8, 4.9), 1),
        onboard_off=onboard, is_active=is_active, until_off=until,
        weight=rng.lognormal(0, 0.9, n),
        by_city=[np.nonzero(city == c)[0] for c in range(len(ref.CITY_NAMES))],
    )


@dataclass
class Partners:
    n: int
    ids: list[str]
    name: list[str]
    phone: list[str]
    city: np.ndarray
    joined_off: np.ndarray
    by_city: list[np.ndarray]


def make_partners(cfg: GenConfig, rng: np.random.Generator, fake: Faker) -> Partners:
    n, days = cfg.n_partners, cfg.days
    city = rng.choice(len(ref.CITY_NAMES), n, p=ref.CITY_SHARES)
    joined = np.where(rng.random(n) < 0.80, -rng.integers(1, 800, n), rng.integers(0, days, n))
    return Partners(
        n=n, ids=[f"PART{i + 1:05d}" for i in range(n)],
        name=[fake.name() for _ in range(n)],
        phone=[f"{p:010d}" for p in (rng.integers(6, 10, n) * 10**9 + rng.integers(0, 10**9, n))],
        city=city, joined_off=joined,
        by_city=[np.nonzero(city == c)[0] for c in range(len(ref.CITY_NAMES))],
    )


@dataclass
class Menu:
    n: int
    ids: list[str]
    restaurant: np.ndarray
    name: list[str]
    category: list[str]
    price: np.ndarray
    start: np.ndarray  # per restaurant: first menu item index
    count: np.ndarray  # per restaurant: how many items


def make_menu(cfg: GenConfig, rng: np.random.Generator, rest: Restaurants) -> Menu:
    counts = np.clip(rng.poisson(cfg.menu_per_restaurant, rest.n), 15, 60)
    starts = np.concatenate([[0], np.cumsum(counts)[:-1]])
    r_idx, names, cats, prices = [], [], [], []
    for r in range(rest.n):
        _, base, dishes = ref.CUISINES[ref.CUISINE_NAMES[rest.cuisine[r]]]
        combos = [(d, v) for d in dishes for v in ref.VARIANTS]  # 8 dishes x 8 variants = 64
        for pick in rng.choice(len(combos), counts[r], replace=False):
            (dish, cat, dmult), (label, vmult) = combos[pick]
            r_idx.append(r)
            names.append(f"{dish} ({label})" if label else dish)
            cats.append(cat)
            noise = rng.lognormal(0, 0.15)
            prices.append(max(29.0, round(base * dmult * vmult * noise / 5) * 5))
    total = len(r_idx)
    return Menu(
        n=total, ids=[f"ITEM{i + 1:07d}" for i in range(total)], restaurant=np.array(r_idx),
        name=names, category=cats, price=np.array(prices), start=starts, count=counts,
    )
