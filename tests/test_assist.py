"""Full sentences through Home Assistant Assist."""

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from fakes import ENTRY_DATA
from homeassistant.components import conversation
from homeassistant.components.automation.config import AUTOMATION_BLUEPRINT_SCHEMA, PLATFORM_SCHEMA
from homeassistant.components.blueprint.models import Blueprint, BlueprintInputs
from homeassistant.components.homeassistant.exposed_entities import (
    async_expose_entity,
    async_should_expose,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Context
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from homeassistant.util.yaml import load_yaml_dict
from pytest_homeassistant_custom_component.common import MockConfigEntry
from test_controls import assert_hold, no_start_delay  # noqa: F401
from test_integration import setup

from custom_components.cosori_fryer import assist
from custom_components.cosori_fryer.const import CONF_CONFIRM_ASSIST, DOMAIN
from custom_components.cosori_fryer.planner import HOLD_STEP_SECONDS

ROOT = Path(__file__).resolve().parents[1]


# A fixed conversation, like one Assist dialog or one voice satellite session.
KITCHEN = "kitchen"


async def say(hass, text, conversation_id=KITCHEN, context=None, device_id=None, satellite_id=None):
    result = await conversation.async_converse(
        hass,
        text,
        conversation_id,
        context or Context(),
        language="en",
        device_id=device_id,
        satellite_id=satellite_id,
    )
    return result.response.speech["plain"]["speech"]


@pytest.fixture
async def assist_on(hass):
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "conversation", {})


def sent_cook(device):
    (sent,) = device.commands
    assert sent["method"] == "startCook"
    act = sent["data"]["startAct"]
    return act["cookTemp"], act["cookSetTime"] // 60, bool(sent["data"].get("hasPreheat"))


@pytest.mark.parametrize(
    "text",
    [
        "Air fry for 12 minutes at 380",
        "air fry at 380 degrees for twelve minutes",
        "cook for 12 minutes at 380 in the air fryer",
    ],
)
async def test_cook_sentence_starts_and_says_so(hass, assist_on, manager, device, text):
    await setup(hass, manager)
    reply = await say(hass, text)
    assert sent_cook(device) == (380, 12, False)
    assert "380°F for 12 min" in reply


async def test_food_states_its_check(hass, assist_on, manager, device):
    await setup(hass, manager)
    reply = await say(hass, "air fry chicken wings")
    assert device.commands
    assert "165°F inside" in reply


async def test_rounding_is_stated(hass, assist_on, manager, device):
    await setup(hass, manager)
    reply = await say(hass, "air fry for 12 minutes at 375")
    assert sent_cook(device) == (370, 12, False)
    assert "375°F isn't a setting; using 370°F" in reply


@pytest.mark.parametrize("text", ["air fry for 12 minutes", "air fry something odd"])
async def test_missing_values_start_nothing(hass, assist_on, manager, device, text):
    await setup(hass, manager)
    reply = await say(hass, text)
    assert not device.commands
    assert reply.endswith("Nothing started.")


async def test_out_of_range_starts_nothing(hass, assist_on, manager, device):
    await setup(hass, manager)
    reply = await say(hass, "air fry for 90 minutes at 380")
    assert not device.commands
    assert "60-minute limit" in reply


async def test_times_add_up_before_the_limit(hass, assist_on, manager, device):
    await setup(hass, manager)
    reply = await say(hass, "air fry for 1 hour 30 minutes at 380")
    assert not device.commands
    assert "60-minute limit" in reply


@pytest.mark.parametrize(
    ("text", "minutes", "said"),
    [
        ("air fry for 12 and a half minutes at 380", 13, "12.5 min rounds up to 13 min"),
        ("air fry for one and a half minutes at 380", 2, "1.5 min rounds up to 2 min"),
        ("air fry for 12 minutes at three eighty", 12, "380°F for 12 min"),
    ],
)
async def test_spoken_times(hass, assist_on, manager, device, text, minutes, said):
    await setup(hass, manager)
    reply = await say(hass, text)
    assert sent_cook(device) == (380, minutes, False)
    assert said in reply
    assert "half" not in reply.split(":")[0]


