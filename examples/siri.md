# Siri Shortcut

On an iPhone with the Home Assistant companion app connected to your Home Assistant:

1. Create a Shortcut named “Air fry”. Ask for the food name (text), temperature (number), and minutes (number).
2. Show an alert: “Air fry [food] at [temperature] °F for [minutes] min?” with Cancel enabled.
3. After the alert, add the Home Assistant “Call service” action for `cosori_fryer.cook`, with the temperature and minutes answers, `temperature_unit: °F`, and `preheat: true`.
4. Run “Hey Siri, air fry”. Confirm to call the action; cancel to exit before any service call.

Verify cancellation sends no action before using the Shortcut. Food is a confirmation label and is not sent to the integration. Companion app action labels may vary by version.
