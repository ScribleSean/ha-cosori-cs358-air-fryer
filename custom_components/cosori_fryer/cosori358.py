"""Client for the COSORI CS358-AF (Pro II 5.8 Qt) through the VeSync cloud.

pyvesync does not support this model, so this talks to VeSync's bypassV2
endpoint directly, using pyvesync only for login and HTTP.

Confirmed on a physical unit:
  - getAirfryerStatus: status read.
  - startStepCook with hasPreheat=1: preheat, then the cook step is kept.
    readyStart=False starts at once; readyStart=True stages it and waits for
    the Start button on the fryer.
  - preheatCook: continues from a finished preheat into the queued cook, at
    the same temperature and time.
  - endCook: stops a staged program, a preheat and a running cook.

Cook without preheat sends startStepCook with hasPreheat=0. That exact
payload was not sent to the unit; the equivalent startCook call was, and it
worked. Pause and resume are not supported: no cloud method is known for
them, and the fryer pauses itself when the basket is pulled.

Nothing in this module reads credentials or prints tokens.
"""

from __future__ import annotations

import copy
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from aiohttp import ClientError, ClientSession
from pyvesync.utils.errors import ErrorTypes, VeSyncError
from pyvesync.utils.helpers import Helpers

DEVICE_TYPE = "CS358-AF"
DEVICE_LIST_PATH = "/cloud/v1/deviceManaged/devices"
BYPASS_V2_PATH = "/cloud/v2/deviceManaged/bypassV2"

# From the CS358-AF user manual: 170-400 F / 77-204 C, 1-60 min.
TEMP_RANGE = {"f": (170, 400), "c": (77, 204)}
MIN_MINUTES = 1
MAX_MINUTES = 60

# Status names. All of these were seen on the real device: "standby",
# "pullOut", "ready", "heating", "preheatStop", "preheatEnd", "cooking",
# "cookStop", "cookEnd". The two "Stop" states come from the Start/Pause
# button on the fryer.
IDLE = {"standby", "cookEnd"}
BASKET_OUT = "pullOut"
READY = "ready"  # a program is staged and waiting for Start
PREHEATING = "heating"
PREHEAT_PAUSED = "preheatStop"
PREHEAT_DONE = "preheatEnd"
COOKING = "cooking"
COOK_PAUSED = "cookStop"
KNOWN_STATUSES = IDLE | {
    BASKET_OUT,
    READY,
    PREHEATING,
    PREHEAT_PAUSED,
    PREHEAT_DONE,
    COOKING,
    COOK_PAUSED,
}
ACTIVE = {PREHEATING, COOKING}  # poll fast while in one of these

_BODY_KEYS = (
    "acceptLanguage",
    "appVersion",
    "phoneBrand",
    "phoneOS",
    "debugMode",
    "traceId",
)
_REDACT = {"token", "accountID", "accountId", "cid", "deviceId"}

# After a rejected token, sign in again at most this often. VeSync rate-limits
# logins hard, and a new login appears to invalidate the account's other tokens.
RELOGIN_COOLDOWN_SECONDS = 300
_last_relogin: dict[int, float] = {}


class FryerError(RuntimeError):
    """The cloud or the fryer rejected a request."""


class Refused(RuntimeError):
    """A command was refused locally, before anything was sent."""


@dataclass(frozen=True)
class Command:
    method: str | None  # bypassV2 payload method; None = not known for this model
    verified: bool  # True only once proven on the real fryer
    heats: bool  # True if it can turn the heater on


COMMANDS: dict[str, Command] = {
    "preheat": Command("startStepCook", verified=True, heats=True),
    "cook": Command("startStepCook", verified=True, heats=True),
    "start": Command("preheatCook", verified=True, heats=True),
    "stop": Command("endCook", verified=True, heats=False),
    "pause": Command(None, verified=False, heats=False),
    "resume": Command(None, verified=False, heats=True),
}


