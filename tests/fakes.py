"""Fakes shared by the tests. VeSync is never contacted."""

from __future__ import annotations

from typing import Any

from custom_components.cosori_fryer.api import (
    Device,
    FryerStatus,
    NoFryer,
    Session,
    TokenRejected,
    check_settings,
)

DEVICE = Device(
    cid="CID-1",
    name="Air Fryer",
    config_module="WiFiBTOnboarding_AirFryer_CS358-AF_US",
    region="US",
)


def raw(state: str, **extra: Any) -> dict[str, Any]:
    result = {
        "cookStatus": state,
        "stepIndex": 0,
        "stepArray": [{"cookTemp": 360, "cookSetTime": 600, "cookLastTime": 600, "mode": "Custom"}],
        "preheatTemp": 360,
        "preheatSetTime": 240,
        "preheatLastTime": 0,
        "totalTimeRemaining": 600,
    }
    result.update(extra)
    return result


class FakeClient:
    """Stands in for FryerClient, recording the commands it is given."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.session = kwargs.get("session") or (args[4] if len(args) > 4 else None)
        self.device: Device | None = None
        self.result = raw("standby")
        self.commands: list[tuple] = []
        self.logins = 0
        self.reject_token = False
        self.login_error: Exception | None = None
        self.find_error: Exception | None = None
        self.no_fryer = False

    terminal_id = "2abc"

    async def login(self) -> Session:
        self.logins += 1
        if self.login_error:
            raise self.login_error
        self.reject_token = False
        self.session = Session(token=f"token-{self.logins}", account_id="ACCOUNT-1")
        return self.session

    async def _guard(self) -> None:
        if self.session is None or self.reject_token:
            raise TokenRejected("rejected")

    async def find_fryer(self) -> Device:
        await self._guard()
        if self.find_error:
            raise self.find_error
        if self.no_fryer:
            raise NoFryer("No CS358-AF on this VeSync account")
        self.device = DEVICE
        return DEVICE

    async def status(self) -> FryerStatus:
        await self._guard()
        return FryerStatus.parse(self.result)

    async def start(self, temperature: int, minutes: int, *, preheat: bool) -> None:
        check_settings(temperature, minutes)
        self.commands.append(("start", temperature, minutes, preheat))

    async def continue_after_preheat(self) -> None:
        self.commands.append(("continue",))

    async def stop(self) -> None:
        self.commands.append(("stop",))
