"""Orders, line items, payments, refunds and ratings, built as numpy arrays.

All timestamps here are IST seconds-since-window-start. Converting to UTC (orders) or leaving
IST-naive (payments) happens at write time so the time-zone defect is easy to see.
"""
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

from swiggy_ai.datagen import reference as ref
from swiggy_ai.datagen.config import GenConfig
from swiggy_ai.datagen.entities import Customers, Menu, Partners, Restaurants

DELIVERED, CANCELLED, REFUNDED, FAILED = 0, 1, 2, 3
IST_OFFSET_S = 5 * 3600 + 1800
DAY_S = 86_400


@dataclass
class Plan:
    """Facts about the story baked into the data. Saved to _meta.json for tests and the eval."""
    festival_days: list[int]
    monsoon_days: np.ndarray  # bool per day offset
    spike_days: np.ndarray  # bool per day offset (the most recent full calendar month)
    spike_month: str
    cluster: np.ndarray  # restaurant indices behind the Mumbai refund spike


@dataclass
class Orders:
    n: int
    day: np.ndarray  # IST day offset
    t_sec: np.ndarray  # IST seconds since midnight
    cust: np.ndarray
    rest: np.ndarray
    part: np.ndarray  # -1 = never assigned
    status: np.ndarray  # DELIVERED / CANCELLED / REFUNDED / FAILED
    status_text: list[str]  # the (messy) spelling the app sent
    delivery_min: np.ndarray  # float with NaN when not delivered; outliers already applied
    true_min: np.ndarray  # the same, before outliers were injected
    outlier_rows: np.ndarray  # indices where delivery_min was corrupted
    order_value: np.ndarray
    discount: np.ndarray
    delivery_fee: np.ndarray
    taxes: np.ndarray
    n_items: np.ndarray
    platform: list[str]
    refund_reason: list[str]  # "" unless status == REFUNDED


@dataclass
class Items:
    order_idx: np.ndarray
    menu_idx: np.ndarray
    qty: np.ndarray


