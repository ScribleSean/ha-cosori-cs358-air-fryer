"""Home Assistant tests. The VeSync client is replaced by a fake."""

from __future__ import annotations

from collections.abc import Generator
from datetime import timedelta
from unittest.mock import patch

import pytest
import voluptuous as vol
from fakes import FakeClient, raw
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.cosori_fryer.api import AuthError, FryerError
from custom_components.cosori_fryer.const import (
    CONF_ACCOUNT_ID,
    CONF_TERMINAL_ID,
    CONF_TOKEN,
    DOMAIN,
    EVENT_COOK_DONE,
    EVENT_PREHEAT_DONE,
    POLL_ACTIVE,
    POLL_IDLE,
)

ENTRY_DATA = {
    CONF_EMAIL: "me@example.com",
    CONF_PASSWORD: "secret",
    CONF_TERMINAL_ID: "2abc",
    CONF_TOKEN: "token-0",
    CONF_ACCOUNT_ID: "ACCOUNT-1",
}


@pytest.fixture(name="fryer")
def fryer_fixture() -> Generator[FakeClient]:
    """One fake client shared by setup and the config flow."""
    fake = FakeClient()

    def make(*args, **kwargs) -> FakeClient:
        fake.session = args[4] if len(args) > 4 else kwargs.get("session", fake.session)
        return fake

    with (
        patch("custom_components.cosori_fryer.FryerClient", side_effect=make),
        patch(
            "custom_components.cosori_fryer.config_flow.FryerClient", side_effect=make
        ),
    ):
        yield fake


async def setup(
    hass: HomeAssistant, fryer: FakeClient, state: str = "standby", **extra
) -> MockConfigEntry:
    hass.config.units = US_CUSTOMARY_SYSTEM
    fryer.result = raw(state, **extra)
    entry = MockConfigEntry(
        domain=DOMAIN, data=ENTRY_DATA, unique_id="ACCOUNT-1", version=2
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def poll(hass: HomeAssistant, after: timedelta = POLL_IDLE) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + after + timedelta(seconds=1))
    await hass.async_block_till_done()


def state(hass: HomeAssistant, entity_id: str) -> str:
    return hass.states.get(entity_id).state


# --- config flow ----------------------------------------------------------


async def test_user_flow_signs_in_and_stores_the_session(
    hass: HomeAssistant, fryer: FakeClient
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: "me@example.com", CONF_PASSWORD: "secret"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Air Fryer"
    assert result["result"].unique_id == "ACCOUNT-1"
    assert result["data"][CONF_TOKEN] == "token-1"
    # The flow's login is reused at setup: one login, not two.
    assert fryer.logins == 1


@pytest.mark.parametrize(
    ("login_error", "find_error", "expected"),
    [
        (AuthError("no"), None, "invalid_auth"),
        (FryerError("down"), None, "cannot_connect"),
        (None, FryerError("No CS358-AF on this VeSync account"), "no_fryer"),
    ],
)
async def test_user_flow_errors(
    hass: HomeAssistant, fryer: FakeClient, login_error, find_error, expected: str
) -> None:
    fryer.login_error, fryer.find_error = login_error, find_error
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
        data={CONF_EMAIL: "me@example.com", CONF_PASSWORD: "x"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}


async def test_same_account_twice_is_refused(
    hass: HomeAssistant, fryer: FakeClient
) -> None:
    await setup(hass, fryer)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
        data={CONF_EMAIL: "me@example.com", CONF_PASSWORD: "secret"},
    )
    assert (
        result["type"] is FlowResultType.ABORT
        and result["reason"] == "already_configured"
    )


