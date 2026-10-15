"""Tests for the VeSync client, against a fake HTTP session."""

from __future__ import annotations

import hashlib
from typing import Any

import pytest
from fakes import raw

from custom_components.cosori_fryer import api
from custom_components.cosori_fryer.api import (
    AuthError,
    FryerClient,
    FryerStatus,
    Session,
    TokenRejected,
    check_settings,
)

DEVICES = {
    "code": 0,
    "result": {
        "list": [
            {"deviceType": "LV-PUR131S", "cid": "X", "configModule": "M"},
            {
                "deviceType": "CS358-AF",
                "cid": "CID-1",
                "deviceName": "Air Fryer",
                "configModule": "CM",
                "deviceRegion": "US",
            },
        ]
    },
}


SIGNED_IN = Session("TOKEN", "ACCOUNT")


class FakeResponse:
    status = 200

    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body

    async def json(self, content_type: str | None = None) -> dict[str, Any]:
        return self._body

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class FakeHttp:
    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self.hosts: list[str] = []
        self.response_type = FakeResponse

    def post(self, url: str, json: dict[str, Any], headers: dict[str, str], **kwargs: Any) -> FakeResponse:
        self.kwargs = kwargs
        host, path = url.removeprefix("https://").split("/", 1)
        path = "/" + path
        self.hosts.append(host)
        self.posts.append((path, json))
        answer = self.routes[path]
        return self.response_type(answer(json) if callable(answer) else answer)


def client(routes: dict[str, Any], session: Session | None = SIGNED_IN) -> tuple[FryerClient, FakeHttp]:
    http = FakeHttp(routes)
    return FryerClient(http, "me@example.com", "secret", "2abc", session), http  # type: ignore[arg-type]


def bypass(result: dict[str, Any]) -> dict[str, Any]:
    return {"code": 0, "result": {"code": 0, "result": result}}


async def test_login_sends_a_hashed_password_and_keeps_the_session() -> None:
    fryer, http = client(
        {
            "/globalPlatform/api/accountAuth/v1/authByPWDOrOTM": {
                "code": 0,
                "result": {"authorizeCode": "AUTH"},
            },
            "/user/api/accountManage/v1/loginByAuthorizeCode4Vesync": {
                "code": 0,
                "result": {
                    "token": "NEW",
                    "accountID": "ACC",
                    "countryCode": "US",
                    "currentRegion": "US",
                },
            },
        },
        session=None,
    )
    session = await fryer.login()
    assert session == Session("NEW", "ACC", "US", "US")
    first = http.posts[0][1]
    assert first["password"] == hashlib.md5(b"secret").hexdigest()
    assert "secret" not in str(http.posts)
    assert http.posts[1][1]["authorizeCode"] == "AUTH"


@pytest.mark.parametrize("code", [-11201000, -11202000])
async def test_wrong_password_or_account_is_an_auth_error(code: int) -> None:
    fryer, _ = client({"/globalPlatform/api/accountAuth/v1/authByPWDOrOTM": {"code": code, "msg": "wrong"}})
    with pytest.raises(AuthError):
        await fryer.login()


@pytest.mark.parametrize("code", [-16906000, -11003000])
async def test_rate_limited_login_is_reported_as_rate_limiting(code: int) -> None:
    fryer, _ = client(
        {"/globalPlatform/api/accountAuth/v1/authByPWDOrOTM": {"code": code, "msg": "slow down"}}
    )
    with pytest.raises(api.FryerError, match="rate limiting") as caught:
        await fryer.login()
    assert not isinstance(caught.value, AuthError)


async def test_find_fryer_picks_the_cs358() -> None:
    fryer, _ = client({"/cloud/v1/deviceManaged/devices": DEVICES})
    device = await fryer.find_fryer()
    assert device.cid == "CID-1" and device.name == "Air Fryer"


async def test_rejected_token_is_reported_not_retried() -> None:
    fryer, http = client({"/cloud/v1/deviceManaged/devices": {"code": -11001000, "msg": "token expired"}})
    with pytest.raises(TokenRejected):
        await fryer.find_fryer()
    assert len(http.posts) == 1


async def test_start_with_preheat_payload() -> None:
    fryer, http = client(
        {
            "/cloud/v1/deviceManaged/devices": DEVICES,
            "/cloud/v2/deviceManaged/bypassV2": bypass({}),
        }
    )
    await fryer.start(360, 12, preheat=True)
    payload = http.posts[-1][1]["payload"]
    assert payload["method"] == "startCook"
    assert payload["data"]["hasPreheat"] == 1 and payload["data"]["readyStart"] is False
    assert payload["data"]["startAct"] == {
        "cookSetTime": 720,
        "cookTemp": 360,
        "preheatTemp": 360,
        "shakeTime": 0,
    }


async def test_start_after_preheat_sends_preheat_cook() -> None:
    fryer, http = client(
        {
            "/cloud/v1/deviceManaged/devices": DEVICES,
            "/cloud/v2/deviceManaged/bypassV2": bypass({}),
        }
    )
    await fryer.continue_after_preheat()
    assert http.posts[-1][1]["payload"] == {
        "method": "preheatCook",
        "source": "APP",
        "data": {},
    }


async def test_cook_without_preheat_payload() -> None:
    fryer, http = client(
        {
            "/cloud/v1/deviceManaged/devices": DEVICES,
            "/cloud/v2/deviceManaged/bypassV2": bypass({}),
        }
    )
    await fryer.start(400, 8, preheat=False)
    data = http.posts[-1][1]["payload"]["data"]
    assert data["hasPreheat"] == 0 and data["startAct"]["preheatTemp"] == 0


