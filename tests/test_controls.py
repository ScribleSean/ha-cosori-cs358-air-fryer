"""The thermostat-style entity, the cook settings and the Kitchen suggestion."""

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest
from fakes import ENTRY_DATA
from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_system import METRIC_SYSTEM
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache_with_extra_data,
)
from test_integration import refresh, setup

from custom_components.cosori_fryer import climate, coordinator
from custom_components.cosori_fryer.const import DOMAIN, PRESETS
from custom_components.cosori_fryer.planner import HOLD_LIMIT_MIN, HOLD_STEP_SECONDS

FRYER = "climate.kitchen_air_fryer"


@pytest.fixture(autouse=True)
def no_start_delay(monkeypatch):
    monkeypatch.setattr(climate, "START_DELAY", 0)
    monkeypatch.setattr(coordinator, "CLEAR_DELAY", 0)


async def call(hass, domain, service, **data):
    await hass.services.async_call(domain, service, data, blocking=True)


async def later(hass, minutes):
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=minutes))
    await hass.async_block_till_done()


async def test_device_suggests_kitchen(hass, manager):
    entry = await setup(hass, manager)
    (device,) = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert ar.async_get(hass).async_get_area(device.area_id).name == "Kitchen"
    assert hass.states.get(FRYER).name == device.name
    assert entry.runtime_data.settings.temperature == 350


async def test_off_state_and_limits(hass, manager):
    await setup(hass, manager)
    state = hass.states.get(FRYER)
    assert state.state == "off"
    assert state.attributes["hvac_modes"] == ["off", "heat"]
    assert state.attributes["hvac_action"] == "off"
    assert (state.attributes["min_temp"], state.attributes["max_temp"]) == (170, 400)
    assert state.attributes["target_temp_step"] == 10
    assert state.attributes["temperature"] == 350
    assert state.attributes["current_temperature"] is None
    assert state.attributes["cook_status"] == "standby"
    assert "time_left" not in state.attributes


async def test_set_temperature_while_off_sends_nothing(hass, manager, device):
    entry = await setup(hass, manager)
    await call(hass, "climate", "set_temperature", entity_id=FRYER, temperature=362)
    assert hass.states.get(FRYER).attributes["temperature"] == 360
    assert entry.runtime_data.settings.temperature == 360
    assert not device.commands


def assert_hold(sent, temperature):
    """A hold is a preheat with the short placeholder cook step behind it."""
    assert sent["method"] == "startCook"
    assert sent["data"]["hasPreheat"] == 1
    assert sent["data"]["startAct"]["cookTemp"] == temperature
    assert sent["data"]["startAct"]["preheatTemp"] == temperature
    assert sent["data"]["startAct"]["cookSetTime"] == HOLD_STEP_SECONDS


def assert_plain_cook(sent, temperature, seconds):
    assert sent["method"] == "startCook"
    assert not sent["data"].get("hasPreheat")
    assert sent["data"]["startAct"]["preheatTemp"] == 0
    assert (sent["data"]["startAct"]["cookTemp"], sent["data"]["startAct"]["cookSetTime"]) == (
        temperature,
        seconds,
    )


async def test_turn_on_preheats_and_holds(hass, manager, device):
    entry = await setup(hass, manager)
    await call(hass, "climate", "set_temperature", entity_id=FRYER, temperature=380)
    await call(hass, "number", "set_value", entity_id="number.kitchen_air_fryer_cook_time", value=7)
    await call(hass, "climate", "turn_on", entity_id=FRYER)
    (sent,) = device.commands
    # The cook time is for a cook, not for a hold.
    assert_hold(sent, 380)
    assert entry.runtime_data.data.held
    state = hass.states.get(FRYER)
    assert state.state == "heat"
    assert state.attributes["hvac_action"] == "preheating"
    assert state.attributes["temperature"] == 380


async def test_set_temperature_with_mode_holds_at_new_temperature(hass, manager, device):
    await setup(hass, manager)
    await call(hass, "climate", "set_temperature", entity_id=FRYER, temperature=390, hvac_mode="heat")
    (sent,) = device.commands
    assert_hold(sent, 390)


