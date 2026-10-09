"""`make check`: is everything wired up? Prints one line per check, exits 1 on failure."""
import sys

from pydantic import BaseModel

from swiggy_ai.config import get_settings
from swiggy_ai.db import connect
from swiggy_ai.llm import LLMError, complete_structured


class Pong(BaseModel):
    reply: str


def check_env() -> tuple[bool, str]:
    s = get_settings()
    missing = [n for n, v in [("OPENAI_API_KEY", s.openai_api_key),
                              ("OPENAI_MODEL", s.openai_model)] if not v]
    return (not missing, "env loaded" if not missing else f"missing in .env: {', '.join(missing)}")


def check_db() -> tuple[bool, str]:
    try:
        with connect("admin") as conn:
            ver = conn.execute("select version()").fetchone()[0].split(",")[0]
            schemas = {r[0] for r in conn.execute(
                "select schema_name from information_schema.schemata")}
        need = {"bronze", "silver", "gold", "analytics", "semantic", "ops"}
        if need - schemas:
            return False, f"schemas missing: {sorted(need - schemas)} (try `make reset-db`)"
        return True, f"database reachable ({ver}), 6 schemas present"
    except Exception as e:  # noqa: BLE001 - we want any failure reported plainly
        return False, f"cannot reach database: {e}. Is Docker running? Try `make up`."


def check_roles() -> tuple[bool, str]:
    try:
        with connect("assistant") as conn:
            conn.execute("select 1")
        with connect("etl") as conn:
            conn.execute("select 1")
        return True, "etl_writer and assistant_ro can log in"
    except Exception as e:  # noqa: BLE001
        return False, f"role login failed: {e}"


def check_llm() -> tuple[bool, str]:
    try:
        r = complete_structured(
            instructions="Reply with the single word pong.",
            user_input="ping",
            schema=Pong,
            tier="low",  # a health check never needs the expensive model
        )
        return True, f"LLM call ok ({r.usage.input_tokens}+{r.usage.output_tokens} tokens)"
    except LLMError as e:
        return False, str(e)


def main() -> int:
    ok_all = True
    for name, fn in [("env", check_env), ("database", check_db),
                     ("db roles", check_roles), ("llm", check_llm)]:
        ok, msg = fn()
        ok_all &= ok
        print(f"[{'ok' if ok else 'FAIL'}] {name}: {msg}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
