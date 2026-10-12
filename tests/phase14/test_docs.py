"""Phase 14, part 2: the diagrams match the code, and the notes and cheatsheet are complete."""
import re

import pytest

from swiggy_ai.agents.graph import build_graph
from swiggy_ai.agents.runtime import Runtime
from swiggy_ai.config import get_settings
from swiggy_ai.db import connect
from swiggy_ai.semantic.loader import load_semantic
from tests.phase14.docs import DOCS, README, ROOT, makefile_targets, mermaid_blocks

ARCH = ROOT / "docs" / "architecture.md"
DIAGRAM_TYPES = ("flowchart", "erDiagram", "sequenceDiagram", "graph", "stateDiagram")


# --- diagrams in general ------------------------------------------------------------
def test_every_fenced_block_is_closed():
    for path in DOCS:
        assert path.read_text().count("```") % 2 == 0, f"{path.name}: an unclosed code fence"


@pytest.mark.parametrize("path", DOCS, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_every_mermaid_block_starts_with_a_real_diagram_type_and_balances_its_brackets(path):
    for block in mermaid_blocks(path):
        assert block.strip().startswith(DIAGRAM_TYPES), block[:40]
        body = re.sub(r'"[^"]*"', '""', block)  # brackets inside labels do not count
        pairs = ("()", "[]") if block.strip().startswith("erDiagram") else ("()", "[]", "{}")
        for open_, close in pairs:  # (an ER diagram's crow's feet use { and } on their own)
            assert body.count(open_) == body.count(close), f"{path.name}: unbalanced {open_}{close}"
        assert body.count("subgraph") == len(re.findall(r"^\s*end\s*$", body, re.M)), path.name


def test_the_architecture_document_has_its_four_pictures():
    assert len(mermaid_blocks(ARCH)) == 4
    text = ARCH.read_text()
    for heading in ("a) Data flow", "b) The agent graph", "c) Security layers", "d) A request"):
        assert heading in text


# --- the agent graph diagram is the compiled graph --------------------------------------------
def diagram_edges(block: str) -> set[tuple[str, str]]:
    edges = set()
    for line in block.splitlines():
        parts = re.split(r"\s*-->(?:\|[^|]*\|)?\s*", line.strip())
        ids = [m.group(1) for p in parts if (m := re.match(r"([A-Za-z_]+)", p))]
        if len(parts) > 1 and len(ids) == len(parts):
            edges |= set(zip(ids, ids[1:], strict=False))
    rename = {"START": "__start__", "END": "__end__"}
    return {(rename.get(a, a), rename.get(b, b)) for a, b in edges}


@pytest.fixture(scope="module")
def compiled_edges():
    rt = Runtime(settings=get_settings(), semantic=load_semantic(), llm=None, open_assistant=None,
                 open_service=None, clock=None)
    g = build_graph(rt).get_graph()
    return {(e.source, e.target) for e in g.edges}, set(g.nodes)


def test_the_agent_graph_diagram_has_exactly_the_edges_of_the_real_graph(compiled_edges):
    edges, _ = compiled_edges
    drawn = diagram_edges(mermaid_blocks(ARCH)[1])
    assert drawn - edges == set(), f"drawn but not in the code: {sorted(drawn - edges)}"
    assert edges - drawn == set(), f"in the code but not drawn: {sorted(edges - drawn)}"


def test_the_agent_graph_diagram_names_every_node(compiled_edges):
    _, nodes = compiled_edges
    ids = {x for edge in diagram_edges(mermaid_blocks(ARCH)[1]) for x in edge}
    assert ids == nodes


def test_the_chapter_8_note_mentions_the_same_guarantees_as_the_graph():
    text = (ROOT / "notes" / "chapter_08.md").read_text()
    for part in ("guard", "healer", "result checker", "narration"):
        assert part in text.lower()


# --- the security picture names real files ----------------------------------------------------
def test_every_file_named_in_the_security_table_exists():
    text = ARCH.read_text()
    pattern = r"`((?:src/swiggy_ai|sql|semantic)/[\w/.-]+|(?:agents|security)/[\w/.]+\.py)`"
    paths = re.findall(pattern, text)
    assert len(paths) >= 6
    for p in paths:
        assert (ROOT / p).exists() or (ROOT / "src" / "swiggy_ai" / p).exists(), p


# --- the ERD ------------------------------------------------------------
@pytest.mark.db
def test_every_entity_and_column_in_the_erd_exists_in_gold():
    text = (ROOT / "docs" / "erd.md").read_text()
    blocks = dict(re.findall(r"^    (\w+) \{\n(.*?)^    \}", text, re.S | re.M))
    assert len(blocks) >= 9
    with connect("etl", autocommit=True) as c:
        for entity, body in blocks.items():
            table = entity.lower()
            cols = {r[0] for r in c.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = 'gold'"
                " AND table_name = %s", (table,))}
            assert cols, f"gold.{table} does not exist"
            for field in (line.split()[1] for line in body.strip().splitlines()):
                assert field in cols, f"{table}.{field}"


