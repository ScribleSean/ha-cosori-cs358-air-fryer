"""Contract tests using fake managers and real pyvesync fryer states."""

from datetime import timedelta

import pytest
import voluptuous as vol
from fakes import ENTRY_DATA
from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_system import METRIC_SYSTEM, US_CUSTOMARY_SYSTEM
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.cosori_fryer._vendor.pyvesync.utils.errors import VeSyncLoginError, VeSyncTokenError
from custom_components.cosori_fryer.const import DOMAIN, POLL_ACTIVE, POLL_IDLE, STATUS
from custom_components.cosori_fryer.diagnostics import async_get_config_entry_diagnostics


async def setup(hass, manager):
    hass.config.units = US_CUSTOMARY_SYSTEM
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id="test-account", version=3)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def refresh(hass, entry):
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


async def start(hass, **changes):
    """The 0.7 action name, kept as an alias of cook."""
    data = {"temperature": 350, "minutes": 10, "preheat": True, **changes}
    await hass.services.async_call(DOMAIN, "start_cook", data, blocking=True)


async def cook(hass, **data):
    await hass.services.async_call(DOMAIN, "cook", data, blocking=True)


async def keep_warm(hass, **data):
    await hass.services.async_call(DOMAIN, "keep_warm", data, blocking=True)


async def test_setup(hass, manager, caplog):
    entry = await setup(hass, manager)
    assert entry.state is ConfigEntryState.LOADED
    counts = {
        "sensor": 4,
        "button": 4,
        "event": 1,
        "climate": 1,
        "number": 1,
        "select": 1,
        "binary_sensor": 1,
    }
    for domain, count in counts.items():
        assert len(hass.states.async_all(domain)) == count, domain
    assert not manager.login.called
    assert not [record for record in caplog.records if record.levelno >= 40]
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_flow_success(hass, manager):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}, data={"email": "TEST@example.com", "password": "test-password"}
    )
    await hass.async_block_till_done()
    assert result["type"] == "create_entry"
    assert result["result"].unique_id == "test-account"
    assert result["data"]["token"] == "test-token"
    assert manager.login.call_count == 1


@pytest.mark.parametrize("failure", [False, VeSyncLoginError("invalid")])
async def test_flow_bad_login(hass, manager, failure):
    manager.login.side_effect = None
    if isinstance(failure, Exception):
        manager.login.side_effect = failure
    else:
        manager.login.return_value = failure
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}, data={"email": "test@example.com", "password": "bad"}
    )
    assert result["errors"] == {"base": "invalid_auth"}


async def test_duplicate_before_login(hass, manager):
    await setup(hass, manager)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}, data={"email": "TEST@example.com", "password": "x"}
    )
    assert result["reason"] == "already_configured"
    manager.login.assert_not_called()


async def test_duplicate_account_id(hass, manager):
    await setup(hass, manager)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}, data={"email": "alias@example.com", "password": "x"}
    )
    assert result["reason"] == "already_configured"


async def test_reauth(hass, manager):
    entry = await setup(hass, manager)
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"password": "new-password"})
    await hass.async_block_till_done()
    assert result["reason"] == "reauth_successful"
    assert entry.data["password"] == "new-password"


@pytest.mark.parametrize("status", list(STATUS))
async def test_entities_every_state(hass, manager, device, status):
    device.status = "keeping" if status == "keeping_warm" else status
    entry = await setup(hass, manager)
    assert hass.states.get("sensor.kitchen_air_fryer_cooking_status").state == STATUS[status]
    for entity in hass.states.async_all():
        assert entity.state != "unavailable"
    data = entry.runtime_data.data
    assert (data.preheat_ends_at is not None) == (status == "heating")
    assert (data.cook_ends_at is not None) == (status == "cooking")
    assert entry.runtime_data.update_interval == (
        POLL_ACTIVE if status in ("heating", "cooking", "keeping_warm") else POLL_IDLE
    )