async def test_cook_and_keep_warm_together_starts_nothing(hass, assist_on, manager, device):
    await setup(hass, manager)
    reply = await say(hass, "air fry for 12 minutes at 380 then keep it warm")
    assert not device.commands
    assert "one at a time" in reply
    assert reply.endswith("Nothing started.")


async def test_an_hour_out_is_capped_and_said(hass, assist_on, manager, device):
    await setup(hass, manager)
    reply = await say(hass, "keep it warm, I'm an hour out")
    assert sent_cook(device) == (170, 60, False)
    assert "65 min" in reply
    assert "Capped at 60" in reply


@pytest.mark.parametrize(
    ("text", "minutes", "said"),
    [
        ("Keep it warm, I'm 10 minutes out", 15, "10 min out + 5 min spare = 15 min"),
        ("keep it warm", 15, "No time given; using 15 min"),
        ("keep the food warm for 20 minutes", 20, "for 20 min"),
        ("keep it warm for 2 hours", 60, "capped at 60"),
    ],
)
async def test_keep_warm(hass, assist_on, manager, device, text, minutes, said):
    await setup(hass, manager)
    reply = await say(hass, text)
    assert sent_cook(device) == (170, minutes, False)
    assert said in reply


@pytest.mark.parametrize("text", ["stop the air fryer", "turn off the air fryer", "cancel the air fryer"])
async def test_stop(hass, assist_on, manager, device, text):
    device.status = "cooking"
    await setup(hass, manager)
    assert await say(hass, text) == "Stopped the air fryer."
    assert [command["method"] for command in device.commands] == ["endCook"]


async def test_stop_when_off(hass, assist_on, manager, device):
    await setup(hass, manager)
    assert await say(hass, "stop the air fryer") == "The air fryer is already off."


async def test_turn_on_holds_at_last_temperature(hass, assist_on, manager, device):
    entry = await setup(hass, manager)
    entry.runtime_data.update_settings(temperature=380)
    reply = await say(hass, "turn on the air fryer")
    assert_hold(device.commands[0], 380)
    assert "Preheat to 380°F and hold" in reply


async def test_set_to_holds_and_states_rounding(hass, assist_on, manager, device):
    entry = await setup(hass, manager)
    reply = await say(hass, "set the air fryer to 405")
    assert_hold(device.commands[0], 400)
    assert "using 400°F" in reply
    assert entry.runtime_data.settings.temperature == 400


async def test_time_into_a_hold_cooks_at_its_temperature(hass, assist_on, manager, device):
    device.status = "preheatEnd"
    device.cook_temp = 390
    device.cook_set_time = HOLD_STEP_SECONDS
    await setup(hass, manager)
    reply = await say(hass, "air fry for 12 minutes")
    methods = [command["method"] for command in device.commands]
    assert methods == ["endCook", "startCook"]
    act = device.commands[1]["data"]["startAct"]
    assert (act["cookTemp"], act["cookSetTime"]) == (390, 720)
    assert "holding at" in reply


async def test_start_continues_only_a_queued_cook(hass, assist_on, manager, device):
    device.status = "preheatEnd"
    await setup(hass, manager)
    assert await say(hass, "start the air fryer") == "Cooking now."
    assert [command["method"] for command in device.commands] == ["preheatCook"]


async def test_start_never_runs_a_stale_timer(hass, assist_on, manager, device):
    await setup(hass, manager)
    reply = await say(hass, "start the air fryer")
    assert not device.commands
    assert reply.endswith("Nothing started.")


async def test_busy_cook_is_refused_plainly(hass, assist_on, manager, device):
    device.status = "cooking"
    await setup(hass, manager)
    reply = await say(hass, "air fry for 12 minutes at 380")
    assert reply == assist.REFUSALS["busy"]
    assert not device.commands


