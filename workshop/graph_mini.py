import os
import sys
from typing import Literal, TypedDict

import psycopg
from langgraph.graph import END, START, StateGraph

from .agent_mini import break_it, narrate, repair, run, write_sql
from .guard_mini import check


class State(TypedDict, total=False):
    question: str
    sql: str
    error: str
    tries: int
    columns: list
    rows: list
    answer: str
    status: str


def write_node(s: State) -> dict:
    sql = write_sql(s["question"])
    return {"sql": break_it(sql) if os.environ.get("BREAK_FIRST") else sql, "tries": 0}


def guard_node(s: State) -> dict:
    ok, out = check(s["sql"])
    return {"sql": out} if ok else {"status": "refused", "error": out}


def run_node(s: State) -> dict:
    try:
        columns, rows = run(s["sql"])
        return {"columns": columns, "rows": rows, "error": ""}
    except psycopg.Error as e:
        print(f"attempt {s['tries'] + 1} failed: {str(e).splitlines()[0]}")
        return {"error": str(e), "tries": s["tries"] + 1}


def repair_node(s: State) -> dict:
    return {"sql": repair(s["question"], s["sql"], s["error"])}


def narrate_node(s: State) -> dict:
    return {"answer": narrate(s["question"], s["columns"], s["rows"]), "status": "answered"}


def after_guard(s: State) -> Literal["run", "__end__"]:
    return END if s.get("status") == "refused" else "run"


def after_run(s: State) -> Literal["narrate", "repair", "__end__"]:
    if not s["error"]:
        return "narrate"
    return "repair" if s["tries"] < 3 else END


g = StateGraph(State)
for name, fn in [("write", write_node), ("guard", guard_node), ("run", run_node),
                 ("repair", repair_node), ("narrate", narrate_node)]:
    g.add_node(name, fn)
g.add_edge(START, "write")
g.add_edge("write", "guard")
g.add_conditional_edges("guard", after_guard)
g.add_conditional_edges("run", after_run)
g.add_edge("repair", "guard")
g.add_edge("narrate", END)
graph = g.compile()

if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "How many orders did we get last week?"
    print(graph.invoke({"question": q}))