async def test_phase_timestamps(hass, manager, device):
    device.status = "heating"
    entry = await setup(hass, manager)
    await refresh(hass, entry)
    sensor = hass.states.get("sensor.kitchen_air_fryer_preheat_ends_at")
    assert sensor.attributes["device_class"] == "timestamp"
    assert (dt_util.parse_datetime(sensor.state) - dt_util.utcnow()).total_seconds() == pytest.approx(
        240, abs=2
    )
    assert entry.runtime_data.data.cook_ends_at is None
    device.status = "cooking"
    await refresh(hass, entry)
    assert (entry.runtime_data.data.cook_ends_at - dt_util.utcnow()).total_seconds() == pytest.approx(
        600, abs=2
    )
    assert hass.states.get("sensor.kitchen_air_fryer_cook_ends_at").attributes["device_class"] == "timestamp"


async def test_current_temperature_zero_is_unknown_and_hidden(hass, manager, device):
    """The recorded firmware response has currentTemp 0 in every phase."""
    device.status = "cooking"
    device.current_temp = 0
    entry = await setup(hass, manager)
    assert entry.runtime_data.data.current_temp is None
    assert hass.states.get("sensor.kitchen_air_fryer_current_temperature") is None
    registry = er.async_get(hass)
    assert registry.async_get("sensor.kitchen_air_fryer_current_temperature").disabled_by is not None
    device.current_temp = 150
    await refresh(hass, entry)
    assert entry.runtime_data.data.current_temp == pytest.approx(150)


async def test_preheat_then_start_keeps_temperature_and_time(hass, manager, device, freezer):
    """Preheat at 400 °F for 12 min, then Start cooking continues with the same settings."""
    entry = await setup(hass, manager)
    await start(hass, temperature=400, minutes=12)
    sent = device.commands[0]
    assert sent["method"] == "startCook"
    assert sent["data"]["hasPreheat"] == 1
    assert sent["data"]["startAct"] == {
        "cookSetTime": 720,
        "cookTemp": 400,
        "preheatTemp": 400,
        "shakeTime": 0,
    }
    target = "climate.kitchen_air_fryer"
    for raw in ("heating", "preheatEnd"):
        device.status = raw
        await refresh(hass, entry)
        assert hass.states.get(target).attributes["temperature"] == 400
        assert entry.runtime_data.data.cook_ends_at is None
    # Waiting at preheatEnd sends nothing on its own, however long it lasts.
    freezer.tick(timedelta(minutes=5))
    await refresh(hass, entry)
    assert len(device.commands) == 1
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.kitchen_air_fryer_start"}, blocking=True
    )
    assert device.commands[1]["method"] == "preheatCook"
    assert device.commands[1]["data"] == {}
    device.status = "cooking"
    await refresh(hass, entry)
    assert hass.states.get(target).attributes["temperature"] == 400
    assert (entry.runtime_data.data.cook_ends_at - dt_util.utcnow()).total_seconds() == pytest.approx(
        720, abs=2
    )
    assert hass.states.get("event.kitchen_air_fryer_finished").attributes["event_type"] == "preheat_finished"


async def test_start_350_ten_minutes(hass, manager, device):
    entry = await setup(hass, manager)
    await start(hass)
    assert device.commands[0]["data"]["startAct"] == {
        "cookSetTime": 600,
        "cookTemp": 350,
        "preheatTemp": 350,
        "shakeTime": 0,
    }
    device.status = "heating"
    await refresh(hass, entry)
    assert (entry.runtime_data.data.preheat_ends_at - dt_util.utcnow()).total_seconds() == pytest.approx(
        240, abs=2
    )
    assert entry.runtime_data.data.cook_ends_at is None


@pytest.mark.parametrize(
    "changes",
    [
        {"temperature": 160},
        {"temperature": 410},
        {"temperature": 164},
        {"minutes": 0},
        {"minutes": 61},
        {"minutes": -1},
        {"temperature": 70, "temperature_unit": "°C"},
        {"temperature": -100, "temperature_unit": "°C"},
    ],
)
async def test_invalid_settings(hass, manager, device, changes):
    await setup(hass, manager)
    with pytest.raises(ServiceValidationError) as exc:
        await start(hass, **changes)
    assert exc.value.translation_key == "invalid_request"
    assert exc.value.translation_placeholders["reason"]
    assert not device.commands