@pytest.mark.parametrize(
    ("status", "text", "reply"),
    [
        ("cookEnd", "is the air fryer done", "It's done. The cook has finished."),
        ("standby", "is the food done?", "The air fryer is off."),
        ("cooking", "how long is left on the air fryer", "Cooking at 350°F, 10 min left."),
        ("keeping", "what's the air fryer doing", "Keeping warm."),
        ("pullOut", "air fryer status", "The basket is out. Put it back to carry on."),
        ("preheatEnd", "is the air fryer finished", 'put the food in and say "start the air fryer"'),
    ],
)
async def test_status(hass, assist_on, manager, device, status, text, reply):
    device.status = status
    await setup(hass, manager)
    assert reply.lower() in (await say(hass, text)).lower()
    assert not device.commands


async def test_status_reads_fresh_state(hass, assist_on, manager, device):
    await setup(hass, manager)
    device.status = "cookEnd"
    assert await say(hass, "is the air fryer done") == "It's done. The cook has finished."


async def test_keep_warm_status(hass, assist_on, manager, device):
    await setup(hass, manager)
    await say(hass, "keep it warm for 20 minutes")
    assert await say(hass, "is the food done") == "Keeping warm at 170°F, 20 min left."


async def test_not_connected(hass, assist_on, manager, device):
    entry = await setup(hass, manager)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert await say(hass, "stop the air fryer") == assist.NOT_CONNECTED
    assert not device.commands


async def test_two_fryers_says_only_one_is_supported(hass, assist_on, manager, device):
    await setup(hass, manager)
    second = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id="second-account", version=3)
    second.add_to_hass(hass)
    assert await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done()
    assert await say(hass, "stop the air fryer") == assist.ONLY_ONE
    assert not device.commands


async def test_other_sentences_left_alone(hass, assist_on, manager, device):
    await setup(hass, manager)
    await say(hass, "what time is it")
    await say(hass, "stop the music")
    assert not device.commands


async def test_without_assist_nothing_is_registered(hass, manager):
    await setup(hass, manager)
    assert assist.async_setup(hass) is None


def test_blueprint_is_valid():
    path = ROOT / "blueprints/automation/cosori_fryer/notify_when_finished.yaml"
    blueprint = Blueprint(
        load_yaml_dict(path), expected_domain="automation", schema=AUTOMATION_BLUEPRINT_SCHEMA
    )
    assert set(blueprint.inputs) == {"finished", "phone", "preheat"}
    use = {
        "path": "notify_when_finished.yaml",
        "input": {"finished": "event.air_fryer_finished", "phone": "abc"},
    }
    config = BlueprintInputs(blueprint, {"use_blueprint": use}).async_substitute()
    PLATFORM_SCHEMA(config)
    assert config["variables"]["preheat"] is True
    assert config["actions"][0]["device_id"] == "abc"


# "Ask before heating from Assist": off by default; on, heat waits for "yes, air fryer".


async def ask_first(hass, manager):
    entry = await setup(hass, manager)
    hass.config_entries.async_update_entry(entry, options={CONF_CONFIRM_ASSIST: True})
    await hass.async_block_till_done()
    return entry


async def test_confirm_is_off_by_default(hass, assist_on, manager, device):
    entry = await setup(hass, manager)
    assert not entry.options.get(CONF_CONFIRM_ASSIST, False)
    reply = await say(hass, "air fry for 12 minutes at 380")
    assert sent_cook(device) == (380, 12, False)
    assert assist.CONFIRM_PROMPT not in reply


async def test_yes_with_nothing_waiting_starts_nothing(hass, assist_on, manager, device):
    await setup(hass, manager)
    assert await say(hass, "yes, air fryer") == assist.NOTHING_WAITING
    assert not device.commands


async def test_options_flow_sets_confirm(hass, manager):
    entry = await setup(hass, manager)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    (key,) = result["data_schema"].schema
    assert key == CONF_CONFIRM_ASSIST and key.default() is False
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_CONFIRM_ASSIST: True})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_CONFIRM_ASSIST: True}
    assert entry.state is ConfigEntryState.LOADED


