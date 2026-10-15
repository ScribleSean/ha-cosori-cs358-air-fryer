"""Home Assistant tests. The VeSync client is replaced by a fake."""

from __future__ import annotations

from collections.abc import Generator
from datetime import timedelta
from unittest.mock import AsyncMock, patch

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

from custom_components.cosori_fryer.api import AuthError, Device, FryerError, Session
from custom_components.cosori_fryer.const import (
    CONF_ACCOUNT_ID,
    CONF_LAST_LOGIN,
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
        patch("custom_components.cosori_fryer.config_flow.FryerClient", side_effect=make),
    ):
        yield fake


async def setup(hass: HomeAssistant, fryer: FakeClient, state: str = "standby", **extra) -> MockConfigEntry:
    hass.config.units = US_CUSTOMARY_SYSTEM
    fryer.result = raw(state, **extra)
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id="ACCOUNT-1", version=2)
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


async def test_user_flow_signs_in_and_stores_the_session(hass: HomeAssistant, fryer: FakeClient) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
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
        (None, "no_fryer", "no_fryer"),
        (RuntimeError("bug"), None, "unknown"),
    ],
)
async def test_user_flow_errors(
    hass: HomeAssistant, fryer: FakeClient, login_error, find_error, expected: str
) -> None:
    fryer.login_error = login_error
    fryer.no_fryer = find_error == "no_fryer"
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
        data={CONF_EMAIL: "me@example.com", CONF_PASSWORD: "x"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}


async def test_same_account_twice_is_refused(hass: HomeAssistant, fryer: FakeClient) -> None:
    await setup(hass, fryer)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
        data={CONF_EMAIL: "me@example.com", CONF_PASSWORD: "secret"},
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "already_configured"


async def test_rejected_password_asks_to_sign_in_again(hass: HomeAssistant, fryer: FakeClient) -> None:
    fryer.reject_token = True
    fryer.login_error = AuthError("password changed")
    hass.config.units = US_CUSTOMARY_SYSTEM
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id="ACCOUNT-1", version=2)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]

    fryer.login_error = None
    result = await hass.config_entries.flow.async_configure(flows[0]["flow_id"], {CONF_PASSWORD: "new"})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "new"
    assert entry.state is ConfigEntryState.LOADED


async def test_version_1_entries_are_migrated(hass: HomeAssistant, fryer: FakeClient) -> None:
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
    assert state(hass, "sensor.air_fryer_cooking_status") == "pull_out"
    assert state(hass, "number.air_fryer_cooking_temperature") == "360"
    assert state(hass, "number.air_fryer_cooking_time") == "10"
    for button in ("preheat", "start", "stop"):
        assert state(hass, f"button.air_fryer_{button}") == STATE_UNAVAILABLE


async def test_preheat_sensors(hass: HomeAssistant, fryer: FakeClient) -> None:
    await setup(
        hass,
        fryer,
        "heating",
        preheatLastTime=230,
        totalTimeRemaining=830,
        preheatSetTime=240,
    )
    assert state(hass, "sensor.air_fryer_cooking_status") == "heating"
    assert state(hass, "sensor.air_fryer_remaining_time") == "4"
    assert state(hass, "sensor.air_fryer_preheating_set_time") == "4"
    assert state(hass, "sensor.air_fryer_cooking_set_temperature") == "360"
    assert state(hass, "sensor.air_fryer_cooking_set_time") == "10"


async def test_standby_has_no_set_values(hass: HomeAssistant, fryer: FakeClient) -> None:
    await setup(hass, fryer, "standby")
    assert state(hass, "sensor.air_fryer_cooking_status") == "standby"
    assert state(hass, "sensor.air_fryer_cooking_set_temperature") == "unknown"


@pytest.mark.parametrize(
    ("fryer_state", "available"),
    [
        ("standby", {"preheat", "start"}),
        ("heating", {"stop"}),
        ("preheatEnd", {"start", "stop"}),
        ("cooking", {"stop"}),
        ("pullOut", set()),
    ],
)
async def test_buttons_are_available_only_when_usable(
    hass: HomeAssistant, fryer: FakeClient, fryer_state: str, available: set[str]
) -> None:
    await setup(hass, fryer, fryer_state)
    shown = {
        b for b in ("preheat", "start", "stop") if state(hass, f"button.air_fryer_{b}") != STATE_UNAVAILABLE
    }
    assert shown == available