async def test_homekit_mode_then_temperature_holds_at_new_temperature(hass, manager, device, monkeypatch):
    """HomeKit sends the mode first, then the temperature, as separate calls."""
    monkeypatch.setattr(climate, "START_DELAY", 0.05)
    await setup(hass, manager)
    mode = hass.async_create_task(
        hass.services.async_call("climate", "set_hvac_mode", {"entity_id": FRYER, "hvac_mode": "heat"}, True)
    )
    await asyncio.sleep(0)
    assert hass.states.get(FRYER).state == "heat"
    assert hass.states.get(FRYER).attributes["cook_status"] == "starting"
    await call(hass, "climate", "set_temperature", entity_id=FRYER, temperature=360)
    await mode
    (sent,) = device.commands
    assert_hold(sent, 360)


@pytest.mark.parametrize("status", ["heating", "preheatEnd"])
async def test_turn_on_during_hold_sends_nothing(hass, manager, device, status):
    entry = await setup(hass, manager)
    await call(hass, "climate", "turn_on", entity_id=FRYER)
    device.status = status
    await refresh(hass, entry)
    assert entry.runtime_data.data.held
    await call(hass, "climate", "turn_on", entity_id=FRYER)
    await call(hass, "climate", "set_temperature", entity_id=FRYER, temperature=350, hvac_mode="heat")
    assert len(device.commands) == 1


@pytest.mark.parametrize("hvac_mode", [None, "heat"])
async def test_set_temperature_during_hold_moves_it(hass, manager, device, hvac_mode):
    entry = await setup(hass, manager)
    await call(hass, "climate", "turn_on", entity_id=FRYER)
    extra = {"hvac_mode": hvac_mode} if hvac_mode else {}
    await call(hass, "climate", "set_temperature", entity_id=FRYER, temperature=400, **extra)
    assert [c["method"] for c in device.commands] == ["startCook", "endCook", "startCook"]
    assert_hold(device.commands[2], 400)
    assert entry.runtime_data.settings.temperature == 400
    assert hass.states.get(FRYER).attributes["temperature"] == 400


async def test_hold_found_after_last_poll_is_kept(hass, manager, device):
    """The last poll says off, but a fresh read finds a hold at the same temperature."""
    entry = await setup(hass, manager)
    await entry.runtime_data.hold()
    device.commands.clear()
    entry.runtime_data.async_set_updated_data(replace(entry.runtime_data.data, status="standby", held=False))
    await call(hass, "climate", "turn_on", entity_id=FRYER)
    assert not device.commands


async def test_start_during_hold_cooks_without_preheat(hass, manager, device):
    entry = await setup(hass, manager)
    await call(hass, "climate", "turn_on", entity_id=FRYER)
    await call(hass, "number", "set_value", entity_id="number.kitchen_air_fryer_cook_time", value=12)
    device.status = "preheatEnd"
    await refresh(hass, entry)
    await call(hass, "button", "press", entity_id="button.kitchen_air_fryer_start")
    assert [c["method"] for c in device.commands] == ["startCook", "endCook", "startCook"]
    assert_plain_cook(device.commands[2], 350, 720)
    assert not entry.runtime_data.data.held
    assert hass.states.get(FRYER).attributes["cook_status"] == "cooking"


async def test_hold_stops_itself(hass, manager, device):
    entry = await setup(hass, manager)
    await call(hass, "climate", "turn_on", entity_id=FRYER)
    device.status = "preheatEnd"
    await refresh(hass, entry)
    await later(hass, HOLD_LIMIT_MIN - 1)
    assert [c["method"] for c in device.commands] == ["startCook"]
    await later(hass, HOLD_LIMIT_MIN + 1)
    assert [c["method"] for c in device.commands] == ["startCook", "endCook"]
    assert hass.states.get(FRYER).state == "off"


async def test_moved_hold_gets_a_new_limit(hass, manager, device):
    entry = await setup(hass, manager)
    coordinator = entry.runtime_data
    await call(hass, "climate", "turn_on", entity_id=FRYER)
    first = coordinator._hold_limit
    await call(hass, "climate", "set_temperature", entity_id=FRYER, temperature=400)
    assert coordinator._hold_limit not in (None, first)
    await later(hass, HOLD_LIMIT_MIN + 1)
    assert [c["method"] for c in device.commands] == ["startCook", "endCook", "startCook", "endCook"]