async def test_cook_waits_for_yes_then_starts_that_plan(hass, assist_on, manager, device):
    await ask_first(hass, manager)
    reply = await say(hass, "air fry for 12 minutes at 375")
    assert not device.commands
    assert "using 370°F" in reply
    assert reply.endswith(assist.CONFIRM_PROMPT)
    reply = await say(hass, "yes, air fryer")
    assert sent_cook(device) == (370, 12, False)
    assert "370°F for 12 min" in reply
    assert await say(hass, "yes, air fryer") == assist.NOTHING_WAITING
    assert len(device.commands) == 1


async def test_preheat_waits_for_yes(hass, assist_on, manager, device):
    await ask_first(hass, manager)
    reply = await say(hass, "set the air fryer to 400")
    assert not device.commands and reply.endswith(assist.CONFIRM_PROMPT)
    await say(hass, "yes the air fryer")
    assert_hold(device.commands[0], 400)


async def test_keep_warm_waits_for_yes(hass, assist_on, manager, device):
    await ask_first(hass, manager)
    reply = await say(hass, "keep it warm for 20 minutes")
    assert not device.commands and reply.endswith(assist.CONFIRM_PROMPT)
    await say(hass, "yes, air fryer")
    assert sent_cook(device) == (170, 20, False)


async def test_start_of_a_queued_cook_waits_for_yes(hass, assist_on, manager, device):
    device.status = "preheatEnd"
    await ask_first(hass, manager)
    reply = await say(hass, "start the air fryer")
    assert not device.commands and reply.endswith(assist.CONFIRM_PROMPT)
    assert await say(hass, "yes, air fryer") == "Cooking now."
    assert [command["method"] for command in device.commands] == ["preheatCook"]


@pytest.mark.parametrize(
    "between",
    [
        "no, air fryer",
        "don't start the air fryer",
        "is the air fryer done",
        "air fry something odd",
        "stop the air fryer",
    ],
)
async def test_anything_else_drops_the_request(hass, assist_on, manager, device, between):
    await ask_first(hass, manager)
    await say(hass, "air fry for 12 minutes at 380")
    await say(hass, between)
    assert await say(hass, "yes, air fryer") == assist.NOTHING_WAITING
    assert not device.commands


async def test_no_says_nothing_started(hass, assist_on, manager, device):
    await ask_first(hass, manager)
    await say(hass, "air fry for 12 minutes at 380")
    assert await say(hass, "no, air fryer") == assist.DECLINED


async def test_a_second_request_replaces_the_first(hass, assist_on, manager, device):
    await ask_first(hass, manager)
    await say(hass, "air fry for 12 minutes at 380")
    await say(hass, "air fry for 10 minutes at 400")
    await say(hass, "yes, air fryer")
    assert sent_cook(device) == (400, 10, False)


async def test_yes_after_the_window_starts_nothing(hass, assist_on, manager, device):
    entry = await ask_first(hass, manager)
    await say(hass, "air fry for 12 minutes at 380")
    coordinator = entry.runtime_data
    late = dt_util.utcnow() - timedelta(seconds=1)
    coordinator.assist_pending = replace(coordinator.assist_pending, expires=late)
    assert await say(hass, "yes, air fryer") == assist.NOTHING_WAITING
    assert not device.commands


async def test_window_is_a_minute(hass, assist_on, manager, device):
    entry = await ask_first(hass, manager)
    before = dt_util.utcnow()
    await say(hass, "air fry for 12 minutes at 380")
    expires = entry.runtime_data.assist_pending.expires
    assert timedelta(seconds=59) < expires - before <= timedelta(seconds=61)


async def test_stop_and_status_never_wait(hass, assist_on, manager, device):
    device.status = "cooking"
    await ask_first(hass, manager)
    assert "Cooking at" in await say(hass, "is the air fryer done")
    assert await say(hass, "stop the air fryer") == "Stopped the air fryer."
    assert [command["method"] for command in device.commands] == ["endCook"]


