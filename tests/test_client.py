"""Tests for the CS358-AF client. The VeSync cloud is mocked."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

import cosori358
from cosori358 import Cosori358, FryerStatus, Refused, plan, redact, validate
from fakes import DEVICE, TOKEN_EXPIRED, make_manager, make_status

# --- limits ---------------------------------------------------------------


@pytest.mark.parametrize("temp", [169, 401, 0, -5])
def test_temperature_outside_range_is_refused(temp):
    with pytest.raises(Refused, match="outside the fryer's range"):
        validate(temp, 10, "f")


@pytest.mark.parametrize("minutes", [0, 61, 600])
def test_cook_time_is_capped_at_60_minutes(minutes):
    with pytest.raises(Refused, match="1-60 min"):
        validate(350, minutes, "f")


def test_limits_accept_the_edges():
    validate(170, 1, "f")
    validate(400, 60, "f")
    validate(204, 60, "c")


def test_celsius_range_is_used_for_a_celsius_fryer():
    with pytest.raises(Refused):
        plan("cook", make_status("standby", tempUnit="c"), "A", 350, 10)


# --- state checks ---------------------------------------------------------


@pytest.mark.parametrize("name", ["preheat", "cook"])
def test_heating_is_refused_while_the_basket_is_out(name):
    with pytest.raises(Refused, match="basket is out"):
        plan(name, make_status("pullOut"), "A", 350, 5)


@pytest.mark.parametrize("state", ["heating", "cooking", "preheatStop", "cookStop"])
def test_preheat_is_refused_while_busy(state):
    with pytest.raises(Refused, match="busy"):
        plan("preheat", make_status(state), "A", 350, 5)


@pytest.mark.parametrize("state", ["standby", "pullOut", "heating", "cooking", "cookEnd"])
def test_start_needs_a_finished_preheat(state):
    with pytest.raises(Refused, match="finished preheat"):
        plan("start", make_status(state), "A")


def test_start_is_planned_after_preheat_ends():
    assert plan("start", make_status("preheatEnd"), "A") == ("preheatCook", {})


@pytest.mark.parametrize("state", ["standby", "pullOut", "cookEnd"])
def test_stop_is_refused_when_nothing_runs(state):
    with pytest.raises(Refused, match="Nothing is running"):
        plan("stop", make_status(state), "A")


@pytest.mark.parametrize("state", ["heating", "cooking", "preheatEnd", "cookStop"])
def test_stop_is_planned_when_something_runs(state):
    assert plan("stop", make_status(state), "A") == ("endCook", {})


def test_unknown_state_refuses_everything():
    with pytest.raises(Refused, match="unrecognised state"):
        plan("cook", make_status("somethingNew"), "A", 350, 5)


def test_pause_and_resume_report_that_the_command_is_unknown():
    with pytest.raises(Refused, match="not known yet"):
        plan("pause", make_status("cooking"), "A")
    with pytest.raises(Refused, match="not known yet"):
        plan("resume", make_status("cookStop"), "A")


# --- payloads -------------------------------------------------------------


def test_preheat_payload_carries_the_cook_temp_and_time():
    method, data = plan("preheat", make_status("standby"), "ACC", 400, 12)
    assert method == "startStepCook"
    assert data["hasPreheat"] == 1
    assert data["preheatTemp"] == 400
    assert data["tempUnit"] == "f"
    assert data["stepArray"][0]["cookTemp"] == 400
    assert data["stepArray"][0]["cookSetTime"] == 12 * 60


def test_cook_payload_has_no_preheat():
    _, data = plan("cook", make_status("standby"), "ACC", 350, 5)
    assert data["hasPreheat"] == 0
    assert data["preheatTemp"] == 0
    assert data["stepArray"][0]["cookSetTime"] == 300


def test_redact_hides_secrets_everywhere():
    body = Cosori358(make_manager(), DEVICE).build_body("startStepCook", {"accountId": "ACC"})
    text = json.dumps(redact(body))
    for secret in ("TOKEN-SECRET", "ACCOUNT-SECRET", "CID-SECRET", '"ACC"'):
        assert secret not in text
    assert body["token"] == "TOKEN-SECRET"  # the original is untouched


# --- status ---------------------------------------------------------------


def test_status_is_parsed_from_the_real_response_shape():
    status = make_status("cooking", currentTemp=355, totalTimeRemaining=400)
    assert status.cook_status == "cooking"
    assert status.cook_temp == 360
    assert status.cook_seconds_set == 720
    assert status.current_temp == 355
    assert status.is_active


def test_status_survives_an_empty_step_array():
    status = FryerStatus.from_result({"cookStatus": "standby", "stepArray": []})
    assert status.cook_temp is None and not status.is_active


# --- rejected tokens: one new login at most, never a loop -----------------


async def test_rejected_token_gets_one_login_and_a_retry_with_the_new_token():
    manager = make_manager("standby")
    good = manager.session.handler

    async def login():
        manager.token = "NEW-TOKEN"
        return True

    manager.login = AsyncMock(side_effect=login)
    manager.session.handler = (
        lambda url, body: TOKEN_EXPIRED if body["token"] == "TOKEN-SECRET" else good(url, body)
    )
    status = await Cosori358(manager, DEVICE).status()
    assert status.cook_status == "standby"
    assert manager.login.await_count == 1
    assert [body["token"] for _, body in manager.session.calls] == ["TOKEN-SECRET", "NEW-TOKEN"]


async def test_token_still_rejected_after_login_stops_with_an_error():
    manager = make_manager("standby")
    manager.session.handler = lambda url, body: TOKEN_EXPIRED
    with pytest.raises(cosori358.FryerError, match="even after signing in again"):
        await Cosori358(manager, DEVICE).status()
    assert manager.login.await_count == 1
    assert len(manager.session.calls) == 2


async def test_no_second_login_inside_the_cooldown():
    manager = make_manager("standby")
    manager.session.handler = lambda url, body: TOKEN_EXPIRED
    fryer = Cosori358(manager, DEVICE)
    for _ in range(5):
        with pytest.raises(cosori358.FryerError):
            await fryer.status()
    assert manager.login.await_count == 1


async def test_failed_login_is_reported_not_retried():
    manager = make_manager("standby")
    manager.session.handler = lambda url, body: TOKEN_EXPIRED
    manager.login = AsyncMock(return_value=False)
    with pytest.raises(cosori358.FryerError, match="signing in again failed"):
        await Cosori358(manager, DEVICE).status()
    assert manager.login.await_count == 1
