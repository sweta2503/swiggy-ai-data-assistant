"""The agent graph, increment 08a: question -> schema -> SQL -> guard -> cost -> run -> narrate.

(Later increments add the self-healing loop, the result checker, and the router/planner.)
"""
from typing import Literal

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from swiggy_ai.agents.cost_check import make_cost_check
from swiggy_ai.agents.executor import make_executor
from swiggy_ai.agents.guard_node import make_guard
from swiggy_ai.agents.narrator import make_narrator
from swiggy_ai.agents.results import make_record_result
from swiggy_ai.agents.runtime import Runtime
from swiggy_ai.agents.schema_agent import make_schema_agent
from swiggy_ai.agents.sql_writer import make_sql_writer
from swiggy_ai.agents.state import AgentState, SubQuestion


def begin(state: AgentState) -> dict:
    """Turn the question into a one-step plan."""
    q = state.working_question or state.question
    return {"route": "simple", "working_question": q,
            "plan": [SubQuestion(id=1, question=q)], "item": 0}


def after_guard(state: AgentState) -> Literal["cost_check", "record_result"]:
    return "record_result" if state.last_error_class else "cost_check"


def after_cost(state: AgentState) -> Literal["sql_writer", "executor", "record_result"]:
    if state.last_error_class:  # the planner already found an error
        return "record_result"
    return "sql_writer" if state.hint else "executor"


def after_record(state: AgentState) -> Literal["schema_agent", "narrator"]:
    return "schema_agent" if state.item < len(state.plan) else "narrator"


def build_graph(rt: Runtime, checkpointer=None):
    g = StateGraph(AgentState)
    g.add_node("begin", begin)
    g.add_node("schema_agent", make_schema_agent(rt))
    g.add_node("sql_writer", make_sql_writer(rt))
    g.add_node("guard", make_guard(rt))
    g.add_node("cost_check", make_cost_check(rt))
    g.add_node("executor", make_executor(rt))
    g.add_node("record_result", make_record_result())
    g.add_node("narrator", make_narrator(rt))

    g.add_edge(START, "begin")
    g.add_edge("begin", "schema_agent")
    g.add_edge("schema_agent", "sql_writer")
    g.add_edge("sql_writer", "guard")
    g.add_conditional_edges("guard", after_guard)
    g.add_conditional_edges("cost_check", after_cost)
    g.add_edge("executor", "record_result")
    g.add_conditional_edges("record_result", after_record)
    g.add_edge("narrator", END)
    return g.compile(checkpointer=checkpointer or MemorySaver())