async def test_questions_with_the_option_on_start_nothing(hass, assist_on, manager, device):
    await ask_first(hass, manager)
    reply = await say(hass, "air fry fries for 12 min and 7 min")
    assert "Did you mean 12 or 7 minutes?" in reply
    assert assist.CONFIRM_PROMPT not in reply
    assert await say(hass, "yes, air fryer") == assist.NOTHING_WAITING
    assert not device.commands


@pytest.mark.parametrize("now", ["cooking", "heating", "pullOut"])
async def test_yes_after_the_fryer_changed_starts_nothing(hass, assist_on, manager, device, now):
    await ask_first(hass, manager)
    await say(hass, "air fry for 12 minutes at 380")
    device.status = now
    assert await say(hass, "yes, air fryer") == assist.CHANGED
    assert not device.commands


async def test_queued_start_rechecks_on_yes(hass, assist_on, manager, device):
    device.status = "preheatEnd"
    await ask_first(hass, manager)
    await say(hass, "start the air fryer")
    device.status = "standby"
    assert await say(hass, "yes, air fryer") == assist.CHANGED
    assert not device.commands


@pytest.mark.parametrize(
    "elsewhere",
    [
        {"conversation_id": "living_room"},
        {"conversation_id": None},
        {"context": Context(user_id="someone_else")},
        {"device_id": "hallway_speaker"},
        {"satellite_id": "assist_satellite.hallway"},
    ],
)
async def test_yes_from_another_conversation_starts_nothing(hass, assist_on, manager, device, elsewhere):
    await ask_first(hass, manager)
    cook = {"context": Context(user_id="cook")}
    await say(hass, "air fry for 12 minutes at 380", **cook)
    assert await say(hass, "yes, air fryer", **{**cook, **elsewhere}) == assist.NOTHING_WAITING
    assert not device.commands
    # The refused "yes" also used up the request.
    assert await say(hass, "yes, air fryer", **cook) == assist.NOTHING_WAITING
    assert not device.commands


async def test_yes_from_the_same_user_and_device_starts(hass, assist_on, manager, device):
    await ask_first(hass, manager)
    asker = {"context": Context(user_id="cook"), "device_id": "kitchen_speaker"}
    await say(hass, "air fry for 12 minutes at 380", **asker)
    await say(hass, "yes, air fryer", **asker)
    assert sent_cook(device) == (380, 12, False)


async def test_a_first_sentence_without_an_id_is_confirmed_in_its_session(hass, assist_on, manager, device):
    await ask_first(hass, manager)
    result = await conversation.async_converse(
        hass, "air fry for 12 minutes at 380", None, Context(), language="en"
    )
    assert not device.commands
    await say(hass, "yes, air fryer", conversation_id=result.conversation_id)
    assert sent_cook(device) == (380, 12, False)


async def test_continue_cook_never_starts_from_settings(hass, manager, device):
    entry = await setup(hass, manager)
    with pytest.raises(ServiceValidationError):
        await entry.runtime_data.continue_cook()
    assert not device.commands


async def test_a_command_from_elsewhere_drops_the_request(hass, assist_on, manager, device):
    await ask_first(hass, manager)
    await say(hass, "air fry for 12 minutes at 380")
    await hass.services.async_call(DOMAIN, "keep_warm", {"minutes": 10}, blocking=True)
    assert await say(hass, "yes, air fryer") == assist.NOTHING_WAITING
    assert sent_cook(device) == (170, 10, False)


# While Assist asks first, Home Assistant's own "turn on" for the thermostat must not heat.


def climate_id(hass, entry):
    entries = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    return next(item.entity_id for item in entries if item.domain == "climate")


def button_id(hass, entry, key):
    entries = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    return next(
        item.entity_id for item in entries if item.domain == "button" and item.unique_id.endswith(f"_{key}")
    )


def heating_ids(hass, entry):
    buttons = [button_id(hass, entry, key) for key in ("start", "preheat", "keep_warm")]
    return [climate_id(hass, entry), *buttons]


