"""Enable custom integrations and replace the VeSync manager."""

from unittest.mock import patch

import pytest
from fakes import FakeDevice, fake_manager


@pytest.fixture(autouse=True)
def auto_enable(enable_custom_integrations):
    yield


@pytest.fixture
def device():
    return FakeDevice()


@pytest.fixture
def manager(device):
    manager = fake_manager(device)
    with (
        patch("custom_components.cosori_fryer.create_manager", return_value=manager),
        patch("custom_components.cosori_fryer.config_flow.create_manager", return_value=manager),
    ):
        yield manager
