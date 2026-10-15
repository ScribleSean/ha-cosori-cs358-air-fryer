"""Minimal client for the COSORI CS358-AF through the VeSync cloud.

Uses only the requests confirmed on a physical unit: login, the device list,
and the bypassV2 methods getAirfryerStatus, startCook, preheatCook and endCook.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout

DEVICE_TYPE = "CS358-AF"
TEMP_MIN, TEMP_MAX, TEMP_STEP = 170, 400, 10
MINUTES_MIN, MINUTES_MAX = 1, 60

APP_VERSION = "5.6.60"
APP_ID = "eldodkfj"
TOKEN_EXPIRED = -11001000
# VeSync error codes, as classified by pyvesync.
CREDENTIAL_ERRORS = {-11201000, -11202000}  # wrong password, unknown account
RATE_LIMITED = {-16906000, -11003000}
CROSS_REGION = -11260022

# A hung request must not hold up Stop; VeSync normally answers within a second or two.
REQUEST_TIMEOUT = ClientTimeout(total=15)

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


class NoFryer(FryerError):
    """The account has no CS358-AF."""


class TooManyFryers(FryerError):
    """The account has more than one CS358-AF; this integration handles one."""


class RateLimited(FryerError):
    """VeSync is refusing logins for now."""


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
    """The fryer's state as reported by getAirfryerStatus."""

    state: str
    cook_set_temp: int | None = None
    cook_set_time: int | None = None
    preheat_set_time: int | None = None
    seconds_left: int = 0

    @property
    def idle(self) -> bool:
        return self.state in ("standby", "cookEnd")

    @property
    def preheated(self) -> bool:
        return self.state == "preheatEnd"

    @property
    def can_preheat(self) -> bool:
        return self.idle

    @property
    def can_start(self) -> bool:
        """Start cooks from idle, or continues into the cook once preheat has finished."""
        return self.idle or self.preheated

    @property
    def can_stop(self) -> bool:
        return not self.idle and self.state != "pullOut"

    @classmethod
    def parse(cls, result: dict[str, Any]) -> FryerStatus:
        """Build a status from getAirfryerStatus; raise FryerError if the reply is malformed."""
        try:
            state = result.get("cookStatus")
            if not isinstance(state, str) or not state:
                raise FryerError("VeSync sent a status without a cooking status")
            steps = result.get("stepArray") or []
            index = int(result.get("stepIndex") or 0)
            step = steps[index] if isinstance(steps, list) and 0 <= index < len(steps) else {}
            if not isinstance(step, dict):
                step = {}
            preheating = state in ("heating", "preheatStop", "preheatEnd")
            # The fryer's total counts a preheat plus the cook queued behind it;
            # report the time left in the phase that is running.
            if state in ("heating", "preheatStop"):
                seconds = int(result.get("preheatLastTime") or 0)
            elif state == "preheatEnd":
                seconds = 0
            else:
                seconds = int(result.get("totalTimeRemaining") or 0)
            preheat_set = int(result.get("preheatSetTime") or 0) if preheating else 0
            cook_temp = step.get("cookTemp")
            cook_time = step.get("cookSetTime")
            return cls(
                state=state,
                cook_set_temp=int(cook_temp) if cook_temp is not None else None,
                cook_set_time=int(cook_time) if cook_time is not None else None,
                preheat_set_time=preheat_set or None,
                seconds_left=max(seconds, 0),
            )
        except (AttributeError, TypeError, ValueError) as err:
            raise FryerError(f"VeSync sent a malformed status: {err}") from err


def check_settings(temperature: int, minutes: int) -> None:
    """Raise ValueError unless the fryer accepts this temperature and time."""
    if not TEMP_MIN <= temperature <= TEMP_MAX or temperature % TEMP_STEP:
        raise ValueError(f"Temperature must be {TEMP_MIN} to {TEMP_MAX} °F in steps of {TEMP_STEP}.")
    if not MINUTES_MIN <= minutes <= MINUTES_MAX:
        raise ValueError(f"Cook time must be {MINUTES_MIN} to {MINUTES_MAX} minutes.")


