"""Read evaluation results: show a report, compare two runs, or gate on the pass marks.

    python -m eval.report show [--file F]     the report for a run (default: the latest)
    python -m eval.report compare [--a F --b F]   what changed between two runs (default: last two)
    python -m eval.report gate [--file F]     exit 0 only if every pass mark is met
"""
import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from eval.models import EVAL_DIR

RESULTS_DIR = EVAL_DIR / "results"
THRESHOLDS = EVAL_DIR / "thresholds.yml"
NOT_COMPUTED = "cost not computed (prices not set)"

# threshold key -> (summary field, direction)
GATES = {
    "min_accuracy_after_healing": ("accuracy", "min"),
    "min_first_attempt_accuracy": ("first_attempt_accuracy", "min"),
    "min_clarification_accuracy": ("clarification_accuracy", "min"),
    "min_refusal_accuracy": ("refusal_accuracy", "min"),
    "max_over_caution": ("over_caution", "max"),
    "max_avg_latency_ms": ("avg_latency_ms", "max"),
    "max_avg_cost_usd": ("avg_cost_usd", "max"),
}


def pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.0f}%"


def frac(num: int, den: int) -> str:
    return f"{num}/{den} ({pct(num / den if den else None)})"


def list_results(directory: Path = RESULTS_DIR) -> list[Path]:
    return sorted(directory.glob("*.json"))


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


# ----------------------------------------------------------------------------- the report
def render_markdown(result: dict[str, Any]) -> str:
    m, s, cases = result["meta"], result["summary"], result["cases"]
    n = s["n"]
    answerable = [c for c in cases if c["answerable"]]
    clar = [c for c in cases if any(v["type"] == "clarification" for v in c["verdicts"])]
    refuse = [c for c in cases if any(v["type"] == "refusal" for v in c["verdicts"])]
    avg_attempts = "n/a" if s["avg_attempts"] is None else f"{s['avg_attempts']:.2f}"
    cost = (f"${s['cost_usd']:.4f} total, ${s['avg_cost_usd']:.4f} per question"
            if s["cost_usd"] is not None else NOT_COMPUTED)
    out = [
        "# Evaluation report", "",
        f"- Run: {m['timestamp']} UTC, commit `{m['git_sha']}`"
        + (" (PARTIAL run: not valid for the gate)" if m["partial"] else ""),
        f"- Models: low `{m['models'].get('low')}`, medium `{m['models'].get('medium')}`, "
        f"high `{m['models'].get('high')}`",
        f"- Data runs until {m['data_until']}. Demo fault injection: "
        f"{'ON (invalid run)' if m['fault_injection'] else 'off'}.", "",
        "## Headline", "", "| Measure | Result |", "|---|---|",
        f"| Accuracy after healing | {frac(s['correct'], n)} |",
        f"| First-attempt accuracy | {pct(s['first_attempt_accuracy'])} "
        "(right with no failed or rejected attempt first) |",
        f"| Data questions answered correctly | "
        f"{frac(sum(c['correct'] for c in answerable), len(answerable))} |",
        f"| Clarification accuracy | {frac(sum(c['correct'] for c in clar), len(clar))} |",
        f"| Refusal accuracy | {frac(sum(c['correct'] for c in refuse), len(refuse))} |",
        f"| Over-cautious answers | {s['over_caution']} (answerable, but refused or clarified) |",
        f"| Healed questions | {s['healed']} ({s['healed_and_correct']} ended correct) |",
        f"| Average attempts per question | {avg_attempts} |",
        f"| Average latency | {s['avg_latency_ms']:,.0f} ms (95th percentile "
        f"{s['p95_latency_ms']:,} ms) |",
        f"| Tokens | {s['tokens_in']:,} in, {s['tokens_out']:,} out |",
        f"| Cost | {cost} |",
        f"| Questions that crashed | {s['errors']} |", "",
        "## By category", "", "| Category | Correct | First attempt |", "|---|---|---|"]
    for cat, row in s["by_category"].items():
        out.append(f"| {cat} | {frac(row['correct'], row['n'])} | "
                   f"{frac(row['first_attempt'], row['n'])} |")

    failures = [c for c in cases if not c["correct"]]
    out += ["", f"## Failures ({len(failures)})", ""]
    if not failures:
        out.append("None.")
    for c in failures:
        out += [f"### {c['id']} ({c['category']}): {c['question']}",
                f"- status `{c['status'] or 'error'}`, route `{c['route']}`, "
                f"attempts {c['attempts']}",
                *[f"- {v['type']}: {v['detail']}" for v in c["verdicts"] if not v["ok"]],
                *([f"- error: {c['error']}"] if c["error"] else []),
                f"- reference preview: {c['reference']}",
                f"- SQL: `{' '.join(c['final_sql'].split())[:300]}`",
                f"- answer: {c['answer'][:300]}", ""]
    healed = [c for c in cases if c["healed"]]
    if healed:
        out += [f"## Healed ({len(healed)})", ""]
        out += [f"- {c['id']}: {c['attempts']} -> {'correct' if c['correct'] else 'WRONG'}"
                for c in healed]
    return "\n".join(out)