@pytest.mark.parametrize("how", ["start", "cook", "keep_warm", "fryer"])
async def test_hold_limit_leaves_food_alone(hass, manager, device, how):
    entry = await setup(hass, manager)
    await call(hass, "button", "press", entity_id="button.kitchen_air_fryer_preheat")
    assert_hold(device.commands[0], 350)
    if how == "start":
        await call(hass, "button", "press", entity_id="button.kitchen_air_fryer_start")
    elif how == "cook":
        await call(hass, DOMAIN, "cook", temperature=380, minutes=20)
    elif how == "keep_warm":
        await call(hass, "button", "press", entity_id="button.kitchen_air_fryer_keep_warm")
    else:
        # Start pressed on the fryer itself runs the placeholder step.
        device.status = "cooking"
        await refresh(hass, entry)
    sent = len(device.commands)
    await later(hass, HOLD_LIMIT_MIN + 1)
    assert len(device.commands) == sent
    assert device.commands[-1]["method"] == "startCook"


async def test_unload_cancels_hold_limit(hass, manager, device):
    entry = await setup(hass, manager)
    await call(hass, "climate", "turn_on", entity_id=FRYER)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await later(hass, HOLD_LIMIT_MIN + 1)
    assert [c["method"] for c in device.commands] == ["startCook"]


async def test_turn_off_stops_hold(hass, manager, device):
    await setup(hass, manager)
    await call(hass, "climate", "turn_on", entity_id=FRYER)
    await call(hass, "climate", "turn_off", entity_id=FRYER)
    assert [c["method"] for c in device.commands] == ["startCook", "endCook"]
    await later(hass, HOLD_LIMIT_MIN + 1)
    assert len(device.commands) == 2


async def test_keep_warm_button_and_turn_off(hass, manager, device):
    await setup(hass, manager)
    await call(hass, "button", "press", entity_id="button.kitchen_air_fryer_keep_warm")
    (sent,) = device.commands
    assert_plain_cook(sent, 170, 900)
    assert hass.states.get(FRYER).state == "heat"
    await call(hass, "climate", "turn_off", entity_id=FRYER)
    assert device.commands[-1]["method"] == "endCook"
    assert hass.states.get(FRYER).state == "off"


async def test_keep_warm_replaces_hold(hass, manager, device):
    await setup(hass, manager)
    await call(hass, "climate", "turn_on", entity_id=FRYER)
    await call(hass, "button", "press", entity_id="button.kitchen_air_fryer_keep_warm")
    assert [c["method"] for c in device.commands] == ["startCook", "endCook", "startCook"]
    assert_plain_cook(device.commands[2], 170, 900)


@pytest.mark.parametrize("status", ["cooking", "cookStop", "pullOut", "preheatEnd", "heating"])
async def test_keep_warm_refused_during_cook(hass, manager, device, status):
    device.status = status
    await setup(hass, manager)
    with pytest.raises(ServiceValidationError) as exc:
        await call(hass, "button", "press", entity_id="button.kitchen_air_fryer_keep_warm")
    assert exc.value.translation_key == "busy"
    assert not device.commands


async def test_off_during_start_delay_sends_nothing(hass, manager, device, monkeypatch):
    monkeypatch.setattr(climate, "START_DELAY", 0.05)
    await setup(hass, manager)
    mode = hass.async_create_task(
        hass.services.async_call("climate", "set_hvac_mode", {"entity_id": FRYER, "hvac_mode": "heat"}, True)
    )
    await asyncio.sleep(0)
    await call(hass, "climate", "turn_off", entity_id=FRYER)
    await mode
    assert not device.commands
    assert hass.states.get(FRYER).state == "off"


@pytest.mark.parametrize(
    "status,action,cook_status,time_left",
    [
        ("heating", "preheating", "heating", "4 min"),
        ("preheatEnd", "idle", "preheat_end", None),
        ("cooking", "heating", "cooking", "10 min"),
        ("cookStop", "idle", "cooking_stop", "10 min"),
        ("keeping", "heating", "keeping_warm", None),
    ],
)
async def test_running_state(hass, manager, device, status, action, cook_status, time_left):
    device.status = status
    device.cook_temp = 400
    await setup(hass, manager)
    state = hass.states.get(FRYER)
    assert state.state == "heat"
    assert state.attributes["hvac_action"] == action
    assert state.attributes["cook_status"] == cook_status
    assert state.attributes["temperature"] == 400
    assert state.attributes.get("time_left") == time_left
    assert hass.states.get("binary_sensor.kitchen_air_fryer_preheated").state == (
        "on" if status == "preheatEnd" else "off"
    )


