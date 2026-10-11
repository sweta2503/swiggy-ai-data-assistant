"""Phase 12, part 3: the daily pipeline. Stages are replaced by stand-ins where a test needs a
particular outcome; everything is one transaction on the real database and is rolled back."""
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from swiggy_ai.db import connect
from swiggy_ai.ops import cache
from swiggy_ai.pipeline import daily
from swiggy_ai.pipeline.bronze_loader import LoadReport
from swiggy_ai.pipeline.gold import GoldReport
from swiggy_ai.pipeline.silver import SilverReport
from swiggy_ai.semantic.loader import SEMANTIC_DIR

pytestmark = pytest.mark.db


@pytest.fixture
def conn():
    c = connect("etl")
    cache.ensure_cache_tables(c)
    yield c
    c.rollback()
    c.close()


def dq_report(blocking=(), warning=()):
    def result(name, severity):
        return SimpleNamespace(check=SimpleNamespace(name=name, severity=severity))
    bad = [result(n, "blocking") for n in blocking]
    return SimpleNamespace(results=[result("fine", "blocking"), *bad,
                                    *[result(n, "warning") for n in warning]],
                           failures=bad, blocking_failures=bad)


class Stub:
    """Stands in for the pipeline stages and records which ones ran."""

    def __init__(self, monkeypatch, *, new_files=0, new_batches=0, gold_rows=0, blocking=(),
                 fail_in=None):
        self.ran = []
        self.catalog_dir = None

        def stage(name, value):
            def fn(*a, **k):
                self.ran.append(name)
                if fail_in == name:
                    raise RuntimeError(f"{name} blew up")
                return value
            return fn
        monkeypatch.setattr(daily.bronze_loader, "load_directory",
                            stage("bronze", LoadReport(files_loaded=new_files)))
        monkeypatch.setattr(daily, "run_silver",
                            stage("silver", SilverReport(new_batches={"orders": new_batches})))
        monkeypatch.setattr(daily, "run_gold",
                            stage("gold", GoldReport(added={"fact_orders": gold_rows})))
        monkeypatch.setattr(daily, "run_checks", stage("dq", dq_report(blocking)))
        monkeypatch.setattr(daily, "default_as_of", lambda d: None)
        monkeypatch.setattr(daily, "refresh_catalog", stage("catalog", SimpleNamespace(
            lines=lambda: [])))


def run(conn, tmp_path, **kw):
    out = []
    r = daily.run_daily(conn, tmp_path, catalog_dir=tmp_path, say=out.append, **kw)
    return r, "\n".join(out)


def record(conn, run_id):
    return conn.execute(
        "SELECT status, changed, data_version_before, data_version_after, dq_checks,"
        " dq_blocking_failed, error, finished_at IS NOT NULL, stages FROM ops.pipeline_runs"
        " WHERE run_id = %s", (run_id,)).fetchone()


# --- the happy paths ------------------------------------------------------------
def test_new_data_runs_every_stage_in_order_and_publishes_a_new_version(conn, tmp_path,
                                                                        monkeypatch):
    stub = Stub(monkeypatch, new_files=2, new_batches=3, gold_rows=500)
    v = cache.get_data_version(conn)
    r, text = run(conn, tmp_path)
    assert stub.ran == ["bronze", "silver", "gold", "dq", "catalog"]
    assert [s.name for s in r.stages] == ["bronze", "silver", "gold", "dq", "catalog", "publish"]
    assert r.status == "published" and r.changed and r.exit_code == 0
    assert (r.version_before, r.version_after) == (v, v + 1) == (v, cache.get_data_version(conn))
    assert "published" in text and f"{v} -> {v + 1}" in text
    row = record(conn, r.run_id)
    status, changed, before, after, checks, blocked, error, finished, stages = row
    assert (status, changed, before, after, checks, blocked, error, finished) == (
        "published", True, v, v + 1, 1, [], None, True)
    assert [s["name"] for s in stages][-1] == "publish" and all("seconds" in s for s in stages)


def test_a_run_with_nothing_new_publishes_but_keeps_the_version_and_the_cache(conn, tmp_path,
                                                                              monkeypatch):
    Stub(monkeypatch)
    v = cache.get_data_version(conn)
    cache.store(conn, "k", "q", v, "ALL", {"answer": "still good"})
    r, text = run(conn, tmp_path)
    assert r.status == "published" and not r.changed and r.version_after == v
    assert "nothing new" in text and cache.lookup(conn, "k") is not None


def test_a_real_run_with_no_new_files_goes_through_every_real_stage(conn, tmp_path):
    raw = Path("data/raw")  # every file here is already loaded, and _meta.json says how fresh
    if not (raw / "_meta.json").exists():
        pytest.skip("no generated data")
    scratch = tmp_path / "semantic"  # a copy, so a test can never edit the real catalog
    shutil.copytree(SEMANTIC_DIR, scratch)
    v = cache.get_data_version(conn)
    r = daily.run_daily(conn, raw, catalog_dir=scratch, say=lambda *_: None)
    assert r.status == "published" and not r.changed and r.version_after == v
    assert [s.status for s in r.stages] == ["ok"] * 6
    assert r.dq_checks > 40  # the real checks ran


