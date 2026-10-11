"""Phase 7 tests check the live security setup, so make sure it is applied first."""
import pytest

from swiggy_ai.security.setup import apply_security


@pytest.fixture(scope="session", autouse=True)
def security_applied():
    apply_security()  # idempotent; the same thing `make security` does