async def test_refusal_states_the_reason(hass, manager, device):
    await setup(hass, manager)
    with pytest.raises(ServiceValidationError) as exc:
        await cook(hass, temperature=350, minutes=90)
    assert "60-minute limit" in exc.value.translation_placeholders["reason"]
    with pytest.raises(ServiceValidationError) as exc:
        await cook(hass, temperature=450, minutes=10)
    assert "outside" in exc.value.translation_placeholders["reason"]
    assert not device.commands


@pytest.mark.parametrize(
    "asked,sent",
    [
        ({"temperature": 355, "minutes": 10}, (350, 600)),
        ({"temperature": 405, "minutes": 10}, (400, 600)),
        ({"temperature": 165, "minutes": 10}, (170, 600)),
        ({"temperature": 350, "minutes": 2.5}, (350, 180)),
        ({"temperature": 180, "temperature_unit": "°C", "minutes": 10}, (360, 600)),
    ],
)
async def test_cook_rounds_to_settings(hass, manager, device, asked, sent, caplog):
    entry = await setup(hass, manager)
    caplog.set_level("INFO", logger="custom_components.cosori_fryer")
    await cook(hass, **asked)
    (command,) = device.commands
    assert not command["data"].get("hasPreheat")
    assert (command["data"]["startAct"]["cookTemp"], command["data"]["startAct"]["cookSetTime"]) == sent
    assert "nearest 10" in caplog.text or "rounds up" in caplog.text
    settings = entry.runtime_data.settings
    assert (settings.temperature, settings.minutes * 60) == sent


@pytest.mark.parametrize("service", ["cook", "start_cook"])
@pytest.mark.parametrize("field", ["temperature", "minutes"])
async def test_required_fields(hass, manager, device, service, field):
    await setup(hass, manager)
    data = {"temperature": 350, "minutes": 10, "preheat": True}
    del data[field]
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(DOMAIN, service, data, blocking=True)
    assert not device.commands


@pytest.mark.parametrize("service", ["cook", "start_cook"])
async def test_preheat_is_off_by_default(hass, manager, device, service):
    await setup(hass, manager)
    await hass.services.async_call(DOMAIN, service, {"temperature": 350, "minutes": 10}, blocking=True)
    assert not device.commands[0]["data"].get("hasPreheat")


async def test_cook_with_preheat_queues_the_cook(hass, manager, device):
    entry = await setup(hass, manager)
    await cook(hass, temperature=400, minutes=12, preheat=True)
    assert device.commands[0]["data"]["hasPreheat"] == 1
    assert device.commands[0]["data"]["startAct"]["cookSetTime"] == 720
    device.status = "preheatEnd"
    await refresh(hass, entry)
    assert not entry.runtime_data.data.held
    # A real cook is queued: another cook is refused rather than replacing it.
    with pytest.raises(ServiceValidationError) as exc:
        await cook(hass, temperature=350, minutes=5)
    assert exc.value.translation_key == "busy"
    with pytest.raises(ServiceValidationError):
        await keep_warm(hass)
    assert len(device.commands) == 1


async def test_cook_replaces_hold(hass, manager, device):
    entry = await setup(hass, manager)
    await entry.runtime_data.hold(380)
    assert entry.runtime_data.data.held
    await cook(hass, temperature=400, minutes=8)
    assert [c["method"] for c in device.commands] == ["startCook", "endCook", "startCook"]
    assert device.commands[2]["data"]["startAct"]["cookSetTime"] == 480
    assert not device.commands[2]["data"].get("hasPreheat")


@pytest.mark.parametrize("status", ["cooking", "cookStop", "heating"])
async def test_cook_refused_while_cooking(hass, manager, device, status):
    device.status = status
    await setup(hass, manager)
    with pytest.raises(ServiceValidationError) as exc:
        await cook(hass, temperature=350, minutes=5)
    assert exc.value.translation_key == "busy"
    assert not device.commands


