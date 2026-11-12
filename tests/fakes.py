"""Recorded HTTP boundary with the real vendored device and decoder."""

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from custom_components.cosori_fryer._vendor.pyvesync.device_map import get_air_fryer
from custom_components.cosori_fryer._vendor.pyvesync.devices.vesynckitchen import VeSyncTurboBlazeFryer
from custom_components.cosori_fryer._vendor.pyvesync.models.vesync_models import ResponseDeviceDetailsModel
from custom_components.cosori_fryer.manager import FryerManager

ENTRY_DATA = {
    "email": "test@example.com",
    "password": "test-password",
    "token": "test-token",
    "account_id": "test-account",
    "country_code": "US",
    "region": "US",
}
RECORDED = json.loads((Path(__file__).parent / "fixtures/cs358_status.json").read_text())


class FakeDevice:
    """Control only HTTP responses; all device methods run their real code."""

    def __init__(self):
        self.status = "standby"
        self.current_temp = 100  # Celsius when reported; the recorded firmware sends 0.
        self.cook_temp = 350
        self.preheat_remaining = 240
        self.cook_remaining = 600
        self.error = None
        self.command_error = None
        self.response_code = 0
        self.command_code = 0
        self.command_inner_code = 0
        self.malformed = False
        self.commands = []
        actual_manager = FryerManager("test@example.com", "test-password")
        actual_manager.set_credentials("test-token", "test-account", "US", "US")
        actual_manager.async_call_api = AsyncMock(side_effect=self.http)
        details = ResponseDeviceDetailsModel(
            deviceRegion="US",
            isOwner=False,
            deviceName="Air fryer",
            cid="test-cid",
            connectionType="WiFi",
            deviceType="CS358-AF",
            type="wifi",
            configModule="test",
        )
        self.real = VeSyncTurboBlazeFryer(details, actual_manager, get_air_fryer("CS358-AF"))

    def __getattr__(self, key):
        return getattr(self.real, key)

    async def http(self, endpoint, method, request, headers):
        payload = request.to_dict()["payload"]
        if payload["method"] != "getAirfryerStatus":
            self.commands.append(payload)
            if self.command_error:
                raise self.command_error
            if payload["method"] == "startCook" and not (self.command_code or self.command_inner_code):
                # The fryer keeps the settings it was sent and reports them back.
                self.cook_temp = payload["data"]["startAct"]["cookTemp"]
                self.cook_remaining = payload["data"]["startAct"]["cookSetTime"]
            return {"code": self.command_code, "result": {"code": self.command_inner_code, "result": {}}}, 200
        if self.error:
            raise self.error
        if self.malformed:
            return {"code": 0, "result": []}, 200
        response = deepcopy(RECORDED)
        response["code"] = self.response_code
        raw = response["result"]["result"]
        raw.update(
            cookStatus=self.status,
            currentTemp=self.current_temp,
            preheatSetTime=240,
            preheatLastTime=self.preheat_remaining,
            preheatTemp=self.cook_temp,
            totalTimeRemaining=self.preheat_remaining + self.cook_remaining,
        )
        raw["stepArray"][0]["cookLastTime"] = self.cook_remaining
        raw["stepArray"][0]["cookTemp"] = self.cook_temp
        if self.status == "standby":
            raw["stepArray"] = []
        return response, 200


def fake_manager(device):
    manager = SimpleNamespace(
        devices=[device.real],
        auth=SimpleNamespace(is_authenticated=False),
        **{key: value for key, value in ENTRY_DATA.items() if key in ("token", "account_id", "country_code")},
        current_region="US",
    )

    async def login():
        manager.auth.is_authenticated = True
        return True

    manager.login = AsyncMock(side_effect=login)
    manager.get_devices = AsyncMock(return_value=True)
    manager.set_credentials = lambda *args: setattr(manager.auth, "is_authenticated", True)
    return manager