@dataclass(frozen=True)
class FryerStatus:
    cook_status: str
    temp_unit: str  # "f" or "c"
    current_temp: int
    preheat_temp: int
    preheat_seconds_set: int
    preheat_seconds_left: int
    cook_temp: int | None
    cook_seconds_set: int | None
    cook_seconds_left: int | None
    total_seconds_left: int
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def from_result(cls, result: dict[str, Any]) -> FryerStatus:
        steps = result.get("stepArray") or []
        index = result.get("stepIndex") or 0
        step = steps[index] if 0 <= index < len(steps) else {}
        return cls(
            cook_status=str(result.get("cookStatus")),
            temp_unit=str(result.get("tempUnit") or "f").lower()[:1],
            current_temp=int(result.get("currentTemp") or 0),
            preheat_temp=int(result.get("preheatTemp") or 0),
            preheat_seconds_set=int(result.get("preheatSetTime") or 0),
            preheat_seconds_left=int(result.get("preheatLastTime") or 0),
            cook_temp=step.get("cookTemp"),
            cook_seconds_set=step.get("cookSetTime"),
            cook_seconds_left=step.get("cookLastTime"),
            total_seconds_left=int(result.get("totalTimeRemaining") or 0),
            raw=result,
        )

    @property
    def is_active(self) -> bool:
        return self.cook_status in ACTIVE


def validate(temp: int, minutes: int, unit: str) -> None:
    """Raise Refused unless temp and minutes are inside the device's limits."""
    low, high = TEMP_RANGE[unit]
    if not low <= temp <= high:
        raise Refused(
            f"Temperature {temp}°{unit.upper()} is outside the fryer's range "
            f"({low}-{high}°{unit.upper()})."
        )
    if not MIN_MINUTES <= minutes <= MAX_MINUTES:
        raise Refused(
            f"Cook time {minutes} min is outside the allowed range "
            f"({MIN_MINUTES}-{MAX_MINUTES} min)."
        )


def _check_state(name: str, status: FryerStatus) -> None:
    state = status.cook_status
    if state not in KNOWN_STATUSES:
        raise Refused(
            f"The fryer reports an unrecognised state '{state}', so '{name}' "
            "was not sent."
        )
    if name in ("preheat", "cook"):
        if state == BASKET_OUT:
            raise Refused("The basket is out. Put it back before heating.")
        if state == PREHEAT_DONE and name == "cook":
            return
        if state == READY:
            raise Refused("A program is already staged on the fryer. Stop it first.")
        if state not in IDLE:
            raise Refused(f"The fryer is busy ({state}). Stop it first.")
    elif name == "start":
        if state != PREHEAT_DONE:
            raise Refused(
                f"'start' continues after a finished preheat, but the fryer is "
                f"in state '{state}'."
            )
    elif name == "pause":
        if state not in ACTIVE:
            raise Refused(f"Nothing is running to pause (state '{state}').")
    elif name == "resume":
        if state not in (PREHEAT_PAUSED, COOK_PAUSED):
            raise Refused(f"Nothing is paused to resume (state '{state}').")
    elif name == "stop":
        if state in IDLE or state == BASKET_OUT:
            raise Refused(f"Nothing is running to stop (state '{state}').")


def _step_cook(
    account_id: str, temp: int, minutes: int, unit: str, preheat: bool, stage: bool
) -> dict:
    # Step fields mirror what the fryer itself reports for a manual cook.
    # readyStart True is confirmed to stage the program and wait for the Start
    # button on the fryer.
    return {
        "accountId": account_id,
        "hasPreheat": 1 if preheat else 0,
        "preheatTemp": temp if preheat else 0,
        "readyStart": stage,
        "tempUnit": unit,
        "stepArray": [
            {
                "cookSetTime": minutes * 60,
                "cookTemp": temp,
                "mode": "Custom",
                "recipeId": 11,
                "recipeName": "",
                "recipeType": 3,
                "shakeTime": 0,
            }
        ],
    }


def plan(
    name: str,
    status: FryerStatus,
    account_id: str,
    temp: int | None = None,
    minutes: int | None = None,
    stage: bool = False,
) -> tuple[str, dict]:
    """Decide what to send for a command, or raise Refused with the reason.

    Returns (payload method, payload data). Sends nothing.
    """
    command = COMMANDS[name]
    if name in ("preheat", "cook"):
        if temp is None or minutes is None:
            raise Refused(f"'{name}' needs a temperature and a cook time.")
        validate(temp, minutes, status.temp_unit)
    _check_state(name, status)
    if command.method is None:
        raise Refused(
            f"The '{name}' command is not known yet for the {DEVICE_TYPE}."
        )
    if name in ("preheat", "cook"):
        data = _step_cook(
            account_id, temp, minutes, status.temp_unit, name == "preheat", stage
        )
    else:
        data = {}
    return command.method, data


def redact(body: dict) -> dict:
    """Copy of a request body that is safe to print."""
    clean = copy.deepcopy(body)

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key in node:
                if key in _REDACT:
                    node[key] = "<redacted>"
                else:
                    walk(node[key])
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(clean)
    return clean


