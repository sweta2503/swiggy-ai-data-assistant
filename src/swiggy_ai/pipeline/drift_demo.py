"""`make drift-demo`: watch the pipeline meet a changed source, and what the owner does about it.

1. Writes data/drift/: a later batch of orders where `order_value` was renamed `order_amount`
   and a new column `tip_amount` appeared (the kind of change a source team makes without telling
   anyone).
2. Loads it with no approvals -> quarantined, alert raised, silver cannot be affected.
3. Loads it with only the rename approved -> still quarantined (tip_amount is unexplained).
4. Loads it with the rename and the new column approved -> loaded, values mapped.
Everything in steps 2 to 4 happens in ONE transaction that is rolled back, so your warehouse is
left exactly as it was. To do it for real, edit pipeline/column_aliases.yml and run
`python -m swiggy_ai.pipeline.bronze_loader --data-dir data/drift`.
"""
import argparse
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

from swiggy_ai.db import connect
from swiggy_ai.pipeline import bronze_loader as bl
from swiggy_ai.pipeline import schema_drift as drift

TIPS = (0, 10, 20, 0, 30)  # deterministic: row i gets TIPS[i % 5]


def make_drift_files(raw_dir: Path, out_dir: Path) -> Path:
    """Re-send the newest orders file as the NEXT day's batch, in the changed format."""
    files = sorted((raw_dir / "orders").glob("orders_*.jsonl"))
    if not files:
        raise FileNotFoundError(f"no orders files in {raw_dir}/orders; run `make data` first")
    newest = files[-1]
    day = date.fromisoformat(re.search(r"(\d{4}-\d{2}-\d{2})", newest.name).group(1))
    target = out_dir / "orders" / f"orders_{day + timedelta(days=1)}.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for i, line in enumerate(newest.read_text().splitlines()):
        if not line.strip():
            continue
        rec = json.loads(line)
        # rebuild the dict so the renamed column keeps its position
        rec = {("order_amount" if k == "order_value" else k): v for k, v in rec.items()}
        rec["tip_amount"] = TIPS[i % len(TIPS)]
        lines.append(json.dumps(rec, separators=(",", ":")))
    target.write_text("\n".join(lines) + "\n")
    return target


def _count(conn, sql: str, *args) -> int:
    return conn.execute(sql, args).fetchone()[0]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--raw", type=Path, default=Path("data/raw"))
    p.add_argument("--out", type=Path, default=Path("data/drift"))
    a = p.parse_args(argv)
    try:
        target = make_drift_files(a.raw, a.out)
    except FileNotFoundError as e:
        print(e)
        return 1
    first = json.loads(target.read_text().splitlines()[0])
    print(f"1. Wrote {target} ({len(target.read_text().splitlines())} orders)")
    print("   its columns now include: order_amount (was order_value) and tip_amount (new)")
    print(f"   first row: order_amount={first['order_amount']}, tip_amount={first['tip_amount']}\n")

    conn = connect("etl")  # autocommit off: the whole demo is one transaction, rolled back below
    try:
        bl.apply_ddl(conn)
        before = _count(conn, "SELECT count(*) FROM bronze.orders")
        print("2. Load it with NO approvals")
        r = bl.load_directory(conn, a.out, aliases=drift.Aliases())
        alert = drift.open_alerts(conn)[-1]
        print(f"   quarantined: {r.files_quarantined}, loaded: {r.files_loaded}")
        after = _count(conn, "SELECT count(*) FROM bronze.orders")
        print(f"   bronze.orders rows: {before} -> {after} "
              "(nothing reached bronze, so silver and gold are untouched)")
        print(f"   ALERT {alert['alert_id']}: {alert['source_file']}  missing={alert['missing']}  "
              f"new={alert['new']}")
        print(f"   suggested rename (a hint to check, not a decision): {alert['suggested']}\n")

        print("3. Approve only the rename:  orders: {order_amount: order_value}")
        r = bl.load_directory(conn, a.out, aliases=drift.Aliases(
            renames={"orders": {"order_amount": "order_value"}}))
        print(f"   still quarantined: {r.files_quarantined}, loaded: {r.files_loaded}"
              "  (tip_amount is new and nobody has decided what to do with it)\n")

        print("4. Approve the rename AND say tip_amount may be ignored for now")
        r = bl.load_directory(conn, a.out, aliases=drift.Aliases(
            renames={"orders": {"order_amount": "order_value"}},
            ignore_new_columns={"orders": ["tip_amount"]}))
        rows = _count(conn, "SELECT count(*) FROM bronze.orders WHERE _source_file = %s",
                      f"orders/{target.name}")
        sample = conn.execute("SELECT order_id, order_value FROM bronze.orders WHERE "
                              "_source_file = %s ORDER BY _row_number LIMIT 1",
                              (f"orders/{target.name}",)).fetchone()
        print(f"   loaded: {r.files_loaded}, released from quarantine: {r.files_released}, "
              f"rows: {rows}")
        print(f"   order_amount landed in order_value: {sample[0]} -> {sample[1]}")
        print(f"   open alerts now: {len(drift.open_alerts(conn))}  "
              "(tip_amount values are not kept)")
    finally:
        conn.rollback()
        conn.close()
    print("\nThe demo ran inside one transaction and was rolled back: your warehouse is unchanged.")
    print("For real: edit src/swiggy_ai/pipeline/column_aliases.yml, then run "
          "`python -m swiggy_ai.pipeline.bronze_loader --data-dir data/drift`.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
