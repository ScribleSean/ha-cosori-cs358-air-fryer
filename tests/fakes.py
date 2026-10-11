"""Fakes shared by the tests. The VeSync cloud is never contacted."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import cosori358
from cosori358 import FryerStatus

DEVICE = {
    "deviceName": "Air Fryer",
    "deviceType": "CS358-AF",
    "configModule": "WiFiBTOnboarding_AirFryer_CS358-AF_US",
    "connectionStatus": "online",
    "deviceRegion": "US",
    "cid": "CID-SECRET",
}


def status_result(cook_status: str, **extra) -> dict:
    result = {
        "stepArray": [{"cookSetTime": 720, "cookTemp": 360, "cookLastTime": 0, "mode": "Custom"}],
        "stepIndex": 0,
        "cookStatus": cook_status,
        "tempUnit": "f",
        "preheatSetTime": 0,
        "preheatLastTime": 0,
        "preheatTemp": 0,
        "totalTimeRemaining": 0,
        "currentTemp": 0,
    }
    result.update(extra)
    return result


def make_status(cook_status: str, **extra) -> FryerStatus:
    return FryerStatus.from_result(status_result(cook_status, **extra))


TOKEN_EXPIRED = {"code": -11001000, "msg": "token expired"}


class FakeResponse:
    status = 200

    def __init__(self, data):
        self._data = data

    async def json(self, content_type=None):
        return self._data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """Records every request and answers from `handler(url, body)`."""

    def __init__(self, handler):
        self.handler = handler
        self.calls: list[tuple[str, dict]] = []

    def request(self, method, url, json=None, headers=None, **kwargs):
        self.calls.append((url, json))
        return FakeResponse(self.handler(url, json))


def make_manager(cook_status: str = "standby", inner_code: int = 0):
    """A fake pyvesync manager that answers device list and bypassV2 calls."""
    manager = SimpleNamespace(
        account_id="ACCOUNT-SECRET",
        token="TOKEN-SECRET",
        time_zone="America/New_York",
        country_code="US",
        current_region="US",
        cook_status=cook_status,
        login=AsyncMock(return_value=True),
        _api_base_url_for_current_region=lambda: "https://vesync.test",
    )

    def handler(url, body):
        if url.endswith(cosori358.DEVICE_LIST_PATH):
            return {"code": 0, "result": {"list": [DEVICE]}}
        if body["payload"]["method"] == "getAirfryerStatus":
            return {"code": 0, "result": {"code": 0, "result": status_result(manager.cook_status)}}
        return {"code": 0, "result": {"code": inner_code, "result": {}}}

    manager.session = FakeSession(handler)
    return manager


def sent_methods(manager) -> list[str]:
    return [
        body["payload"]["method"]
        for url, body in manager.session.calls
        if url.endswith(cosori358.BYPASS_V2_PATH)
    ]