async def test_buttons_follow_the_fryer(hass: HomeAssistant, fryer: FakeClient) -> None:
    await setup(hass, fryer, "standby")
    assert state(hass, "button.air_fryer_stop") == STATE_UNAVAILABLE
    fryer.result = raw("cooking")
    await poll(hass)
    assert state(hass, "button.air_fryer_stop") != STATE_UNAVAILABLE
    assert state(hass, "button.air_fryer_preheat") == STATE_UNAVAILABLE


async def press(hass: HomeAssistant, button: str) -> None:
    await hass.services.async_call(
        "button", "press", {"entity_id": f"button.air_fryer_{button}"}, blocking=True
    )


async def test_buttons_work_like_the_fryer(hass: HomeAssistant, fryer: FakeClient) -> None:
    await setup(hass, fryer, "standby")
    for entity, value in (("cooking_temperature", 380), ("cooking_time", 12)):
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": f"number.air_fryer_{entity}", "value": value},
            blocking=True,
        )
    assert fryer.commands == []

    await press(hass, "preheat")
    assert fryer.commands[-1] == ("start", 380, 12, True)

    # Start after preheat continues the queued cook, like the button on the fryer.
    fryer.result = raw("preheatEnd")
    await poll(hass, POLL_ACTIVE)
    await press(hass, "start")
    assert fryer.commands[-1] == ("continue",)

    # Start from standby cooks straight away.
    fryer.result = raw("standby")
    await poll(hass, POLL_ACTIVE * 2)
    await press(hass, "start")
    assert fryer.commands[-1] == ("start", 380, 12, False)


async def test_temperature_setting_moves_in_tens(hass: HomeAssistant, fryer: FakeClient) -> None:
    await setup(hass, fryer)
    assert hass.states.get("number.air_fryer_cooking_temperature").attributes["step"] == 10


# --- action ---------------------------------------------------------------


async def test_start_cooking_action(hass: HomeAssistant, fryer: FakeClient) -> None:
    await setup(hass, fryer, "standby")
    await hass.services.async_call(
        DOMAIN, "start_cooking", {"temperature": 400, "cooking_time": 8}, blocking=True
    )
    await hass.services.async_call(
        DOMAIN,
        "start_cooking",
        {"temperature": 360, "cooking_time": 12, "preheat": True},
        blocking=True,
    )
    assert fryer.commands == [("start", 400, 8, False), ("start", 360, 12, True)]


async def setup_other_account(hass: HomeAssistant, *, loaded: bool) -> tuple[MockConfigEntry, FakeClient]:
    other = FakeClient()
    other.session = Session("other-token", "ACCOUNT-2")
    other.device = Device(cid="CID-2", name="Other Fryer", config_module="CM", region="US")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**ENTRY_DATA, CONF_EMAIL: "other@example.com", CONF_ACCOUNT_ID: "ACCOUNT-2"},
        unique_id="ACCOUNT-2",
        version=2,
    )
    entry.add_to_hass(hass)
    if loaded:
        with (
            patch("custom_components.cosori_fryer.FryerClient", return_value=other),
            patch.object(other, "find_fryer", new=AsyncMock(return_value=other.device)),
        ):
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
    return entry, other


@pytest.mark.parametrize("loaded", [False, True])
async def test_untargeted_cook_refuses_multiple_accounts(
    hass: HomeAssistant, fryer: FakeClient, loaded: bool
) -> None:
    await setup(hass, fryer)
    _, other = await setup_other_account(hass, loaded=loaded)
    with pytest.raises(ServiceValidationError) as caught:
        await hass.services.async_call(
            DOMAIN, "start_cooking", {"temperature": 360, "cooking_time": 10}, blocking=True
        )
    assert caught.value.translation_key == "choose_account"
    assert fryer.commands == other.commands == []


@pytest.mark.parametrize("target_second", [False, True])
async def test_cook_only_uses_the_selected_account(
    hass: HomeAssistant, fryer: FakeClient, target_second: bool
) -> None:
    first = await setup(hass, fryer)
    second, other = await setup_other_account(hass, loaded=True)
    await hass.services.async_call(
        DOMAIN,
        "start_cooking",
        {
            "temperature": 380,
            "cooking_time": 12,
            "config_entry_id": second.entry_id if target_second else first.entry_id,
        },
        blocking=True,
    )
    selected, untouched = (other, fryer) if target_second else (fryer, other)
    assert selected.commands == [("start", 380, 12, False)]
    assert untouched.commands == []