class FryerClient:
    """Talks to one CS358-AF. It never signs in on its own; callers decide when to."""

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
    def terminal_id(self) -> str:
        return self._terminal_id

    def _base(self, region: str | None) -> str:
        region = region or (self.session.region if self.session else "US")
        return "https://smartapi.vesync.eu" if region == "EU" else "https://smartapi.vesync.com"

    def _common(self) -> dict[str, Any]:
        return {
            "acceptLanguage": "en",
            "debugMode": False,
            "timeZone": self._time_zone,
            "traceId": str(int(time.time())),
            "userCountryCode": self.session.country_code if self.session else "US",
        }

    async def _post(self, path: str, body: dict[str, Any], region: str | None = None) -> dict[str, Any]:
        try:
            async with self._http.post(
                self._base(region) + path, json=body, headers=_HEADERS, timeout=REQUEST_TIMEOUT
            ) as resp:
                if resp.status != 200:
                    raise FryerError(f"VeSync answered HTTP {resp.status}")
                data = await resp.json(content_type=None)
        except (ClientError, TimeoutError) as err:
            raise FryerError(f"Could not reach VeSync: {err}") from err
        except ValueError as err:
            raise FryerError("VeSync sent an unreadable response") from err
        if not isinstance(data, dict):
            raise FryerError("VeSync sent an unreadable response")
        return data

    @staticmethod
    def _login_failed(answer: dict[str, Any]) -> FryerError:
        code = answer.get("code")
        message = str(answer.get("msg") or f"code {code}")
        if code in CREDENTIAL_ERRORS:
            return AuthError(message)
        if code in RATE_LIMITED:
            return RateLimited("VeSync is rate limiting logins; try again later")
        return FryerError(f"VeSync login failed: {message}")

    async def login(self) -> Session:
        """Sign in with email and password. Signs other clients of the account out."""
        identity = {
            **self._common(),
            **_IDENTITY,
            "accountID": "",
            "token": "",
            "terminalId": self._terminal_id,
        }
        auth = {
            **identity,
            "method": "authByPWDOrOTM",
            "email": self._email,
            "password": hashlib.md5(self._password.encode()).hexdigest(),
            "authProtocolType": "generic",
            "appID": APP_ID,
            "sourceAppID": APP_ID,
        }
        first = await self._post("/globalPlatform/api/accountAuth/v1/authByPWDOrOTM", auth)
        first_result = first.get("result")
        code = first_result.get("authorizeCode") if isinstance(first_result, dict) else None
        if first.get("code") != 0 or not code:
            raise self._login_failed(first)

        path = "/user/api/accountManage/v1/loginByAuthorizeCode4Vesync"
        exchange = {
            **identity,
            "method": "loginByAuthorizeCode4Vesync",
            "authorizeCode": code,
            "emailSubscriptions": False,
        }
        answer = await self._post(path, exchange)
        result = answer.get("result") if isinstance(answer.get("result"), dict) else {}
        region = None
        if answer.get("code") == CROSS_REGION and result.get("bizToken"):
            # The account lives in another region: retry once there with the token VeSync returns.
            region = result.get("currentRegion") or None
            exchange |= {
                "bizToken": result["bizToken"],
                "regionChange": "lastRegion",
                "userCountryCode": result.get("countryCode") or "US",
            }
            answer = await self._post(path, exchange, region)
            result = answer.get("result") if isinstance(answer.get("result"), dict) else {}
        if answer.get("code") != 0:
            raise self._login_failed(answer)
        if not result.get("token") or not result.get("accountID"):
            raise FryerError("VeSync login returned no session")
        self.session = Session(
            token=result["token"],
            account_id=result["accountID"],
            country_code=result.get("countryCode") or "US",
            region=result.get("currentRegion") or region or "US",
        )
        return self.session

    def _require_session(self) -> Session:
        if self.session is None or not self.session.token:
            raise TokenRejected("Not signed in")
        return self.session

    async def _authorised(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        answer = await self._post(path, body)
        if answer.get("code") == TOKEN_EXPIRED:
            raise TokenRejected("VeSync rejected the session token")
        if answer.get("code") != 0:
            raise FryerError(f"VeSync refused the request (code {answer.get('code')})")
        return answer

    async def find_fryer(self) -> Device:
        """Find the CS358-AF on the account. Exactly one is supported."""
        session = self._require_session()
        body = {
            **self._common(),
            "appVersion": APP_VERSION,
            "phoneBrand": "pyvesync",
            "phoneOS": "Android",
            "method": "devices",
            "pageNo": 1,
            "pageSize": 100,
            "accountID": session.account_id,
            "token": session.token,
        }
        answer = await self._authorised("/cloud/v1/deviceManaged/devices", body)
        result = answer.get("result")
        items = result.get("list") if isinstance(result, dict) else None
        fryers = [
            item
            for item in (items if isinstance(items, list) else [])
            if isinstance(item, dict)
            and item.get("deviceType") == DEVICE_TYPE
            and item.get("cid")
            and item.get("configModule")
        ]
        if not fryers:
            raise NoFryer(f"No {DEVICE_TYPE} on this VeSync account")
        if len(fryers) > 1:
            raise TooManyFryers(f"More than one {DEVICE_TYPE} on this VeSync account")
        item = fryers[0]
        self.device = Device(
            cid=item["cid"],
            name=item.get("deviceName") or "Air fryer",
            config_module=item["configModule"],
            region=item.get("deviceRegion") or "US",
        )
        return self.device

    async def _call(self, method: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        device = self.device or await self.find_fryer()
        session = self._require_session()
        body = {
            **self._common(),
            "appVersion": APP_VERSION,
            "phoneBrand": "pyvesync",
            "phoneOS": "Android",
            "accountID": session.account_id,
            "token": session.token,
            "cid": device.cid,
            "deviceId": device.cid,
            "configModule": device.config_module,
            "configModel": device.config_module,
            "deviceRegion": device.region,
            "method": "bypassV2",
            "payload": {"method": method, "source": "APP", "data": data or {}},
        }
        answer = await self._authorised("/cloud/v2/deviceManaged/bypassV2", body)
        outer = answer.get("result")
        if not isinstance(outer, dict):
            raise FryerError(f"VeSync sent a malformed reply to '{method}'")
        if outer.get("code") != 0:
            raise FryerError(f"The fryer refused '{method}' (code {outer.get('code')})")
        result = outer.get("result") or {}
        if not isinstance(result, dict):
            raise FryerError(f"VeSync sent a malformed reply to '{method}'")
        return result

    async def status(self) -> FryerStatus:
        return FryerStatus.parse(await self._call("getAirfryerStatus"))

    async def start(self, temperature: int, minutes: int, *, preheat: bool) -> None:
        """Start a manual program, with or without a preheat phase first."""
        check_settings(temperature, minutes)
        session = self._require_session()
        await self._call(
            "startCook",
            {
                "accountId": session.account_id,
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

    async def continue_after_preheat(self) -> None:
        """Start the cook queued behind a finished preheat, as the fryer's Start button does."""
        await self._call("preheatCook")

    async def stop(self) -> None:
        await self._call("endCook")
