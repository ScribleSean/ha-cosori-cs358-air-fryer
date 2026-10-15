# COSORI CS358-AF air fryer for Home Assistant

A custom integration for the COSORI Pro II 5.8 Qt Smart Air Fryer (CS358-AF), controlled through the VeSync cloud.

Home Assistant's built-in VeSync integration does not support this model yet. Support has been proposed upstream in [webdjoe/pyvesync#557](https://github.com/webdjoe/pyvesync/pull/557). Use this until that lands.

Unofficial. Not affiliated with COSORI, VeSync or Home Assistant.

## What you get

| Kind | Entities |
| --- | --- |
| Buttons | Preheat, Start, Stop |
| Settings | Cooking temperature (170 to 400 °F, steps of 10), Cooking time (1 to 60 min) |
| Sensors | Cooking status, Cooking set temperature, Cooking set time, Preheating set time, Remaining time |

The buttons work like the ones on the fryer:

- **Preheat** heats to the cooking temperature, with the cooking time queued behind it.
- **Start** cooks at the cooking temperature for the cooking time. Once preheating has finished, it starts the queued cook instead.
- **Stop** ends a preheat or a cook.

A button is unavailable whenever the fryer can't do that right now, for example Preheat while it's cooking, or anything while the drawer is out.

Sensor names and Cooking status states follow Home Assistant's built-in VeSync integration. **Remaining time** counts down the phase that's running: the preheat while preheating, then the cook.

The fryer is checked every 30 seconds while it's busy and every minute in standby.

Tested on a physical US unit. Not supported: pause and resume from the cloud (the fryer pauses itself when the drawer is pulled), starting presets, and the current temperature, which the fryer always reports as 0.

## Action

`cosori_fryer.start_cooking` starts a manual program with `temperature` (multiples of 10), `cooking_time` in minutes, and optionally `preheat: true`. It works when the fryer is in standby or has finished cooking; otherwise it's refused with the reason.

If more than one VeSync account is configured, select the account using `config_entry_id`. Without it, the action is refused rather than choosing a fryer. The field is optional when only one account is configured.

## Events

`cosori_fryer_preheat_done` fires when preheating finishes, and `cosori_fryer_cook_done` when a cook runs to the end. Stopping a cook early fires nothing. Both carry the `device_id`. `examples/automations.yaml` turns them into phone notifications.

## Before you install

- **This starts a heating appliance remotely.** Only start it when the basket is in and the fryer is clear.
- **VeSync keeps one session per account.** Anything else signed in to the same account (the VeSync phone app, another integration, a script) signs this out, and the other way round. When that happens the integration waits a few minutes before signing in again, and the buttons and sensors are unavailable meanwhile. Use a VeSync account of its own, with the fryer shared to it, to avoid this.
- **Don't run it next to the built-in VeSync integration on the same account,** for the same reason.

## Install

With [HACS](https://hacs.xyz): add this repository as a custom repository of type Integration, install it, and restart Home Assistant.

By hand: copy `custom_components/cosori_fryer` into the `custom_components` folder of your Home Assistant config and restart.

Needs Home Assistant 2025.2 or later. One fryer per VeSync account.

Then go to Settings, Devices & services, Add integration, "COSORI Air Fryer (CS358)", and sign in. If VeSync later rejects the password, Home Assistant asks you to sign in again.

## Examples

Entity IDs come from the fryer's name in the VeSync app; the examples assume it's called "Air Fryer" (`button.air_fryer_start` and so on).

`examples/` has optional YAML: notification automations, scripts for Assist, and voice sentences with spoken replies.

## Tests

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

## License

MIT
