import os
import re
import sys

import psycopg
from pydantic import BaseModel

from swiggy_ai.security.scope import assistant_connection
from swiggy_ai.semantic.context_builder import build_context

from .guard_mini import check
from .llm_mini import ask


class SQLDraft(BaseModel):
    sql: str


class Narration(BaseModel):
    answer: str


def write_sql(question: str) -> str:
    rules = "Write ONE PostgreSQL SELECT. Use only the views below. No semicolon.\n\n"
    return ask(rules + build_context(question).text, question, SQLDraft).sql


def repair(question: str, bad_sql: str, error: str) -> str:
    prompt = f"Question: {question}\nFailed query:\n{bad_sql}\nError: {error}\nFix it."
    rules = "Repair this PostgreSQL query with the smallest change.\n\n"
    return ask(rules + build_context(question).text, prompt, SQLDraft).sql


def run(sql: str, scope: str = "ALL"):
    with assistant_connection(scope) as conn:
        cur = conn.execute(sql)
        return [d.name for d in cur.description], cur.fetchall()


def break_it(sql: str) -> str:
    return sql.replace(" FROM ", ", total_amount FROM ", 1)


def numbers_ok(text: str, rows) -> bool:
    text = re.sub(r"\d{4}-\d{2}-\d{2}", "", text)
    said = [float(x.replace(",", "")) for x in re.findall(r"\d[\d,]*\.?\d*", text)]
    have = []
    for row in rows:
        for v in row:
            try:
                have.append(float(v))
            except (TypeError, ValueError):
                pass
    return all(any(abs(s - h) <= 0.005 * max(1, abs(h)) for h in have) for s in said)


def narrate(question: str, columns, rows) -> str:
    table = "\n".join(" | ".join(map(str, r)) for r in [columns, *rows[:20]])
    rules = "Explain this result in 2 or 3 plain sentences. Use ONLY numbers from the result."
    text = ask(rules, f"Question: {question}\n{table}", Narration, tier="low").answer
    return text if numbers_ok(text, rows) else "Result:\n" + table


def answer(question: str, scope: str = "ALL", max_tries: int = 3) -> dict:
    sql, seen = write_sql(question), set()
    if os.environ.get("BREAK_FIRST"):
        sql = break_it(sql)
    for attempt in range(1, max_tries + 1):
        ok, safe = check(sql)
        if not ok:
            return {"status": "refused", "why": safe, "sql": sql}
        try:
            columns, rows = run(safe, scope)
            text = narrate(question, columns, rows)
            return {"status": "answered", "sql": safe, "answer": text, "attempts": attempt}
        except psycopg.Error as e:
            print(f"attempt {attempt} failed: {str(e).splitlines()[0]}")
            if safe in seen:
                break
            seen.add(safe)
            sql = repair(question, safe, str(e))
    return {"status": "failed", "sql": sql}


if __name__ == "__main__":
    print(answer(" ".join(sys.argv[1:]) or "How many orders did we get last week?"))
