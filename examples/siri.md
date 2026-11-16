# Siri Shortcut

Apple Home already handles "turn on", "set to" and "turn off". This Shortcut adds everything Assist understands, like "air fry for 12 minutes at 380" or "keep it warm, I'm 10 minutes out", through Siri.

On an iPhone with the Home Assistant app connected to your Home Assistant:

1. Create a Shortcut named "Air fryer".
2. Add **Dictate Text**.
3. Add the Home Assistant **Assist** action, with the dictated text as its input.
4. Add **Speak Text** with the Assist result.
5. Say "Hey Siri, air fryer", then the request: "air fry for 12 minutes at 380".

Siri reads back what Assist set, including any rounding. A request that is missing the temperature or the time starts nothing; Assist says what to add. Home Assistant app action names can differ between versions.

## With a confirmation first

To confirm every cook before it starts, use these steps instead:

1. Ask for the temperature (number) and the minutes (number).
2. Show an alert: "Air fry at [temperature] °F for [minutes] min?" with Cancel enabled.
3. After the alert, add the Home Assistant action that calls `cosori_fryer.cook`, with the two answers, `temperature_unit: °F`, and `preheat: true`.

Cancel exits before anything is sent.
