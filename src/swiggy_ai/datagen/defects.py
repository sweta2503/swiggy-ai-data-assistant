"""Defects that are about WHICH FILE a row lands in: late arrivals and duplicates."""
from dataclasses import dataclass

import numpy as np


@dataclass
class FilePlacement:
    file_day: np.ndarray  # per order: UTC day offset of the file it arrives in
    late_idx: np.ndarray
    late_shift: np.ndarray
    dup_idx: np.ndarray  # orders that get a second copy
    dup_day: np.ndarray  # file the copy lands in
    dup_same_file: np.ndarray  # bool


def place_in_files(rng: np.random.Generator, natural_day: np.ndarray,
                   last_day: int) -> FilePlacement:
    """2% of orders arrive 1-2 days late; 1.5% get duplicated (70% same file, 30% next day)."""
    n = len(natural_day)
    file_day = natural_day.copy()

    late = rng.choice(n, round(0.02 * n), replace=False)
    shift = rng.choice([1, 2], len(late), p=[0.6, 0.4])
    ok = file_day[late] + shift <= last_day  # cannot arrive after the data ends
    late, shift = late[ok], shift[ok]
    order = np.argsort(late)
    late, shift = late[order], shift[order]
    file_day[late] += shift

    dup = np.sort(rng.choice(n, round(0.015 * n), replace=False))
    same = rng.random(len(dup)) < 0.70
    dup_day = np.where(same, file_day[dup], file_day[dup] + 1)
    ok = dup_day <= last_day
    return FilePlacement(file_day, late, shift, dup[ok], dup_day[ok], same[ok])