@pytest.mark.parametrize(
    "minutes,seconds", [(None, 900), (20, 1200), (2.5, 180), (90, 3600), ("1 hour", None)]
)
async def test_keep_warm_action(hass, manager, device, minutes, seconds):
    await setup(hass, manager)
    if seconds is None:
        with pytest.raises(vol.Invalid):
            await keep_warm(hass, minutes=minutes)
        assert not device.commands
        return
    await keep_warm(hass, **({} if minutes is None else {"minutes": minutes}))
    (sent,) = device.commands
    assert not sent["data"].get("hasPreheat")
    assert (sent["data"]["startAct"]["cookTemp"], sent["data"]["startAct"]["cookSetTime"]) == (170, seconds)


async def test_keep_warm_action_refusals(hass, manager, device):
    await setup(hass, manager)
    with pytest.raises(ServiceValidationError) as exc:
        await keep_warm(hass, minutes=0)
    assert exc.value.translation_key == "invalid_request"
    device.status = "cooking"
    with pytest.raises(ServiceValidationError) as exc:
        await keep_warm(hass)
    assert exc.value.translation_key == "busy"
    assert not device.commands


@pytest.mark.parametrize("value", [True, None, "nan", "inf", [], {}])
async def test_invalid_numbers(hass, manager, value):
    await setup(hass, manager)
    with pytest.raises(vol.Invalid):
        await start(hass, temperature=value)


@pytest.mark.parametrize("explicit", [True, False])
@pytest.mark.parametrize("temperature,fahrenheit", [(180, 360), (200, 390)])
async def test_celsius_conversion(hass, manager, device, explicit, temperature, fahrenheit, caplog):
    await setup(hass, manager)
    hass.config.units = METRIC_SYSTEM
    kwargs = {"temperature_unit": "°C"} if explicit else {}
    caplog.set_level("INFO", logger="custom_components.cosori_fryer")
    await start(hass, temperature=temperature, **kwargs)
    assert device.commands[0]["data"]["startAct"]["cookTemp"] == fahrenheit
    assert f"{fahrenheit} °F" in caplog.text


async def test_without_preheat(hass, manager, device):
    await setup(hass, manager)
    await start(hass, preheat=False)
    assert device.commands[0]["data"]["hasPreheat"] == 0


@pytest.mark.parametrize(
    "button,status,method", [("stop", "cooking", "end"), ("start", "preheatEnd", "cook_from_preheat")]
)
async def test_buttons(hass, manager, device, button, status, method):
    device.status = status
    await setup(hass, manager)
    await hass.services.async_call(
        "button", "press", {"entity_id": f"button.kitchen_air_fryer_{button}"}, blocking=True
    )
    assert [c["method"] for c in device.commands] == [
        "preheatCook" if method == "cook_from_preheat" else "endCook"
    ]


@pytest.mark.parametrize("button", ["start", "preheat"])
@pytest.mark.parametrize("status", ["heating", "cooking", "cookStop", "pullOut", "ready"])
async def test_start_button_refused(hass, manager, device, status, button):
    device.status = status
    await setup(hass, manager)
    with pytest.raises(ServiceValidationError) as exc:
        await hass.services.async_call(
            "button", "press", {"entity_id": f"button.kitchen_air_fryer_{button}"}, blocking=True
        )
    assert exc.value.translation_key == "busy"
    assert hass.states.get(f"button.kitchen_air_fryer_{button}").state != "unavailable"
    assert not device.commands


async def test_busy_and_refused_commands_stay_available(hass, manager, device):
    device.status = "cooking"
    await setup(hass, manager)
    with pytest.raises(ServiceValidationError):
        await start(hass)
    device.command_code = 1
    with pytest.raises(HomeAssistantError) as exc:
        await hass.services.async_call(
            "button", "press", {"entity_id": "button.kitchen_air_fryer_stop"}, blocking=True
        )
    assert exc.value.translation_key == "command_refused"
    assert hass.states.get("sensor.kitchen_air_fryer_cooking_status").state == "cooking"


