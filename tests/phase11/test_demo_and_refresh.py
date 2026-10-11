"""Phase 11, part 3: the demo files, and `make refresh-catalog` on a scratch copy of the catalog."""
import json
import shutil

import pytest

from swiggy_ai.db import connect
from swiggy_ai.pipeline import bronze_loader as bl
from swiggy_ai.pipeline.drift_demo import main as demo_main
from swiggy_ai.pipeline.drift_demo import make_drift_files
from swiggy_ai.semantic import refresh as rf
from swiggy_ai.semantic.check import check_catalog
from swiggy_ai.semantic.loader import SEMANTIC_DIR, SemanticError, load_semantic

ORIGINAL = (SEMANTIC_DIR / "catalog.yml").read_text()


# --- the drift demo -----------------------------------------------------------------------------
def make_raw(tmp_path):
    raw = tmp_path / "raw"
    (raw / "orders").mkdir(parents=True, exist_ok=True)
    rows = [{c: (f"O{i}" if c == "order_id" else 100.0 + i if c == "order_value" else "x")
             for c in bl.SPECS[4].columns} for i in range(10)]
    for day in ("2026-01-01", "2026-01-02"):
        (raw / "orders" / f"orders_{day}.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n")
    return raw


def test_the_demo_batch_is_the_next_day_with_a_rename_and_a_new_column(tmp_path):
    out = tmp_path / "drift"
    target = make_drift_files(make_raw(tmp_path), out)
    assert target.name == "orders_2026-01-03.jsonl"
    rows = [json.loads(line) for line in target.read_text().splitlines()]
    assert len(rows) == 10
    assert "order_amount" in rows[0] and "order_value" not in rows[0]
    assert [r["tip_amount"] for r in rows[:6]] == [0, 10, 20, 0, 30, 0]  # deterministic
    assert [r["order_amount"] for r in rows[:2]] == [100.0, 101.0]  # values carried over
    assert list(rows[0]).index("order_amount") == bl.SPECS[4].columns.index("order_value")
    assert make_drift_files(make_raw(tmp_path), out).read_text() == target.read_text()


def test_the_demo_needs_data_and_says_so(tmp_path, capsys):
    assert demo_main(["--raw", str(tmp_path / "nothing"), "--out", str(tmp_path / "o")]) == 1
    assert "make data" in capsys.readouterr().out


@pytest.mark.db
def test_the_demo_runs_the_whole_story_and_leaves_the_warehouse_untouched(tmp_path, capsys):
    def state():
        with connect("etl", autocommit=True) as c:
            return [c.execute(q).fetchone()[0] for q in (
                "SELECT count(*) FROM bronze.orders", "SELECT count(*) FROM bronze.load_ledger",
                "SELECT count(*) FROM ops.schema_alerts",
                "SELECT count(*) FROM bronze.quarantine_files")]
    before = state()
    assert demo_main(["--raw", str(make_raw(tmp_path)), "--out", str(tmp_path / "d")]) == 0
    out = capsys.readouterr().out
    assert "quarantined: 1, loaded: 0" in out and "still quarantined: 1" in out
    assert "released from quarantine: 1" in out and "rolled back" in out
    assert "order_amount landed in order_value: O0 -> 100.0" in out
    assert state() == before


# --- refresh-catalog --------------------------------------------------------------------------
@pytest.fixture
def scratch(tmp_path):
    d = tmp_path / "semantic"
    shutil.copytree(SEMANTIC_DIR, d)
    return d


@pytest.fixture
def conn():
    c = connect("etl", autocommit=True)
    yield c
    c.close()


def edit(scratch, old, new, count=1):
    path = scratch / "catalog.yml"
    text = path.read_text()
    assert text.count(old) >= 1, old
    path.write_text(text.replace(old, new, count))


def test_a_catalog_that_matches_the_database_is_left_exactly_alone(conn, scratch):
    plan = rf.refresh_catalog(conn, scratch)
    assert not plan.changed and plan.lines() == [] and plan.needs_description == []
    assert (scratch / "catalog.yml").read_text() == ORIGINAL


def test_a_renamed_column_keeps_its_description_and_the_file_round_trips(conn, scratch):
    edit(scratch, "{name: platform,", "{name: app_platform,")  # as if the database had renamed it
    plan = rf.refresh_catalog(conn, scratch)
    assert plan.renamed == [("analytics.orders", "app_platform", "platform")]
    assert (scratch / "catalog.yml").read_text() == ORIGINAL  # identical, comments and all
    assert plan.needs_description == []


