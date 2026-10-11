"""Hand-made Answers for the interface tests (no model, no database)."""
from decimal import Decimal

from swiggy_ai.agents.service import Answer
from swiggy_ai.agents.state import Attempt, Check, Clarification, StepResult, Usage


def attempt(no, source="writer", status="ok", error_class="", error_text="", item=1, ms=40):
    return Attempt(item=item, no=no, source=source, sql=f"SELECT {no}", status=status,
                   error_class=error_class, error_text=error_text, latency_ms=ms)


def answered(**kw) -> Answer:
    base = dict(status="answered", answer="You received 781 orders.", sql="SELECT 1",
                route="simple", attempts=[attempt(1)], latency_ms=900, run_id="r1",
                usage=Usage(calls=3, tokens_in=3000, tokens_out=200), est_cost=None,
                steps=[StepResult(id=1, question="orders?", ok=True, columns=["orders"],
                                  rows=[[781]], sql="SELECT 1")])
    base.update(kw)
    return Answer(**base)


def healed() -> Answer:
    return answered(attempts=[
        attempt(1, status="error", error_class="undefined_column",
                error_text='column "total_amount" does not exist'),
        attempt(2, source="healer", ms=83)])


def with_flag() -> Answer:
    return answered(checks=[Check(name="fan_out_suspected", passed=False, severity="warn",
                                  detail="x")])


def clarify() -> Answer:
    return Answer(status="clarify", answer="Best by what?", route="ambiguous",
                  clarification=Clarification(question="Best by what?", options=["revenue"]))


def with_money() -> Answer:
    rows = [[Decimal("12.50"), "Pune", i] for i in range(80)]
    return answered(steps=[StepResult(id=1, question="q", ok=True, columns=["a", "b", "c"],
                                      rows=rows)], est_cost=Decimal("0.0123"))