def expose(hass, entity_ids):
    for entity_id in entity_ids:
        async_expose_entity(hass, "conversation", entity_id, True)


async def set_confirm(hass, entry, value):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(result["flow_id"], {CONF_CONFIRM_ASSIST: value})
    await hass.async_block_till_done()


async def test_option_hides_the_thermostat_from_assist(hass, assist_on, manager, device):
    entry = await setup(hass, manager)
    climate = climate_id(hass, entry)
    assert async_should_expose(hass, "conversation", climate)
    await set_confirm(hass, entry, True)
    assert not async_should_expose(hass, "conversation", climate)
    assert entry.options[assist.HID_FROM_ASSIST] == [climate]
    await set_confirm(hass, entry, False)
    assert async_should_expose(hass, "conversation", climate)
    assert assist.HID_FROM_ASSIST not in entry.options


async def test_option_hides_exposed_heating_buttons_and_restores_them(hass, assist_on, manager, device):
    entry = await setup(hass, manager)
    heating = heating_ids(hass, entry)
    stop = button_id(hass, entry, "stop")
    expose(hass, [*heating, stop])
    await set_confirm(hass, entry, True)
    assert not any(async_should_expose(hass, "conversation", entity_id) for entity_id in heating)
    assert async_should_expose(hass, "conversation", stop)
    assert sorted(entry.options[assist.HID_FROM_ASSIST]) == sorted(heating)
    await set_confirm(hass, entry, False)
    assert all(async_should_expose(hass, "conversation", entity_id) for entity_id in [*heating, stop])
    assert assist.HID_FROM_ASSIST not in entry.options


async def test_buttons_the_owner_left_hidden_stay_hidden(hass, assist_on, manager, device):
    entry = await setup(hass, manager)
    start = button_id(hass, entry, "start")
    expose(hass, [start])
    await set_confirm(hass, entry, True)
    assert entry.options[assist.HID_FROM_ASSIST] == [climate_id(hass, entry), start]
    await set_confirm(hass, entry, False)
    assert not async_should_expose(hass, "conversation", button_id(hass, entry, "preheat"))
    assert async_should_expose(hass, "conversation", start)


async def test_built_in_turn_on_reaches_an_exposed_preheat_button(hass, assist_on, manager, device):
    entry = await setup(hass, manager)
    expose(hass, heating_ids(hass, entry))
    reply = await say(hass, "turn on the kitchen air fryer preheat")
    await hass.async_block_till_done()
    assert device.commands, reply
    assert_hold(device.commands[0], 350)


async def test_built_in_turn_on_of_a_button_does_not_heat_while_asking(hass, assist_on, manager, device):
    entry = await setup(hass, manager)
    expose(hass, heating_ids(hass, entry))
    await set_confirm(hass, entry, True)
    await say(hass, "turn on the kitchen air fryer preheat")
    await hass.async_block_till_done()
    assert not device.commands


async def test_a_heating_button_shown_while_asking_is_hidden_again(hass, assist_on, manager, device):
    entry = await setup(hass, manager)
    preheat = button_id(hass, entry, "preheat")
    expose(hass, [preheat])
    await set_confirm(hass, entry, True)
    expose(hass, [preheat])
    await hass.async_block_till_done()
    assert not async_should_expose(hass, "conversation", preheat)
    await set_confirm(hass, entry, False)
    assert async_should_expose(hass, "conversation", preheat)


async def test_a_renamed_entity_is_restored_under_its_new_id(hass, assist_on, manager, device):
    entry = await setup(hass, manager)
    preheat = button_id(hass, entry, "preheat")
    expose(hass, [preheat])
    await set_confirm(hass, entry, True)
    er.async_get(hass).async_update_entity(preheat, new_entity_id="button.fryer_warm_up")
    await hass.async_block_till_done()
    assert "button.fryer_warm_up" in entry.options[assist.HID_FROM_ASSIST]
    await set_confirm(hass, entry, False)
    assert async_should_expose(hass, "conversation", "button.fryer_warm_up")