def make_plan(cfg: GenConfig, rest: Restaurants, rng: np.random.Generator) -> Plan:
    days = cfg.days
    festival = [int(days * 0.38), int(days * 0.72)]

    monsoon = np.zeros(days, dtype=bool)
    spike = np.zeros(days, dtype=bool)
    for d in range(days):
        cal = cfg.day(d)
        monsoon[d] = (cal.month, cal.day) >= (6, 20) and (cal.month, cal.day) <= (9, 10)

    # Most recent FULL calendar month inside the window
    end = cfg.end_date
    last_of_month = (end + timedelta(days=1)).day == 1
    first_of_end_month = date(end.year, end.month, 1)
    month_start = (first_of_end_month if last_of_month
                   else (first_of_end_month - timedelta(days=1)).replace(day=1))
    for d in range(days):
        cal = cfg.day(d)
        spike[d] = (cal.year, cal.month) == (month_start.year, month_start.month)

    # 12 popular, long-standing, still-open Mumbai restaurants
    mumbai = ref.MUMBAI
    pool = [r for r in rest.by_city[mumbai]
            if rest.onboard_off[r] < 0 and rest.until_off[r] >= days]
    pool.sort(key=lambda r: -rest.weight[r])
    top = pool[: max(15, len(pool) // 3)]
    cluster = np.sort(rng.choice(top, min(12, len(top)), replace=False))
    return Plan(festival, monsoon, spike, month_start.strftime("%Y-%m"), cluster)


def _day_volumes(cfg: GenConfig, plan: Plan, rng: np.random.Generator) -> np.ndarray:
    d = np.arange(cfg.days)
    growth = 0.75 + 0.5 * d / max(cfg.days - 1, 1)  # steady growth over the year
    dow = np.array([ref.DAY_OF_WEEK_LIFT[cfg.day(i).weekday()] for i in d])
    festival = np.ones(cfg.days)
    festival[plan.festival_days] = 1.9
    w = growth * dow * festival
    return rng.multinomial(cfg.n_orders, w / w.sum())


def _pick_spelling(rng: np.random.Generator, status: np.ndarray) -> list[str]:
    out = np.empty(len(status), dtype=object)
    for code, name in enumerate(ref.STATUS_CANONICAL):
        rows = np.nonzero(status == code)[0]
        texts, probs = zip(*ref.STATUS_SPELLINGS[name], strict=True)
        out[rows] = rng.choice(texts, len(rows), p=probs)
    return out.tolist()


def generate_orders(cfg: GenConfig, rng: np.random.Generator, cust: Customers,
                    rest: Restaurants, part: Partners, menu: Menu,
                    plan: Plan) -> tuple[Orders, Items]:
    days = cfg.days
    per_day = _day_volumes(cfg, plan, rng)
    n_signed = np.searchsorted(cust.signup_off, np.arange(days), side="right")
    hour_p = np.array(ref.HOUR_WEIGHTS, dtype=float)
    hour_p /= hour_p.sum()
    fast = ref.CUISINE_NAMES.index(ref.FAST_GROWING_CUISINE)
    is_fast = rest.cuisine == fast

    d_all, t_all, c_all, r_all, p_all = [], [], [], [], []
    for d in range(days):
        n = int(per_day[d])
        if n == 0:
            continue
        t = np.sort(rng.choice(24, n, p=hour_p) * 3600 + rng.integers(0, 3600, n))
        # Older customers order more: index skews toward early signups
        c = (n_signed[d] * rng.random(n) ** 1.25).astype(np.int64)
        city = cust.true_city_on(c, d)
        r = np.empty(n, dtype=np.int64)
        p = np.empty(n, dtype=np.int64)
        for k in range(len(ref.CITY_NAMES)):
            sel = np.nonzero(city == k)[0]
            if sel.size == 0:
                continue
            open_now = rest.by_city[k][(rest.onboard_off[rest.by_city[k]] <= d)
                                       & (rest.until_off[rest.by_city[k]] > d)]
            w = rest.weight[open_now] * (1 + 2.5 * (d / days) * is_fast[open_now])
            r[sel] = open_now[rng.choice(open_now.size, sel.size, p=w / w.sum())]
            free = part.by_city[k][part.joined_off[part.by_city[k]] <= d]
            p[sel] = free[rng.integers(0, free.size, sel.size)]
        for bucket, values in zip((d_all, t_all, c_all, r_all, p_all),
                                  (np.full(n, d), t, c, r, p), strict=True):
            bucket.append(values)

    day, t_sec, cu, re_, pa = (np.concatenate(x) for x in (d_all, t_all, c_all, r_all, p_all))
    n = len(day)

    # --- status (the spike: cluster restaurants in the spike month refund far more) --------
    u = rng.random(n)
    status = np.select([u < 0.88, u < 0.96, u < 0.995], [DELIVERED, CANCELLED, REFUNDED], FAILED)
    in_spike = np.isin(re_, plan.cluster) & plan.spike_days[day]
    flip = in_spike & (status == DELIVERED) & (rng.random(n) < 0.45)
    status[flip] = REFUNDED
    part_assigned = np.where(
        (status == FAILED) | ((status == CANCELLED) & (rng.random(n) < 0.6)), -1, pa)

    reasons = np.array([r for r, _ in ref.REFUND_REASONS])
    reason_p = np.array([p for _, p in ref.REFUND_REASONS])
    reason = rng.choice(reasons, n, p=reason_p)
    spoiled = in_spike & (rng.random(n) < 0.85)
    reason = np.where(spoiled, ref.SPIKE_REASON, reason)
    reason = np.where(status == REFUNDED, reason, "")

    # --- line items and money -----------------------------------------------------------------
    n_items = np.clip(1 + rng.poisson(2.2, n), 1, 8)
    order_idx = np.repeat(np.arange(n), n_items)
    menu_idx = menu.start[re_[order_idx]] + (rng.random(len(order_idx))
                                              * menu.count[re_[order_idx]]).astype(int)
    qty = 1 + (rng.random(len(order_idx)) < 0.22) + (rng.random(len(order_idx)) < 0.05)
    value = np.round(np.bincount(order_idx, weights=qty * menu.price[menu_idx], minlength=n), 2)

    has_discount = rng.random(n) < 0.38
    discount = np.where(has_discount, np.minimum(value * rng.uniform(0.10, 0.40, n), 200), 0)
    discount = np.round(discount, 2)
    fee = np.where(value >= 499, 0.0, rng.choice([25.0, 35.0, 45.0], n))
    taxes = np.round(0.05 * (value - discount), 2)

    # --- delivery minutes -----------------------------------------------------------------------
    base = np.array(ref.CITY_BASE_MINUTES)[rest.city[re_]].astype(float)
    hour = t_sec // 3600
    mean = (base + 4 * np.isin(hour, list(ref.PEAK_HOURS))
            + 2 * (np.array([cfg.day(i).weekday() for i in range(days)])[day] >= 5)
            + 12 * ((rest.city[re_] == ref.MUMBAI) & plan.monsoon_days[day]))
    minutes = np.clip(rng.gamma(6.0, mean / 6.0), 12, 150).round()
    delivered_like = (status == DELIVERED) | (status == REFUNDED)
    minutes = np.where(delivered_like, minutes, np.nan)

    true_minutes = minutes.copy()

    # Defect: a few impossible values (negative / absurd)
    eligible = np.nonzero(delivered_like)[0]
    outliers = np.sort(rng.choice(eligible, min(round(0.003 * n), len(eligible)), replace=False))
    minutes[outliers] = rng.choice([-30.0, -5.0, -1.0, 600.0, 1440.0, 9999.0], len(outliers))

    orders = Orders(
        n=n, day=day, t_sec=t_sec, cust=cu, rest=re_, part=part_assigned, status=status,
        status_text=_pick_spelling(rng, status), delivery_min=minutes, true_min=true_minutes,
        outlier_rows=outliers,
        order_value=value, discount=discount, delivery_fee=fee, taxes=taxes, n_items=n_items,
        platform=rng.choice(["android", "ios", "web"], n, p=[0.62, 0.30, 0.08]).tolist(),
        refund_reason=reason.tolist(),
    )
    return orders, Items(order_idx=order_idx, menu_idx=menu_idx, qty=qty)