@pytest.mark.parametrize("target", ["unknown", "unloaded", "other_integration"])
async def test_invalid_cooking_target_never_falls_back(
    hass: HomeAssistant, fryer: FakeClient, target: str
) -> None:
    await setup(hass, fryer)
    entry = MockConfigEntry(domain=DOMAIN if target == "unloaded" else "other", data={})
    if target != "unknown":
        entry.add_to_hass(hass)
    with pytest.raises(ServiceValidationError) as caught:
        await hass.services.async_call(
            DOMAIN,
            "start_cooking",
            {"temperature": 360, "cooking_time": 10, "config_entry_id": entry.entry_id},
            blocking=True,
        )
    assert caught.value.translation_key == "not_loaded"
    assert fryer.commands == []


@pytest.mark.parametrize(
    "data",
    [{"temperature": 375, "cooking_time": 8}, {"temperature": 360, "cooking_time": 61}],
)
async def test_action_refuses_values_the_fryer_does_not_take(
    hass: HomeAssistant, fryer: FakeClient, data: dict
) -> None:
    await setup(hass, fryer)
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(DOMAIN, "start_cooking", data, blocking=True)
    assert fryer.commands == []


async def test_action_explains_a_wrong_state(hass: HomeAssistant, fryer: FakeClient) -> None:
    await setup(hass, fryer, "heating")
    with pytest.raises(ServiceValidationError) as caught:
        await hass.services.async_call(
            DOMAIN,
            "start_cooking",
            {"temperature": 360, "cooking_time": 5, "preheat": True},
            blocking=True,
        )
    assert caught.value.translation_key == "cannot_preheat"
    assert caught.value.translation_placeholders == {"state": "heating"}
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
    assert state(hass, "sensor.air_fryer_cooking_status") == "standby"


async def test_no_second_login_within_the_cooldown(hass: HomeAssistant, fryer: FakeClient) -> None:
    await setup(hass, fryer)
    fryer.reject_token = True
    await poll(hass)
    fryer.reject_token = True
    await poll(hass, POLL_IDLE * 2)
    assert fryer.logins == 1
    assert state(hass, "sensor.air_fryer_cooking_status") == STATE_UNAVAILABLE


@pytest.mark.parametrize("failed", [False, True])
async def test_login_cooldown_survives_reload(
    hass: HomeAssistant, fryer: FakeClient, freezer, failed: bool
) -> None:
    entry = await setup(hass, fryer)
    fryer.reject_token = True
    if failed:
        fryer.login_error = FryerError("VeSync is rate limiting logins")
        with pytest.raises(FryerError):
            await entry.runtime_data._signed_in(fryer.status)
    else:
        await entry.runtime_data._signed_in(fryer.status)
    assert fryer.logins == 1
    attempted_at = entry.data[CONF_LAST_LOGIN]

    # Recreating the coordinator must not bypass the previous attempt's cooldown.
    fryer.reject_token = True
    assert not await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert fryer.logins == 1
    assert entry.data[CONF_LAST_LOGIN] == attempted_at

    # Once the persisted cooldown expires, recovery is allowed again.
    fryer.login_error = None
    freezer.tick(timedelta(seconds=301))
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert fryer.logins == 2
    assert entry.data[CONF_LAST_LOGIN] > attempted_at


async def test_restored_entry_keeps_the_login_cooldown(hass: HomeAssistant, fryer: FakeClient) -> None:
    import time

    fryer.reject_token = True
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**ENTRY_DATA, CONF_LAST_LOGIN: time.time()},
        unique_id="ACCOUNT-1",
        version=2,
    )
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert fryer.logins == 0


async def test_events_when_preheat_and_cooking_finish(hass: HomeAssistant, fryer: FakeClient) -> None:
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


async def test_no_persistent_notifications(hass: HomeAssistant, fryer: FakeClient) -> None:
    await setup(hass, fryer, "heating")
    fryer.result = raw("preheatEnd")
    with patch("homeassistant.components.persistent_notification.async_create") as create:
        await poll(hass, POLL_ACTIVE)
    create.assert_not_called()


# --- review fixes ---------------------------------------------------------


async def test_old_entries_get_the_account_id_and_can_sign_in_again(
    hass: HomeAssistant, fryer: FakeClient
) -> None:
    """Entries from before 0.3.0 used the email as unique id; reauth must still work."""
    hass.config.units = US_CUSTOMARY_SYSTEM
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: "me@example.com", CONF_PASSWORD: "secret"},
        unique_id="me@example.com",
        version=1,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.unique_id == "ACCOUNT-1"

    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PASSWORD: "new"})
    await hass.async_block_till_done()
    assert result["reason"] == "reauth_successful"


