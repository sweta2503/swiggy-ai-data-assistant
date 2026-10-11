"""Schema drift: noticing that a source file no longer looks the way it used to.

Every file type has an expected list of columns (`ops.expected_schemas`), summarised by a
fingerprint. A file is compared before it is loaded:

  same columns (any order)         -> load it
  differences the owner approved   -> load it, mapping the renamed columns   (column_aliases.yml)
  anything else                    -> quarantine it, raise an alert, load nothing

Approval is a deliberate edit to a file in the repository. Nothing here guesses.
"""
import difflib
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import psycopg
import yaml
from pydantic import BaseModel, ConfigDict, Field

ALIASES_PATH = Path(__file__).with_name("column_aliases.yml")


def fingerprint(columns: list[str] | tuple[str, ...] | set[str]) -> str:
    """Order does not matter: a reordered header is the same schema."""
    return hashlib.sha256("\n".join(sorted(columns)).encode()).hexdigest()


class Aliases(BaseModel):
    model_config = ConfigDict(extra="forbid")
    renames: dict[str, dict[str, str]] = Field(default_factory=dict)
    ignore_new_columns: dict[str, list[str]] = Field(default_factory=dict)


def load_aliases(path: Path = ALIASES_PATH) -> Aliases:
    data = yaml.safe_load(path.read_text()) if path.exists() else None
    return Aliases(**(data or {}))


@dataclass
class Diff:
    status: str  # match | approved | drift
    missing: list[str] = field(default_factory=list)  # expected, absent (after approved renames)
    new: list[str] = field(default_factory=list)  # present, not expected (after approvals)
    renames: dict[str, str] = field(default_factory=dict)  # approved: name in file -> expected
    ignored: list[str] = field(default_factory=list)  # approved new columns that are dropped
    suggested: dict[str, str] = field(default_factory=dict)  # a guess for the owner to confirm

    def describe(self) -> str:
        parts = []
        if self.missing:
            parts.append(f"missing {self.missing}")
        if self.new:
            parts.append(f"new {self.new}")
        return "; ".join(parts) or self.status


def compare(file_type: str, expected: list[str], observed: list[str],
            aliases: Aliases) -> Diff:
    exp_set, obs_set = set(expected), set(observed)
    if exp_set == obs_set:
        return Diff("match")
    renames = {old: new for old, new in aliases.renames.get(file_type, {}).items()
               if old in obs_set and new in exp_set and new not in obs_set}
    after = {renames.get(c, c) for c in obs_set}
    missing = sorted(exp_set - after)
    extra = sorted(after - exp_set)
    allowed_extra = set(aliases.ignore_new_columns.get(file_type, []))
    ignored = [c for c in extra if c in allowed_extra]
    new = [c for c in extra if c not in allowed_extra]
    if not missing and not new:
        return Diff("approved", renames=renames, ignored=ignored)
    return Diff("drift", missing=missing, new=new, renames=renames, ignored=ignored,
                suggested=suggest_renames(missing, new))


def suggest_renames(missing: list[str], new: list[str]) -> dict[str, str]:
    """Pair each unexplained new column with the closest-named missing one. A hint, not a rule."""
    out: dict[str, str] = {}
    left = list(missing)
    for n in new:
        close = difflib.get_close_matches(n, left, n=1, cutoff=0.5)
        if close:
            out[n] = close[0]
            left.remove(close[0])
    if not out and len(missing) == 1 and len(new) == 1:  # one gone, one appeared: likely a rename
        out[new[0]] = missing[0]
    return out


# ------------------------------------------------------------------------------ the database
def seed_expected(conn: psycopg.Connection, specs) -> None:
    """Record what each file type looks like today. Existing rows are never overwritten: changing
    an expectation is the owner's decision, not the loader's."""
    for s in specs:
        conn.execute(
            "INSERT INTO ops.expected_schemas (file_type, columns, fingerprint)"
            " VALUES (%s, %s, %s) ON CONFLICT (file_type) DO NOTHING",
            (s.table, json.dumps(list(s.columns)), fingerprint(s.columns)))


def expected_columns(conn: psycopg.Connection, file_type: str) -> list[str]:
    row = conn.execute("SELECT columns FROM ops.expected_schemas WHERE file_type = %s",
                       (file_type,)).fetchone()
    if row is None:
        raise LookupError(f"no expected schema recorded for '{file_type}'")
    return list(row[0])


def quarantine(conn: psycopg.Connection, *, file_type: str, rel_name: str, file_hash: str,
               observed: list[str], expected: list[str], diff: Diff,
               records: list[tuple[int, dict]]) -> bool:
    """Park the file and raise one alert. Returns False if this exact file was already parked."""
    row = conn.execute(
        "INSERT INTO bronze.quarantine_files (file_hash, source_file, file_type, observed_columns,"
        " observed_fingerprint, row_count) VALUES (%s, %s, %s, %s, %s, %s)"
        " ON CONFLICT (file_hash) DO NOTHING RETURNING quarantine_id",
        (file_hash, rel_name, file_type, json.dumps(observed), fingerprint(observed),
         len(records))).fetchone()
    if row is None:
        return False
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO bronze.quarantine_rows (quarantine_id, row_number, record)"
            " VALUES (%s, %s, %s)", [(row[0], n, json.dumps(r)) for n, r in records])
    conn.execute(
        "INSERT INTO ops.schema_alerts (file_type, source_file, file_hash, expected_fingerprint,"
        " observed_fingerprint, missing_columns, new_columns, suggested_renames)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (file_hash) DO NOTHING",
        (file_type, rel_name, file_hash, fingerprint(expected), fingerprint(observed),
         json.dumps(diff.missing), json.dumps(diff.new), json.dumps(diff.suggested)))
    return True


def release(conn: psycopg.Connection, file_hash: str, how: str) -> None:
    """A quarantined file has now been loaded: close its quarantine entry and its alert."""
    conn.execute("UPDATE bronze.quarantine_files SET status = 'released', released_at = now()"
                 " WHERE file_hash = %s AND status = 'open'", (file_hash,))
    conn.execute("UPDATE ops.schema_alerts SET status = 'resolved', resolved_at = now(),"
                 " resolution = %s WHERE file_hash = %s AND status = 'open'", (how, file_hash))


def open_alerts(conn: psycopg.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT alert_id, file_type, source_file, missing_columns, new_columns, suggested_renames,"
        " detected_at FROM ops.schema_alerts WHERE status = 'open' ORDER BY alert_id").fetchall()
    return [dict(zip(("alert_id", "file_type", "source_file", "missing", "new", "suggested",
                      "detected_at"), r, strict=True)) for r in rows]


def alert_warning(conn: psycopg.Connection) -> list[str]:
    """One line for the people reading answers: new data may be sitting in quarantine."""
    try:
        alerts = open_alerts(conn)
    except psycopg.errors.UndefinedTable:  # the drift tables are created by the bronze loader
        return []
    if not alerts:
        return []
    files = ", ".join(a["source_file"] for a in alerts[:3]) + (", ..." if len(alerts) > 3 else "")
    return [f"{len(alerts)} source file(s) with a changed schema are waiting in quarantine "
            f"({files}), so the newest data may be missing from answers."]
