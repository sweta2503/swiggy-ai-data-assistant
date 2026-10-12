"""Small helpers for reading the documentation."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
DOCS = sorted([README, *(ROOT / "docs").glob("*.md"), *(ROOT / "notes").glob("*.md")])
MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
FENCE = re.compile(r"^```(\w*)\n(.*?)^```", re.S | re.M)


def mermaid_blocks(path: Path) -> list[str]:
    return [body for lang, body in FENCE.findall(path.read_text()) if lang == "mermaid"]


def makefile_targets() -> dict[str, str]:
    """target -> its `##` description, for every documented target."""
    out = {}
    for line in (ROOT / "Makefile").read_text().splitlines():
        m = re.match(r"^([a-zA-Z0-9_-]+):[^#]*## (.*)$", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def section(text: str, heading: str) -> str:
    m = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    assert m, f"no '## {heading}' section"
    return m.group(1)
