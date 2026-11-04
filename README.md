# Cosori air fryer

Home Assistant custom integration for the Cosori Pro II CS358-AF, using pyvesync and the VeSync cloud.

In HACS, add this repository as a custom repository with category **Integration**, install it, and restart Home Assistant. Under **Settings → Devices & services → Add integration**, select **Cosori air fryer** and enter your VeSync email and password.

VeSync permits one session per account. Signing in with the same account in the phone app can expire Home Assistant's session. Use a second VeSync account and share the fryer with it from the primary account. Home Assistant asks you to sign in again when its session expires; it does not automatically sign in again.

Entities: cooking status, current temperature, target temperature, preheat end and cook end timestamps, Stop, Start cooking, and a Finished event with `preheat_finished` and `cook_finished` types. Timestamps are unknown while paused or staged. A relative-time dashboard display counts down between polls; see [dashboard example](examples/dashboard.yaml). Start cooking continues the queued cook only after preheating. The example shows that button conditionally.

Use the `cosori_fryer.start_cook` action. All three cook fields are required:

```yaml
action: cosori_fryer.start_cook
data:
  temperature: 350
  temperature_unit: "°F"
  minutes: 10
  preheat: true
```

Without `temperature_unit`, temperature uses Home Assistant's configured unit. Celsius values are rounded to the nearest 10 °F, then checked against 170–400 °F. For example, 180 °C sends 360 °F. With debug logging on, the log records the Fahrenheit setting sent. Time is 1–60 whole minutes. With multiple accounts, supply `config_entry_id`.

Limits: one CS358-AF per account, cloud access required, 60-second idle and 15-second running polls. The firmware chooses preheat duration; no resume command is available for paused programs. Finish events require observing the running phase. A private copy of pyvesync is bundled until upstream supports this model. Existing installations migrate automatically; expect one request to sign in again after upgrading. The integration registers no Assist phrase that starts heating. Siri setup and confirmation instructions are in [the Shortcut guide](examples/siri.md).

Development on Ubuntu with Python 3.14.2 or newer:

```sh
python3.14 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

To remove: delete the integration under **Settings → Devices & services**, uninstall it in HACS, then restart Home Assistant.