async def test_an_earlier_single_id_record_is_restored(hass, assist_on, manager, device):
    entry = await setup(hass, manager)
    climate = climate_id(hass, entry)
    async_expose_entity(hass, "conversation", climate, False)
    hass.config_entries.async_update_entry(
        entry, options={CONF_CONFIRM_ASSIST: True, assist.HID_FROM_ASSIST: climate}
    )
    await hass.async_block_till_done()
    assert entry.options[assist.HID_FROM_ASSIST] == [climate]
    await set_confirm(hass, entry, False)
    assert async_should_expose(hass, "conversation", climate)


async def test_exposed_thermostat_lets_assist_heat(hass, assist_on, manager, device):
    entry = await setup(hass, manager)
    assert async_should_expose(hass, "conversation", climate_id(hass, entry))
    reply = await say(hass, "air fry for 12 minutes at 380")
    assert reply != assist.NOT_EXPOSED
    assert sent_cook(device) == (380, 12, False)


@pytest.mark.parametrize(
    "text",
    [
        "air fry for 12 minutes at 380",
        "set the air fryer to 400",
        "turn on the air fryer",
        "keep it warm for 20 minutes",
    ],
)
async def test_hidden_thermostat_refuses_heating(hass, assist_on, manager, device, text):
    entry = await setup(hass, manager)
    async_expose_entity(hass, "conversation", climate_id(hass, entry), False)
    assert await say(hass, text) == assist.NOT_EXPOSED
    await hass.async_block_till_done()
    assert not device.commands


async def test_hidden_thermostat_refuses_a_queued_start(hass, assist_on, manager, device):
    device.status = "preheatEnd"
    entry = await setup(hass, manager)
    async_expose_entity(hass, "conversation", climate_id(hass, entry), False)
    assert await say(hass, "start the air fryer") == assist.NOT_EXPOSED
    assert not device.commands


async def test_hidden_thermostat_still_stops_and_answers(hass, assist_on, manager, device):
    device.status = "cooking"
    entry = await setup(hass, manager)
    async_expose_entity(hass, "conversation", climate_id(hass, entry), False)
    assert "Cooking at" in await say(hass, "is the air fryer done")
    assert await say(hass, "stop the air fryer") == "Stopped the air fryer."
    assert [command["method"] for command in device.commands] == ["endCook"]


async def test_owner_hidden_thermostat_refuses_even_when_asking(hass, assist_on, manager, device):
    entry = await setup(hass, manager)
    async_expose_entity(hass, "conversation", climate_id(hass, entry), False)
    await set_confirm(hass, entry, True)
    assert entry.options[assist.HEAT_ALLOWED] is False
    assert await say(hass, "air fry for 12 minutes at 380") == assist.NOT_EXPOSED
    assert await say(hass, "yes, air fryer") == assist.NOTHING_WAITING
    assert not device.commands


async def test_exposing_the_thermostat_later_does_not_grant_heating(hass, assist_on, manager, device):
    entry = await setup(hass, manager)
    climate = climate_id(hass, entry)
    async_expose_entity(hass, "conversation", climate, False)
    await set_confirm(hass, entry, True)
    async_expose_entity(hass, "conversation", climate, True)
    await hass.async_block_till_done()
    assert not async_should_expose(hass, "conversation", climate)
    assert entry.options[assist.HEAT_ALLOWED] is False
    assert await say(hass, "air fry for 12 minutes at 380") == assist.NOT_EXPOSED
    assert not device.commands


async def test_option_records_that_assist_may_heat_once(hass, assist_on, manager, device):
    entry = await setup(hass, manager)
    await set_confirm(hass, entry, True)
    assert entry.options[assist.HEAT_ALLOWED] is True
    # Later syncs, with the thermostat now hidden by the option, keep the answer.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.options[assist.HEAT_ALLOWED] is True


