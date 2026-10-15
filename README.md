# COSORI CS358-AF air fryer for Home Assistant

A custom integration for the COSORI Pro II 5.8 Qt Smart Air Fryer (CS358-AF), controlled through the VeSync cloud.

Home Assistant's built-in VeSync integration does not support this model yet. Support has been proposed upstream in [webdjoe/pyvesync#557](https://github.com/webdjoe/pyvesync/pull/557). Use this until that lands.

Unofficial. Not affiliated with COSORI, VeSync or Home Assistant.

## What you get

| Kind | Entities |
| --- | --- |
| Buttons | Preheat, Air fry, Stop |
| Settings | Cook temperature (170 to 400 °F, steps of 10), Cook time (1 to 60 min) |
| Sensors | Cooking status, Set temperature, Time remaining |

- **Preheat** heats to the cook temperature.
- **Air fry** cooks at the cook temperature for the cook time, from idle or once preheat has finished.
- **Stop** ends a preheat or a cook.

A button is unavailable whenever the fryer can't do that right now, for example Preheat while it's cooking or anything while the basket is out.

**Time remaining** counts down the phase that's running: the preheat while preheating, then the cook.

The fryer is checked every 30 seconds while it's busy and every minute when idle.

Tested on a physical US unit. Not supported: pause and resume from the cloud (the fryer pauses itself when the basket is pulled), starting presets, and the current temperature, which the fryer always reports as 0.

## Actions

| Action | Fields |
| --- | --- |
| `cosori_fryer.preheat` | `temperature` |
| `cosori_fryer.air_fry` | `temperature`, `minutes` |
| `cosori_fryer.stop` | none |

Temperatures must be multiples of 10. An action the fryer can't do in its current state is refused with the reason.

## Events

`cosori_fryer_preheat_done` and `cosori_fryer_cook_done` fire when preheat and cooking finish. `examples/automations.yaml` turns them into phone notifications.

## Before you install

- **This starts a heating appliance remotely.** Only start it when the basket is in and the fryer is clear.
- **VeSync keeps one session per account.** Anything else signed in to the same account (the VeSync phone app, another integration, a script) signs this out, and the other way round. When that happens the integration waits a few minutes before signing in again, and its entities are unavailable meanwhile. Use a VeSync account of its own, with the fryer shared to it, to avoid this.
- **Don't run it next to the built-in VeSync integration on the same account,** for the same reason.

## Install

With [HACS](https://hacs.xyz): add this repository as a custom repository of type Integration, install it, and restart Home Assistant.

By hand: copy `custom_components/cosori_fryer` into the `custom_components` folder of your Home Assistant config and restart.

Then go to Settings, Devices & services, Add integration, "COSORI Air Fryer (CS358)", and sign in. If VeSync later rejects the password, Home Assistant asks you to sign in again.

## Examples

`examples/` has optional YAML: notification automations, scripts for Assist, and voice sentences with spoken replies.

## Tests

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

## License

MIT