async def test_future_entry_versions_are_refused(hass: HomeAssistant, fryer: FakeClient) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id="ACCOUNT-1", version=3)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.MIGRATION_ERROR


@pytest.mark.parametrize(
    ("platform", "old_key", "new_entity"),
    [
        ("number", "temperature", "number.air_fryer_cooking_temperature"),
        ("number", "minutes", "number.air_fryer_cooking_time"),
        ("sensor", "set_temperature", "sensor.air_fryer_cooking_set_temperature"),
        ("sensor", "time_remaining", "sensor.air_fryer_remaining_time"),
        ("button", "air_fry", "button.air_fryer_start"),
    ],
)
async def test_entities_from_0_3_keep_their_entity_ids(
    hass: HomeAssistant, fryer: FakeClient, platform: str, old_key: str, new_entity: str
) -> None:
    from homeassistant.helpers import entity_registry as er

    hass.config.units = US_CUSTOMARY_SYSTEM
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id="ACCOUNT-1", version=2)
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    old = registry.async_get_or_create(
        platform, DOMAIN, f"CID-1_{old_key}", config_entry=entry, suggested_object_id=f"kept_{old_key}"
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    kept = registry.async_get(old.entity_id)
    assert kept is not None and kept.unique_id != f"CID-1_{old_key}"
    assert registry.async_get(new_entity) is None  # no duplicate created alongside


async def test_stop_does_not_fire_cook_done(hass: HomeAssistant, fryer: FakeClient) -> None:
    await setup(hass, fryer, "cooking")
    done = async_capture_events(hass, EVENT_COOK_DONE)
    await press(hass, "stop")
    fryer.result = raw("standby")
    await poll(hass, POLL_ACTIVE)
    assert done == []


async def test_events_carry_the_device(hass: HomeAssistant, fryer: FakeClient) -> None:
    await setup(hass, fryer, "heating")
    preheated = async_capture_events(hass, EVENT_PREHEAT_DONE)
    fryer.result = raw("preheatEnd")
    await poll(hass, POLL_ACTIVE)
    assert "device_id" in preheated[0].data


async def test_overlapping_requests_share_one_login(hass: HomeAssistant, fryer: FakeClient) -> None:
    import asyncio

    entry = await setup(hass, fryer)
    fryer.reject_token = True
    coordinator = entry.runtime_data
    results = await asyncio.gather(
        coordinator._signed_in(fryer.status), coordinator._signed_in(fryer.status), return_exceptions=True
    )
    assert fryer.logins == 1
    assert not any(isinstance(r, Exception) for r in results)


async def test_rejected_password_during_a_command_asks_to_sign_in_again(
    hass: HomeAssistant, fryer: FakeClient
) -> None:
    from homeassistant.exceptions import HomeAssistantError

    await setup(hass, fryer, "standby")
    fryer.reject_token = True
    fryer.login_error = AuthError("password changed")
    with pytest.raises(HomeAssistantError) as caught:
        await press(hass, "start")
    assert caught.value.translation_key == "auth_failed"
    await hass.async_block_till_done()
    assert [f["context"]["source"] for f in hass.config_entries.flow.async_progress()] == ["reauth"]


async def test_unreachable_vesync_retries_setup_quietly(
    hass: HomeAssistant, fryer: FakeClient, caplog: pytest.LogCaptureFixture
) -> None:
    fryer.find_error = FryerError("down")
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id="ACCOUNT-1", version=2)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert "Unexpected error" not in caplog.text


async def test_device_from_0_2_is_kept(hass: HomeAssistant, fryer: FakeClient) -> None:
    from homeassistant.helpers import device_registry as dr

    hass.config.units = US_CUSTOMARY_SYSTEM
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id="ACCOUNT-1", version=2)
    entry.add_to_hass(hass)
    devices = dr.async_get(hass)
    old = devices.async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, entry.entry_id)})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert devices.async_get_device({(DOMAIN, "CID-1")}).id == old.id
    assert len(dr.async_entries_for_config_entry(devices, entry.entry_id)) == 1


async def test_two_old_entries_for_one_account_do_not_share_a_unique_id(
    hass: HomeAssistant, fryer: FakeClient
) -> None:
    hass.config.units = US_CUSTOMARY_SYSTEM
    first = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id="ACCOUNT-1", version=2)
    second = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id="me@example.com", version=2)
    first.add_to_hass(hass)
    second.add_to_hass(hass)
    await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done()
    assert second.unique_id == "me@example.com"
