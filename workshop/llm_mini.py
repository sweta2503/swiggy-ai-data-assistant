import os

import openai
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()
client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])


def model_for(tier: str) -> str:
    return os.environ.get(f"OPENAI_MODEL_{tier.upper()}") or os.environ["OPENAI_MODEL"]


def ask(instructions: str, question: str, schema: type[BaseModel], tier: str = "medium"):
    reply = client.responses.parse(
        model=model_for(tier),
        instructions=instructions,
        input=question,
        text_format=schema,
    )
    return reply.output_parsed


if __name__ == "__main__":
    class Route(BaseModel):
        kind: str

    print(ask("Say 'data' or 'other'.", "How many orders last week?", Route, tier="low"))