async def test_turning_the_option_off_then_hiding_revokes_heating(hass, assist_on, manager, device):
    entry = await setup(hass, manager)
    await set_confirm(hass, entry, True)
    await set_confirm(hass, entry, False)
    assert assist.HEAT_ALLOWED not in entry.options
    async_expose_entity(hass, "conversation", climate_id(hass, entry), False)
    assert await say(hass, "air fry for 12 minutes at 380") == assist.NOT_EXPOSED
    await set_confirm(hass, entry, True)
    assert entry.options[assist.HEAT_ALLOWED] is False
    assert await say(hass, "air fry for 12 minutes at 380") == assist.NOT_EXPOSED
    assert not device.commands


async def test_a_stale_record_never_allows_heating_with_the_option_off(hass, assist_on, manager, device):
    entry = await setup(hass, manager)
    climate = climate_id(hass, entry)
    async_expose_entity(hass, "conversation", climate, False)
    hass.config_entries.async_update_entry(
        entry, options={assist.HEAT_ALLOWED: True, assist.HID_FROM_ASSIST: []}
    )
    await hass.async_block_till_done()
    assert assist.HEAT_ALLOWED not in entry.options
    assert await say(hass, "air fry for 12 minutes at 380") == assist.NOT_EXPOSED
    assert not device.commands


async def test_yes_rechecks_that_assist_may_heat(hass, assist_on, manager, device):
    entry = await setup(hass, manager)
    await set_confirm(hass, entry, True)
    assert (await say(hass, "air fry for 12 minutes at 380")).endswith(assist.CONFIRM_PROMPT)
    hass.config_entries.async_update_entry(entry, options={**entry.options, assist.HEAT_ALLOWED: False})
    await hass.async_block_till_done()
    assert await say(hass, "yes, air fryer") == assist.NOT_EXPOSED
    assert not device.commands


async def test_thermostat_hidden_by_the_option_still_heats_after_yes(hass, assist_on, manager, device):
    entry = await setup(hass, manager)
    await set_confirm(hass, entry, True)
    assert not async_should_expose(hass, "conversation", climate_id(hass, entry))
    reply = await say(hass, "air fry for 12 minutes at 380")
    assert reply.endswith(assist.CONFIRM_PROMPT)
    await say(hass, "yes, air fryer")
    assert sent_cook(device) == (380, 12, False)


async def test_a_thermostat_the_owner_hid_stays_hidden(hass, assist_on, manager, device):
    entry = await setup(hass, manager)
    climate = climate_id(hass, entry)
    async_expose_entity(hass, "conversation", climate, False)
    await set_confirm(hass, entry, True)
    await set_confirm(hass, entry, False)
    assert not async_should_expose(hass, "conversation", climate)


async def test_option_already_on_at_startup_hides_it(hass, assist_on, manager, device):
    entry = await setup(hass, manager)
    hass.config_entries.async_update_entry(entry, options={CONF_CONFIRM_ASSIST: True})
    await hass.async_block_till_done()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert not async_should_expose(hass, "conversation", climate_id(hass, entry))


@pytest.mark.parametrize("text", ["switch on the air fryer", "turn the air fryer on"])
async def test_built_in_turn_on_reaches_the_thermostat_by_default(hass, assist_on, manager, device, text):
    await setup(hass, manager)
    await say(hass, text)
    await hass.async_block_till_done()
    assert_hold(device.commands[0], 350)


@pytest.mark.parametrize("text", ["switch on the air fryer", "turn the air fryer on"])
async def test_built_in_turn_on_does_not_heat_while_asking(hass, assist_on, manager, device, text):
    await ask_first(hass, manager)
    await say(hass, text)
    await hass.async_block_till_done()
    assert not device.commands


async def test_our_turn_on_still_asks(hass, assist_on, manager, device):
    await ask_first(hass, manager)
    reply = await say(hass, "turn on the air fryer")
    assert not device.commands and reply.endswith(assist.CONFIRM_PROMPT)
    await say(hass, "yes, air fryer")
    assert_hold(device.commands[0], 350)
