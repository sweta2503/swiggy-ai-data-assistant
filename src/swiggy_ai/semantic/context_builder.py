"""Build the prompt context for ONE question: just the metrics, views and terms it needs.

Why not paste the whole catalog? Cost, and focus: a model asked about refunds does better when
shown the refunds view than 77 columns it will never use. No LLM is involved here, so the
choice is deterministic and testable.

The text is trimmed in a fixed order until it fits the token budget. Never trimmed: the
conventions (IST, Monday weeks, what "today" means), the best metric, and the do-not notes of
the best view.
"""
import math
import re
from dataclasses import dataclass, field

from swiggy_ai.semantic.loader import Semantic, load_semantic
from swiggy_ai.semantic.models import Metric, Table

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "by", "can", "did", "do", "does", "for", "from", "get",
    "give", "had", "has", "have", "how", "i", "in", "is", "it", "me", "my", "of", "on", "or",
    "our", "show", "tell", "than", "that", "the", "their", "there", "these", "this", "to", "was",
    "we", "were", "what", "when", "where", "which", "who", "why", "with", "us", "you", "many",
    "much", "all", "any", "each", "per", "last", "total",
}
TIME_WORDS = {"today", "yesterday", "week", "month", "recent", "recently", "ago", "current",
              "latest", "year", "quarter", "days", "day", "weekly", "monthly", "daily"}


def estimate_tokens(text: str) -> int:
    """A rough count (about 4 characters per token). Good enough for budgeting."""
    return math.ceil(len(text) / 4)


def words(text: str) -> set[str]:
    out = set()
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        if w in STOPWORDS:
            continue
        out.add(w[:-1] if len(w) > 3 and w.endswith("s") else w)  # orders -> order
    return out


def phrase_in(phrase: str, question: str) -> bool:
    return bool(re.search(r"\b" + re.escape(phrase.lower()) + r"s?\b", question.lower()))


@dataclass
class ContextBundle:
    text: str
    tables: list[str]
    metrics: list[str]
    glossary: list[str]
    tokens: int
    trimmed: bool = False
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- scoring ---------------------
def score_metric(m: Metric, question: str, qwords: set[str]) -> int:
    score = 0
    if phrase_in(m.name.replace("_", " "), question) or phrase_in(m.name, question):
        score += 6
    for syn in m.synonyms:
        if phrase_in(syn, question):
            score += 5 if " " in syn else 4
    score += 2 * len(qwords & words(m.name.replace("_", " ")))
    score += len(qwords & words(m.description)) // 2
    return score


def value_owners(sem: Semantic) -> dict[frozenset[str], str]:
    """allowed value (as a set of words) -> the ONE view whose column allows it.

    "beverage" appears only in order_items.item_category ('Beverages'), so a question that says it
    is about that view. A value that several views allow ('delivered', 'Pune', 'new') says nothing
    about which view is meant, so it is left out. A value of several words ('LATE_DELIVERY') only
    counts when the question has all of them: 'delivery' alone is an everyday word."""
    seen: dict[frozenset[str], set[str]] = {}
    for t in sem.catalog.tables:
        for c in t.columns:
            for value in c.allowed_values:
                tokens = frozenset(words(value.replace("_", " ")))
                if tokens:
                    seen.setdefault(tokens, set()).add(t.name)
    return {tokens: next(iter(names)) for tokens, names in seen.items() if len(names) == 1}


def score_table(t: Table, question: str, qwords: set[str],
                owners: dict[frozenset[str], str] | None = None) -> int:
    score = 0
    if owners:  # naming a value only this view can hold is strong evidence for it
        hits = sum(1 for tokens, owner in owners.items() if owner == t.name and tokens <= qwords)
        score += min(8, 4 * hits)
    short = t.name.split(".")[1]
    if phrase_in(short.replace("_", " "), question):
        score += 6
    for syn in t.synonyms:
        if phrase_in(syn, question):
            score += 4
    names = {c.name for c in t.columns}
    score += 2 * len({w for w in qwords if w in names or w + "s" in names})
    col_words = set().union(*(words(c.name.replace("_", " ")) for c in t.columns))
    score += len(qwords & col_words)
    return score


# ---------------------------------------------------------------- rendering ---------------------
def render_conventions(sem: Semantic) -> str:
    c = sem.metrics.conventions
    lines = ["## Conventions", f"- Timezone {c.timezone}. Weeks start on {c.week_starts_on}.",
             f"- {c.reference_date.strip()}"]
    lines += [f"- {r}" for r in c.rules]
    if c.period_templates:
        lines.append("- Period filters (T = today):")
        lines += [f"    {k}: {v}" for k, v in c.period_templates.items()]
    return "\n".join(lines)


