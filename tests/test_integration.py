"""Home Assistant integration tests. The VeSync cloud is mocked."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
import voluptuous as vol
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

from custom_components.cosori_fryer.const import (
    CONF_ALLOW_UNVERIFIED,
    DOMAIN,
    EVENT_COOK_DONE,
    EVENT_PREHEAT_DONE,
    POLL_ACTIVE,
    POLL_IDLE,
)
from fakes import make_manager, sent_methods

CREDS = {CONF_EMAIL: "Owner@Example.com", CONF_PASSWORD: "pw"}


async def setup_fryer(hass: HomeAssistant, cook_status="standby", allow=False):
    hass.config.units = US_CUSTOMARY_SYSTEM  # like the real instance; avoids °C conversion
    manager = make_manager(cook_status)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=CREDS,
        unique_id="owner@example.com",
        options={CONF_ALLOW_UNVERIFIED: allow},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.cosori_fryer.VeSync", return_value=manager):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry, manager


def last_payload(manager) -> dict:
    return manager.session.calls[-2][1]["payload"]


# --- config flow ----------------------------------------------------------


async def test_config_flow_creates_an_entry(hass: HomeAssistant):
    with (
        patch("custom_components.cosori_fryer.config_flow.VeSync", return_value=make_manager()),
        patch("custom_components.cosori_fryer.VeSync", return_value=make_manager()),
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
        assert result["type"] is FlowResultType.FORM
        result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Air Fryer"
    assert result["result"].unique_id == "owner@example.com"


async def test_config_flow_reports_a_bad_login(hass: HomeAssistant):
    manager = make_manager()
    manager.login.return_value = False
    with patch("custom_components.cosori_fryer.config_flow.VeSync", return_value=manager):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}, data=CREDS
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_config_flow_reports_a_missing_fryer(hass: HomeAssistant):
    manager = make_manager()
    manager.session.handler = lambda url, body: {"code": 0, "result": {"list": []}}
    with patch("custom_components.cosori_fryer.config_flow.VeSync", return_value=manager):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}, data=CREDS
        )
    assert result["errors"] == {"base": "no_fryer"}


# --- entities -------------------------------------------------------------


async def test_entities_are_created(hass: HomeAssistant):
    await setup_fryer(hass, "pullOut")
    assert hass.states.get("sensor.air_fryer_status").state == "pullOut"
    assert hass.states.get("sensor.air_fryer_target_temperature").state == "360"
    assert hass.states.get("number.air_fryer_target_temperature").state == "350"
    assert hass.states.get("number.air_fryer_cook_minutes").state == "10"
    for name in ("preheat", "start_cooking", "stop"):
        assert hass.states.get(f"button.air_fryer_{name}") is not None
    for name in ("preheat", "start", "cook", "stop"):
        assert hass.services.has_service(DOMAIN, name)


# --- services: same checks and caps as the CLI ----------------------------


async def test_unproven_command_is_blocked_by_default(hass: HomeAssistant, monkeypatch):
    from custom_components.cosori_fryer import cosori358 as client

    monkeypatch.setitem(
        client.COMMANDS, "cook", client.Command("startStepCook", verified=False, heats=True)
    )
    _, manager = await setup_fryer(hass, "standby", allow=False)
    with pytest.raises(ServiceValidationError, match="not been proven"):
        await hass.services.async_call(
            DOMAIN, "cook", {"temp": 400, "minutes": 12}, blocking=True
        )
    assert "startStepCook" not in sent_methods(manager)


async def test_proven_preheat_needs_no_opt_in(hass: HomeAssistant):
    _, manager = await setup_fryer(hass, "standby", allow=False)
    await hass.services.async_call(
        DOMAIN, "preheat", {"temp": 400, "minutes": 12}, blocking=True
    )
    payload = last_payload(manager)
    assert payload["method"] == "startStepCook"
    assert payload["data"]["preheatTemp"] == 400
    assert payload["data"]["stepArray"][0]["cookSetTime"] == 720


async def test_basket_out_is_refused(hass: HomeAssistant):
    _, manager = await setup_fryer(hass, "pullOut", allow=True)
    with pytest.raises(ServiceValidationError, match="basket is out"):
        await hass.services.async_call(DOMAIN, "cook", {"temp": 350, "minutes": 5}, blocking=True)
    assert "startStepCook" not in sent_methods(manager)


async def test_cook_time_over_60_is_rejected(hass: HomeAssistant):
    _, manager = await setup_fryer(hass, "standby", allow=True)
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(DOMAIN, "cook", {"temp": 350, "minutes": 61}, blocking=True)
    assert "startStepCook" not in sent_methods(manager)


async def test_temperature_out_of_range_is_rejected(hass: HomeAssistant):
    _, manager = await setup_fryer(hass, "standby", allow=True)
    with pytest.raises(ServiceValidationError, match="outside the fryer's range"):
        await hass.services.async_call(DOMAIN, "cook", {"temp": 450, "minutes": 5}, blocking=True)
    assert "startStepCook" not in sent_methods(manager)


async def test_start_needs_a_finished_preheat(hass: HomeAssistant):
    await setup_fryer(hass, "standby", allow=True)
    with pytest.raises(ServiceValidationError, match="finished preheat"):
        await hass.services.async_call(DOMAIN, "start", {}, blocking=True)


async def test_preheat_button_uses_the_number_entities(hass: HomeAssistant):
    _, manager = await setup_fryer(hass, "standby", allow=True)
    await hass.services.async_call(
        "number", "set_value",
        {"entity_id": "number.air_fryer_target_temperature", "value": 375}, blocking=True,
    )
    await hass.services.async_call(
        "number", "set_value",
        {"entity_id": "number.air_fryer_cook_minutes", "value": 8}, blocking=True,
    )
    assert sent_methods(manager).count("startStepCook") == 0  # numbers send nothing
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.air_fryer_preheat"}, blocking=True
    )
    data = last_payload(manager)["data"]
    assert data["preheatTemp"] == 375
    assert data["stepArray"][0]["cookSetTime"] == 480


# --- polling and notifications --------------------------------------------


async def test_polling_is_slow_when_idle_and_fast_when_busy(hass: HomeAssistant):
    entry, _ = await setup_fryer(hass, "standby")
    assert entry.runtime_data.update_interval == POLL_IDLE
    busy_entry, _ = await setup_fryer_second(hass, "cooking")
    assert busy_entry.runtime_data.update_interval == POLL_ACTIVE


async def setup_fryer_second(hass: HomeAssistant, cook_status: str):
    manager = make_manager(cook_status)
    entry = MockConfigEntry(domain=DOMAIN, data=CREDS, unique_id="second@example.com")
    entry.add_to_hass(hass)
    with patch("custom_components.cosori_fryer.VeSync", return_value=manager):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry, manager


async def test_preheat_end_fires_an_event_and_a_notification(hass: HomeAssistant):
    _, manager = await setup_fryer(hass, "heating")
    events = async_capture_events(hass, EVENT_PREHEAT_DONE)
    manager.cook_status = "preheatEnd"
    async_fire_time_changed(hass, dt_util.utcnow() + POLL_ACTIVE + timedelta(seconds=1))
    await hass.async_block_till_done()
    assert len(events) == 1
    assert hass.states.get("sensor.air_fryer_status").state == "preheatEnd"


async def test_cook_done_fires_on_finish_but_not_on_basket_pull(hass: HomeAssistant):
    _, manager = await setup_fryer(hass, "cooking")
    events = async_capture_events(hass, EVENT_COOK_DONE)
    manager.cook_status = "pullOut"
    async_fire_time_changed(hass, dt_util.utcnow() + POLL_ACTIVE + timedelta(seconds=1))
    await hass.async_block_till_done()
    assert events == []
    manager.cook_status = "cooking"
    async_fire_time_changed(hass, dt_util.utcnow() + 2 * POLL_ACTIVE + timedelta(seconds=2))
    await hass.async_block_till_done()
    manager.cook_status = "standby"
    async_fire_time_changed(hass, dt_util.utcnow() + 3 * POLL_ACTIVE + timedelta(seconds=3))
    await hass.async_block_till_done()
    assert len(events) == 1