@pytest.mark.parametrize("error", [VeSyncTokenError("expired"), VeSyncLoginError("expired")])
async def test_token_poll_reauth_no_login(hass, manager, device, error):
    entry = await setup(hass, manager)
    device.error = error
    await refresh(hass, entry)
    await refresh(hass, entry)
    assert len(hass.config_entries.flow.async_progress()) == 1
    assert hass.config_entries.flow.async_progress()[0]["context"]["source"] == "reauth"
    manager.login.assert_not_called()


async def test_token_command_reauth_no_login(hass, manager, device):
    await setup(hass, manager)
    device.command_error = VeSyncTokenError("expired")
    with pytest.raises(HomeAssistantError) as exc:
        await start(hass)
    await hass.async_block_till_done()
    assert exc.value.translation_key == "auth_failed"
    assert len(hass.config_entries.flow.async_progress()) == 1
    manager.login.assert_not_called()


async def test_read_failure_and_recovery_within_two_minutes(hass, manager, device, freezer):
    entry = await setup(hass, manager)
    device.error = TimeoutError()
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert not entry.runtime_data.last_update_success
    assert hass.states.get("sensor.kitchen_air_fryer_cooking_status").state == "unavailable"
    device.error = None
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert hass.states.get("sensor.kitchen_air_fryer_cooking_status").state == "standby"


@pytest.mark.parametrize(
    "running,finished,event_type",
    [("heating", "preheatEnd", "preheat_finished"), ("cooking", "cookEnd", "cook_finished")],
)
async def test_finish_once(hass, manager, device, running, finished, event_type):
    device.status = running
    entry = await setup(hass, manager)
    device.status = "pullOut"
    await refresh(hass, entry)
    device.status = finished
    await refresh(hass, entry)
    entity = hass.states.get("event.kitchen_air_fryer_finished")
    assert entity.attributes["event_type"] == event_type
    timestamp = entity.state
    await refresh(hass, entry)
    assert hass.states.get(entity.entity_id).state == timestamp
    assert not hass.states.async_all("persistent_notification")


async def test_event_entity_writes_event_type(hass, manager, device):
    device.status = "cooking"
    entry = await setup(hass, manager)
    device.status = "cookEnd"
    await refresh(hass, entry)
    entity = hass.states.get("event.kitchen_air_fryer_finished")
    assert entity.attributes["event_type"] == "cook_finished"
    timestamp = entity.state
    await refresh(hass, entry)
    assert hass.states.get(entity.entity_id).state == timestamp


async def test_finish_at_startup_not_announced(hass, manager, device):
    device.status = "cookEnd"
    await setup(hass, manager)
    assert hass.states.get("event.kitchen_air_fryer_finished").state == "unknown"


async def test_diagnostics(hass, manager, device):
    entry = await setup(hass, manager)
    hass.config_entries.async_update_entry(
        entry,
        data={
            **entry.data,
            "nested": {key: "private-value" for key in ("uuid", "mac", "serial", "device_name")},
        },
    )
    result = await async_get_config_entry_diagnostics(hass, entry)
    assert result["entry"]["email"] == "**REDACTED**"
    assert result["device"]["cid"] == "**REDACTED**"
    assert "private-value" not in str(result)
    assert device.device_name not in str(result)
    assert device.cid not in str(result)
    assert result["device"]["model"] == "CS358-AF"
    for key in ("email", "password", "token", "account_id"):
        assert ENTRY_DATA[key] not in str(result)


@pytest.mark.parametrize(
    "failure,expected", [(TimeoutError(), "cannot_connect"), (VeSyncTokenError("expired"), "invalid_auth")]
)
async def test_flow_discovery_errors(hass, manager, failure, expected):
    manager.get_devices.side_effect = failure
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}, data={"email": "test@example.com", "password": "test-password"}
    )
    assert result["errors"] == {"base": expected}


@pytest.mark.parametrize("count,error", [(0, "no_fryer"), (2, "multiple_fryers")])
async def test_flow_device_count(hass, manager, device, count, error):
    manager.devices = [device] * count
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}, data={"email": "test@example.com", "password": "test-password"}
    )
    assert result["errors"] == {"base": error}
    # Retrying discovery with the same credentials reuses the session.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"email": "test@example.com", "password": "test-password"}
    )
    assert result["errors"] == {"base": error}
    assert manager.login.call_count == 1