def render_metric(m: Metric) -> str:
    syn = f" (also: {', '.join(m.synonyms[:4])})" if m.synonyms else ""
    return (f"- {m.name}{syn}: {m.description.strip()}\n"
            f"    use exactly: {m.sql_expression}\n"
            f"    FROM {m.source}; filter dates on {m.time_column}")


def render_table(t: Table, level: int) -> str:
    """level 2 = everything, 1 = compact, 0 = one line."""
    if level == 0:
        return f"- {t.name}: {t.description.strip()} Grain: {t.grain}."
    out = [f"### {t.name}  ({t.kind}; grain: {t.grain})", t.description.strip()]
    for c in t.columns:
        extra = ""
        if c.allowed_values:
            extra = f" [one of: {', '.join(c.allowed_values)}]"
        elif level == 2 and c.examples:
            extra = f" [e.g. {', '.join(c.examples[:2])}]"
        text = f" - {c.description.strip()}" if level == 2 else ""
        out.append(f"  {c.name} {c.type}{extra}{text}")
    if level == 2 and t.join_hints:
        out += ["Joins:"] + [f"  - {h.strip()}" for h in t.join_hints]
    if t.do_not:
        out += ["Do not:"] + [f"  - {d.strip()}" for d in t.do_not]
    return "\n".join(out)


# ---------------------------------------------------------------- the builder -------------------
def build_context(question: str, budget_tokens: int = 2200,
                  sem: Semantic | None = None) -> ContextBundle:
    sem = sem or load_semantic()
    qwords = words(question)

    # 1. choose what is relevant
    scored_m = sorted(((score_metric(m, question, qwords), m) for m in sem.metrics.metrics),
                      key=lambda x: (-x[0], x[1].name))
    metrics = [m for s, m in scored_m if s >= 4][:4]

    owners = value_owners(sem)
    scored_t = sorted(((score_table(t, question, qwords, owners), t) for t in sem.catalog.tables),
                      key=lambda x: (-x[0], x[1].name))
    tables: list[Table] = []
    for m in metrics:  # the views the chosen metrics run on come first
        t = sem.catalog.table(m.source)
        if t and t not in tables:
            tables.append(t)
    for s, t in scored_t:
        if s >= 4 and t not in tables and len(tables) < 3:
            tables.append(t)
    if not tables:  # nothing matched: fall back to the main fact table
        tables = [sem.catalog.table("analytics.orders")]
    wants_dates = bool(qwords & TIME_WORDS)
    as_of = sem.catalog.table("analytics.data_as_of")
    if wants_dates and as_of and as_of not in tables:
        tables.append(as_of)

    glossary = [g for g in sem.glossary
                if any(phrase_in(name, question) for name in [g.term, *g.also_called])][:4]

    others = [t for t in sem.catalog.tables if t not in tables]

    # 2. render, then trim in a fixed order until it fits
    levels = {t.name: 2 for t in tables}
    keep_glossary = list(glossary)
    keep_metrics = list(metrics)
    show_others = True
    notes: list[str] = []

    def render() -> str:
        parts = [render_conventions(sem)]
        if keep_metrics:
            parts += ["## Metrics (use these definitions verbatim)"] + [
                render_metric(m) for m in keep_metrics]
        parts.append("## Views you may use (and nothing else)")
        parts += [render_table(t, levels[t.name]) for t in tables]
        if show_others and others:
            parts += ["## Other views available"] + [render_table(t, 0) for t in others]
        if keep_glossary:
            parts += ["## Terms"] + [f"- {g.term}: {g.definition}" for g in keep_glossary]
        return "\n".join(parts)

    text = render()
    trimmed = False

    def over() -> bool:
        return estimate_tokens(text) > budget_tokens

    while over():
        trimmed = True
        if keep_glossary:
            notes.append(f"dropped glossary term '{keep_glossary.pop().term}'")
        elif show_others:
            show_others = False
            notes.append("dropped the list of other views")
        elif len(keep_metrics) > 1:
            notes.append(f"dropped metric '{keep_metrics.pop().name}'")
        elif any(levels[t.name] == 2 for t in tables[1:]):
            t = [t for t in tables[1:] if levels[t.name] == 2][-1]
            levels[t.name] = 1
            notes.append(f"shortened {t.name}")
        elif levels[tables[0].name] == 2:
            levels[tables[0].name] = 1
            notes.append(f"shortened {tables[0].name}")
        elif any(levels[t.name] == 1 for t in tables[1:]):
            t = [t for t in tables[1:] if levels[t.name] == 1][-1]
            levels[t.name] = 0
            notes.append(f"reduced {t.name} to one line")
        else:
            break  # what is left is the part that must never be cut
        text = render()

    return ContextBundle(text=text, tables=[t.name for t in tables],
                         metrics=[m.name for m in keep_metrics],
                         glossary=[g.term for g in keep_glossary],
                         tokens=estimate_tokens(text), trimmed=trimmed, notes=notes)