# ----------------------------------------------------------------------------- compare
def compare(a: dict[str, Any], b: dict[str, Any]) -> str:
    sa, sb = a["summary"], b["summary"]
    out = [f"Comparing {a['meta']['timestamp']} ({a['meta']['git_sha']}) "
           f"with {b['meta']['timestamp']} ({b['meta']['git_sha']})", ""]
    for what, key in (("models", "models"), ("data until", "data_until"), ("partial", "partial")):
        if a["meta"][key] != b["meta"][key]:
            out.append(f"! {what} differ: {a['meta'][key]} vs {b['meta'][key]}")

    def row(label: str, key: str, as_pct: bool = True, fmt: str = "{:,.0f}") -> str:
        x, y = sa[key], sb[key]

        def show(v: float | None) -> str:
            return "n/a" if v is None else (pct(v) if as_pct else fmt.format(v))
        arrow = ""
        if x is not None and y is not None and x != y:
            arrow = "  (better)" if (y > x) == as_pct else "  (changed)"
        return f"  {label:<28} {show(x):>8}  ->  {show(y):>8}{arrow}"
    out += ["Measures",
            row("accuracy after healing", "accuracy"),
            row("first-attempt accuracy", "first_attempt_accuracy"),
            row("clarification accuracy", "clarification_accuracy"),
            row("refusal accuracy", "refusal_accuracy"),
            row("over-cautious answers", "over_caution", False),
            row("healed questions", "healed", False),
            row("average latency (ms)", "avg_latency_ms", False)]
    ca = {c["id"]: c for c in a["cases"]}
    cb = {c["id"]: c for c in b["cases"]}
    both = [i for i in cb if i in ca]
    regressions = [i for i in both if ca[i]["correct"] and not cb[i]["correct"]]
    fixes = [i for i in both if not ca[i]["correct"] and cb[i]["correct"]]
    still = [i for i in both if not ca[i]["correct"] and not cb[i]["correct"]]
    out += ["", f"Regressions (right before, wrong now): {len(regressions)}"]
    out += [f"  - {i}: {cb[i]['question']}  | now: {cb[i]['status']} {cb[i]['attempts']}"
            for i in regressions]
    out += [f"Fixed (wrong before, right now): {len(fixes)}"]
    out += [f"  - {i}: {cb[i]['question']}" for i in fixes]
    out += [f"Still wrong: {len(still)}  {still}"]
    only_a, only_b = sorted(set(ca) - set(cb)), sorted(set(cb) - set(ca))
    if only_a or only_b:
        out.append(f"Not in both runs: only in the first {only_a}, only in the second {only_b}")
    return "\n".join(out)


# ----------------------------------------------------------------------------- gate
def gate(result: dict[str, Any], thresholds: dict[str, Any]) -> tuple[int, list[str]]:
    """(exit code, messages). 0 = pass, 1 = a pass mark was missed, 2 = the gate cannot decide."""
    messages: list[str] = []
    set_marks = {k: v for k, v in thresholds.items() if k in GATES and v is not None}
    floors = thresholds.get("min_category_accuracy") or {}
    if not set_marks and not floors:
        return 2, ["No pass marks are set in eval/thresholds.yml. Run `make eval`, read the "
                   "numbers, then set them. The gate will not pass on an empty list."]
    if result["meta"]["partial"]:
        return 2, ["This was a partial run (--only/--category/--limit). Run the full `make eval`."]
    if result["meta"]["fault_injection"]:
        return 2, ["Demo fault injection was on during this run, so it proves nothing."]

    failed = False
    for key, limit in set_marks.items():
        field, direction = GATES[key]
        got = result["summary"].get(field)
        if got is None:
            if field == "avg_cost_usd":
                messages.append(f"skip {key}: prices are not set, so cost is unknown")
                continue
            messages.append(f"FAIL {key}: this run has no value for {field}")
            failed = True
            continue
        ok = got >= limit if direction == "min" else got <= limit
        failed |= not ok
        messages.append(f"{'ok  ' if ok else 'FAIL'} {key}: {got:.4g} "
                        f"({'needs >=' if direction == 'min' else 'needs <='} {limit})")
    for cat, floor in floors.items():
        row = result["summary"]["by_category"].get(cat)
        got = row["correct"] / row["n"] if row else None
        ok = got is not None and got >= floor
        failed |= not ok
        messages.append(f"{'ok  ' if ok else 'FAIL'} category {cat}: "
                        f"{'missing' if got is None else f'{got:.4g}'} (needs >= {floor})")
    return (1 if failed else 0), messages


# ----------------------------------------------------------------------------- command line
def pick(path: str | None, offset: int = 1) -> dict[str, Any] | None:
    if path:
        return load(Path(path))
    files = list_results()
    return load(files[-offset]) if len(files) >= offset else None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    show, cmp_, gt = (sub.add_parser(c) for c in ("show", "compare", "gate"))
    for sp in (show, gt):
        sp.add_argument("--file")
    cmp_.add_argument("--a")
    cmp_.add_argument("--b")
    a = p.parse_args(argv)

    if a.cmd == "show":
        r = pick(a.file)
        print(render_markdown(r) if r else "No results yet. Run `make eval`.")
        return 0 if r else 1
    if a.cmd == "compare":
        first, second = (pick(a.a, 2), pick(a.b, 1)) if not (a.a or a.b) else (pick(a.a),
                                                                               pick(a.b))
        if not first or not second:
            print("Need two results to compare. Run `make eval` at least twice.")
            return 1
        print(compare(first, second))
        return 0
    r = pick(a.file)
    if not r:
        print("No results yet. Run `make eval`.")
        return 2
    code, msgs = gate(r, yaml.safe_load(THRESHOLDS.read_text()) or {})
    print("\n".join(msgs))
    print({0: "GATE PASSED", 1: "GATE FAILED", 2: "GATE CANNOT DECIDE"}[code])
    return code


if __name__ == "__main__":
    sys.exit(main())
