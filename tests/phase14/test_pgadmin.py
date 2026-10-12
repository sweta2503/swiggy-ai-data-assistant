"""Phase 14, part 4: the optional pgAdmin, and the database tour that goes with it.
Nothing here starts pgAdmin or downloads its image; the tour's SQL is run for real."""
import json
import re

import pytest
import yaml

from swiggy_ai.db import connect
from tests.phase14.docs import ROOT, makefile_targets

COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
TOUR = (ROOT / "docs" / "db_tour.md").read_text()


def test_pgadmin_is_optional_pinned_and_bound_to_this_machine_only():
    svc = COMPOSE["services"]["pgadmin"]
    assert svc["profiles"] == ["tools"]  # `make up` does not start it
    assert "profiles" not in COMPOSE["services"]["postgres"]
    assert not svc["image"].endswith(":latest") and ":" in svc["image"]  # pinned
    assert svc["ports"] == ["127.0.0.1:5050:80"]  # never exposed on the network
    assert svc["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert svc["environment"]["PGADMIN_CONFIG_SERVER_MODE"] == "False"


def test_the_registered_servers_exist_and_point_at_the_database_inside_compose():
    mounted = [v for v in COMPOSE["services"]["pgadmin"]["volumes"] if "servers.json" in v]
    assert len(mounted) == 1 and mounted[0].endswith(":ro")  # read-only
    servers = json.loads((ROOT / "scripts" / "pgadmin_servers.json").read_text())["Servers"]
    assert len(servers) == 2
    for server in servers.values():
        assert (server["Host"], server["Port"], server["MaintenanceDB"]) == (
            "postgres", 5432, "swiggy")
        assert "Password" not in server  # no password is ever stored in the repository
    assert {s["Username"] for s in servers.values()} == {"swiggy_admin", "assistant_ro"}


@pytest.mark.db
def test_the_roles_in_the_server_list_are_real_roles():
    with connect("admin", autocommit=True) as c:
        roles = {r[0] for r in c.execute("SELECT rolname FROM pg_roles")}
    assert {"swiggy_admin", "assistant_ro"} <= roles


def test_the_make_targets_exist_and_the_readme_and_tour_say_how_to_start_it():
    targets = makefile_targets()
    assert "pgadmin" in targets and "pgadmin-down" in targets
    assert "--profile tools" in (ROOT / "Makefile").read_text()
    assert "make pgadmin" in TOUR and "http://localhost:5050" in TOUR
    assert "docs/db_tour.md" in (ROOT / "README.md").read_text()


def test_the_passwords_the_tour_names_are_the_dev_defaults_in_the_example_env():
    example = (ROOT / ".env.example").read_text()
    for var, value in (("POSTGRES_ADMIN_PASSWORD", "admin_dev_pw"),
                       ("ASSISTANT_PASSWORD", "assistant_dev_pw")):
        assert f"{var}={value}" in example and value in TOUR
    assert "port 5433" in TOUR  # for anyone using their own client


@pytest.mark.db
def test_every_sql_block_in_the_tour_runs_and_only_the_intended_ones_are_refused():
    """The guide must not rot: it is run as the role each part tells you to use."""
    blocks = re.findall(r"```sql\n(.*?)```", TOUR, re.S)
    assert len(blocks) >= 12
    admin, assistant = connect("admin", autocommit=True), connect("assistant", autocommit=True)
    refused = []
    try:
        for block in blocks:
            as_ai = ("table_schema = 'analytics'" in block) or ("SET app.allowed_cities" in block) \
                or ("gold.fact_orders;" in block and "ops.assistant_runs;" in block)
            conn = assistant if as_ai else admin
            for stmt in (x.strip() for x in re.sub(r"--[^\n]*", "", block).split(";")):
                if not stmt:
                    continue
                try:
                    conn.execute(stmt).fetchall() if stmt.upper().startswith("SELECT") \
                        else conn.execute(stmt)
                except Exception as e:  # noqa: BLE001
                    assert conn is assistant and "permission denied" in str(e), stmt[:80]
                    refused.append(stmt)
    finally:
        admin.close()
        assistant.close()
    assert len(refused) == 2
    assert "gold.fact_orders" in refused[0] and "ops.assistant_runs" in refused[1]


def test_the_tour_makes_no_claims_about_this_machines_data_volumes():
    assert not re.search(r"\b\d{2,3},\d{3}\b", TOUR), "row counts change; leave them out"


def test_the_pgadmin_login_email_is_not_a_reserved_name():
    """pgAdmin refuses to start if the email's domain is special-use ('admin@swiggy.local' did)."""
    email = COMPOSE["services"]["pgadmin"]["environment"]["PGADMIN_DEFAULT_EMAIL"]
    assert re.fullmatch(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", email), email
    labels = email.split("@")[1].lower().split(".")
    reserved = {"local", "localhost", "test", "invalid", "internal", "lan", "home", "corp",
                "localdomain", "onion"}
    assert labels[-1] not in reserved, f"{email}: the domain ends in a reserved name"