def test_a_block_style_column_can_be_renamed_too(conn, scratch):
    edit(scratch, "      - name: cuisine\n", "      - name: cuisine_type\n")
    plan = rf.refresh_catalog(conn, scratch)
    assert plan.renamed[0][1:] == ("cuisine_type", "cuisine")
    assert (scratch / "catalog.yml").read_text() == ORIGINAL


def test_a_new_database_column_is_added_flagged_and_the_file_still_loads(conn, scratch):
    text = ORIGINAL.splitlines()
    line = next(x for x in text if "{name: platform," in x)
    (scratch / "catalog.yml").write_text("\n".join(x for x in text if x != line))
    plan = rf.refresh_catalog(conn, scratch)
    assert plan.added == [("analytics.orders", "platform")]
    assert plan.needs_description == ["analytics.orders.platform"]
    assert any("platform" in x and rf.TODO in x for x in
               (scratch / "catalog.yml").read_text().splitlines())
    sem = load_semantic(scratch, check=False)  # still valid YAML and still valid against the model
    col = sem.catalog.table("analytics.orders").column("platform")
    assert col.type == "text" and col.description == rf.TODO
    assert not any("platform" in p and "missing from catalog" in p
                   for p in check_catalog(conn, sem, check_values=False))


def test_a_column_the_database_no_longer_has_is_removed(conn, scratch):
    edit(scratch, "      - {name: platform,",
         "      - {name: legacy_flag, type: boolean, description: An old flag that is gone., "
         "examples: [x]}\n      - {name: platform,")
    plan = rf.refresh_catalog(conn, scratch)
    assert plan.removed == [("analytics.orders", "legacy_flag")]
    assert (scratch / "catalog.yml").read_text() == ORIGINAL


def test_a_changed_type_is_updated_in_place(conn, scratch):
    edit(scratch, "{name: delivery_minutes, type: integer,", "{name: delivery_minutes, type: text,")
    plan = rf.refresh_catalog(conn, scratch)
    assert plan.retyped == [("analytics.orders", "delivery_minutes", "text", "integer")]
    assert (scratch / "catalog.yml").read_text() == ORIGINAL


def test_several_changes_at_once_are_all_applied(conn, scratch):
    edit(scratch, "{name: platform,", "{name: app_platform,")
    edit(scratch, "{name: taxes, type: numeric,", "{name: taxes, type: text,")
    edit(scratch, "      - {name: order_id,",
         "      - {name: legacy_flag, type: boolean, description: An old flag that is gone.}\n"
         "      - {name: order_id,")
    plan = rf.refresh_catalog(conn, scratch)
    assert len(plan.renamed) == len(plan.retyped) == len(plan.removed) == 1
    assert (scratch / "catalog.yml").read_text() == ORIGINAL


def test_dry_run_changes_nothing(conn, scratch):
    edit(scratch, "{name: platform,", "{name: app_platform,")
    before = (scratch / "catalog.yml").read_text()
    plan = rf.refresh_catalog(conn, scratch, write=False)
    assert plan.changed and (scratch / "catalog.yml").read_text() == before


def test_an_edit_that_would_break_the_catalog_is_undone(conn, scratch, monkeypatch):
    edit(scratch, "{name: platform,", "{name: app_platform,")
    before = (scratch / "catalog.yml").read_text()

    def broken(*a, **k):
        raise SemanticError(["boom"])
    monkeypatch.setattr(rf, "load_semantic", broken)
    with pytest.raises(SemanticError):
        rf.refresh_catalog(conn, scratch)
    assert (scratch / "catalog.yml").read_text() == before


def test_metrics_that_still_mention_a_vanished_column_are_pointed_out(conn, scratch):
    edit(scratch, "{name: platform,", "{name: app_platform,")
    with (scratch / "metrics.yml").open("a") as f:
        f.write("\n# note: split by app_platform\n")
    plan = rf.refresh_catalog(conn, scratch)
    assert plan.metric_mentions == ["app_platform"]
    assert "metrics.yml still mentions 'app_platform'" in plan.lines()


def test_new_and_missing_views_are_reported_never_invented(conn, scratch):
    import yaml
    doc = yaml.safe_load(ORIGINAL)
    gone = doc["tables"][-1]["name"]
    text = (scratch / "catalog.yml").read_text()
    start = text.index(f"  - name: {gone}")
    (scratch / "catalog.yml").write_text(text[:start])  # forget the last view
    plan = rf.refresh_catalog(conn, scratch)
    assert plan.new_views == [gone] and not plan.changed