async def test_rejected_password_asks_to_sign_in_again(
    hass: HomeAssistant, fryer: FakeClient
) -> None:
    fryer.reject_token = True
    fryer.login_error = AuthError("password changed")
    hass.config.units = US_CUSTOMARY_SYSTEM
    entry = MockConfigEntry(
        domain=DOMAIN, data=ENTRY_DATA, unique_id="ACCOUNT-1", version=2
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]

    fryer.login_error = None
    result = await hass.config_entries.flow.async_configure(
        flows[0]["flow_id"], {CONF_PASSWORD: "new"}
    )
    await hass.async_block_till_done()
    assert (
        result["type"] is FlowResultType.ABORT
        and result["reason"] == "reauth_successful"
    )
    assert entry.data[CONF_PASSWORD] == "new"
    assert entry.state is ConfigEntryState.LOADED


async def test_version_1_entries_are_migrated(
    hass: HomeAssistant, fryer: FakeClient
) -> None:
    hass.config.units = US_CUSTOMARY_SYSTEM
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: "me@example.com", CONF_PASSWORD: "secret"},
        options={"notify_service": "x"},
        unique_id="me@example.com",
        version=1,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.version == 2 and entry.data[CONF_TERMINAL_ID].startswith("2")
    assert entry.options == {}
    assert entry.state is ConfigEntryState.LOADED


# --- entities -------------------------------------------------------------


async def test_entities(hass: HomeAssistant, fryer: FakeClient) -> None:
    await setup(hass, fryer, "pullOut")
    assert state(hass, "sensor.air_fryer_cooking_status") == "basket_out"
    assert state(hass, "number.air_fryer_cook_temperature") == "360"
    assert state(hass, "number.air_fryer_cook_time") == "10"
    for button in ("preheat", "air_fry", "stop"):
        assert state(hass, f"button.air_fryer_{button}") == STATE_UNAVAILABLE


async def test_preheat_shows_preheat_time_only(
    hass: HomeAssistant, fryer: FakeClient
) -> None:
    await setup(
        hass,
        fryer,
        "heating",
        preheatLastTime=230,
        totalTimeRemaining=830,
        preheatTemp=380,
    )
    assert state(hass, "sensor.air_fryer_cooking_status") == "preheating"
    assert state(hass, "sensor.air_fryer_time_remaining") == "4"
    assert state(hass, "sensor.air_fryer_set_temperature") == "380"


@pytest.mark.parametrize(
    ("fryer_state", "available"),
    [
        ("standby", {"preheat", "air_fry"}),
        ("heating", {"stop"}),
        ("preheatEnd", {"air_fry", "stop"}),
        ("cooking", {"stop"}),
        ("pullOut", set()),
    ],
)
async def test_buttons_are_available_only_when_usable(
    hass: HomeAssistant, fryer: FakeClient, fryer_state: str, available: set[str]
) -> None:
    await setup(hass, fryer, fryer_state)
    shown = {
        b
        for b in ("preheat", "air_fry", "stop")
        if state(hass, f"button.air_fryer_{b}") != STATE_UNAVAILABLE
    }
    assert shown == available


async def test_buttons_follow_the_fryer(hass: HomeAssistant, fryer: FakeClient) -> None:
    await setup(hass, fryer, "standby")
    assert state(hass, "button.air_fryer_stop") == STATE_UNAVAILABLE
    fryer.result = raw("cooking")
    await poll(hass)
    assert state(hass, "button.air_fryer_stop") != STATE_UNAVAILABLE
    assert state(hass, "button.air_fryer_preheat") == STATE_UNAVAILABLE


async def test_preheat_and_air_fry_buttons_use_the_settings(
    hass: HomeAssistant, fryer: FakeClient
) -> None:
    await setup(hass, fryer, "standby")
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": "number.air_fryer_cook_temperature", "value": 380},
        blocking=True,
    )
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": "number.air_fryer_cook_time", "value": 12},
        blocking=True,
    )
    assert fryer.commands == []
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.air_fryer_preheat"}, blocking=True
    )
    assert fryer.commands[-1] == ("start", 380, 12, True)

    fryer.result = raw("preheatEnd")
    await poll(hass, POLL_ACTIVE)
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.air_fryer_air_fry"}, blocking=True
    )
    assert fryer.commands[-1] == ("air_fry", 380, 12, True)


