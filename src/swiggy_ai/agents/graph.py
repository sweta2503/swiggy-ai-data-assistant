"""The agent graph.

    question -> router -+-> clarifier ----------------------------------+
                        +-> refusal ------------------------------------+
                        +-> (planner) -> schema -> SQL writer -> guard -> cost check -> run
                        |                    ^          ^                                |
                        |                    |          +-- healer <-- error ------------+
                        |                    |          +-- result checker (suspect) <---+
                        |                    +---- next step (multi-step) ---- record result
                        +-> narrator (one step) / synthesizer (several) -> finalize -> END

08a  the happy path          08b  self-healing          08c  silent-error detection
08d  router, clarifier, planner, synthesizer and conversation memory
"""
from collections.abc import Callable
from typing import Literal

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from swiggy_ai.agents.clarifier import make_clarifier
from swiggy_ai.agents.cost_check import make_cost_check
from swiggy_ai.agents.executor import make_executor
from swiggy_ai.agents.finish import make_finalize, plan_simple, refusal
from swiggy_ai.agents.guard_node import make_guard
from swiggy_ai.agents.healer import can_heal, make_healer
from swiggy_ai.agents.narrator import make_narrator
from swiggy_ai.agents.planner import make_planner
from swiggy_ai.agents.result_checker import make_result_checker
from swiggy_ai.agents.results import make_record_result
from swiggy_ai.agents.router import make_router
from swiggy_ai.agents.runtime import Runtime
from swiggy_ai.agents.schema_agent import make_schema_agent
from swiggy_ai.agents.sql_writer import make_sql_writer
from swiggy_ai.agents.state import AgentState
from swiggy_ai.agents.synthesizer import make_synthesizer


def after_router(state: AgentState) -> Literal["plan_simple", "planner", "clarifier", "refusal"]:
    return {"simple": "plan_simple", "multi_step": "planner", "ambiguous": "clarifier",
            "unsafe": "refusal", "out_of_scope": "refusal"}[state.route]


def after_planner(state: AgentState) -> Literal["schema_agent"]:
    return "schema_agent"


def after_record(state: AgentState) -> Literal["schema_agent", "narrator", "synthesizer"]:
    if state.item < len(state.plan):
        return "schema_agent"  # the next step of the plan
    return "narrator" if len(state.plan) == 1 else "synthesizer"


def make_routes(rt: Runtime):
    """The routing decisions inside the query loop. They need the runtime for the heal limit."""

    def failure(state: AgentState) -> Literal["healer", "record_result"]:
        """Something went wrong with this query: repair it, or give up and report."""
        return "healer" if can_heal(rt, state) else "record_result"

    def after_guard(state: AgentState) -> Literal["cost_check", "healer", "record_result"]:
        return failure(state) if state.last_error_class else "cost_check"

    def after_cost(state: AgentState) -> Literal["sql_writer", "executor", "healer",
                                                   "record_result"]:
        if state.last_error_class:  # the planner already found an error
            return failure(state)
        return "sql_writer" if state.hint else "executor"

    def after_executor(state: AgentState) -> Literal["result_checker", "healer", "record_result"]:
        return "result_checker" if state.item_ok else failure(state)

    def after_healer(state: AgentState) -> Literal["guard", "record_result"]:
        return "record_result" if state.last_error_class else "guard"

    def after_checker(state: AgentState) -> Literal["sql_writer", "record_result"]:
        """A suspect result goes back to the writer with an explanation (hint set)."""
        return "sql_writer" if state.hint else "record_result"

    return after_guard, after_cost, after_executor, after_healer, after_checker


def build_graph(rt: Runtime, checkpointer=None):
    nodes: dict[str, Callable] = {
        "router": make_router(rt), "clarifier": make_clarifier(rt), "refusal": refusal,
        "plan_simple": plan_simple, "planner": make_planner(rt),
        "schema_agent": make_schema_agent(rt), "sql_writer": make_sql_writer(rt),
        "guard": make_guard(rt), "cost_check": make_cost_check(rt),
        "executor": make_executor(rt), "healer": make_healer(rt),
        "result_checker": make_result_checker(rt), "record_result": make_record_result(),
        "narrator": make_narrator(rt), "synthesizer": make_synthesizer(rt),
        "finalize": make_finalize(),
    }
    g = StateGraph(AgentState)
    for name, fn in nodes.items():
        g.add_node(name, fn)

    after_guard, after_cost, after_executor, after_healer, after_checker = make_routes(rt)
    g.add_edge(START, "router")
    g.add_conditional_edges("router", after_router)
    g.add_edge("plan_simple", "schema_agent")
    g.add_conditional_edges("planner", after_planner)
    g.add_edge("schema_agent", "sql_writer")
    g.add_edge("sql_writer", "guard")
    g.add_conditional_edges("guard", after_guard)
    g.add_conditional_edges("cost_check", after_cost)
    g.add_conditional_edges("executor", after_executor)
    g.add_conditional_edges("healer", after_healer)
    g.add_conditional_edges("result_checker", after_checker)
    g.add_conditional_edges("record_result", after_record)
    for end in ("narrator", "synthesizer", "clarifier", "refusal"):
        g.add_edge(end, "finalize")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer or MemorySaver())