@pytest.mark.parametrize("status", ["standby", "cookEnd"])
async def test_idle_is_off(hass, manager, device, status):
    device.status = status
    await setup(hass, manager)
    assert hass.states.get(FRYER).state == "off"
    assert hass.states.get("sensor.kitchen_air_fryer_time_left").state == "0"
    await call(hass, "climate", "turn_off", entity_id=FRYER)
    assert not device.commands


async def test_time_left_sensor(hass, manager, device):
    device.status = "cooking"
    device.cook_remaining = 541
    entry = await setup(hass, manager)
    sensor = hass.states.get("sensor.kitchen_air_fryer_time_left")
    assert sensor.state == "10"
    assert sensor.attributes["unit_of_measurement"] == "min"
    device.status = "heating"
    device.preheat_remaining = 59
    await refresh(hass, entry)
    assert hass.states.get("sensor.kitchen_air_fryer_time_left").state == "1"


async def test_running_temperature_change_refused(hass, manager, device):
    device.status = "cooking"
    await setup(hass, manager)
    with pytest.raises(ServiceValidationError) as exc:
        await call(hass, "climate", "set_temperature", entity_id=FRYER, temperature=400)
    assert exc.value.translation_key == "running"
    await call(hass, "climate", "set_temperature", entity_id=FRYER, temperature=350)
    assert not device.commands


async def test_turn_off_stops(hass, manager, device):
    device.status = "cooking"
    await setup(hass, manager)
    await call(hass, "climate", "turn_off", entity_id=FRYER)
    assert [c["method"] for c in device.commands] == ["endCook"]


async def test_turn_off_stops_cook_started_since_last_poll(hass, manager, device):
    await setup(hass, manager)
    assert hass.states.get(FRYER).state == "off"
    device.status = "cooking"
    await call(hass, "climate", "turn_off", entity_id=FRYER)
    assert [c["method"] for c in device.commands] == ["endCook"]


async def test_turn_off_after_cook_ended_since_last_poll(hass, manager, device):
    device.status = "cooking"
    await setup(hass, manager)
    assert hass.states.get(FRYER).state == "heat"
    device.status = "standby"
    await call(hass, "climate", "turn_off", entity_id=FRYER)
    assert not device.commands
    assert hass.states.get(FRYER).state == "off"


async def test_turn_off_refused_by_cloud_raises(hass, manager, device):
    device.status = "cooking"
    await setup(hass, manager)
    device.command_code = 1
    with pytest.raises(HomeAssistantError) as exc:
        await call(hass, "climate", "turn_off", entity_id=FRYER)
    assert exc.value.translation_key == "command_refused"


async def test_turn_on_after_preheat_waits_for_start(hass, manager, device):
    """A cook queued behind a preheat starts from the Start button, not from turn on."""
    device.status = "preheatEnd"
    entry = await setup(hass, manager)
    assert not entry.runtime_data.data.held
    await call(hass, "climate", "set_hvac_mode", entity_id=FRYER, hvac_mode="heat")
    assert not device.commands


async def test_turn_on_while_running_sends_nothing(hass, manager, device):
    device.status = "cooking"
    await setup(hass, manager)
    await call(hass, "climate", "turn_on", entity_id=FRYER)
    assert not device.commands


async def test_preset_fills_settings_and_sends_program(hass, manager, device):
    await setup(hass, manager)
    await call(hass, "select", "select_option", entity_id="select.kitchen_air_fryer_preset", option="chicken")
    assert hass.states.get(FRYER).attributes["temperature"] == 380
    assert hass.states.get("number.kitchen_air_fryer_cook_time").state == "23"
    await call(hass, "button", "press", entity_id="button.kitchen_air_fryer_start")
    sent = device.commands[0]["data"]
    assert not sent.get("hasPreheat")
    assert (sent["mode"], sent["recipeId"]) == ("Chicken", 2)
    assert sent["startAct"]["cookTemp"] == 380
    assert sent["startAct"]["cookSetTime"] == 1380


