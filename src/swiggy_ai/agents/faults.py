"""Demo-only fault injection. See eval/fault_injection.yml for what it does and why."""
import re
from pathlib import Path

import yaml

FAULT_FILE = Path(__file__).resolve().parents[3] / "eval" / "fault_injection.yml"


def load_faults(path: Path = FAULT_FILE) -> list[dict]:
    return yaml.safe_load(path.read_text())["faults"]


def inject_fault(sql: str, name: str = "", path: Path = FAULT_FILE) -> tuple[str, dict]:
    """Return (broken SQL, the fault that was used). Picks `name`, else the first fault that
    applies to this query, else the first fault's fallback."""
    faults = load_faults(path)
    if name:
        faults = [f for f in faults if f["name"] == name] or faults
    for f in faults:
        if f.get("find") and re.search(f["find"], sql, flags=re.I):
            return re.sub(f["find"], f["replace"], sql, count=1, flags=re.I), f
    return faults[0]["fallback_sql"], faults[0]
