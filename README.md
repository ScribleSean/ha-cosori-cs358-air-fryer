# COSORI CS358-AF air fryer for Home Assistant

A custom integration for the COSORI Pro II 5.8 Qt Smart Air Fryer (CS358-AF), through the VeSync cloud.

Home Assistant's built-in VeSync integration doesn't support this model yet; support is proposed upstream in [webdjoe/pyvesync#557](https://github.com/webdjoe/pyvesync/pull/557). Use this until the built-in integration supports the CS358-AF.

Unofficial. Not affiliated with COSORI, VeSync or Home Assistant.

## Entities

| Kind | Entities |
| --- | --- |
| Buttons | Preheat, Start, Stop |
| Settings | Cooking temperature (170 to 400 °F, steps of 10), Cooking time (1 to 60 min) |
| Sensors | Cooking status, Cooking set temperature, Cooking set time, Preheating set time, Remaining time |

The buttons work like the ones on the fryer:

- **Preheat** heats to the cooking temperature, with a cook of the cooking time queued behind it.
- **Start** cooks at the cooking temperature for the cooking time. Once preheating has finished, it starts the queued cook instead.
- **Stop** ends a preheat or a cook.

Cooking temperature and Cooking time are the values the next Preheat or Start uses. Changing them sends nothing to the fryer.

A button is unavailable whenever the fryer can't do it, for example Preheat while cooking, or anything while the drawer is out.

Sensor names and Cooking status states match the built-in VeSync integration. Remaining time counts down the phase that's running: the preheat, then the cook.

The fryer is read every 30 seconds while busy and every minute in standby.

Tested on a physical US unit. Not supported: cloud pause and resume (pulling the drawer pauses the fryer), presets, and current temperature, which the fryer always reports as 0.

## Action

`cosori_fryer.start_cooking` starts a manual program: `temperature` (multiples of 10), `cooking_time` in minutes, and optionally `preheat: true`. It's refused, with the reason, unless the fryer is in standby or finished. With more than one VeSync account set up, pass `config_entry_id`.

## Events

`cosori_fryer_preheat_done` and `cosori_fryer_cook_done` fire when a preheat or a cook is seen running to the end, including after a pause or a pulled drawer. A stopped cook fires nothing, and a cycle that ended while Home Assistant wasn't running isn't announced. Both carry `device_id`. `examples/automations.yaml` turns them into phone notifications.

## Before you install

- **This starts a heating appliance remotely.** Only start it with the drawer in and the fryer clear.
- **VeSync allows one session per account.** Anything else signed in to the account, including the VeSync app and the built-in VeSync integration, signs this out and the other way round. Logins are at least five minutes apart, and the fryer's entities are unavailable until it's signed in again. Give it a VeSync account of its own, with the fryer shared to it.
- **One fryer per account,** and Home Assistant 2025.3 or later.

## Install

With [HACS](https://hacs.xyz): add this repository as a custom repository of type Integration, install it, and restart Home Assistant. By hand: copy `custom_components/cosori_fryer` into your config's `custom_components` folder and restart.

Then add "COSORI Air Fryer (CS358)" under Settings, Devices & services. If VeSync later rejects the password, Home Assistant asks you to sign in again.

## Examples

`examples/` has optional YAML: notification automations, scripts for Assist, and voice sentences with replies. Entity IDs follow the fryer's name in the VeSync app; the examples assume "Air Fryer".

## Tests

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

## License

MIT