async def test_preheat_button_holds_at_setting(hass, manager, device):
    await setup(hass, manager)
    await call(hass, "select", "select_option", entity_id="select.kitchen_air_fryer_preset", option="chicken")
    await call(hass, "button", "press", entity_id="button.kitchen_air_fryer_preheat")
    (sent,) = device.commands
    assert_hold(sent, 380)


async def test_manual_keeps_chosen_settings(hass, manager, device):
    await setup(hass, manager)
    await call(hass, "select", "select_option", entity_id="select.kitchen_air_fryer_preset", option="steak")
    await call(hass, "select", "select_option", entity_id="select.kitchen_air_fryer_preset", option="custom")
    assert hass.states.get(FRYER).attributes["temperature"] == 400
    await call(hass, "button", "press", entity_id="button.kitchen_air_fryer_start")
    sent = device.commands[0]["data"]
    assert sent["hasPreheat"] == 0
    assert sent["mode"] != "Steak"
    assert sent["startAct"]["cookTemp"] == 400


async def test_presets_match_device(hass, manager, device):
    await setup(hass, manager)
    assert set(PRESETS) == {str(mode) for mode in device.real.cook_modes}
    assert hass.states.get("select.kitchen_air_fryer_preset").attributes["options"] == PRESETS


async def test_action_updates_settings(hass, manager, device):
    await setup(hass, manager)
    await hass.services.async_call(
        "cosori_fryer", "start_cook", {"temperature": 330, "minutes": 9, "preheat": False}, blocking=True
    )
    assert hass.states.get(FRYER).attributes["temperature"] == 330
    assert hass.states.get("number.kitchen_air_fryer_cook_time").state == "9"


async def test_action_does_not_continue_preheat(hass, manager, device):
    device.status = "preheatEnd"
    await setup(hass, manager)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "cosori_fryer", "start_cook", {"temperature": 330, "minutes": 9, "preheat": False}, blocking=True
        )
    assert not device.commands


async def test_celsius_step(hass, manager):
    await setup(hass, manager)
    hass.config.units = METRIC_SYSTEM
    await call(hass, "climate", "set_temperature", entity_id=FRYER, temperature=180)
    state = hass.states.get(FRYER)
    assert state.attributes["target_temp_step"] == 5
    assert state.attributes["temperature"] == 182


async def test_settings_restored(hass, manager):
    mock_restore_cache_with_extra_data(
        hass,
        [
            (State(FRYER, "off", {"temperature": 390}), {}),
            (State("select.kitchen_air_fryer_preset", "bake"), {}),
            (
                State("number.kitchen_air_fryer_cook_time", "12"),
                {
                    "native_value": 12,
                    "native_min_value": 1,
                    "native_max_value": 60,
                    "native_step": 1,
                    "native_unit_of_measurement": "min",
                },
            ),
        ],
    )
    entry = await setup(hass, manager)
    settings = entry.runtime_data.settings
    assert (settings.temperature, settings.minutes, settings.preset) == (390, 12, "bake")


async def test_celsius_restore(hass, manager):
    """Saved states hold °C on a metric install; 182 °C is the 360 °F step."""
    hass.config.units = METRIC_SYSTEM
    mock_restore_cache_with_extra_data(hass, [(State(FRYER, "off", {"temperature": 182}), {})])
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id="test-account", version=3)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.settings.temperature == 360


async def test_off_while_start_is_sending_stops(hass, manager, device, monkeypatch):
    entry = await setup(hass, manager)
    coordinator = entry.runtime_data
    original = coordinator.hold

    async def slow_start(*args, **kwargs):
        await asyncio.sleep(0.05)
        await original(*args, **kwargs)
        # The fryer is heating, but no poll has seen it yet.
        device.status = "heating"

    monkeypatch.setattr(coordinator, "hold", slow_start)
    mode = hass.async_create_task(
        hass.services.async_call("climate", "set_hvac_mode", {"entity_id": FRYER, "hvac_mode": "heat"}, True)
    )
    await asyncio.sleep(0.01)
    assert hass.states.get(FRYER).state == "heat"
    await call(hass, "climate", "turn_off", entity_id=FRYER)
    await mode
    assert [c["method"] for c in device.commands] == ["startCook", "endCook"]