# --- the stop sign ------------------------------------------------------------
def test_a_blocking_data_quality_failure_stops_before_publishing(conn, tmp_path, monkeypatch):
    stub = Stub(monkeypatch, new_files=1, gold_rows=10,
                blocking=["gold_fact_orders_unique_key"])
    v = cache.get_data_version(conn)
    cache.store(conn, "k", "q", v, "ALL", {"answer": "from the last good data"})
    r, text = run(conn, tmp_path)
    assert r.status == "blocked" and r.exit_code == 1
    assert r.blocked_by == ["gold_fact_orders_unique_key"]
    assert "catalog" not in stub.ran  # the catalog was not touched
    assert [(s.name, s.status) for s in r.stages][-2:] == [("catalog", "skipped"),
                                                           ("publish", "skipped")]
    assert cache.get_data_version(conn) == v  # no new version...
    assert cache.lookup(conn, "k") is not None  # ...so the last good answers stay valid
    assert "STOPPED" in text and "gold_fact_orders_unique_key" in text
    row = record(conn, r.run_id)
    assert row[0] == "blocked" and row[5] == ["gold_fact_orders_unique_key"] and row[7]


def test_after_a_blocked_run_the_next_clean_run_publishes_even_if_nothing_is_new(
        conn, tmp_path, monkeypatch):
    v = cache.get_data_version(conn)
    Stub(monkeypatch, new_files=1, blocking=["bad"])
    assert run(conn, tmp_path)[0].status == "blocked"
    Stub(monkeypatch)  # the owner fixed the problem; no new files arrive
    r, _ = run(conn, tmp_path)
    assert r.status == "published" and r.version_after == v + 1  # the data did change earlier


# --- failures ------------------------------------------------------------
@pytest.mark.parametrize("stage, skipped", [
    ("bronze", ["silver", "gold", "dq", "catalog", "publish"]),
    ("silver", ["gold", "dq", "catalog", "publish"]),
    ("gold", ["dq", "catalog", "publish"]),
    ("dq", ["catalog", "publish"]),
    ("catalog", ["publish"]),
])
def test_a_failing_stage_stops_the_run_records_why_and_publishes_nothing(
        conn, tmp_path, monkeypatch, stage, skipped):
    Stub(monkeypatch, new_files=1, fail_in=stage)
    v = cache.get_data_version(conn)
    r, text = run(conn, tmp_path)
    assert r.status == "failed" and r.exit_code == 2 and f"{stage} blew up" in r.error
    by_name = {s.name: s.status for s in r.stages}
    assert by_name[stage] == "failed" and all(by_name[n] == "skipped" for n in skipped)
    assert cache.get_data_version(conn) == v and r.version_after == v
    row = record(conn, r.run_id)
    assert row[0] == "failed" and f"{stage} blew up" in row[6] and row[7]  # recorded, finished


def test_open_schema_alerts_are_reported_but_do_not_stop_a_healthy_run(conn, tmp_path,
                                                                        monkeypatch):
    from swiggy_ai.pipeline.schema_drift import Aliases, quarantine
    Stub(monkeypatch)
    quarantine(conn, file_type="orders", rel_name="orders/x.jsonl", file_hash="h" * 64,
               observed=["a"], expected=["b"], diff=SimpleNamespace(
                   missing=["b"], new=["a"], suggested={}), records=[(1, {"a": 1})])
    assert Aliases()
    r, text = run(conn, tmp_path)
    assert r.status == "published" and r.schema_alerts == 1
    assert "WARNING: 1 source file(s)" in text
    assert conn.execute("SELECT schema_alerts_open FROM ops.pipeline_runs WHERE run_id = %s",
                        (r.run_id,)).fetchone()[0] == 1


# --- publish only (for Airflow), the lock, and the command line -------------------------------
def test_publish_only_bumps_the_version_when_the_latest_checks_are_healthy(conn, monkeypatch):
    import swiggy_ai.dq.runner as runner
    monkeypatch.setattr(runner, "assert_gold_healthy", lambda c: runner.Health(True, "ok"))
    v = cache.get_data_version(conn)
    r = daily.publish_only(conn)
    assert r.status == "published" and cache.get_data_version(conn) == v + 1
    assert r.trigger == "airflow"


def test_publish_only_refuses_when_data_quality_is_not_healthy(conn, monkeypatch):
    import swiggy_ai.dq.runner as runner
    monkeypatch.setattr(runner, "assert_gold_healthy", lambda c: runner.Health(
        False, "data quality warning: x failed", ["x"]))
    v = cache.get_data_version(conn)
    r = daily.publish_only(conn)
    assert r.status == "blocked" and r.blocked_by == ["x"] and r.exit_code == 1
    assert cache.get_data_version(conn) == v


def test_two_runs_cannot_overlap(tmp_path, capsys):
    other = connect("etl", autocommit=True)
    try:
        assert other.execute("SELECT pg_try_advisory_lock(%s)", (daily.LOCK_ID,)).fetchone()[0]
        assert daily.run_locked("test", tmp_path) is None
        assert "already in progress" in capsys.readouterr().out
    finally:
        other.execute("SELECT pg_advisory_unlock(%s)", (daily.LOCK_ID,))
        other.close()


def test_the_command_line_maps_outcomes_to_exit_codes(monkeypatch):
    for status, code in (("published", 0), ("blocked", 1), ("failed", 2)):
        monkeypatch.setattr(daily, "run_locked", lambda *a, _s=status, **k: daily.DailyRun(
            run_id=None, status=_s))
        assert daily.main([]) == code
    monkeypatch.setattr(daily, "run_locked", lambda *a, **k: None)
    assert daily.main([]) == 3


def test_a_stage_error_message_is_scrubbed_before_it_is_stored(conn, tmp_path, monkeypatch):
    stub = Stub(monkeypatch, fail_in="silver")
    monkeypatch.setattr(daily, "run_silver", lambda c: (_ for _ in ()).throw(
        RuntimeError("bad row for priya@example.com phone 9876543210")))
    r, _ = run(conn, tmp_path)
    stored = record(conn, r.run_id)[6]
    assert "priya@example.com" not in stored and "9876543210" not in stored
    assert stub and Path(tmp_path).exists()
