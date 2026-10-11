import pytest


@pytest.fixture(autouse=True)
def _enable_custom_integrations(request):
    """Let Home Assistant tests load custom_components; a no-op for CLI tests."""
    if "hass" in request.fixturenames:
        request.getfixturevalue("enable_custom_integrations")
