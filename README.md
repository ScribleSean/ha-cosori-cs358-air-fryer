# Cosori air fryer

Home Assistant integration for the Cosori Pro II 5.8 Qt (CS358-AF). Start a cook, preheat first if you like, see when it ends, and stop it.

## Install

Takes about two minutes. You need [HACS](https://hacs.xyz) and Home Assistant 2026.10.4 or newer.

1. Open **HACS**, select the three dots at the top right, then **Custom repositories**.
2. Paste `https://github.com/ScribleSean/ha-cosori-cs358-air-fryer`, choose the type **Integration**, and select **Add**.
3. Search HACS for **Cosori air fryer**, open it, and select **Download**.
4. Restart Home Assistant: **Settings → System**, the power icon at the top right, **Restart Home Assistant**.
5. Go to **Settings → Devices & services → Add integration**, pick **Cosori air fryer**, and enter a VeSync email and password.

The fryer appears as one device. Nothing else needs configuring.

### Which VeSync account

VeSync allows one sign-in per account, so signing in here signs the phone app out of that account. To keep both working, create a second VeSync account, share the fryer with it from the VeSync app, and give Home Assistant the second account. If the session ever expires, Home Assistant asks once for the password; it never signs in again on its own.

## What you get

| Entity | Shows |
| --- | --- |
| Cooking status | Standby, Preheating, Preheated, Cooking, Finished and the paused states |
| Target temperature | The temperature the fryer is set to |
| Preheat ends at, Cook ends at | When the running phase ends; a dashboard can show these as a countdown |
| Stop | Ends the preheat or cook |
| Start cooking | After a preheat, starts the cook with the temperature and time already chosen |
| Finished | An event when a preheat or a cook finishes, for automations |

The integration sends no notifications. The Current temperature sensor is off by default, because this fryer's firmware always reports 0.

## Start a cook

Use the **Cosori air fryer: Start cook** action from a dashboard button, an automation, or **Developer tools → Actions**:

```yaml
action: cosori_fryer.start_cook
data:
  temperature: 400
  temperature_unit: "°F"
  minutes: 12
  preheat: true
```

With `preheat: true` the fryer heats to the temperature, shows **Preheated**, and waits. Put the food in and press **Start cooking**: it cooks at the same temperature for the time you gave, with nothing to enter again. With `preheat: false` it cooks at once.

Temperature is 170–400 °F in 10 °F steps, and time is 1–60 whole minutes. A Celsius value is rounded to the nearest 10 °F step, so 180 °C sends 360 °F. Without `temperature_unit`, the Home Assistant unit is used.

A ready-made card is in [examples/dashboard.yaml](examples/dashboard.yaml), and a Siri Shortcut in [examples/siri.md](examples/siri.md).

## Limits

- One CS358-AF per VeSync account, and the VeSync cloud must be reachable.
- Status updates every 15 seconds while running and every 60 seconds when idle.
- The fryer chooses how long a preheat takes.
- A program paused on the fryer can only be resumed on the fryer.
- Upgrading from 0.4 or older asks you to sign in once.
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