async def _post(manager: Any, path: str, build_body: Callable[[], dict]) -> dict:
    """POST to VeSync and return the parsed response.

    This deliberately bypasses pyvesync's own request wrapper, which re-logs in
    and retries without limit when a token is rejected. Here a rejected token
    gets exactly one new login, and no more than one per cooldown period.
    """
    if manager.session is None:
        manager.session = ClientSession()
        manager._close_session = True  # noqa: SLF001 - so the manager closes it
    url = manager._api_base_url_for_current_region() + path  # noqa: SLF001
    for attempt in (1, 2):
        try:
            async with manager.session.request(
                "post", url, json=build_body(), headers=Helpers.req_header_bypass()
            ) as response:
                if response.status != 200:
                    raise FryerError(f"VeSync answered HTTP {response.status}.")
                data = await response.json(content_type=None)
        except (ClientError, TimeoutError) as err:
            raise FryerError(f"Could not reach VeSync: {err}") from err
        if not isinstance(data, dict):
            raise FryerError("VeSync sent an unreadable response.")
        if Helpers.parse_error_code(data).error_type != ErrorTypes.TOKEN_ERROR:
            return data
        if attempt == 2:
            break
        last = _last_relogin.get(id(manager))
        if last is not None and time.monotonic() - last < RELOGIN_COOLDOWN_SECONDS:
            raise FryerError("VeSync rejected the login token. Not signing in again yet.")
        _last_relogin[id(manager)] = time.monotonic()
        try:
            if not await manager.login():
                raise FryerError("VeSync rejected the login token and signing in again failed.")
        except VeSyncError as err:
            raise FryerError(f"VeSync rejected the login token; new login failed: {err}") from err
    raise FryerError("VeSync rejected the login token even after signing in again.")


class Cosori358:
    """One CS358-AF fryer, reached through a logged-in pyvesync manager."""

    def __init__(self, manager: Any, device: dict[str, Any]) -> None:
        self._manager = manager
        self._device = device
        self.name: str = device.get("deviceName", "Air Fryer")
        self.device_type: str = device.get("deviceType", "")
        self.config_module: str = device.get("configModule", "")
        self.online: bool = device.get("connectionStatus") == "online"

    @classmethod
    async def discover(cls, manager: Any) -> Cosori358:
        """Find the CS358-AF on the account. Read-only."""
        def body() -> dict:
            return {
                **Helpers.get_defaultvalues_attributes(_BODY_KEYS),
                "method": "devices",
                "pageNo": 1,
                "pageSize": 100,
                "accountID": manager.account_id,
                "token": manager.token,
                "timeZone": manager.time_zone,
            }

        response = await _post(manager, DEVICE_LIST_PATH, body)
        if response.get("code") != 0:
            raise FryerError("Could not read the device list from VeSync.")
        devices = (response.get("result") or {}).get("list") or []
        for device in devices:
            if device.get("deviceType") == DEVICE_TYPE:
                return cls(manager, device)
        found = ", ".join(str(d.get("deviceType")) for d in devices) or "none"
        raise FryerError(f"No {DEVICE_TYPE} on this account (found: {found}).")

    def build_body(self, method: str, data: dict) -> dict:
        """The exact JSON body for a bypassV2 call."""
        manager = self._manager
        return {
            **Helpers.get_defaultvalues_attributes(_BODY_KEYS),
            "accountID": manager.account_id,
            "token": manager.token,
            "timeZone": manager.time_zone,
            "userCountryCode": manager.country_code,
            "cid": self._device["cid"],
            "deviceId": self._device["cid"],
            "configModule": self.config_module,
            "configModel": self.config_module,
            "deviceRegion": self._device.get("deviceRegion"),
            "method": "bypassV2",
            "payload": {"method": method, "source": "APP", "data": data},
        }

    async def _call(self, method: str, data: dict) -> dict:
        # The body is rebuilt per attempt so a retry carries the fresh token.
        response = await _post(
            self._manager, BYPASS_V2_PATH, lambda: self.build_body(method, data)
        )
        if response.get("code") != 0:
            raise FryerError(f"VeSync rejected '{method}' (code {response.get('code')}).")
        outer = response.get("result") or {}
        if outer.get("code") != 0:
            raise FryerError(
                f"The fryer rejected '{method}' (code {outer.get('code')})."
            )
        return outer.get("result") or {}

    async def status(self) -> FryerStatus:
        return FryerStatus.from_result(await self._call("getAirfryerStatus", {}))

    async def send(self, method: str, data: dict) -> dict:
        """Send a command. Callers must have gone through plan() first."""
        return await self._call(method, data)
