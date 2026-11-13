# Cosori air fryer

Home Assistant integration for the Cosori Pro II 5.8 Qt (CS358-AF). The fryer shows up like a thermostat: turn it on or off, set the temperature, and see what it's doing and how long is left. It works from a dashboard tile, Apple Home and Siri.

## Install

Takes about two minutes. You need [HACS](https://hacs.xyz) and Home Assistant 2026.10.4 or newer.

1. Open **HACS**, select the three dots at the top right, then **Custom repositories**.
2. Paste `https://github.com/ScribleSean/ha-cosori-cs358-air-fryer`, choose the type **Integration**, and select **Add**.
3. Search HACS for **Cosori air fryer**, open it, and select **Download**.
4. Restart Home Assistant: **Settings → System**, the power icon at the top right, **Restart Home Assistant**.
5. Go to **Settings → Devices & services → Add integration**, pick **Cosori air fryer**, and enter a VeSync email and password.

A new install appears as one device in the **Kitchen** area. Nothing else needs configuring.

### Which VeSync account

VeSync allows one sign-in per account, so signing in here signs the phone app out of that account. To keep both working, create a second VeSync account, share the fryer with it from the VeSync app, and give Home Assistant the second account. If the session ever expires, Home Assistant asks once for the password; it never signs in again on its own.

## What you get

| Entity | What it does |
| --- | --- |
| Air fryer | Off or On, the temperature (170–400 °F in 10 °F steps), and what it's doing: Preheating, Preheated, Cooking, Paused, Finished. Turning it on cooks at that temperature for the cook time. |
| Preset | Manual or one of the fryer's programs (French fries, Chicken, Steak and more). A program fills in its temperature and time. |
| Cook time | Minutes for the next cook, 1–60 |
| Start, Preheat, Stop | Start cooks now, or continues after a preheat. Preheat heats first and waits for the food. Stop ends either. |
| Time left | Minutes left in the preheat or the cook |
| Preheated | On while the fryer is hot and waiting for the food |
| Cooking status, Preheat ends at, Cook ends at | For automations |
| Finished | An event when a preheat or a cook finishes |

Changing the temperature, preset or cook time sends nothing to the fryer until you start it. The temperature can't be changed mid-cook; stop first. The integration sends no notifications. The Current temperature sensor is off by default, because this fryer's firmware always reports 0.

## Add to your dashboard

If you use the default dashboard, the fryer is already under Kitchen. To add it to your own sections dashboard, edit the Kitchen section, add a card, choose **Manual**, and paste:

```yaml
type: tile
entity: climate.kitchen_air_fryer
state_content:
  - cook_status
  - time_left
icon_tap_action:
  action: toggle
features_position: inline
features:
  - type: target-temperature
grid_options:
  columns: 12
```

It reads like "Cooking · 12 min" with − and + for the temperature. Tap the icon to start or stop. If your entity ID differs, find it under **Settings → Devices & services → Cosori air fryer**.

## Show in Apple Home and Siri

This uses Home Assistant's built-in HomeKit Bridge. If your home is already bridged to Apple Home, add the fryer to that bridge:

1. Go to **Settings → Devices & services → HomeKit Bridge** and select **Configure** on your bridge.
2. Keep the mode and inclusion settings you use, add **Climate** to the domains if it isn't there, and select **Air fryer** in the entity list. Select **Submit** through the remaining steps.
3. The fryer appears in Apple Home as a thermostat. Open its settings there and set the room to **Kitchen**.

Without a bridge yet, add the **HomeKit Bridge** integration, choose the **Climate** domain, and scan the pairing code from the notification with the Home app.

Siri then understands:

- "Turn on the air fryer" (cooks at the set temperature for the set time)
- "Set the air fryer to 360 degrees"
- "Turn off the air fryer"
- "What's the air fryer set to?"

Turning it on from Apple Home or Siri starts heating right away, with no confirmation. Apple Home shows a room temperature of about 70 °F for the fryer, because HomeKit requires one and the fryer doesn't report it.

## Start a cook from an automation

Use the **Cosori air fryer: Start cook** action from a script, an automation, or **Developer tools → Actions**:

```yaml
action: cosori_fryer.start_cook
data:
  temperature: 400
  temperature_unit: "°F"
  minutes: 12
  preheat: true
```

With `preheat: true` the fryer heats to the temperature, shows **Preheated**, and waits. Put the food in and press **Start**: it cooks at the same temperature for the time you gave, with nothing to enter again. With `preheat: false` it cooks at once.

Temperature is 170–400 °F in 10 °F steps, and time is 1–60 whole minutes. A Celsius value is rounded to the nearest 10 °F step, so 180 °C sends 360 °F. Without `temperature_unit`, the Home Assistant unit is used.

For a Siri Shortcut that asks before it starts, see [examples/siri.md](examples/siri.md).

## Limits

- One CS358-AF per VeSync account, and the VeSync cloud must be reachable.
- Status updates every 15 seconds while running and every 60 seconds when idle.
- The fryer chooses how long a preheat takes.
- A program paused on the fryer can only be resumed on the fryer.
- Upgrading from 0.4 or older asks you to sign in once.
- Upgrading from 0.6 keeps your entity IDs and area. The Start cooking button is now Start, and it also starts a cook when the fryer is off.
- A copy of pyvesync is bundled until a pyvesync release supports this model.

## Remove

Delete the integration under **Settings → Devices & services**, remove it in HACS, and restart Home Assistant.

## Development

On Linux with Python 3.14.2 or newer:

```sh
python3.14 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```
