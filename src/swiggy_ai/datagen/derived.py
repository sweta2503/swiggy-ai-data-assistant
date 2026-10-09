"""Payments, refunds and ratings derived from orders, plus time-formatting helpers."""
from dataclasses import dataclass

import numpy as np

from swiggy_ai.datagen import reference as ref
from swiggy_ai.datagen.config import GenConfig
from swiggy_ai.datagen.orders import (
    CANCELLED,
    DAY_S,
    DELIVERED,
    FAILED,
    IST_OFFSET_S,
    REFUNDED,
    Orders,
)


def ist_rel(o: Orders) -> np.ndarray:
    """Order time as IST seconds since the first midnight of the window."""
    return o.day * DAY_S + o.t_sec


def fmt(cfg: GenConfig, rel_ist_sec: np.ndarray, *, utc: bool) -> list[str]:
    """IST-relative seconds -> text. UTC gets a trailing Z; IST stays naive (no zone marker)."""
    base = np.datetime64(f"{cfg.start_date.isoformat()}T00:00:00")
    shifted = rel_ist_sec - (IST_OFFSET_S if utc else 0)
    text = np.datetime_as_string(base + shifted.astype("timedelta64[s]"), unit="s")
    return (np.char.add(text, "Z") if utc else np.char.replace(text, "T", " ")).tolist()


def utc_day(rel_ist_sec: np.ndarray) -> np.ndarray:
    """UTC calendar-day offset (can be -1 for the first hours of the window)."""
    return (rel_ist_sec - IST_OFFSET_S) // DAY_S


@dataclass
class Payments:
    ids: list[str]
    method: list[str]
    status: list[str]
    amount: np.ndarray
    ist_sec: np.ndarray  # IST-relative
    day: np.ndarray  # IST day offset == file day


@dataclass
class Refunds:
    order_idx: np.ndarray
    amount: np.ndarray
    utc_sec_ist_rel: np.ndarray  # IST-relative seconds (format with utc=True)
    reason: list[str]


@dataclass
class Ratings:
    order_idx: np.ndarray
    restaurant_rating: np.ndarray
    delivery_rating: np.ndarray
    rel_sec: np.ndarray


def order_total(o: Orders) -> np.ndarray:
    return np.round(o.order_value - o.discount + o.delivery_fee + o.taxes, 2)


def delivered_rel(o: Orders) -> np.ndarray:
    return ist_rel(o) + np.nan_to_num(o.true_min) * 60


def make_payments(cfg: GenConfig, rng: np.random.Generator, o: Orders) -> Payments:
    # Stay on the order's own IST date so the gateway file date == order IST date
    pay_t = np.minimum(o.t_sec + rng.integers(5, 91, o.n), DAY_S - 1)
    methods, probs = zip(*ref.PAYMENT_METHODS, strict=True)
    status = np.select([o.status == CANCELLED, o.status == FAILED], ["voided", "failed"], "success")
    return Payments(
        ids=[f"PAY{i + 1:08d}" for i in range(o.n)],
        method=rng.choice(methods, o.n, p=probs).tolist(), status=status.tolist(),
        amount=order_total(o), ist_sec=o.day * DAY_S + pay_t, day=o.day,
    )


def make_refunds(cfg: GenConfig, rng: np.random.Generator, o: Orders) -> Refunds:
    idx = np.nonzero(o.status == REFUNDED)[0]
    end_rel = (cfg.days - 1) * DAY_S + DAY_S - 1
    when = np.minimum(delivered_rel(o)[idx] + rng.uniform(600, 36 * 3600, len(idx)), end_rel)
    full = rng.random(len(idx)) < 0.70
    amount = np.round(order_total(o)[idx] * np.where(full, 1.0, rng.uniform(0.4, 0.9, len(idx))), 2)
    return Refunds(idx, amount, when.astype(np.int64), [o.refund_reason[i] for i in idx])


def make_ratings(cfg: GenConfig, rng: np.random.Generator, o: Orders) -> Ratings:
    delivered_like = (o.status == DELIVERED) | (o.status == REFUNDED)
    idx = np.nonzero(delivered_like & (rng.random(o.n) < 0.40))[0]
    mean = np.where(o.status[idx] == REFUNDED, 2.1, 4.1)
    end_rel = (cfg.days - 1) * DAY_S + DAY_S - 1

    def stars() -> np.ndarray:
        return np.clip(np.round(rng.normal(mean, 0.9)), 1, 5).astype(int)

    when = np.minimum(delivered_rel(o)[idx] + rng.uniform(300, 10_800, len(idx)), end_rel)
    return Ratings(idx, stars(), stars(), when.astype(np.int64))
