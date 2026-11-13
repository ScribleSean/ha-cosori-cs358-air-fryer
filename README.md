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
| Air fryer | Off or On, the temperature (170–400 °F in 10 °F steps), and what it's doing: Preheating, Preheated, Cooking, Paused, Finished. Turning it on preheats to the set temperature and holds until you start a cook or stop it. A hold with no cook stops itself after 30 minutes. |
| Preset | Manual or one of the fryer's programs (French fries, Chicken, Steak and more). A program fills in its temperature and time. |
| Cook time | Minutes for the next cook, 1–60 |
| Start, Preheat, Stop | Start cooks now at the set temperature and cook time, or continues after a preheat. Preheat heats to the set temperature and holds, like turning it on. Stop ends anything. |
| Keep warm | Holds food at 170 °F for 15 minutes. Replaces a hold; refused while cooking. |
| Time left | Minutes left in the preheat or the cook |
| Preheated | On while the fryer is hot and waiting for the food |
| Cooking status, Preheat ends at, Cook ends at | For automations |
| Finished | An event when a preheat or a cook finishes |

Changing the temperature, preset or cook time sends nothing to the fryer until you start it, except during a hold, where a new temperature moves the hold. The temperature can't be changed mid-cook; stop first. The integration sends no notifications. The Current temperature sensor is off by default, because this fryer's firmware always reports 0.

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

- "Turn on the air fryer" (preheats to the set temperature and holds ready)
- "Set the air fryer to 400 degrees" (preheats to 400 and holds; on the dashboard, a new temperature while off is only saved)
- "Turn off the air fryer" (stops anything, including keep warm)
- "What's the air fryer set to?"

Apple Home sees only a thermostat, so cooking for a time and keeping warm are on the dashboard buttons, or the `cook` and `keep_warm` actions below. Turning it on from Apple Home or Siri starts heating right away, with no confirmation. Apple Home shows a room temperature of about 70 °F for the fryer, because HomeKit requires one and the fryer doesn't report it.

## Cook or keep warm from an automation

Use the **Cosori air fryer: Cook** action from a script, an automation, or **Developer tools → Actions**:

```yaml
action: cosori_fryer.cook
data:
  temperature: 400
  temperature_unit: "°F"
  minutes: 12
  preheat: true
```

With `preheat: true` the fryer heats to the temperature, shows **Preheated**, and waits. Put the food in and press **Start**: it cooks at the same temperature for the time you gave, with nothing to enter again. Without `preheat` it cooks at once. A cook replaces a hold, and is refused while another cook runs or waits.

Temperature moves to the nearest 10 °F step from 170 to 400 °F, so 375 °F sends 370 °F, 405 °F sends 400 °F and 180 °C sends 360 °F; anything more than 5 °F outside that range is refused. Time is 1–60 minutes and a fraction rounds up; over 60 is refused. Without `temperature_unit`, the Home Assistant unit is used.

To keep food warm at 170 °F:

```yaml
action: cosori_fryer.keep_warm
data:
  minutes: 20
```

Without `minutes` it keeps warm for 15 minutes; over 60 is cut to 60.

For a Siri Shortcut that asks before it starts, see [examples/siri.md](examples/siri.md).

## Limits

- One CS358-AF per VeSync account, and the VeSync cloud must be reachable.
- Status updates every 15 seconds while running and every 60 seconds when idle.
- The fryer chooses how long a preheat takes.
- A program paused on the fryer can only be resumed on the fryer.
- Upgrading from 0.4 or older asks you to sign in once.
- Upgrading from 0.7: turning the fryer on now preheats and holds instead of starting a cook. Use Start or the Cook action to cook; older automations keep working.
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
