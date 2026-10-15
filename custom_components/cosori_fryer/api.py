"""Minimal client for the COSORI CS358-AF through the VeSync cloud.

Uses only the requests confirmed on a physical unit: login,
the device list, and the bypassV2 methods getAirfryerStatus, startCook
and endCook.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

from aiohttp import ClientError, ClientSession

DEVICE_TYPE = "CS358-AF"
TEMP_MIN, TEMP_MAX, TEMP_STEP = 170, 400, 10
MINUTES_MIN, MINUTES_MAX = 1, 60

APP_VERSION = "5.6.60"
APP_ID = "eldodkfj"
TOKEN_EXPIRED = -11001000
SETTLE_SECONDS = 2

_HEADERS = {
    "Content-Type": "application/json; charset=UTF-8",
    "User-Agent": "okhttp/3.12.1",
}
_IDENTITY = {
    "clientInfo": "pyvesync",
    "clientType": "vesyncApp",
    "clientVersion": f"VeSync {APP_VERSION}",
    "osInfo": "Android",
}


class FryerError(Exception):
    """VeSync or the fryer could not complete a request."""


class AuthError(FryerError):
    """VeSync rejected the email or password."""


class TokenRejected(FryerError):
    """VeSync rejected the session token."""


@dataclass(frozen=True)
class Session:
    """What a login returns; stored so restarts do not need a new login."""

    token: str
    account_id: str
    country_code: str = "US"
    region: str = "US"


@dataclass(frozen=True)
class Device:
    cid: str
    name: str
    config_module: str
    region: str


@dataclass(frozen=True)
class FryerStatus:
    """The fryer's state, with times for the phase that is running."""

    state: str
    target_temp: int | None = None
    seconds_left: int = 0
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def idle(self) -> bool:
        return self.state in ("standby", "cookEnd")

    @property
    def waiting(self) -> bool:
        """A program is loaded but not running: preheat finished, or staged."""
        return self.state in ("preheatEnd", "ready")

    @property
    def can_preheat(self) -> bool:
        return self.idle

    @property
    def can_air_fry(self) -> bool:
        return self.idle or self.waiting

    @property
    def can_stop(self) -> bool:
        return not self.idle and self.state not in ("pullOut", "unknown")

    @classmethod
    def parse(cls, result: dict[str, Any]) -> FryerStatus:
        state = str(result.get("cookStatus") or "unknown")
        steps = result.get("stepArray") or []
        index = result.get("stepIndex") or 0
        step = steps[index] if 0 <= index < len(steps) else {}
        target = step.get("cookTemp")
        # The fryer's total counts a preheat plus the cook queued behind it.
        seconds = int(result.get("totalTimeRemaining") or 0)
        if state in ("heating", "preheatStop"):
            seconds = int(result.get("preheatLastTime") or 0)
            target = result.get("preheatTemp") or target
        elif state == "preheatEnd":
            seconds = 0
        return cls(state=state, target_temp=target, seconds_left=seconds, raw=result)


def check_settings(temperature: int, minutes: int) -> None:
    """Raise ValueError unless the fryer accepts this temperature and time."""
    if not TEMP_MIN <= temperature <= TEMP_MAX or temperature % TEMP_STEP:
        raise ValueError(
            f"Temperature must be {TEMP_MIN} to {TEMP_MAX} °F in steps of {TEMP_STEP}."
        )
    if not MINUTES_MIN <= minutes <= MINUTES_MAX:
        raise ValueError(f"Cook time must be {MINUTES_MIN} to {MINUTES_MAX} minutes.")


