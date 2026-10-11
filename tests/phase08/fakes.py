"""A scripted stand-in for the model, so the agent's behaviour can be tested without calling OpenAI.

    llm = FakeLLM()
    llm.say(SQLDraft, SQLDraft(sql="SELECT ...", assumptions=[]))    # the next SQLDraft reply
    llm.say(Narration, lambda user_input: Narration(answer="..."))  # or compute it from the prompt
Every call is recorded in llm.calls (schema name, tier, the prompt text and the user input).
"""
from collections import defaultdict, deque
from dataclasses import dataclass

from swiggy_ai.llm import LLMResult, Usage


@dataclass
class Call:
    schema: str
    tier: str
    instructions: str
    user_input: str


class FakeLLM:
    def __init__(self):
        self.script: dict[str, deque] = defaultdict(deque)
        self.calls: list[Call] = []

    def say(self, schema, *replies):
        for r in replies:
            self.script[schema.__name__].append(r)
        return self

    def __call__(self, *, instructions, user_input, schema, tier="medium", temperature=0.0,
                 retries=3):
        name = schema.__name__
        self.calls.append(Call(name, tier, instructions, user_input))
        queue = self.script[name]
        if not queue:
            raise AssertionError(f"the test did not script a reply for {name}")
        reply = queue.popleft()
        parsed = reply(user_input) if callable(reply) else reply
        return LLMResult(parsed=parsed, usage=Usage(input_tokens=100, output_tokens=20))

    def count(self, schema) -> int:
        return sum(1 for c in self.calls if c.schema == schema.__name__)
