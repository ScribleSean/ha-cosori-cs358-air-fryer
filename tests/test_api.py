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

    def post(
        self, url: str, json: dict[str, Any], headers: dict[str, str]
    ) -> FakeResponse:
        path = url.split(".com", 1)[-1].split(".eu", 1)[-1]
        self.posts.append((path, json))
        answer = self.routes[path]
        return FakeResponse(answer(json) if callable(answer) else answer)


def client(
    routes: dict[str, Any], session: Session | None = SIGNED_IN
) -> tuple[FryerClient, FakeHttp]:
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


async def test_login_retries_once_in_the_accounts_region() -> None:
    answers = iter(
        [
            {
                "code": -11260022,
                "result": {
                    "bizToken": "BIZ",
                    "countryCode": "GB",
                    "currentRegion": "EU",
                },
            },
            {
                "code": 0,
                "result": {
                    "token": "T",
                    "accountID": "A",
                    "countryCode": "GB",
                    "currentRegion": "EU",
                },
            },
        ]
    )
    fryer, http = client(
        {
            "/globalPlatform/api/accountAuth/v1/authByPWDOrOTM": {
                "code": 0,
                "result": {"authorizeCode": "AUTH"},
            },
            "/user/api/accountManage/v1/loginByAuthorizeCode4Vesync": lambda body: next(
                answers
            ),
        },
        session=None,
    )
    session = await fryer.login()
    assert session.region == "EU"
    assert http.posts[2][1]["bizToken"] == "BIZ"


async def test_wrong_password_is_an_auth_error() -> None:
    fryer, _ = client(
        {
            "/globalPlatform/api/accountAuth/v1/authByPWDOrOTM": {
                "code": -11201000,
                "msg": "wrong",
            }
        }
    )
    with pytest.raises(AuthError):
        await fryer.login()


async def test_rate_limited_login_is_not_an_auth_error() -> None:
    fryer, _ = client(
        {
            "/globalPlatform/api/accountAuth/v1/authByPWDOrOTM": {
                "code": -1,
                "msg": "device or ip request is too frequent",
            }
        }
    )
    with pytest.raises(api.FryerError) as caught:
        await fryer.login()
    assert not isinstance(caught.value, AuthError)


async def test_find_fryer_picks_the_cs358() -> None:
    fryer, _ = client({"/cloud/v1/deviceManaged/devices": DEVICES})
    device = await fryer.find_fryer()
    assert device.cid == "CID-1" and device.name == "Air Fryer"


async def test_rejected_token_is_reported_not_retried() -> None:
    fryer, http = client(
        {"/cloud/v1/deviceManaged/devices": {"code": -11001000, "msg": "token expired"}}
    )
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


async def test_air_fry_after_preheat_clears_the_program_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api, "SETTLE_SECONDS", 0)
    fryer, http = client(
        {
            "/cloud/v1/deviceManaged/devices": DEVICES,
            "/cloud/v2/deviceManaged/bypassV2": bypass({}),
        }
    )
    await fryer.air_fry(400, 8, clear_first=True)
    methods = [
        body["payload"]["method"] for path, body in http.posts if "payload" in body
    ]
    assert methods == ["endCook", "startCook"]
    assert http.posts[-1][1]["payload"]["data"]["hasPreheat"] == 0


@pytest.mark.parametrize(
    ("temperature", "minutes"), [(375, 10), (160, 10), (410, 10), (360, 0), (360, 61)]
)
def test_settings_outside_what_the_fryer_takes_are_refused(
    temperature: int, minutes: int
) -> None:
    with pytest.raises(ValueError):
        check_settings(temperature, minutes)


def test_preheat_shows_only_preheat_time() -> None:
    status = FryerStatus.parse(
        raw("heating", preheatLastTime=200, totalTimeRemaining=800, preheatTemp=380)
    )
    assert status.seconds_left == 200 and status.target_temp == 380


def test_preheated_shows_no_time_and_allows_air_fry() -> None:
    status = FryerStatus.parse(raw("preheatEnd"))
    assert (
        status.seconds_left == 0
        and status.can_air_fry
        and not status.can_preheat
        and status.waiting
    )


@pytest.mark.parametrize(
    ("state", "preheat", "air_fry", "stop"),
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
def test_what_each_state_allows(
    state: str, preheat: bool, air_fry: bool, stop: bool
) -> None:
    status = FryerStatus.parse(raw(state))
    assert (status.can_preheat, status.can_air_fry, status.can_stop) == (
        preheat,
        air_fry,
        stop,
    )