class FryerClient:
    """Talks to one CS358-AF. A rejected token gets one new login, no more."""

    def __init__(
        self,
        http: ClientSession,
        email: str,
        password: str,
        terminal_id: str,
        session: Session | None = None,
        time_zone: str = "America/New_York",
    ) -> None:
        self._http = http
        self._time_zone = time_zone
        self._email = email
        self._password = password
        self._terminal_id = terminal_id
        self.session = session
        self.device: Device | None = None

    @property
    def _base(self) -> str:
        region = self.session.region if self.session else "US"
        return (
            "https://smartapi.vesync.eu"
            if region == "EU"
            else "https://smartapi.vesync.com"
        )

    def _common(self) -> dict[str, Any]:
        return {
            "acceptLanguage": "en",
            "debugMode": False,
            "timeZone": self._time_zone,
            "traceId": str(int(time.time())),
            "userCountryCode": self.session.country_code if self.session else "US",
        }

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            async with self._http.post(
                self._base + path, json=body, headers=_HEADERS
            ) as resp:
                if resp.status != 200:
                    raise FryerError(f"VeSync answered HTTP {resp.status}")
                data = await resp.json(content_type=None)
        except (ClientError, TimeoutError) as err:
            raise FryerError(f"Could not reach VeSync: {err}") from err
        if not isinstance(data, dict):
            raise FryerError("VeSync sent an unreadable response")
        return data

    async def login(self) -> Session:
        """Sign in with email and password. Signs other clients out."""
        auth = {
            **self._common(),
            **_IDENTITY,
            "method": "authByPWDOrOTM",
            "email": self._email,
            "password": hashlib.md5(self._password.encode()).hexdigest(),  # noqa: S324
            "authProtocolType": "generic",
            "accountID": "",
            "token": "",
            "terminalId": self._terminal_id,
            "appID": APP_ID,
            "sourceAppID": APP_ID,
        }
        first = await self._post(
            "/globalPlatform/api/accountAuth/v1/authByPWDOrOTM", auth
        )
        code = (first.get("result") or {}).get("authorizeCode")
        if first.get("code") != 0 or not code:
            message = str(first.get("msg") or "")
            if "frequent" in message.lower():
                raise FryerError("VeSync is rate limiting logins; try again later")
            raise AuthError(message or "VeSync rejected the login")

        exchange = {
            **self._common(),
            **_IDENTITY,
            "method": "loginByAuthorizeCode4Vesync",
            "authorizeCode": code,
            "emailSubscriptions": False,
            "accountID": "",
            "token": "",
            "terminalId": self._terminal_id,
        }
        path = "/user/api/accountManage/v1/loginByAuthorizeCode4Vesync"
        answer = await self._post(path, exchange)
        result = answer.get("result") or {}
        if answer.get("code") != 0 and result.get("bizToken"):
            # The account lives in another region: retry once with the token VeSync returns.
            exchange |= {
                "bizToken": result["bizToken"],
                "regionChange": "lastRegion",
                "userCountryCode": result.get("countryCode", "US"),
            }
            self.session = Session(
                "",
                "",
                result.get("countryCode", "US"),
                result.get("currentRegion") or "US",
            )
            answer = await self._post(path, exchange)
            result = answer.get("result") or {}
        if answer.get("code") != 0 or not result.get("token"):
            raise AuthError(str(answer.get("msg") or "VeSync rejected the login"))
        self.session = Session(
            token=result["token"],
            account_id=result["accountID"],
            country_code=result.get("countryCode") or "US",
            region=result.get("currentRegion")
            or (self.session.region if self.session else "US"),
        )
        return self.session

    async def _authorised(self, path: str, build: Any) -> dict[str, Any]:
        if self.session is None or not self.session.token:
            raise TokenRejected("Not signed in")
        answer = await self._post(path, build())
        if answer.get("code") == TOKEN_EXPIRED:
            raise TokenRejected("VeSync rejected the session token")
        if answer.get("code") != 0:
            raise FryerError(f"VeSync refused the request (code {answer.get('code')})")
        return answer

    async def find_fryer(self) -> Device:
        """Find the CS358-AF on the account."""

        def body() -> dict[str, Any]:
            assert self.session
            return {
                **self._common(),
                "appVersion": APP_VERSION,
                "phoneBrand": "pyvesync",
                "phoneOS": "Android",
                "method": "devices",
                "pageNo": 1,
                "pageSize": 100,
                "accountID": self.session.account_id,
                "token": self.session.token,
            }

        answer = await self._authorised("/cloud/v1/deviceManaged/devices", body)
        for item in (answer.get("result") or {}).get("list") or []:
            if item.get("deviceType") == DEVICE_TYPE:
                self.device = Device(
                    cid=item["cid"],
                    name=item.get("deviceName") or "Air fryer",
                    config_module=item["configModule"],
                    region=item.get("deviceRegion") or "US",
                )
                return self.device
        raise FryerError(f"No {DEVICE_TYPE} on this VeSync account")

    async def _call(
        self, method: str, data: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        device = self.device or await self.find_fryer()

        def body() -> dict[str, Any]:
            assert self.session
            return {
                **self._common(),
                "appVersion": APP_VERSION,
                "phoneBrand": "pyvesync",
                "phoneOS": "Android",
                "accountID": self.session.account_id,
                "token": self.session.token,
                "cid": device.cid,
                "deviceId": device.cid,
                "configModule": device.config_module,
                "configModel": device.config_module,
                "deviceRegion": device.region,
                "method": "bypassV2",
                "payload": {"method": method, "source": "APP", "data": data or {}},
            }

        answer = await self._authorised("/cloud/v2/deviceManaged/bypassV2", body)
        outer = answer.get("result") or {}
        if outer.get("code") != 0:
            raise FryerError(f"The fryer refused '{method}' (code {outer.get('code')})")
        return outer.get("result") or {}

    async def status(self) -> FryerStatus:
        return FryerStatus.parse(await self._call("getAirfryerStatus"))

    async def start(self, temperature: int, minutes: int, *, preheat: bool) -> None:
        """Start a manual program, with or without a preheat phase first."""
        check_settings(temperature, minutes)
        assert self.session
        await self._call(
            "startCook",
            {
                "accountId": self.session.account_id,
                "hasPreheat": 1 if preheat else 0,
                "hasWarm": False,
                "hasLinkage": False,
                "mode": "Custom",
                "readyStart": False,
                "recipeId": 1,
                "recipeName": "Manual Cook",
                "recipeType": 3,
                "tempUnit": "f",
                "startAct": {
                    "cookSetTime": minutes * 60,
                    "cookTemp": temperature,
                    "preheatTemp": temperature if preheat else 0,
                    "shakeTime": 0,
                },
            },
        )

    async def air_fry(
        self, temperature: int, minutes: int, *, clear_first: bool
    ) -> None:
        """Cook now. A loaded program (for example after preheat) is cleared first."""
        check_settings(temperature, minutes)
        if clear_first:
            await self.stop()
            await asyncio.sleep(SETTLE_SECONDS)
        await self.start(temperature, minutes, preheat=False)

    async def stop(self) -> None:
        await self._call("endCook")
