"""The thermostat-style entity, the cook settings and the Kitchen suggestion."""

import asyncio

import pytest
from fakes import ENTRY_DATA
from homeassistant.core import State
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.util.unit_system import METRIC_SYSTEM
from pytest_homeassistant_custom_component.common import MockConfigEntry, mock_restore_cache_with_extra_data
from test_integration import refresh, setup

from custom_components.cosori_fryer import climate
from custom_components.cosori_fryer.const import DOMAIN, PRESETS

FRYER = "climate.kitchen_air_fryer"


@pytest.fixture(autouse=True)
def no_start_delay(monkeypatch):
    monkeypatch.setattr(climate, "START_DELAY", 0)


async def call(hass, domain, service, **data):
    await hass.services.async_call(domain, service, data, blocking=True)


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


async def test_turn_on_cooks_with_settings(hass, manager, device):
    await setup(hass, manager)
    await call(hass, "climate", "set_temperature", entity_id=FRYER, temperature=380)
    await call(hass, "number", "set_value", entity_id="number.kitchen_air_fryer_cook_time", value=7)
    await call(hass, "climate", "turn_on", entity_id=FRYER)
    sent = device.commands[0]
    assert sent["method"] == "startCook"
    assert sent["data"]["hasPreheat"] == 0
    assert sent["data"]["startAct"]["cookTemp"] == 380
    assert sent["data"]["startAct"]["cookSetTime"] == 420


async def test_set_temperature_with_mode_cooks_at_new_temperature(hass, manager, device):
    await setup(hass, manager)
    await call(hass, "climate", "set_temperature", entity_id=FRYER, temperature=390, hvac_mode="heat")
    assert device.commands[0]["data"]["startAct"]["cookTemp"] == 390


async def test_homekit_mode_then_temperature_cooks_at_new_temperature(hass, manager, device, monkeypatch):
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
    assert len(device.commands) == 1
    assert device.commands[0]["data"]["startAct"]["cookTemp"] == 360


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


async def test_turn_on_after_preheat_continues(hass, manager, device):
    device.status = "preheatEnd"
    await setup(hass, manager)
    await call(hass, "climate", "set_hvac_mode", entity_id=FRYER, hvac_mode="heat")
    assert [c["method"] for c in device.commands] == ["preheatCook"]


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
    await call(hass, "button", "press", entity_id="button.kitchen_air_fryer_preheat")
    sent = device.commands[0]["data"]
    assert sent["hasPreheat"] == 1
    assert (sent["mode"], sent["recipeId"]) == ("Chicken", 2)
    assert sent["startAct"]["cookTemp"] == 380
    assert sent["startAct"]["cookSetTime"] == 1380


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
    original = coordinator.start

    async def slow_start(*args, **kwargs):
        await asyncio.sleep(0.05)
        await original(*args, **kwargs)
        # The fryer is cooking, but no poll has seen it yet.
        device.status = "cooking"

    monkeypatch.setattr(coordinator, "start", slow_start)
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