async def test_reauth_wrong_account(hass, manager):
    entry = await setup(hass, manager)
    result = await entry.start_reauth_flow(hass)
    manager.account_id = "another-account"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"password": "new-password"})
    assert result["reason"] == "wrong_account"
    assert entry.data["account_id"] == "test-account"


async def test_setup_expired_session_never_logs_in(hass, manager):
    manager.get_devices.side_effect = VeSyncTokenError("expired")
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id="test-account", version=3)
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert len(hass.config_entries.flow.async_progress()) == 1
    manager.login.assert_not_called()


async def test_timestamp_is_stable_between_polls(hass, manager, device, freezer):
    device.status = "cooking"
    await setup(hass, manager)
    entity = hass.states.get("sensor.kitchen_air_fryer_cook_ends_at")
    end = dt_util.parse_datetime(entity.state)
    before = (end - dt_util.utcnow()).total_seconds()
    freezer.tick(timedelta(seconds=5))
    assert hass.states.get(entity.entity_id).state == entity.state
    assert (end - dt_util.utcnow()).total_seconds() == before - 5


async def test_no_popup_on_finish(hass, manager, device):
    from homeassistant.helpers import issue_registry

    device.status = "cooking"
    entry = await setup(hass, manager)
    calls = []
    hass.bus.async_listen("call_service", lambda event: calls.append(event.data))
    device.status = "cookEnd"
    await refresh(hass, entry)
    assert not [call for call in calls if call["domain"] in ("notify", "persistent_notification", "repairs")]
    assert not issue_registry.async_get(hass).issues


async def test_command_inner_refusal(hass, manager, device):
    await setup(hass, manager)

    device.command_inner_code = 123
    with pytest.raises(HomeAssistantError) as exc:
        await start(hass)
    assert exc.value.translation_key == "command_refused"
    assert hass.states.get("sensor.kitchen_air_fryer_cooking_status").state == "standby"


async def test_failed_read_response_unavailable(hass, manager, device):
    entry = await setup(hass, manager)
    device.response_code = 1
    await refresh(hass, entry)
    assert hass.states.get("sensor.kitchen_air_fryer_cooking_status").state == "unavailable"
    device.response_code = 0
    await refresh(hass, entry)
    assert hass.states.get("sensor.kitchen_air_fryer_cooking_status").state == "standby"


async def test_action_rejects_unloaded_entry(hass, manager):
    entry = await setup(hass, manager)
    assert await hass.config_entries.async_unload(entry.entry_id)
    with pytest.raises(ServiceValidationError):
        await start(hass)


async def test_multiple_accounts_require_selection(hass, manager, device):
    entry = await setup(hass, manager)
    other = MockConfigEntry(
        domain=DOMAIN, data={**ENTRY_DATA, "email": "other@example.com"}, unique_id="other-account", version=3
    )
    other.add_to_hass(hass)
    with pytest.raises(ServiceValidationError):
        await start(hass)
    await start(hass, config_entry_id=entry.entry_id)
    assert len(device.commands) == 1


async def test_malformed_saved_response_is_read_failure(hass, manager, device):
    entry = await setup(hass, manager)
    device.malformed = True
    await refresh(hass, entry)
    assert hass.states.get("sensor.kitchen_air_fryer_cooking_status").state == "unavailable"


@pytest.mark.parametrize("error", [TimeoutError(), ValueError("invalid response")])
async def test_failed_read_during_command_marks_entities_unavailable(hass, manager, device, error):
    await setup(hass, manager)
    device.error = error
    with pytest.raises(HomeAssistantError) as exc:
        await start(hass)
    await hass.async_block_till_done()
    assert exc.value.translation_key == "command_failed"
    assert not device.commands
    for entity in hass.states.async_all():
        assert entity.state == "unavailable", entity.entity_id


