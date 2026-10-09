import pytest

from swiggy_ai.config import get_settings


def pytest_collection_modifyitems(config, items):
    s = get_settings()
    for item in items:
        if "live" in item.keywords and not (s.openai_api_key and s.openai_model):
            item.add_marker(pytest.mark.skip(reason="no OPENAI_API_KEY/OPENAI_MODEL"))