# --- the notes ------------------------------------------------------------
NOTES = sorted((ROOT / "notes").glob("chapter_*.md"))


def test_there_is_a_note_for_every_phase_from_00_to_14():
    assert [p.name for p in NOTES] == [f"chapter_{i:02d}.md" for i in range(15)]


@pytest.mark.parametrize("note", NOTES, ids=lambda p: p.name)
def test_each_note_has_the_five_parts_and_fits_on_a_page(note):
    text = note.read_text()
    assert text.startswith(f"# Chapter {note.stem[-2:]}")
    for part in ("**What we build", "**Why it matters at work", "**Commands", "**Common mistakes",
                 "**Interview line"):
        assert part in text, f"{note.name} lacks {part}"
    assert len(text.split()) <= 560, f"{note.name} is {len(text.split())} words: over a page"


def test_every_command_a_note_names_exists():
    real = set(makefile_targets())
    for note in NOTES:
        for line in note.read_text().splitlines():
            if line.startswith("**Commands"):
                for cmd in re.findall(r"`make ([A-Za-z0-9-]+)", line):
                    assert cmd in real or cmd == "test-NN", f"{note.name}: make {cmd}"


def test_notes_give_no_accuracy_claim_except_the_eval_chapter():
    for note in NOTES:
        if note.name in ("chapter_10.md", "chapter_13.md"):
            continue
        assert not re.search(r"\b(accuracy|accurate)\b[^.\n]*\d+\s*%", note.read_text(), re.I)


# --- the cheatsheet ------------------------------------------------------------
def test_the_cheatsheet_has_a_two_minute_pitch_and_twelve_answers():
    text = (ROOT / "docs" / "interview_cheatsheet.md").read_text()
    assert "## The two-minute version" in text
    questions = re.findall(r"^\*\*(\d+)\. (.+\?)\*\*$", text, re.M)
    assert [int(n) for n, _ in questions] == list(range(1, 13))
    pitch = text.split("## Twelve follow-ups")[0]
    assert 150 <= len(pitch.split()) <= 400  # about two minutes aloud
    lowered = text.lower()
    for topic in ("idempot", "scd type 2", "personal data", "fan-out", "guard", "partition",
                  "schema", "evaluate"):
        assert topic in lowered, topic
    for answer in re.split(r"^\*\*\d+\. .+\*\*$", text, flags=re.M)[1:]:
        assert 25 <= len(answer.split()) <= 200  # short, not an essay


def test_the_cheatsheet_quotes_no_unproven_numbers():
    text = (ROOT / "docs" / "interview_cheatsheet.md").read_text()
    assert not re.search(r"\d\s*%", text)
    assert "Forty-five" in text and "Thirty" in text  # structural facts: checks and questions


# --- housekeeping ------------------------------------------------------------
def test_every_make_target_is_declared_phony():
    makefile = (ROOT / "Makefile").read_text()
    phony = set(re.search(r"^\.PHONY: (.*)$", makefile, re.M).group(1).split())
    missing = [t for t in makefile_targets() if t not in phony]
    assert not missing, f"not in .PHONY: {missing}"


def test_the_readme_links_to_every_doc():
    text = README.read_text()
    for doc in ("docs/architecture.md", "docs/erd.md", "docs/interview_cheatsheet.md", "notes/"):
        assert doc in text


def test_the_diagram_comparison_really_notices_a_wrong_diagram():
    block = mermaid_blocks(ARCH)[1]
    good = diagram_edges(block)
    removed = block.replace("    healer -->|new query| guard\n", "")
    assert removed != block and diagram_edges(removed) == good - {("healer", "guard")}
    invented = block + "    router --> executor\n"
    assert diagram_edges(invented) - good == {("router", "executor")}
