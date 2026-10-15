# COSORI CS358-AF air fryer for Home Assistant

A custom integration for the COSORI Pro II 5.8 Qt Smart Air Fryer (CS358-AF), controlled through the VeSync cloud.

Home Assistant's built-in VeSync integration does not support this model yet. Support has been proposed upstream in [webdjoe/pyvesync#557](https://github.com/webdjoe/pyvesync/pull/557). This integration is for use until that lands.

Unofficial. Not affiliated with COSORI, VeSync or Home Assistant.

## What it does

Tested on a physical US unit.

| Action | Notes |
| --- | --- |
| Status | state, target temperature, time left |
| Preheat, then cook | the cook temperature and time are kept through preheat |
| Start after preheat | also after pulling the basket to add food |
| Cook without preheat | |
| Stop | while staged, preheating or cooking |

Not supported: pause and resume from the cloud (the fryer pauses itself when the basket is pulled), starting preset programs, and a current temperature reading, which the fryer always reports as 0.

Limits: 170 to 400 °F, 1 to 60 minutes.

## Before you install

- **This starts a heating appliance remotely.** Only start it when the basket is in place and the fryer is clear. The automation in `examples/` that starts cooking on its own is off by default.
- **VeSync keeps one session per account.** Signing in here signs the VeSync phone app out, and signing in on the phone signs this out. A second VeSync account with the fryer shared to it avoids the conflict.
- **Do not run it next to the built-in VeSync integration on the same account.** They would sign each other out.

## Install

With [HACS](https://hacs.xyz): add this repository as a custom repository of type Integration, install it, and restart Home Assistant.

By hand: copy `custom_components/cosori_fryer` into the `custom_components` folder of your Home Assistant config and restart.

Then go to Settings, Devices & services, Add integration, "COSORI Air Fryer (CS358)", and sign in with the VeSync account that owns the fryer.

## Entities and services

| Kind | Names |
| --- | --- |
| Services | `cosori_fryer.preheat`, `cosori_fryer.cook` (both take `temp`, `minutes`), `cosori_fryer.start`, `cosori_fryer.stop` |
| Buttons | Preheat, Start cooking, Stop |
| Numbers | Target temperature, Cook minutes (used by the Preheat button) |
| Sensors | Status, Target temperature, Time remaining (for the phase that is running) |
| Events | `cosori_fryer_preheat_done`, `cosori_fryer_cook_done` |

Every command reads the fryer's state first and is refused, with the reason, if the state does not allow it.

Status is read every 30 seconds while the fryer is busy and every minute when idle.

To get a phone notification when preheat or cooking ends, open the integration's options and enter a notify service name, for example `mobile_app_my_phone`.

## Examples

`examples/` has optional YAML you can copy into your own config: scripts, voice sentences with spoken replies, and the off-by-default automation that starts cooking when the basket goes back in after preheat.

## Tests

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

## License

MIT