async def test_temperature_setting_moves_in_tens(
    hass: HomeAssistant, fryer: FakeClient
) -> None:
    await setup(hass, fryer)
    assert hass.states.get("number.air_fryer_cook_temperature").attributes["step"] == 10


# --- actions --------------------------------------------------------------


async def test_actions(hass: HomeAssistant, fryer: FakeClient) -> None:
    await setup(hass, fryer, "standby")
    await hass.services.async_call(
        DOMAIN, "preheat", {"temperature": 400}, blocking=True
    )
    await hass.services.async_call(
        DOMAIN, "air_fry", {"temperature": 360, "minutes": 8}, blocking=True
    )
    fryer.result = raw("cooking")
    await hass.services.async_call(DOMAIN, "stop", {}, blocking=True)
    assert fryer.commands == [
        ("start", 400, 10, True),
        ("air_fry", 360, 8, False),
        ("stop",),
    ]


@pytest.mark.parametrize(
    "data", [{"temperature": 375, "minutes": 8}, {"temperature": 360, "minutes": 61}]
)
async def test_actions_refuse_values_the_fryer_does_not_take(
    hass: HomeAssistant, fryer: FakeClient, data: dict
) -> None:
    await setup(hass, fryer)
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(DOMAIN, "air_fry", data, blocking=True)
    assert fryer.commands == []


async def test_actions_explain_a_wrong_state(
    hass: HomeAssistant, fryer: FakeClient
) -> None:
    await setup(hass, fryer, "heating")
    with pytest.raises(ServiceValidationError) as caught:
        await hass.services.async_call(
            DOMAIN, "preheat", {"temperature": 360}, blocking=True
        )
    assert caught.value.translation_key == "cannot_preheat"
    assert caught.value.translation_placeholders == {"state": "preheating"}
    assert fryer.commands == []


# --- sessions and events --------------------------------------------------


async def test_rejected_token_signs_in_once_and_saves_the_session(
    hass: HomeAssistant, fryer: FakeClient
) -> None:
    entry = await setup(hass, fryer)
    assert fryer.logins == 0
    fryer.reject_token = True
    await poll(hass)
    assert fryer.logins == 1
    assert entry.data[CONF_TOKEN] == "token-1"
    assert state(hass, "sensor.air_fryer_cooking_status") == "idle"


async def test_no_second_login_within_the_cooldown(
    hass: HomeAssistant, fryer: FakeClient
) -> None:
    await setup(hass, fryer)
    fryer.reject_token = True
    await poll(hass)
    fryer.reject_token = True
    await poll(hass, POLL_IDLE * 2)
    assert fryer.logins == 1
    assert state(hass, "sensor.air_fryer_cooking_status") == STATE_UNAVAILABLE


async def test_events_when_preheat_and_cooking_finish(
    hass: HomeAssistant, fryer: FakeClient
) -> None:
    await setup(hass, fryer, "heating")
    preheated = async_capture_events(hass, EVENT_PREHEAT_DONE)
    done = async_capture_events(hass, EVENT_COOK_DONE)
    fryer.result = raw("preheatEnd")
    await poll(hass, POLL_ACTIVE)
    fryer.result = raw("cooking")
    await poll(hass, POLL_ACTIVE * 2)
    fryer.result = raw("pullOut")
    await poll(hass, POLL_ACTIVE * 3)
    assert len(preheated) == 1 and done == []
    fryer.result = raw("cooking")
    await poll(hass, POLL_ACTIVE * 4)
    fryer.result = raw("cookEnd")
    await poll(hass, POLL_ACTIVE * 5)
    assert len(done) == 1


async def test_no_persistent_notifications(
    hass: HomeAssistant, fryer: FakeClient
) -> None:
    await setup(hass, fryer, "heating")
    fryer.result = raw("preheatEnd")
    with patch(
        "homeassistant.components.persistent_notification.async_create"
    ) as create:
        await poll(hass, POLL_ACTIVE)
    create.assert_not_called()