async def test_refused_action_keeps_settings(hass, manager, device):
    device.status = "cooking"
    entry = await setup(hass, manager)
    await call(hass, "select", "select_option", entity_id="select.kitchen_air_fryer_preset", option="bacon")
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "cosori_fryer", "start_cook", {"temperature": 330, "minutes": 9, "preheat": False}, blocking=True
        )
    settings = entry.runtime_data.settings
    assert (settings.temperature, settings.minutes, settings.preset) == (320, 8, "bacon")


async def test_unload_during_start_delay_sends_nothing(hass, manager, device, monkeypatch):
    monkeypatch.setattr(climate, "START_DELAY", 0.05)
    entry = await setup(hass, manager)
    mode = hass.async_create_task(
        hass.services.async_call("climate", "set_hvac_mode", {"entity_id": FRYER, "hvac_mode": "heat"}, True)
    )
    await asyncio.sleep(0)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await mode
    assert not device.commands


async def test_hold_limit_retries_after_a_failed_read(hass, manager, device, caplog):
    entry = await setup(hass, manager)
    await call(hass, "climate", "turn_on", entity_id=FRYER)
    device.error = TimeoutError()
    await later(hass, HOLD_LIMIT_MIN + 1)
    assert [c["method"] for c in device.commands] == ["startCook"]
    assert "Could not stop a hold" in caplog.text
    device.error = None
    # A good poll must not grant the hold a fresh 30 minutes.
    await refresh(hass, entry)
    assert entry.runtime_data.data.held
    await later(hass, 0.5)
    assert [c["method"] for c in device.commands] == ["startCook"]
    await later(hass, 1.5)
    assert [c["method"] for c in device.commands] == ["startCook", "endCook"]
    assert hass.states.get(FRYER).state == "off"


async def test_hold_limit_gives_up_after_bounded_tries(hass, manager, device):
    await setup(hass, manager)
    await call(hass, "climate", "turn_on", entity_id=FRYER)
    device.error = TimeoutError()
    await later(hass, HOLD_LIMIT_MIN + 1)
    for _ in range(coordinator.LIMIT_RETRIES + 2):
        await later(hass, 1.5)
    assert [c["method"] for c in device.commands] == ["startCook"]


async def test_temperature_alone_never_starts_a_stopped_fryer(hass, manager, device):
    """The last poll showed a hold, but the fryer was stopped at its panel since."""
    entry = await setup(hass, manager)
    await call(hass, "climate", "turn_on", entity_id=FRYER)
    assert entry.runtime_data.data.held
    device.status = "standby"
    await call(hass, "climate", "set_temperature", entity_id=FRYER, temperature=400)
    assert [c["method"] for c in device.commands] == ["startCook"]
    assert entry.runtime_data.settings.temperature == 400
    assert hass.states.get(FRYER).state == "off"
    await later(hass, HOLD_LIMIT_MIN + 1)
    assert [c["method"] for c in device.commands] == ["startCook"]


@pytest.mark.parametrize("status", ["cooking", "hold"])
async def test_set_temperature_with_off_stops_first(hass, manager, device, status):
    entry = await setup(hass, manager)
    if status == "hold":
        await call(hass, "climate", "turn_on", entity_id=FRYER)
    else:
        device.status = status
        await refresh(hass, entry)
    await call(hass, "climate", "set_temperature", entity_id=FRYER, temperature=400, hvac_mode="off")
    assert device.commands[-1]["method"] == "endCook"
    assert entry.runtime_data.settings.temperature == 400
    assert hass.states.get(FRYER).state == "off"


async def test_hold_move_waits_for_cloud_to_report_idle(hass, manager, device):
    await setup(hass, manager)
    await call(hass, "climate", "turn_on", entity_id=FRYER)
    device.end_lag = 2
    await call(hass, "climate", "set_temperature", entity_id=FRYER, temperature=400)
    assert [c["method"] for c in device.commands] == ["startCook", "endCook", "startCook"]
    assert_hold(device.commands[2], 400)


async def test_hold_move_gives_up_if_never_idle(hass, manager, device):
    await setup(hass, manager)
    await call(hass, "climate", "turn_on", entity_id=FRYER)
    device.end_lag = coordinator.CLEAR_POLLS + 1
    with pytest.raises(HomeAssistantError) as exc:
        await call(hass, "climate", "set_temperature", entity_id=FRYER, temperature=400)
    assert exc.value.translation_key == "command_refused"
    assert [c["method"] for c in device.commands] == ["startCook", "endCook"]