@pytest.mark.parametrize(("temperature", "minutes"), [(375, 10), (160, 10), (410, 10), (360, 0), (360, 61)])
def test_settings_outside_what_the_fryer_takes_are_refused(temperature: int, minutes: int) -> None:
    with pytest.raises(ValueError):
        check_settings(temperature, minutes)


def test_preheat_reports_preheat_time_and_set_values() -> None:
    status = FryerStatus.parse(
        raw("heating", preheatLastTime=200, totalTimeRemaining=800, preheatSetTime=240)
    )
    assert status.seconds_left == 200
    assert (status.cook_set_temp, status.cook_set_time, status.preheat_set_time) == (
        360,
        600,
        240,
    )


def test_preheat_set_time_is_only_reported_during_preheat() -> None:
    assert FryerStatus.parse(raw("cooking", preheatSetTime=240)).preheat_set_time is None


def test_preheated_shows_no_time_and_allows_start() -> None:
    status = FryerStatus.parse(raw("preheatEnd"))
    assert status.seconds_left == 0 and status.can_start and status.preheated
    assert not status.can_preheat


@pytest.mark.parametrize(
    ("state", "preheat", "start", "stop"),
    [
        ("standby", True, True, False),
        ("cookEnd", True, True, False),
        ("pullOut", False, False, False),
        ("heating", False, False, True),
        ("preheatEnd", False, True, True),
        ("cooking", False, False, True),
        ("cookStop", False, False, True),
    ],
)
def test_what_each_state_allows(state: str, preheat: bool, start: bool, stop: bool) -> None:
    status = FryerStatus.parse(raw(state))
    assert (status.can_preheat, status.can_start, status.can_stop) == (
        preheat,
        start,
        stop,
    )


async def test_region_retry_goes_to_the_eu_host() -> None:
    answers = iter(
        [
            {"code": -11260022, "result": {"bizToken": "BIZ", "countryCode": "GB", "currentRegion": "EU"}},
            {
                "code": 0,
                "result": {"token": "T", "accountID": "A", "countryCode": "GB", "currentRegion": "EU"},
            },
        ]
    )
    fryer, http = client(
        {
            "/globalPlatform/api/accountAuth/v1/authByPWDOrOTM": {
                "code": 0,
                "result": {"authorizeCode": "AUTH"},
            },
            "/user/api/accountManage/v1/loginByAuthorizeCode4Vesync": lambda body: next(answers),
        },
        session=None,
    )
    session = await fryer.login()
    assert session.region == "EU"
    assert http.posts[2][1]["bizToken"] == "BIZ"
    assert http.hosts == ["smartapi.vesync.com", "smartapi.vesync.com", "smartapi.vesync.eu"]


async def test_unreadable_reply_is_a_fryer_error() -> None:
    class Html(FakeResponse):
        async def json(self, content_type: str | None = None) -> dict[str, Any]:
            raise ValueError("Expecting value")

    fryer, http = client({"/cloud/v1/deviceManaged/devices": DEVICES})
    http.response_type = Html
    with pytest.raises(api.FryerError):
        await fryer.find_fryer()


async def test_malformed_device_list_is_no_fryer() -> None:
    fryer, _ = client(
        {"/cloud/v1/deviceManaged/devices": {"code": 0, "result": {"list": [{"deviceType": "CS358-AF"}]}}}
    )
    with pytest.raises(api.NoFryer):
        await fryer.find_fryer()


async def test_requests_have_a_short_timeout() -> None:
    fryer, http = client({"/cloud/v1/deviceManaged/devices": DEVICES})
    await fryer.find_fryer()
    assert http.kwargs["timeout"].total == 15


async def test_timeout_is_a_fryer_error() -> None:
    class Hung(FakeHttp):
        def post(self, *args: Any, **kwargs: Any) -> FakeResponse:
            raise TimeoutError

    fryer = FryerClient(Hung({}), "me@example.com", "secret", "2abc", SIGNED_IN)  # type: ignore[arg-type]
    with pytest.raises(api.FryerError):
        await fryer.status()


@pytest.mark.parametrize(
    "reply",
    [
        {"code": 0, "result": "nonsense"},
        {"code": 0, "result": {"code": 0, "result": ["not", "a", "dict"]}},
        {"code": 0, "result": {"code": 0, "result": {"cookStatus": "cooking", "stepIndex": "x"}}},
        {"code": 0, "result": {"code": 0, "result": {"cookStatus": "heating", "preheatLastTime": "soon"}}},
        {"code": 0, "result": {"code": 0, "result": {}}},
    ],
)
async def test_malformed_status_is_a_fryer_error(reply: dict[str, Any]) -> None:
    fryer, _ = client({"/cloud/v1/deviceManaged/devices": DEVICES, "/cloud/v2/deviceManaged/bypassV2": reply})
    with pytest.raises(api.FryerError):
        await fryer.status()


async def test_device_list_with_junk_items_is_no_fryer() -> None:
    fryer, _ = client({"/cloud/v1/deviceManaged/devices": {"code": 0, "result": {"list": ["junk", None]}}})
    with pytest.raises(api.NoFryer):
        await fryer.find_fryer()


async def test_more_than_one_fryer_is_refused() -> None:
    second = {"deviceType": "CS358-AF", "cid": "CID-2", "configModule": "CM", "deviceRegion": "US"}
    listing = {"code": 0, "result": {"list": [*DEVICES["result"]["list"], second]}}
    fryer, _ = client({"/cloud/v1/deviceManaged/devices": listing})
    with pytest.raises(api.TooManyFryers):
        await fryer.find_fryer()