async def test_keep_warm_sequence(hass, manager, device, freezer):
    device.status = "cooking"
    entry = await setup(hass, manager)
    event_id = "event.kitchen_air_fryer_finished"
    for raw, expected in [
        ("cookStop", "cooking_stop"),
        ("pullOut", "pull_out"),
        ("cooking", "cooking"),
        ("keeping", "keeping_warm"),
    ]:
        device.status = raw
        await refresh(hass, entry)
        assert hass.states.get("sensor.kitchen_air_fryer_cooking_status").state == expected
        assert hass.states.get(event_id).state == "unknown"
        if raw == "keeping":
            assert entry.runtime_data.data.cook_ends_at is None
            assert entry.runtime_data.data.preheat_ends_at is None
    device.status = "cookEnd"
    await refresh(hass, entry)
    event = hass.states.get(event_id)
    assert event.attributes["event_type"] == "cook_finished"
    freezer.tick(timedelta(seconds=15))
    await refresh(hass, entry)
    assert hass.states.get(event_id).state == event.state


@pytest.mark.parametrize(
    "phase,entity_id,remaining",
    [
        ("heating", "sensor.kitchen_air_fryer_preheat_ends_at", "preheat_remaining"),
        ("cooking", "sensor.kitchen_air_fryer_cook_ends_at", "cook_remaining"),
    ],
)
async def test_timestamp_poll_jitter(hass, manager, device, freezer, phase, entity_id, remaining):
    device.status = phase
    entry = await setup(hass, manager)
    original = hass.states.get(entity_id).state
    freezer.tick(timedelta(seconds=15))
    setattr(device, remaining, getattr(device, remaining) - 12)  # 3 second drift
    await refresh(hass, entry)
    assert hass.states.get(entity_id).state == original
    setattr(device, remaining, getattr(device, remaining) + 10)
    await refresh(hass, entry)
    assert hass.states.get(entity_id).state != original


@pytest.fixture(params=[1, 2], ids=["v0.2-0.3", "v0.4"])
def v1_entry(hass, request):
    """An entry saved by an earlier release (0.4.x already used version 2)."""
    legacy = {**ENTRY_DATA, "terminal_id": "test-terminal", "last_login": 0}
    entry = MockConfigEntry(domain=DOMAIN, data=legacy, version=request.param, unique_id=None)
    entry.add_to_hass(hass)
    return entry


async def test_migrate_v1_entry(hass, manager, v1_entry):
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    stale = [
        ("number", "cook_temperature"),
        ("number", "cook_time"),
        ("sensor", "cook_set_time"),
        ("sensor", "preheat_set_time"),
        ("sensor", "remaining_time"),
    ]
    stale_ids = [
        registry.async_get_or_create(platform, DOMAIN, f"test-cid_{key}", config_entry=v1_entry).entity_id
        for platform, key in stale
    ]
    retained = registry.async_get_or_create("sensor", DOMAIN, "test-cid_cook_status", config_entry=v1_entry)
    other = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, version=1, disabled_by=ConfigEntryDisabler.USER)
    other.add_to_hass(hass)
    other_entity = registry.async_get_or_create("number", DOMAIN, "other-cid_cook_time", config_entry=other)
    assert await hass.config_entries.async_setup(v1_entry.entry_id)
    await hass.async_block_till_done()
    assert v1_entry.version == 3
    assert v1_entry.unique_id == ENTRY_DATA["account_id"]
    assert {k: v1_entry.data[k] for k in ENTRY_DATA} == ENTRY_DATA
    assert not any(registry.async_get(entity_id) for entity_id in stale_ids)
    assert registry.async_get(retained.entity_id)
    assert registry.async_get(other_entity.entity_id)
    manager.login.assert_not_called()


@pytest.mark.parametrize("unique_id", [None, "test@example.com", "email:test@example.com"])
@pytest.mark.parametrize("stored_account", [True, False])
async def test_reauth_legacy_id(hass, manager, unique_id, stored_account):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "email": ENTRY_DATA["email"],
            "password": "old",
            **({"account_id": ENTRY_DATA["account_id"]} if stored_account else {}),
        },
        version=3,
        unique_id=unique_id,
    )
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"password": "new-password"})
    await hass.async_block_till_done()
    assert result["reason"] == "reauth_successful"
    assert entry.unique_id == ENTRY_DATA["account_id"]
