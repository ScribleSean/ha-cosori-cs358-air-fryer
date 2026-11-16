# Using the air fryer

For anyone at home. You need the Home Assistant app on your phone, or Apple Home if the fryer has been added there. You don't need a VeSync account.

## Say it

**Siri** (Apple Home):

| Say | What happens |
| --- | --- |
| "Turn on the air fryer" | Heats to the last temperature and stays hot, waiting for food |
| "Set the air fryer to 400 degrees" | Heats to 400 °F and stays hot |
| "Turn off the air fryer" | Stops anything it's doing |

**Assist** (the Home Assistant app: tap the Assist button and talk or type):

| Say | What happens |
| --- | --- |
| "Air fry for 12 minutes at 380" | Cooks now. Put the food in first. |
| "Air fry for 12 minutes" (while it's hot and waiting) | Cooks at the temperature it's holding |
| "Air fry chicken wings" | Picks a temperature and time, and says how to check they're cooked |
| "Keep it warm, I'm 10 minutes out" | Keeps food warm at 170 °F for 15 minutes (your time plus 5) |
| "Keep it warm" | Keeps food warm for 15 minutes |
| "Stop the air fryer" | Stops anything it's doing |
| "Is the air fryer done?" or "How long is left on the air fryer?" | Says what it's doing and the time left |
| "Start the air fryer" | Starts a cook that was waiting for its preheat to finish |

Assist answers out loud with what it set. If the fryer can't use the exact number, it says so: 375 °F becomes 370 °F, for example. If something is missing, like the temperature, it asks and starts nothing; say the whole thing again.

If you give two times for one food, like "fries 12 minutes and 7 minutes", it asks which one you meant and starts nothing. To add time, say "then 5 more minutes".

Your home may have Assist set to check first. Then Assist tells you the plan and waits: say "yes, air fryer" within a minute, in the same conversation, to start, or "no, air fryer" to cancel. Stopping and asking how it's doing never wait. Only Assist checks first: the tile, the buttons and Apple Home still turn the fryer on right away.

To use Assist sentences through Siri, see [the Siri Shortcut](../examples/siri.md).

## Tap it

In the Home Assistant app, the air fryer tile is in the Kitchen area.

- **Tap the icon** to turn it on (heat and hold) or off.
- **− and +** change the temperature.
- **Start** cooks for the **Cook time** shown. **Stop** ends anything.
- **Keep warm** holds food at 170 °F for 15 minutes.

## Good to know

- "On" means hot and waiting. It never starts a cook timer by itself. If nothing goes in, it turns itself off after 30 minutes.
- Keep warm never runs longer than 60 minutes.
- Stop works whatever started the fryer.
- Pull the basket out and the fryer pauses; push it back in to carry on.
- Times and temperatures for food are starting points. For meat, check the inside temperature: 165 °F for chicken, 160 °F for ground meat, 145 °F for steaks and fish.

## Get a notification when it's done

Ask whoever runs Home Assistant to add the **Air fryer finished** automation for your phone. It sends "The food is done." when a cook ends, and "Preheated. Put the food in." when a preheat ends.

## If it doesn't respond

- Assist says the fryer isn't connected, or the tile shows **Unavailable**: check the fryer is plugged in and on Wi-Fi, then wait a minute.
- Assist says the sign-in has expired: ask whoever runs Home Assistant to sign in again.
- It says the fryer is busy: say "Stop the air fryer" first.
