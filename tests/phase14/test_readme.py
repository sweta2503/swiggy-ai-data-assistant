"""Phase 14, part 1: the README is true, complete and copy-pasteable."""
import re
import shlex

import pytest

from tests.phase14.docs import DOCS, MARKDOWN_LINK, README, ROOT, makefile_targets, section

TEXT = README.read_text()


def test_every_documented_make_target_is_in_the_readme():
    missing = []
    for target in makefile_targets():
        if target == "help":
            continue
        if re.fullmatch(r"test-\d\d", target):
            if "make test-NN" not in TEXT:
                missing.append("test-NN")
            continue
        if f"make {target}" not in TEXT:
            missing.append(target)
    assert not missing, f"README does not mention: {sorted(set(missing))}"


def test_every_make_command_in_the_readme_is_a_real_target():
    real = set(makefile_targets()) | {"help"}
    for line in TEXT.splitlines():
        for cmd in re.findall(r"`make ([A-Za-z0-9-]+)", line) + re.findall(
                r"^make ([A-Za-z0-9-]+)", line.strip()):
            if cmd == "test-NN":
                continue
            assert cmd in real, f"README mentions `make {cmd}` which does not exist"


def test_the_quick_start_has_ten_numbered_steps_and_every_command_exists():
    block = re.search(r"```bash\n(.*?)```", section(TEXT, "Quick start"), re.S).group(1)
    steps = re.findall(r"#\s*(\d+)\.", block)
    assert steps == [str(i) for i in range(1, 11)]
    real = set(makefile_targets())
    for line in block.splitlines():
        words = shlex.split(line, comments=True)
        if words[:1] == ["make"]:
            targets = [w for w in words[1:] if "=" not in w]
            assert targets and all(t in real for t in targets), line


def test_the_quick_start_runs_the_pipeline_in_a_working_order():
    block = re.search(r"```bash\n(.*?)```", section(TEXT, "Quick start"), re.S).group(1)
    order = [t for t in re.findall(r"make ([a-z0-9-]+(?: [a-z0-9-]+)*)", block.replace(
        'Q="How many orders did we get last week?" WHO=admin', "")) for t in t.split()]
    pipeline = ["venv", "env", "up", "check", "data", "bronze", "silver-full", "gold-full", "dq",
                "security", "seed-users", "ask", "ui"]
    positions = [order.index(t) for t in pipeline]
    assert positions == sorted(positions)


def test_the_readme_has_the_sections_the_spec_asks_for():
    for heading in ("What it does", "Architecture", "Prerequisites", "Quick start",
                    "Run the demo", "Make targets", "Folder tour", "Troubleshooting"):
        assert re.search(rf"^## {heading}$", TEXT, re.M), heading
    assert "```mermaid" in section(TEXT, "Architecture")


def test_the_readme_states_no_accuracy_figures():
    assert not re.search(r"\d\s*%", TEXT), "a percentage in the README needs a source"
    assert not re.search(r"\b\d+\s*(?:/|of)\s*30\b", TEXT)
    lowered = TEXT.lower()
    assert "run `make eval`" in lowered and "no accuracy figure" in lowered


def test_every_path_in_the_folder_tour_exists():
    tour = section(TEXT, "Folder tour")
    paths = re.findall(r"^\| `([^`]+)` \|", tour, re.M)
    assert len(paths) >= 12
    for raw in paths:
        for part in re.split(r",\s*", raw):
            base = part.split()[0]
            candidates = [ROOT / base]
            if base.endswith("/") or "." not in base.rsplit("/", 1)[-1]:
                candidates.append(ROOT / "src" / "swiggy_ai" / base.rstrip("/"))
            assert any(c.exists() for c in candidates), f"folder tour lists {base}"
    assert (ROOT / "tests" / "phase14").exists()


def test_the_demo_commands_use_real_settings_and_users():
    from swiggy_ai.config import Settings
    from swiggy_ai.security.__main__ import DEMO_USERS
    assert "demo_fault_injection" in Settings.model_fields and "DEMO_FAULT_INJECTION=true" in TEXT
    users = {u[0] for u in DEMO_USERS}
    for name in re.findall(r"WHO=(\w+)", TEXT) + re.findall(r"`WHO=(\w+)`", TEXT):
        assert name == "admin" or name in users, name


def test_the_env_variables_the_readme_tells_you_to_set_exist_in_the_example():
    example = (ROOT / ".env.example").read_text()
    for var in re.findall(r"`((?:OPENAI|API_KEY|PRICE|POSTGRES|DATABASE)[A-Z_*]*)`", TEXT):
        assert var.replace("*", "") in example, var


def test_the_readme_names_the_dangerous_target_and_the_key_hygiene():
    assert "**danger:**" in TEXT and "never commit it" in TEXT.lower()


@pytest.mark.parametrize("path", DOCS, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_every_relative_link_resolves(path):
    for target in MARKDOWN_LINK.findall(path.read_text()):
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        assert (path.parent / target.split("#")[0]).resolve().exists(), f"{path.name}: {target}"
