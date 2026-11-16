"""Full sentences for Home Assistant Assist, answered by the shared planner.

"Air fry for 12 minutes at 380", "keep it warm, I'm 10 minutes out", "stop the
air fryer" and "is the air fryer done?" work in Assist, in the Home Assistant
app and from a Shortcut that sends text to Assist. Every reply says what the
fryer will do, with any rounding. A request that is missing something starts
nothing and says what to add.
Nothing heats unless the owner exposes the thermostat to Assist.

With the "Ask before heating from Assist" option on, a request that would heat
says the plan and starts nothing; "yes, air fryer" within a minute, from the
same person, device and conversation, starts that exact plan. Any other fryer
sentence, "no, air fryer", or the minute running out drops it. Stop and status
never wait. While it's on, the thermostat and the start, preheat and keep warm
buttons are hidden from Assist, because Home Assistant's own "turn on" and
"press" sentences would heat them without asking.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import chat_session
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from . import planner
from .const import CONF_CONFIRM_ASSIST, CONFIRM_WINDOW, DOMAIN, IDLE
from .coordinator import FryerCoordinator, FryerData

_LOGGER = logging.getLogger(__name__)

FRYER = "[the] air fryer"
# Hassil templates. {request} is a wildcard; the whole sentence goes to the planner.
SENTENCES = [
    "air fry {request}",
    f"(cook|bake|roast|reheat|crisp|fry|make) {{request}} in {FRYER}",
    f"(set|turn on|heat up|preheat|warm up) {FRYER} to {{request}}",
    f"(turn on|heat up|preheat|warm up) {FRYER}",
    "keep (it|them|the food|food|dinner|everything) warm",
    "keep (it|them|the food|food|dinner|everything) warm {request}",
    f"keep (it|them|the food|food|dinner|everything) warm in {FRYER}",
    f"keep (it|them|the food|food|dinner|everything) warm in {FRYER} {{request}}",
    f"(stop|cancel|turn off|shut off|switch off) {FRYER}",
    f"start {FRYER}",
    "start cooking",
    f"is {FRYER} (done|ready|finished|preheated|hot|on|running)",
    "is (it|the food) (done|ready)",
    f"how (long|much longer|much time) [is] [left] (on|for|in) {FRYER}",
    f"(what's|what is) {FRYER} doing",
    f"{FRYER} status",
    f"yes {FRYER}",
    f"no {FRYER}",
    f"(don't|do not) start {FRYER}",
]
_START = re.compile(r"(please )?start (the )?(air fryer|cooking)( please)?")
# The question sentences above; the planner would read "finished" as a food.
_QUESTION = re.compile(r"(is|how|what's|what is) .*|.*\bstatus")
_CONFIRM = re.compile(r"yes (the )?air fryer")
_DECLINE = re.compile(r"no (the )?air fryer|(don't|do not) start (the )?air fryer")
CONFIRM_PROMPT = 'Say "yes, air fryer" to start.'
NOTHING_WAITING = "Nothing is waiting to start. Say the whole request again."
DECLINED = "OK. Nothing started."
CHANGED = "The air fryer changed since you asked. Nothing started; say the whole request again."


@dataclass(frozen=True)
class Pending:
    """A heating request waiting for "yes, air fryer"."""

    run: Callable[[], Awaitable[str]]
    expires: datetime
    # The fryer state the plan was made for; "yes" runs it only in that state.
    state: tuple
    # Who asked: "yes" counts only from the same user, device and conversation.
    requester: tuple


def requester(user_input) -> tuple:
    """The user, device, satellite and conversation a sentence came from."""
    # A first sentence has no conversation ID; the session Assist opens for it does.
    session = chat_session.current_session.get()
    conversation_id = session.conversation_id if session else user_input.conversation_id
    return (user_input.context.user_id, user_input.device_id, user_input.satellite_id, conversation_id)


def _state(data: FryerData) -> tuple:
    return ("idle" if data.status in IDLE else data.status, data.held, data.cook_temp_f)


NOT_CONNECTED = "The air fryer isn't connected to Home Assistant right now."
NOT_EXPOSED = "The air fryer isn't exposed to Assist, so Assist can't heat it. Nothing started."
ONLY_ONE = "Assist works with one air fryer, and more than one is set up. Use the dashboard instead."
REFUSALS = {
    "busy": 'The air fryer is busy with a cook. Say "stop the air fryer" first.',
    "running": 'The air fryer is busy with a cook. Say "stop the air fryer" first.',
    "auth_failed": (
        "The air fryer's VeSync sign-in has expired. Whoever set up Home Assistant needs to sign in again."
    ),
    "command_failed": "I couldn't reach the air fryer. Check it before trying again.",
}
REFUSED = "The air fryer didn't take that. Check it before trying again."


def async_setup(hass: HomeAssistant) -> CALLBACK_TYPE | None:
    """Register the sentences with Assist, when Assist is set up."""
    if "conversation" not in hass.config.components:
        return None
    from homeassistant.components.conversation.agent_manager import get_agent_manager

    async def respond(user_input, _result) -> str:
        return await async_answer(hass, user_input.text, requester(user_input))

    return get_agent_manager(hass).register_trigger(SENTENCES, respond)


# Options key: the entities this integration hid from Assist, so turning the
# option off shows only what it hid, never one the owner hid.
HID_FROM_ASSIST = "hid_from_assist"
CONVERSATION = "conversation"
# Entities that heat when turned on or pressed, by domain and unique ID key.
HEATING = {"climate": ("fryer",), "button": ("start", "preheat", "keep_warm")}
# Options key: whether Assist may heat while the option keeps the thermostat hidden.
HEAT_ALLOWED = "assist_heat_allowed"


def _heating_entities(registry: er.EntityRegistry, entry) -> list[str]:
    return [
        item.entity_id
        for item in er.async_entries_for_config_entry(registry, entry.entry_id)
        if any(item.unique_id.endswith(f"_{key}") for key in HEATING.get(item.domain, ()))
    ]


def _hidden(entry) -> list[str]:
    hidden = entry.options.get(HID_FROM_ASSIST, [])
    # A single ID is the older record, when only the thermostat was hidden.
    return [hidden] if isinstance(hidden, str) else list(hidden)


@callback
def async_watch_exposure(hass: HomeAssistant, entry) -> None:
    """Hide a heating entity again if it is shown to Assist while the option is on,
    and keep the record pointing at entities that are renamed."""
    from homeassistant.components.homeassistant.const import DATA_EXPOSED_ENTITIES

    if DATA_EXPOSED_ENTITIES not in hass.data:
        return
    from homeassistant.components.homeassistant.exposed_entities import async_listen_entity_updates

    @callback
    def exposure_changed() -> None:
        if entry.options.get(CONF_CONFIRM_ASSIST, False):
            # Called while a setting is being saved, so sync once it's done.
            hass.loop.call_soon(async_sync_exposure, hass, entry)

    @callback
    def registry_updated(event) -> None:
        old = event.data.get("old_entity_id")
        hidden = _hidden(entry)
        if event.data["action"] == "update" and old in hidden:
            hidden[hidden.index(old)] = event.data["entity_id"]
            hass.config_entries.async_update_entry(entry, options={**entry.options, HID_FROM_ASSIST: hidden})

    entry.async_on_unload(async_listen_entity_updates(hass, CONVERSATION, exposure_changed))
    entry.async_on_unload(hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, registry_updated))


@callback
def async_sync_exposure(hass: HomeAssistant, entry) -> None:
    """Hide what can heat from Assist while Assist must ask before heating."""
    from homeassistant.components.homeassistant.const import DATA_EXPOSED_ENTITIES

    if DATA_EXPOSED_ENTITIES not in hass.data:
        return
    from homeassistant.components.homeassistant.exposed_entities import (
        async_expose_entity,
        async_should_expose,
    )

    registry = er.async_get(hass)
    hidden = _hidden(entry)
    options = {
        key: value for key, value in entry.options.items() if key not in (HID_FROM_ASSIST, HEAT_ALLOWED)
    }
    if options.get(CONF_CONFIRM_ASSIST, False):
        heating = _heating_entities(registry, entry)
        if HEAT_ALLOWED in entry.options:
            options[HEAT_ALLOWED] = entry.options[HEAT_ALLOWED]
        else:
            # Decided once, as the option turns on, from whether the owner showed
            # the thermostat to Assist; hiding it below doesn't change the answer.
            options[HEAT_ALLOWED] = any(
                entity_id.startswith("climate.")
                and (async_should_expose(hass, CONVERSATION, entity_id) or entity_id in hidden)
                for entity_id in heating
            )
        for entity_id in heating:
            if async_should_expose(hass, CONVERSATION, entity_id):
                async_expose_entity(hass, CONVERSATION, entity_id, False)
                if entity_id not in hidden:
                    hidden.append(entity_id)
        if hidden:
            options[HID_FROM_ASSIST] = hidden
    else:
        for entity_id in hidden:
            if registry.async_get(entity_id):
                async_expose_entity(hass, CONVERSATION, entity_id, True)
    if options != entry.options:
        hass.config_entries.async_update_entry(entry, options=options)


def _coordinator(hass: HomeAssistant) -> FryerCoordinator | str:
    """The one loaded fryer, or what to say instead."""
    entries = [
        entry for entry in hass.config_entries.async_entries(DOMAIN) if entry.state is ConfigEntryState.LOADED
    ]
    if len(entries) > 1:
        return ONLY_ONE
    return entries[0].runtime_data if entries else NOT_CONNECTED


async def async_answer(hass: HomeAssistant, text: str, asked_by: tuple) -> str:
    """Carry out one sentence and say what happened."""
    coordinator = _coordinator(hass)
    if isinstance(coordinator, str):
        return coordinator
    plain = " ".join(re.sub(r"[^a-z0-9' ]", " ", text.lower()).split())
    # Whatever is said next, a request that was waiting is used once or dropped.
    pending, coordinator.assist_pending = coordinator.assist_pending, None
    try:
        if _CONFIRM.fullmatch(plain):
            if pending is None or pending.requester != asked_by or dt_util.utcnow() > pending.expires:
                return NOTHING_WAITING
            await coordinator.async_refresh()
            if not coordinator.last_update_success or _state(coordinator.data) != pending.state:
                return CHANGED
            if not _assist_may_heat(coordinator):
                return NOT_EXPOSED
            return await pending.run()
        if _DECLINE.fullmatch(plain):
            return DECLINED
        if _START.fullmatch(plain):
            return await _start(coordinator, asked_by)
        intent = planner.Intent("status") if _QUESTION.fullmatch(plain) else planner.parse(text)
        if intent.kind == "status":
            await coordinator.async_refresh()
            return status(coordinator)
        if intent.kind == "stop":
            return await _stop(coordinator)
        return await _run(coordinator, intent, asked_by)
    except planner.PlanError as err:
        return f"The air fryer can't do that: {err} Nothing started."
    except ServiceValidationError as err:
        return REFUSALS.get(err.translation_key or "", REFUSED)
    except HomeAssistantError as err:
        return REFUSALS.get(err.translation_key or "", REFUSED)


async def _stop(coordinator: FryerCoordinator) -> str:
    try:
        await coordinator.stop()
    except ServiceValidationError as err:
        if err.translation_key == "command_refused":
            return "The air fryer is already off."
        raise
    return "Stopped the air fryer."


async def _start(coordinator: FryerCoordinator, asked_by: tuple) -> str:
    """Continue a cook waiting behind a finished preheat; never start a stale timer."""
    await coordinator.async_refresh()
    data = coordinator.data
    if data.status == "preheatEnd" and not data.held:

        async def run() -> str:
            await coordinator.continue_cook()
            return "Cooking now."

        return await _heat(coordinator, "Start the cook that's waiting.", run, asked_by)
    return 'Say how hot and how long, like "air fry for 12 minutes at 380". Nothing started.'


async def _run(coordinator: FryerCoordinator, intent: planner.Intent, asked_by: tuple) -> str:
    if _ask_first(coordinator):
        # Plan from fresh state, since "yes" checks the fryer is still in it.
        await coordinator.async_refresh()
    data = coordinator.data
    extra: list[str] = []
    if intent.kind == "cook" and intent.temp_f is None and not intent.food and data.held:
        # Food going into a hot, holding fryer: cook at the temperature it holds.
        intent = replace(intent, temp_f=data.cook_temp_f, preheat=False)
        extra.append(f"Using the {data.cook_temp_f}°F it's holding at.")
    if intent.kind == "preheat" and intent.temp_f is None and not intent.food:
        # The same temperature turning it on from the tile or Apple Home uses.
        intent = replace(intent, temp_f=coordinator.settings.temperature)
        extra.append("That's the last temperature set.")
    plan = planner.plan(intent)
    if plan.question:
        return f"{plan.question} Nothing started."
    said = " ".join([plan.describe(), *extra])

    async def run() -> str:
        after = []
        if plan.intent == "preheat":
            await coordinator.hold(plan.temp_f)
            coordinator.update_settings(temperature=plan.temp_f)
        elif plan.intent == "keep_warm":
            await coordinator.keep_warm(plan.minutes)
        else:
            await coordinator.cook(plan.temp_f, plan.minutes, plan.preheat)
            if plan.preheat:
                after.append('When it says preheated, put the food in and say "start the air fryer".')
        _LOGGER.info("Assist: %s", plan.describe())
        return " ".join([said, *after])

    return await _heat(coordinator, said, run, asked_by)


async def _heat(
    coordinator: FryerCoordinator, said: str, run: Callable[[], Awaitable[str]], asked_by: tuple
) -> str:
    """Heat now, or, with the confirm option on, keep the request for "yes, air fryer"."""
    if not _assist_may_heat(coordinator):
        return NOT_EXPOSED
    if not _ask_first(coordinator):
        return await run()
    coordinator.assist_pending = Pending(
        run, dt_util.utcnow() + CONFIRM_WINDOW, _state(coordinator.data), asked_by
    )
    return f"{said} {CONFIRM_PROMPT}"


def _assist_may_heat(coordinator: FryerCoordinator) -> bool:
    """Whether the owner lets Assist control the fryer's thermostat.

    While the confirm option is on, this integration hides the thermostat itself
    so the built-in sentences can't heat it; the answer recorded as the option
    turned on stands in for its exposure until the option is turned off.
    """
    from homeassistant.components.homeassistant.const import DATA_EXPOSED_ENTITIES

    hass, entry = coordinator.hass, coordinator.config_entry
    if DATA_EXPOSED_ENTITIES not in hass.data:
        return False
    if _ask_first(coordinator):
        return entry.options.get(HEAT_ALLOWED) is True
    from homeassistant.components.homeassistant.exposed_entities import async_should_expose

    climate = next(
        (
            entity_id
            for entity_id in _heating_entities(er.async_get(hass), entry)
            if entity_id.startswith("climate.")
        ),
        None,
    )
    return climate is not None and async_should_expose(hass, CONVERSATION, climate)


def _ask_first(coordinator: FryerCoordinator) -> bool:
    return bool(coordinator.config_entry.options.get(CONF_CONFIRM_ASSIST, False))


def _minutes(seconds: int | None) -> str:
    left = math.ceil(seconds / 60) if seconds else 0
    return "under a minute" if left < 1 else f"{left} min"


def status(coordinator: FryerCoordinator) -> str:
    """What the fryer is doing, in one or two short sentences."""
    if not coordinator.last_update_success:
        return "I can't reach the air fryer right now."
    data = coordinator.data
    temp = f"{data.cook_temp_f}°F" if data.cook_temp_f else "temperature"
    left = _minutes(data.time_left)
    if data.status == "cookEnd":
        return "It's done. The cook has finished."
    if data.status in ("standby", "ready"):
        return "The air fryer is off."
    if data.status == "heating":
        then = "then holds until the food goes in" if data.held else "then waits for the food"
        return f"Preheating to {temp}, about {left} left; it {then}."
    if data.status == "preheatEnd":
        if data.held:
            return (
                f"It's hot and holding at {temp}. Put the food in and say how long, "
                'like "air fry for 12 minutes".'
            )
        return f'It\'s preheated to {temp}. Put the food in and say "start the air fryer".'
    if data.status == "cooking" and data.cook_temp_f == planner.KEEP_WARM_TEMP_F:
        return f"Keeping warm at {temp}, {left} left."
    if data.status == "cooking":
        return f"Cooking at {temp}, {left} left."
    if data.status == "keeping_warm":
        return "Keeping warm."
    if data.status == "pullOut":
        return "The basket is out. Put it back to carry on."
    if data.status in ("preheatStop", "cookStop"):
        return 'It\'s paused. Press start on the fryer to carry on, or say "stop the air fryer".'
    return "I'm not sure what it's doing. Say \"stop the air fryer\" to stop it."
