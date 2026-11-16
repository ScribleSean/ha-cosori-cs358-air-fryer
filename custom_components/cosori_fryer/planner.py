"""Plan what an air fryer should do: cook, preheat (heat and hold), keep warm,
stop, or report status.

Pure and dependency-free, so one file can ship in more than one project.
Nothing here talks to a device. `plan()` takes an `Intent` (or a plain
sentence, read by `parse()`) and returns a `Plan`: the setting to send, plus
every rounding or assumption stated in words. Values the fryer can't run raise
`PlanError`; a request that is genuinely ambiguous returns a plan with a
`question` and must not be run until it is answered.

Device facts (Cosori CS358-AF):
- Temperatures are 170-400 °F in 10° steps; cook times are 1-60 minutes.
- Keep warm is a plain cook at the lowest setting, 170 °F. Hot food held
  above 140 °F stays out of the bacterial danger zone (USDA FSIS).
- A preheat that holds with no food is stopped after HOLD_LIMIT_MIN.

Food table sources:
- Temperatures and times are common air-fryer starting points, cross-checked
  against the CS358-AF's own program defaults (Chicken 380 °F, Frozen 380 °F,
  Steak 400 °F, French fries 380 °F) and typical frozen-package directions.
- Doneness checks are the USDA FSIS safe minimum internal temperatures:
  poultry and leftovers 165 °F; ground meat and sausage 160 °F; steaks, chops
  and fish 145 °F. Times are starting points; the check is the real test.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

MIN_TEMP_F = 170
MAX_TEMP_F = 400
TEMP_STEP_F = 10
ALLOWED_TEMPS_F = tuple(range(MIN_TEMP_F, MAX_TEMP_F + 1, TEMP_STEP_F))
MIN_MINUTES = 1
MAX_MINUTES = 60
# The fryer's manual-program default, used when a preheat names no temperature.
DEFAULT_TEMP_F = 350
KEEP_WARM_TEMP_F = 170
KEEP_WARM_DEFAULT_MIN = 15
KEEP_WARM_MAX_MIN = 60
# "I'm N minutes out" keeps food warm for N plus this.
ARRIVAL_BUFFER_MIN = 5
# A preheat holding with no food is stopped after this many minutes.
HOLD_LIMIT_MIN = 30
# The CS358-AF has no preheat-only command: a hold is a preheat with this
# short placeholder cook step behind it, which is how a hold is recognised.
HOLD_STEP_SECONDS = 60

INTENTS = ("cook", "preheat", "keep_warm", "stop", "status")


class PlanError(ValueError):
    """A request the fryer can't or shouldn't run, in plain words."""


def _fmt(number: float) -> str:
    return str(int(number)) if number == int(number) else f"{number:g}"


# --- Temperatures --------------------------------------------------------


@dataclass(frozen=True)
class Temp:
    asked: float
    unit: str  # "F" or "C"
    setting: int

    @property
    def note(self) -> str:
        """One line when the asked value isn't a fryer setting, else ''."""
        if self.unit == "C":
            f = self.asked * 9 / 5 + 32
            return (
                f"{_fmt(self.asked)}°C is {_fmt(round(f))}°F; "
                f"using {self.setting}°F (nearest 10°)."
            )
        if self.asked == self.setting:
            return ""
        return (
            f"{_fmt(self.asked)}°F isn't a setting; using {self.setting}°F "
            "(nearest 10°, halfway goes lower)."
        )


_TEMP_VALUE = re.compile(
    r"^\s*(-?\d+(?:\.\d+)?)\s*(?:°|º|deg\.?|degrees?)?\s*"
    r"(f|fahrenheit|c|celsius)?\.?\s*$",
    re.IGNORECASE,
)


def parse_temp(value: object) -> Temp:
    """°F (or °C, converted) from 375, "375", "375F", "375 °F" or "190C".

    Moves to the nearest setting; halfway goes lower (375 -> 370). A value more
    than half a step outside 170-400 °F is refused, not clamped.
    """
    if isinstance(value, bool) or value is None:
        raise PlanError('temperature must be a number in °F, like 375 or "375°F".')
    if isinstance(value, int | float):
        number, unit = float(value), "F"
    elif match := _TEMP_VALUE.match(str(value)):
        number = float(match[1])
        unit = "C" if (match[2] or "f").lower().startswith("c") else "F"
    else:
        raise PlanError(f'couldn\'t read {value!r} as °F; use 375 or "375°F".')
    if not math.isfinite(number):
        raise PlanError('temperature must be a number in °F, like 375 or "375°F".')
    fahrenheit = number * 9 / 5 + 32 if unit == "C" else number
    setting = min(ALLOWED_TEMPS_F, key=lambda t: (abs(t - fahrenheit), t))
    if abs(setting - fahrenheit) > TEMP_STEP_F / 2:
        shown = f"{_fmt(number)}°C" if unit == "C" else f"{_fmt(number)}°F"
        raise PlanError(f"{shown} is outside the fryer's {MIN_TEMP_F}–{MAX_TEMP_F}°F.")
    return Temp(number, unit, setting)


# --- Minutes -------------------------------------------------------------

_MINUTES_VALUE = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*(m|mins?|minutes?|h|hrs?|hours?|s|secs?|seconds?)?\.?\s*$",
    re.IGNORECASE,
)


def read_minutes(value: object) -> float:
    """Minutes (maybe fractional) from 12, "12", "12 min", "1 hour" or "90 sec"."""
    if isinstance(value, bool) or value is None:
        raise PlanError('minutes must be a number, like 12 or "12 min".')
    if isinstance(value, int | float):
        number, unit = float(value), ""
    elif match := _MINUTES_VALUE.match(_words_to_digits(str(value).lower())):
        number, unit = float(match[1]), (match[2] or "").lower()
    else:
        raise PlanError(f'couldn\'t read {value!r} as minutes; use 12 or "12 min".')
    if not math.isfinite(number):
        raise PlanError('minutes must be a number, like 12 or "12 min".')
    if unit.startswith("h"):
        number *= 60
    elif unit.startswith("s"):
        number /= 60
    return number


def whole_minutes(number: float, limit: int = MAX_MINUTES, cap: bool = False):
    """(minutes, note): rounded to whole minutes and checked against the limits.

    Over the limit is refused, or capped with a note when `cap` is set.
    """
    note = ""
    whole = max(1, math.ceil(number - 1e-9)) if number > 0 else 0
    if whole != number and number > 0:
        note = f"{_fmt(round(number, 2))} min rounds up to {whole} min."
    if whole < MIN_MINUTES:
        raise PlanError(
            f"{_fmt(number)} min is too short; use {MIN_MINUTES}–{limit} min."
        )
    if whole > limit:
        if not cap:
            raise PlanError(
                f"{_fmt(number)} min is over the fryer's {limit}-minute limit. "
                "Split it into two cooks and check in between."
            )
        note = f"{_fmt(number)} min is over the {limit}-minute limit; capped at {limit}."
        whole = limit
    return whole, note


def parse_minutes(value: object) -> int:
    """Whole minutes for a cook, 1-60. Rounds fractions up."""
    return whole_minutes(read_minutes(value))[0]


# --- Food table ----------------------------------------------------------

POULTRY = "165°F inside (USDA)"
GROUND = "160°F inside (USDA)"
WHOLE_CUT = "145°F inside (USDA)"
FISH = "145°F inside, flakes with a fork (USDA)"
LEFTOVER = "165°F inside (USDA, leftovers)"
HOT = "hot all the way through"
STEAMING = "165°F inside, steaming hot (USDA)"
SHELLFISH = "pearly and opaque (USDA)"


@dataclass(frozen=True)
class Food:
    name: str
    words: str  # regex; any hit names this food
    temp_f: int
    minutes: int
    preheat: bool = False  # does better going into a hot basket
    shake: bool = False  # shake or flip halfway
    check: str = HOT
    frozen: bool | None = None  # True frozen, False fresh, None either
    needs: str = ""  # regex that must also hit (e.g. "leftover")
    unit: str = ""  # "piece" or "lb": what the time scales with
    base: float = 1  # amount `minutes` is for
    per_extra: float = 0  # minutes per unit over `base`
    batch: float = 0  # most that fits in one layer; 0 = no limit


# Order matters: earlier entries win ties, so specific foods come before
# generic ones and the likelier variant (usually frozen) comes first.
# fmt: off
FOODS: tuple[Food, ...] = (
    Food("leftover fries", r"fries|chips|tots", 380, 4, shake=True,
         needs=r"leftover|reheat|cold|yesterday"),
    Food("leftover pizza", r"pizza", 350, 4, check=HOT,
         needs=r"leftover|reheat|cold|yesterday|slices?", unit="piece", base=2,
         per_extra=0.5, batch=4),
    Food("leftover chicken", r"chicken|wings?|tenders?|nuggets?", 350, 6, shake=True,
         check=LEFTOVER, needs=r"leftover|reheat|cold|yesterday"),
    Food("leftovers", r"leftovers?|reheat", 350, 5, shake=True, check=LEFTOVER),
    Food("frozen fries", r"fries|french fries|chips", 400, 15, shake=True, frozen=True,
         unit="lb", base=1, per_extra=4, batch=2),
    Food("fresh-cut fries", r"fries|french fries|chips", 380, 20, shake=True,
         frozen=False, unit="lb", base=1, per_extra=5, batch=2),
    Food("frozen tater tots", r"tater tots?|tots", 400, 12, shake=True, frozen=True,
         unit="lb", base=1, per_extra=3, batch=2),
    Food("frozen hash brown patties", r"hash ?browns?", 400, 10, shake=True, frozen=True,
         unit="piece", base=4, per_extra=0.5, batch=6),
    Food("frozen chicken tenders", r"tenders?|chicken strips?|chicken fingers?|tenderloins?",
         400, 12, shake=True, check=POULTRY, frozen=True,
         unit="piece", base=6, per_extra=0.5, batch=10),
    Food("fresh chicken tenders", r"tenders?|chicken strips?|chicken fingers?|tenderloins?",
         380, 12, shake=True, check=POULTRY, frozen=False,
         unit="piece", base=6, per_extra=0.5, batch=10),
    Food("frozen chicken nuggets", r"nuggets?", 400, 10, shake=True, check=POULTRY,
         frozen=True, unit="piece", base=10, per_extra=0.25, batch=20),
    Food("chicken wings", r"wings?|drumettes?|flats", 380, 24, shake=True, check=POULTRY,
         frozen=False, unit="lb", base=1, per_extra=4, batch=2),
    Food("frozen chicken wings", r"wings?|drumettes?|flats", 390, 28, shake=True,
         check=POULTRY, frozen=True, unit="lb", base=1, per_extra=4, batch=2),
    Food("chicken breast", r"breasts?", 380, 18, shake=True, check=POULTRY, frozen=False,
         unit="piece", base=1, per_extra=2, batch=2),
    Food("frozen chicken breast", r"breasts?", 360, 28, shake=True, check=POULTRY,
         frozen=True, unit="piece", base=1, per_extra=2, batch=2),
    Food("chicken thighs", r"thighs?|drumsticks?", 380, 22, shake=True, check=POULTRY,
         unit="piece", base=4, per_extra=1, batch=6),
    Food("fresh salmon fillet", r"salmon", 400, 9, preheat=True, check=FISH,
         frozen=False, unit="piece", base=1, per_extra=1, batch=3),
    Food("frozen salmon fillet", r"salmon", 390, 14, check=FISH, frozen=True,
         unit="piece", base=1, per_extra=1, batch=3),
    Food("white fish fillet", r"cod|tilapia|haddock|halibut|white ?fish|fish fillets?",
         400, 10, preheat=True, check=FISH, unit="piece", base=1, per_extra=1, batch=3),
    Food("frozen fish sticks", r"fish sticks?|fish fingers?", 400, 10, shake=True,
         frozen=True, unit="piece", base=10, per_extra=0.25, batch=16),
    Food("shrimp", r"shrimps?|prawns?", 400, 7, shake=True, check=SHELLFISH, frozen=False,
         unit="lb", base=1, per_extra=2, batch=1.5),
    Food("frozen breaded shrimp", r"shrimps?|prawns?", 400, 9, shake=True, check=SHELLFISH,
         frozen=True, unit="lb", base=1, per_extra=2, batch=1.5),
    Food("steak", r"steaks?|ribeye|sirloin|strip steak|new york strip|filet mignon",
         400, 10, preheat=True, shake=True, check=WHOLE_CUT,
         unit="piece", base=1, per_extra=1, batch=2),
    Food("burgers", r"burgers?|patt(?:y|ies)", 370, 10, preheat=True, shake=True,
         check=GROUND, unit="piece", base=2, per_extra=1, batch=4),
    Food("pork chops", r"pork chops?|chops?", 400, 12, preheat=True, shake=True,
         check=WHOLE_CUT, unit="piece", base=1, per_extra=1, batch=3),
    Food("bacon", r"bacon", 350, 9, check="crisp to your liking",
         unit="piece", base=6, per_extra=0.5, batch=8),
    Food("sausages", r"sausages?|brats?|bratwurst", 370, 12, shake=True, check=GROUND,
         unit="piece", base=4, per_extra=0.5, batch=6),
    Food("hot dogs", r"hot ?dogs?|franks?", 390, 5, check=STEAMING,
         unit="piece", base=4, per_extra=0.25, batch=6),
    Food("frozen egg rolls", r"egg ?rolls?|spring rolls?", 390, 10, shake=True,
         frozen=True, unit="piece", base=4, per_extra=0.5, batch=8),
    Food("frozen mozzarella sticks", r"mozz(?:arella)? sticks?|cheese sticks?", 390, 7,
         frozen=True, unit="piece", base=6, per_extra=0.25, batch=12),
    Food("frozen dumplings", r"dumplings?|potstickers?|gyoza", 380, 10, shake=True,
         frozen=True, unit="piece", base=10, per_extra=0.25, batch=16),
    Food("frozen pizza", r"pizza", 380, 10, frozen=True),
    Food("baked potato", r"baked potato(?:es)?|whole potato(?:es)?|jacket potato(?:es)?",
         400, 40, check="fork-tender in the middle",
         unit="piece", base=1, per_extra=3, batch=4),
    Food("potato wedges", r"potato(?:es)?|wedges|home ?fries", 400, 20, shake=True,
         check="fork-tender", unit="lb", base=1, per_extra=5, batch=2),
    Food("brussels sprouts", r"brussels?(?: sprouts?)?|sprouts", 380, 15, shake=True,
         check="fork-tender", unit="lb", base=1, per_extra=4, batch=1.5),
    Food("broccoli", r"broccoli|cauliflower", 380, 8, shake=True, check="fork-tender",
         unit="lb", base=1, per_extra=3, batch=1.5),
    Food("asparagus", r"asparagus|green beans?", 400, 7, shake=True, check="fork-tender",
         unit="lb", base=1, per_extra=2, batch=1.5),
    Food("chicken pieces", r"chicken", 380, 20, shake=True, check=POULTRY, frozen=False),
)
# fmt: on

_FROZEN = re.compile(r"\bfrozen\b")
_FRESH = re.compile(r"\b(fresh|raw|homemade|hand[- ]?cut|from scratch|thawed)\b")
_BIGGER = re.compile(r"\b(thick|large|big|jumbo|extra[- ]large|bone[- ]in|double)\b")
_SMALLER = re.compile(r"\b(thin|small|mini|bite[- ]size|cubed)\b")


@dataclass(frozen=True)
class FoodMatch:
    food: Food
    count: float | None = None  # pieces
    pounds: float | None = None
    size: float = 1.0  # time multiplier from size words


def find_food(text: str) -> FoodMatch | None:
    """The table entry a food description names, with any amount and size."""
    text = _words_to_digits(text.lower())
    frozen, fresh = bool(_FROZEN.search(text)), bool(_FRESH.search(text))
    hits = []
    for index, food in enumerate(FOODS):
        if not re.search(rf"\b(?:{food.words})\b", text):
            continue
        if food.needs and not re.search(rf"\b(?:{food.needs})\b", text):
            continue
        if frozen:
            rank = {True: 0, None: 1, False: 2}[food.frozen]
        elif fresh:
            rank = {False: 0, None: 1, True: 2}[food.frozen]
        else:
            rank = 0
        hits.append((rank, 0 if food.needs else 1, index, food))
    if not hits:
        return None
    food = min(hits, key=lambda hit: hit[:3])[3]
    count, pounds = _amounts(text)
    size = 1.25 if _BIGGER.search(text) else 0.8 if _SMALLER.search(text) else 1.0
    return FoodMatch(food, count, pounds, size)


def _amounts(text: str) -> tuple[float | None, float | None]:
    pounds = None
    if match := re.search(r"(\d+(?:\.\d+)?)\s*(?:lbs?|pounds?)\b", text):
        pounds = float(match[1])
        text = text[: match.start()] + text[match.end() :]
    elif match := re.search(r"(\d+(?:\.\d+)?)\s*(?:oz|ounces?)\b", text):
        pounds = float(match[1]) / 16
        text = text[: match.start()] + text[match.end() :]
    count = None
    if match := re.search(
        r"\bx\s*(\d+)\b|\b(\d+)\s*x\b|\b(\d+)\s*(?:pcs?|pieces?)\b", text
    ):
        count = float(next(g for g in match.groups() if g))
    elif match := re.search(r"\b(\d+)\b", text):
        count = float(match[1])
    return count, pounds


def food_plan(match: FoodMatch) -> tuple[int, int, list[str]]:
    """(temp °F, minutes, notes) for a food, scaled for amount and size."""
    food, notes = match.food, []
    amount = match.count if food.unit == "piece" else match.pounds
    if food.unit == "lb" and amount is None and match.count is not None:
        notes.append(
            f"Amount read as {_fmt(match.count)} pieces; timed for {_fmt(food.base)} lb."
        )
    minutes = float(food.minutes)
    if amount is not None and food.unit:
        unit = "lb" if food.unit == "lb" else ""
        if food.batch and amount > food.batch:
            notes.append(
                f"{_fmt(amount)}{' ' + unit if unit else ''} won't fit in one layer; "
                f"cook in batches of about {_fmt(food.batch)}{' ' + unit if unit else ''}. "
                "Time is per batch."
            )
            amount = food.batch
        minutes += max(0.0, amount - food.base) * food.per_extra
    if match.size != 1.0:
        minutes *= match.size
        notes.append(
            "Thicker or bigger pieces: more time."
            if match.size > 1
            else "Thinner or smaller pieces: less time."
        )
    whole = max(MIN_MINUTES, math.ceil(minutes - 1e-9))
    if whole > MAX_MINUTES:
        notes.append(
            f"Needs about {whole} min; capped at {MAX_MINUTES}. Check, then add time."
        )
        whole = MAX_MINUTES
    return food.temp_f, whole, notes


def food_label(match: FoodMatch) -> str:
    food = match.food
    if food.unit == "lb" and match.pounds is not None:
        return f"{food.name} ({_fmt(match.pounds)} lb)"
    if food.unit == "piece" and match.count is not None:
        return f"{food.name} ({_fmt(match.count)})"
    return food.name


# --- Intents and plans ---------------------------------------------------


@dataclass(frozen=True)
class Intent:
    kind: str  # one of INTENTS
    food: str | None = None
    temp_f: object = None
    minutes: object = None
    preheat: bool | None = None
    food_in: bool | None = None  # the food is already in the basket
    arriving_in_min: object = None  # keep warm: "I'm N minutes out"
    question: str | None = None  # set by parse() for a request that must not run as is


@dataclass(frozen=True)
class Plan:
    intent: str
    temp_f: int | None = None
    minutes: int | None = None
    preheat: bool = False
    shake_at: int | None = None
    food: str | None = None
    check: str | None = None
    hold_limit_min: int | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)
    question: str | None = None

    @property
    def runnable(self) -> bool:
        return self.question is None

    def describe(self) -> str:
        """The plan in one or two plain sentences, rounding stated."""
        if self.question:
            head = f"Need an answer first: {self.question}"
        elif self.intent == "stop":
            head = "Stop the fryer."
        elif self.intent == "status":
            head = "Check what the fryer is doing."
        elif self.intent == "keep_warm":
            head = f"Keep warm at {self.temp_f}°F for {self.minutes} min."
        elif self.intent == "preheat":
            head = (
                f"Preheat to {self.temp_f}°F and hold until the food is in "
                f"(stops after {self.hold_limit_min} min if nothing goes in)."
            )
        else:
            what = f" {self.food}" if self.food else ""
            first = "Preheat, then cook" if self.preheat else "Cook"
            head = f"{first}{what}: {self.temp_f}°F for {self.minutes} min"
            head += ", preheat first." if self.preheat else ", no preheat."
            if self.shake_at:
                head = head[:-1] + f", shake at {self.shake_at} min."
            if self.check:
                head += f" Check: {self.check}."
        return " ".join([head, *self.notes])


_STOP = re.compile(
    r"\b(stop|cancel|abort|halt|kill|shut (?:it |the (?:air )?fryer )?(?:off|down)"
    r"|(?:turn|switch) (?:it |the (?:air )?fryer )?off)\b"
)
_KEEP_WARM = re.compile(
    r"\b(keep(?:ing)? (?:it|them|the food|food|things|everything|that|this|dinner)? ?warm"
    r"|warm hold|hold (?:it |them )?warm)\b"
)
_NO_KEEP_WARM = re.compile(
    r"\b(?:don'?t|do not|no need to|no need|never|not) (?:\w+ )?(?:keep|hold)\w*"
    r"(?: \w+)? warm\b"
)
_STATUS = re.compile(
    r"\b(status|done|ready|how long|time left|minutes left|how much (?:longer|time)"
    r"|what'?s (?:it|the (?:air )?fryer) doing|is (?:it|the (?:air )?fryer) (?:on|hot|preheated|running)"
    r"|check (?:on )?(?:it|the (?:air )?fryer))\b"
)
_PREHEAT = re.compile(
    r"\b(pre-?heat\w*|heat (?:it |the (?:air )?fryer )?up|get (?:it|the (?:air )?fryer) hot"
    r"|(?:turn|switch) (?:it |the (?:air )?fryer )?on|warm up the (?:air )?fryer)\b"
)
_NO_PREHEAT = re.compile(
    r"\b(no preheat\w*|without (?:a )?preheat\w*|skip (?:the )?preheat\w*"
    r"|don'?t preheat|do not preheat)\b"
)
_COOK = re.compile(r"\b(cook|air ?fry|fry|bake|roast|reheat|crisp|make|do|start)\b")
# Words that ask for a cook even next to "keep it warm".
_COOKING = re.compile(
    r"\b(cook\w*|air ?fry\w*|fry|bake|roast|reheat|crisp|pre-?heat\w*)\b"
)
_FOOD_IN = re.compile(
    r"\b((?:is|are|it'?s|they'?re|'s|'re) (?:already )?in(?: the basket)?"
    r"|in the basket|already in|put (?:it|them) in)\b"
)
_SET_TO = re.compile(r"\bset (?:it |the (?:air )?fryer )?to\b")

_NUM = r"(\d+(?:\.\d+)?)"
_TEMP_UNIT = re.compile(
    _NUM
    + r"\s*(?:(?:°|º|degrees?|deg\.?)\s*(fahrenheit|celsius|f|c)?|(fahrenheit|celsius|f|c))\b"
    r"|" + _NUM + r"\s*(?:°|º)"
)
_MIN_UNIT = re.compile(
    _NUM + r"\s*(?:more\s+|extra\s+)?(hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?)\b"
)
# "1 hour 30 minutes", "20 minutes and 30 seconds": parts of one duration.
_JOINED = re.compile(r"\s*,?\s*(?:and\s*)?")
# "12 minutes plus 3 minutes" says outright that the times add.
_PLUS = re.compile(r"\s*,?\s*plus\s*")
# "10 minutes then 5 more minutes": "then" joins a time only when it says "more".
_THEN_MORE = re.compile(r"\s*,?\s*(?:and\s+)?then\s*")
# "5 more", "3 extra": a top-up, which needs a base time to add to.
_TOPUP = re.compile(_NUM + r"\s*(?:more|extra)\b")
# "12 or 15 minutes", "12 to 15 min", "12-15 minutes": two times, not one. A
# three-digit number is a temperature, so "at 380 - 12 minutes" is one time.
_SHORT = r"(?<![\d.])(\d{1,2}(?:\.\d+)?)"
_RANGE = re.compile(
    _SHORT + r"\s*(?:or|to|-)\s*" + _SHORT + r"\s*(hours?|hrs?|h|minutes?|mins?|m)\b"
)
# Numbers a food description may carry: "x6", "6x", "2 lb", "8 oz", "4 pieces".
_AMOUNT = re.compile(
    r"\bx\s*\d+\b|\b\d+\s*x\b|\b\d+(?:\.\d+)?\s*(?:lbs?|pounds?|oz|ounces?|pcs?|pieces?)\b"
)
# A shake or flip time is a reminder, not part of the cook time.
_SHAKE = re.compile(
    r"\b(?:shake|flip|turn)(?:\s+(?:it|them|the basket|over))?\s+"
    r"(?:(?:after|at|in)\s+" + _NUM + r"\s*(?:hours?|hrs?|h|minutes?|mins?|m)?\b"
    r"|halfway(?:\s+through)?\b)"
)


def _unit_rank(match: re.Match) -> int:
    """0 for hours, 1 for minutes, 2 for seconds."""
    return {"h": 0, "s": 2}.get(match[2][0], 1)


def _duration(t: str) -> tuple[object, str]:
    """(minutes, text without them). One duration in falling units ("1 hour 30
    minutes") adds up to 90, and so does a stated top-up ("10 minutes then 5
    more"). Two separate times ("12 minutes and 7 minutes") leave the time
    unknown, so the plan asks instead of adding or picking one."""
    found = list(_MIN_UNIT.finditer(t))
    if not found:
        return None, t
    run = [found[0]]
    for match in found[1:]:
        between = t[run[-1].end() : match.start()]
        more = re.search(r"\b(?:more|extra)\b", match[0])
        smaller = _unit_rank(match) > _unit_rank(run[-1])
        joined = (
            (smaller and not more and _JOINED.fullmatch(between))
            or (more and (_JOINED.fullmatch(between) or _THEN_MORE.fullmatch(between)))
            or _PLUS.fullmatch(between)
        )
        if not joined:
            break
        run.append(match)
    total = sum(read_minutes(f"{m[1]} {m[2]}") for m in run)
    minutes = (
        None
        if len(run) < len(found)
        else (f"{run[0][1]} {run[0][2]}" if len(run) == 1 else total)
    )
    for match in reversed(found):
        t = t[: match.start()] + " " + t[match.end() :]
    return minutes, t


_ARRIVAL_UNIT = r"\s*(?P<unit>hours?|hrs?|h|min\w*|m)?\b"
_ARRIVAL = re.compile(
    r"(?:" + _NUM + r"\s*(?P<before>min\w*|hours?|hrs?)?\s*(?:out|away)\b"
    r"|\b(?:home|back|there|here|arriv\w*|land\w*)\s+in\s+"
    + _NUM
    + r"|\bin\s+"
    + _NUM
    + r"(?=\s*(?:min|hour|hr)))"
    + _ARRIVAL_UNIT
)
_AT_TEMP = re.compile(r"\b(?:at|to)\s+" + _NUM + r"\b(?!\s*(?:lbs?|pounds?|pieces?|x))")
_FOR_MIN = re.compile(r"\bfor\s+" + _NUM + r"\b(?!\s*(?:lbs?|pounds?|pieces?|people|x))")

# fmt: off
_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
# fmt: on


_DEGREES_NEXT = re.compile(r"\s*(?:°|º|degrees?\b|deg\b|fahrenheit\b|f\b)")
# Only "preheat to" / "heat the fryer to" make a clock-like number heat;
# "set the timer to three thirty" is a time.
_HEAT_TO = re.compile(r"\b(?:pre-?heat\w*|heat)(?:\s+(?:it|the\s+(?:air\s+)?fryer))?\s*$")
_UNIT_NEXT = (
    r"°|º|degrees?|deg\b|fahrenheit|f\b|c\b|celsius|h\b|hours?|hrs?|m\b|min\w*"
    r"|secs?|seconds?|lbs?|pounds?|oz|pcs?|pieces?|x\b"
)
# "at 4", "by 6", "at 3 30" (a clock-like "three thirty" left as words): a
# small number after a time word, with no unit, is a time as likely as heat.
_AT_CLOCK = re.compile(
    r"\b(?:at|to|by|until|till|around)\s+\d{1,2}(?::\d{2})?"
    r"(?![\d.]|\s*(?:" + _UNIT_NEXT + r"))"
)
# A time of day or a schedule anywhere in the request: the fryer can only
# start now, so it asks rather than guess which number is heat.
_CLOCK_WORDS = re.compile(
    r"\b\d{1,2}:\d{2}\b|\b\d{1,4}\s*[ap]\.?\s*m\b\.?|\b\d+\s*-?\s*ish\b"
    r"|\bo'?\s*clock\b|\b(?:noon|midnight|tonight|today|tomorrow)\b"
    r"|\b(?:in the|this|tomorrow) (?:morning|afternoon|evening|night)\b"
    r"|\b(?:after|before) (?:work|school|dinner|lunch)\b"
    r"|\bwhen (?:i|we|you|they|he|she)(?:'m| am| are| is)? (?:get|gets|am|are|is)? ?"
    r"(?:home|back|here|there|in)\b"
)


# Doneness or a cook step, not a start: "when golden", "until they're
# crispy", "until a toothpick comes out clean", "when the timer says". After
# when/until, a doneness word within five words lets the cook run, unless a
# person comes first ("when i'm ready", "until we get home").
_DONENESS = (
    r"(?:done|ready|finished|hot|golden\w*|brown\w*|crisp\w*|cooked|tender|halfway"
    r"|bubbl\w*|melt\w*|sizzl\w*|soft|heated|warm(?:ed)?|\d{3}|pre-?heated|clear"
    r"|clean|set|rare|medium|opaque|pink|firm|flak\w*|char\w*|caramel\w*"
    r"|reach\w*|beeps?|dings?|ends?|stops?|says|goes off|is up)\b"
)
_PERSON = (
    r"(?:i|i'm|we|we're|you|you're|he|he's|she|she's|they get|they arrive\w*"
    r"|they come|they're (?:home|back|here)|everyone|guests|people|company|my|our"
    r"|the (?:guests|kids|family|game|party|show|movie))\b"
)
_NOT_A_START = r"(?! (?!" + _PERSON + r")(?:\S+ ){0,4}?" + _DONENESS + r")"
# A food step, not a start: "after shaking", "before i put the fries in", a
# trailing "add sauce after".
_STEP = (
    r"(?! \w+ing\b)(?! (?:i|we|you) (?:put|add|drop|toss|throw|load)\b)"
    r"(?!\s*(?:$|[,.;!?]|and\b|then\b))"
)
_MEAL = r"(?:breakfast|brunch|lunch|dinner|supper)\b(?! rolls?\b)"

# A cook or preheat meant for later: "375 later", "400 on friday", "for
# dinner", "in 20 minutes". The fryer can only start now, so it asks. Keep
# warm reads "home in 20" itself and never reaches this.
_SCHEDULE = re.compile(
    r"\b(?:later|soon|shortly|eventually|sometime|whenever|afterwards?"
    r"|another time|next time|not (?:yet|now)|hold off|(?:don'?t|do not) start"
    r"|first thing|next (?:week|hour|day|month)|(?:this |next )?weekend"
    r"|tmrw?|tmrrw|tomoz|2morrow|tonight\w*|tonite\w*|2nite|half ?time|kick ?off"
    r"|(?<!last )(?:over)?night\b(?! oats)|evening\b|afternoon\b(?! snack)"
    r"|morning\b|this [ap]m\b|on the \d+(?:st|nd|rd|th)\b"
    r"|on (?:my|the|our|his|her|their) way"
    r"|(?:get|gets|getting|got|am|i'm|we're|arrive|arriving) (?:home|back|here|there)\b"
    r"|(?:mon|tues|wednes|thurs|fri|satur|sun)day\b(?! roast)"
    r"|(?:on|this|next|by) (?:mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)\b"
    r"|in (?:a (?:bit|while|few|sec\w*|moment)|a little (?:bit|while)|an? hrs?\b)"
    # "in 20 minutes" is when to start; "done in 15 minutes" is how long.
    r"|(?<!done )(?<!ready )(?<!cooked )in \d+(?:\.\d+)?\s*"
    r"(?:hours?|hrs?|h|minutes?|mins?|m)\b"
    r"|\d+(?:\.\d+)?\s*(?:min\w*|hours?|hrs?|h|m)?\s*(?:out|away)\b"
    r"|(?:home|back|here|there) in \d"
    r"|(?:breakfast|brunch|lunch|dinner|supper) ?time"
    r"|(?:for|at|by|before|until|till|around|with) (?:my |our |the )?"
    + _MEAL
    + r"|for (?:the )?(?:party|game|guests|company)"
    # A clause that sets the start: "before i get home", "when they arrive",
    # "until he gets back", "after the game".
    r"|after\b" + _STEP + r"(?! (?:pre-?heat\w*|it'?s|it is|the preheat)\b)"
    r"|before\b"
    + _STEP
    + r"|(?:when|until|till|til)\b"
    + _NOT_A_START
    + r"|(?:once|by the time) (?:i|i'm|we|we're|you|they|he|she|everyone|guests"
    r"|people|company|the (?:guests|kids|game|party))\b)"
)
SCHEDULE_QUESTION = (
    "Start it now? The fryer can only start right away, so ask again when you "
    "want it to start. Say a cook time as 'for 15 minutes'."
)


def scheduled(text: str | None) -> bool:
    """True when words like "later", "on friday" or "for dinner" put a cook or
    preheat in the future. parse() checks the whole sentence; the MCP tools
    also check a `food` passed as a field."""
    if not text:
        return False
    t = _words_to_digits(str(text).lower().replace("’", "'"))
    return bool(_SCHEDULE.search(_SHAKE.sub(" ", t)))


def _schedule_question(t: str) -> str | None:
    """A cook or preheat for later is never started now without asking."""
    status_only = _STATUS.search(t) and not (
        _COOK.search(t) or _PREHEAT.search(t) or re.search(r"\b\d{3}\b", t)
    )
    if _SCHEDULE.search(_SHAKE.sub(" ", t)) and not status_only:
        return SCHEDULE_QUESTION
    return None


def _clock_question(digits: str) -> str | None:
    """A time of day, or a number that could be one, is never read as heat."""
    t = _SHAKE.sub(" ", digits)
    at = (
        m
        for m in _AT_CLOCK.finditer(t)
        if not (m[0].startswith("to") and _HEAT_TO.search(t, 0, m.start()))
    )
    if next(at, None) or _CLOCK_WORDS.search(t):
        return (
            "Is that a temperature or a time of day? "
            "Say the temperature in degrees, like 350 degrees."
        )
    return None


def _spoken_heat(m: re.Match) -> str:
    """'at three eighty' is 380: no clock reads :80. 'at three thirty' could be
    3:30, so it becomes 330 only with degrees after it or 'heat/set ... to'
    before it; otherwise the words stay and the plan asks."""
    tens = _TENS[m[3]]
    clock_like = tens <= 50
    sure = _DEGREES_NEXT.match(m.string, m.end()) or (
        m[1] == "to" and _HEAT_TO.search(m.string, 0, m.start())
    )
    if clock_like and not sure:
        return m[0]
    return f"{m[1]} {_UNITS[m[2]] * 100 + tens + (_UNITS[m[4]] if m[4] else 0)}"


def _small(words: str) -> int | None:
    parts = re.split(r"[- ]", words)
    if len(parts) == 1 and parts[0] in _UNITS:
        return _UNITS[parts[0]]
    if parts[0] in _TENS and (len(parts) == 1 or parts[1] in _UNITS):
        return _TENS[parts[0]] + (_UNITS[parts[1]] if len(parts) > 1 else 0)
    return None


def _hundreds(match: re.Match) -> str:
    hundreds = _UNITS[match[1]] * 100
    if not match[2]:
        return str(hundreds)
    if (rest := _small(match[2])) is not None:
        return str(hundreds + rest)
    first, *tail = re.split(r"[- ]", match[2], maxsplit=1)
    if (rest := _small(first)) is not None:  # "three hundred ninety for ..."
        return " ".join([str(hundreds + rest), *tail])
    return f"{hundreds} {match[2]}"  # "three hundred degrees": keep the word


def _words_to_digits(text: str) -> str:
    text = re.sub(r"\bhalf an? hour\b", "30 min", text)
    text = re.sub(r"\bhalf an? minute\b", "0.5 min", text)
    text = re.sub(r"\ban? hour and a half\b", "90 min", text)
    text = re.sub(r"\ban? (hour|minute)\b", r"1 \1", text)
    text = re.sub(r"\bhalf a dozen\b", "6", text)
    text = re.sub(r"\ba dozen\b", "12", text)
    text = re.sub(r"\ba (?:couple|pair)(?: of)?\b", "2", text)

    text = re.sub(
        r"\b(one|two|three|four)[- ]hundred(?:[- ]and)?(?:[- ](\w+(?:[- ]\w+)?))?\b",
        _hundreds,
        text,
    )
    # "at three eighty" is 380; only after at or to, so counts like "three twenty"
    # stay. Clock-like ones stay words unless degrees make them heat.
    text = re.sub(
        r"\b(at|to) (one|two|three|four)[- ]("
        + "|".join(_TENS)
        + r")(?:[- ]("
        + "|".join(list(_UNITS)[1:10])
        + r"))?\b",
        _spoken_heat,
        text,
    )

    def tens(match: re.Match) -> str:
        return str(_TENS[match[1]] + (_UNITS[match[2]] if match[2] else 0))

    text = re.sub(
        r"\b(" + "|".join(_TENS) + r")(?:[- ](" + "|".join(list(_UNITS)[1:10]) + r"))?\b",
        tens,
        text,
    )
    text = re.sub(r"\b(" + "|".join(_UNITS) + r")\b", lambda m: str(_UNITS[m[1]]), text)
    # "12 and a half" is 12.5, so the round-up is stated, not dropped.
    return re.sub(
        r"\b(\d+(?:\.\d+)?) and a half\b", lambda m: _fmt(float(m[1]) + 0.5), text
    )


_FILLER = re.compile(
    r"\b(please|can you|could you|would you|go ahead and|i want to|i'd like to|let'?s"
    r"|the|a|an|some|my|me|us|it|them|this|that|now|then|and|for|at|to|in|on|of|with"
    r"|cook|cooking|air ?fry|fry|bake|roast|crisp|make|do|start|pre-?heat\w*|basket"
    r"|is|are|already|put|degrees?|minutes?|mins?|go|up|fryer|air|please|ok|okay|hey"
    r"|no|without|skip|don'?t|do not|turn|switch|set|get|hot|heat|off|be|i'?m|you|ready)\b"
)


def parse(text: str) -> Intent:
    """Read a plain sentence as an Intent. Never raises; plan() checks values."""
    raw = " ".join(str(text).split())
    t = _words_to_digits(raw.lower().replace("’", "'").replace("º", "°"))

    # Stop always wins, so "stop keeping it warm" never heats.
    if _STOP.search(t) or _NO_KEEP_WARM.search(t):
        return Intent("stop")
    if question := _clock_question(t):
        return Intent("cook", question=question)

    if _KEEP_WARM.search(t):
        arriving = minutes = None
        if match := _ARRIVAL.search(t):
            number = next(g for g in (match[1], match[3], match[4]) if g)
            arriving = f"{number} {match['before'] or match['unit'] or 'min'}"
            t = t[: match.start()] + " " + t[match.end() :]
        timed = list(_MIN_UNIT.finditer(t))
        said = t
        minutes, t = _duration(t)
        both = _COOKING.search(t) or any(
            int(n) != KEEP_WARM_TEMP_F
            for n in re.findall(r"\b(\d{3})\b", _FOR_MIN.sub(" ", t))
        )
        if both:
            return Intent(
                "cook",
                question=(
                    "That's a cook and keep warm together; do one at a time. "
                    "Start the cook; ask for keep warm when it's done."
                ),
            )
        if (minutes is None and timed) or _RANGE.search(said):
            return Intent("keep_warm", question=_unclear(said, t, timed, None))
        if minutes is None and (match := _FOR_MIN.search(t)):
            minutes = match[1]
        return Intent("keep_warm", minutes=minutes, arriving_in_min=arriving)

    if question := _schedule_question(t):
        kind = (
            "preheat"
            if _PREHEAT.search(t) and not _COOKING.search(_PREHEAT.sub(" ", t))
            else "cook"
        )
        return Intent(kind, question=question)

    t = _SHAKE.sub(" ", t)
    set_to = bool(_SET_TO.search(t))
    raw_t = t
    temp = minutes = None
    if match := _TEMP_UNIT.search(t):
        if match[1]:
            unit = (match[2] or match[3] or "f")[0]
            temp = f"{match[1]}{unit}"
        else:
            temp = match[4]
        t = t[: match.start()] + " " + t[match.end() :]
    timed = list(_MIN_UNIT.finditer(t))
    minutes, t = _duration(t)
    if temp is None and (match := _AT_TEMP.search(t)):
        temp = match[1]
        t = t[: match.start()] + " " + t[match.end() :]
    if (match := _FOR_MIN.search(t)) and float(match[1]) >= 100:
        return Intent(
            "cook",
            question=(
                f"'for {match[1]}': minutes or degrees? "
                "Say it like 350 degrees for 15 minutes."
            ),
        )
    if not timed and (match := _FOR_MIN.search(t)) and float(match[1]) < 100:
        minutes = match[1]
        t = t[: match.start()] + " " + t[match.end() :]
    if temp is None and (match := re.search(r"\b(\d{3})\b", t)):
        temp = match[1]
        t = t[: match.start()] + " " + t[match.end() :]
    if question := _unclear(raw_t, t, timed, minutes):
        return Intent("cook", question=question)

    preheat_word = bool(_PREHEAT.search(t)) or (set_to and temp is not None)
    no_preheat = bool(_NO_PREHEAT.search(t))
    food_in = True if _FOOD_IN.search(t) else None
    cook_word = bool(_COOK.search(t))

    leftover = _FILLER.sub(" ", t)
    leftover = re.sub(r"[^a-z0-9.\s'-]", " ", leftover)
    food = " ".join(leftover.split()) or None
    if food and not re.search(r"[a-z]{2}", food):
        food = None  # only numbers left: an amount with no food
    # Keep the food words as written, so counts like "x6" survive.
    if food:
        food = _food_text(raw)

    if _STATUS.search(t) and not (cook_word or preheat_word or minutes or temp):
        return Intent("status")
    if not (cook_word or preheat_word or minutes or temp or food):
        return Intent("status") if raw.endswith("?") else Intent("cook")

    if preheat_word and not no_preheat and minutes is None and not cook_word:
        return Intent("preheat", food=food, temp_f=temp)
    preheat = False if no_preheat else (True if preheat_word else None)
    return Intent(
        "cook", food=food, temp_f=temp, minutes=minutes, preheat=preheat, food_in=food_in
    )


def _unclear(said: str, left: str, timed: list, minutes: object) -> str | None:
    """Why a cook can't run as heard, or None. Fails closed: a time or a
    temperature that was said but not used never falls back to a default."""
    if match := _RANGE.search(said):
        a, b = _fmt(float(match[1])), _fmt(float(match[2]))
        return f"Did you mean {a} or {b} {match[3]}? Say one time."
    topups = list(_TOPUP.finditer(said))
    # A time to add to: one that isn't itself a top-up, or a plain "for 10".
    base = any(not _TOPUP.match(m[0]) for m in timed) or (
        minutes is not None and not timed
    )
    if topups and not base and (timed or _COOK.search(said)):
        return (
            f"{_fmt(float(topups[0][1]))} more minutes on top of what? "
            "Say the total time."
        )
    if minutes is None and timed:
        if len(timed) > 1 and not topups and all(_unit_rank(m) == 1 for m in timed):
            heard = " or ".join(_fmt(float(m[1])) for m in timed)
            return f"Did you mean {heard} minutes? Say one time."
        return "How long in total?"
    rest = _AMOUNT.sub(" ", left)
    dropped = (
        _TOPUP.search(rest)
        or _TEMP_UNIT.search(rest)
        or _AT_TEMP.search(rest)
        or re.search(r"\b\d{3}\b", rest)
        or re.search(r"\b\d+(?:\.\d+)?\s*(?:then\b|and\b|$)", rest.strip())
        # "3 30", "3, 20 minutes": a number beside another number or a comma.
        or re.search(r"\b\d+(?:\.\d+)?(?![\d.])(?=\s*(?:\d|[^\w\s.'-]))", rest)
    )
    if dropped:
        return (
            f"I didn't follow '{dropped[0].strip()}'. "
            "How long in total, and at what temperature?"
        )
    return None


def _food_text(raw: str) -> str:
    """The food part of a sentence: command words, temps and times removed."""
    t = _words_to_digits(raw.lower().replace("’", "'").replace("º", "°"))
    for pattern in (_SHAKE, _TEMP_UNIT, _MIN_UNIT, _AT_TEMP, _FOR_MIN, _ARRIVAL):
        t = pattern.sub(" ", t)
    t = re.sub(r"\b\d{3}\b", " ", t)
    t = _NO_PREHEAT.sub(" ", t)
    t = re.sub(r"\bafter (?:the )?pre-?heat\w*", " ", t)
    t = _FOOD_IN.sub(" ", t)
    t = _FILLER.sub(" ", t)
    t = re.sub(r"[^a-z0-9.\s'-]", " ", t)
    return " ".join(t.split())


def plan(intent: Intent | str) -> Plan:
    """The plan for an intent or a plain sentence. Raises PlanError for values
    the fryer can't run; returns a plan with a `question` when ambiguous."""
    if isinstance(intent, str):
        intent = parse(intent)
    if intent.kind not in INTENTS:
        raise PlanError(
            f"unknown request {intent.kind!r}; use one of {', '.join(INTENTS)}."
        )
    if intent.question:
        return Plan(intent.kind, question=intent.question)
    if intent.kind == "stop":
        return Plan("stop")
    if intent.kind == "status":
        return Plan("status")
    if intent.kind == "keep_warm":
        return _keep_warm(intent)
    if intent.kind == "preheat":
        return _preheat(intent)
    return _cook(intent)


def _keep_warm(intent: Intent) -> Plan:
    notes = []
    if intent.minutes not in (None, ""):
        minutes, note = whole_minutes(
            read_minutes(intent.minutes), KEEP_WARM_MAX_MIN, cap=True
        )
        if note:
            notes.append(note)
        if intent.arriving_in_min not in (None, ""):
            notes.append("Both a time and an arrival given; using the time.")
    elif intent.arriving_in_min not in (None, ""):
        away = read_minutes(intent.arriving_in_min)
        if away < 0:
            raise PlanError("arriving_in_min can't be negative.")
        wanted = math.ceil(away - 1e-9) + ARRIVAL_BUFFER_MIN
        minutes = min(wanted, KEEP_WARM_MAX_MIN)
        notes.append(
            f"{_fmt(away)} min out + {ARRIVAL_BUFFER_MIN} min spare = {wanted} min."
        )
        if wanted > KEEP_WARM_MAX_MIN:
            notes.append(
                f"Capped at {KEEP_WARM_MAX_MIN} min; warm it again when you're back."
            )
    else:
        minutes = KEEP_WARM_DEFAULT_MIN
        notes.append(f"No time given; using {KEEP_WARM_DEFAULT_MIN} min.")
    return Plan("keep_warm", temp_f=KEEP_WARM_TEMP_F, minutes=minutes, notes=tuple(notes))


def _preheat(intent: Intent) -> Plan:
    notes = []
    match = find_food(intent.food) if intent.food else None
    if intent.temp_f not in (None, ""):
        temp = parse_temp(intent.temp_f)
        setting = temp.setting
        if temp.note:
            notes.append(temp.note)
    elif match:
        setting = match.food.temp_f
        notes.append(f"{setting}°F for {match.food.name}.")
    else:
        setting = DEFAULT_TEMP_F
        notes.append(f"No temperature given; using {DEFAULT_TEMP_F}°F.")
    return Plan(
        "preheat",
        temp_f=setting,
        food=food_label(match) if match else None,
        hold_limit_min=HOLD_LIMIT_MIN,
        notes=tuple(notes),
    )


def _cook(intent: Intent) -> Plan:
    notes: list[str] = []
    match = find_food(intent.food) if intent.food else None
    temp = minutes = None
    if intent.temp_f not in (None, ""):
        parsed = parse_temp(intent.temp_f)
        temp = parsed.setting
        if parsed.note:
            notes.append(parsed.note)
    if intent.minutes not in (None, ""):
        minutes, note = whole_minutes(read_minutes(intent.minutes))
        if note:
            notes.append(note)

    label = food_label(match) if match else (intent.food or None)
    check = shake_at = None
    benefits = False
    if match:
        table_temp, table_minutes, food_notes = food_plan(match)
        if temp is None:
            temp = table_temp
        if minutes is None:
            minutes = table_minutes
            notes.extend(food_notes)
        check = match.food.check
        if check == GROUND and re.search(
            r"\b(chicken|turkey|poultry)\b", intent.food.lower()
        ):
            check = POULTRY  # ground poultry: 165°F, not 160°F (USDA)
        benefits = match.food.preheat
        if match.food.shake and minutes >= 4:
            shake_at = minutes // 2

    question = None
    if temp is None or minutes is None:
        if intent.food and not match:
            question = f"{intent.food!r} isn't in the food table. " + (
                "How long?"
                if temp is not None
                else "What temperature?"
                if minutes is not None
                else "What temperature and how long?"
            )
        elif temp is None and minutes is None:
            question = "What's cooking, or what temperature and how long?"
        elif minutes is None:
            question = "How long should it cook?"
        else:
            question = "What temperature?"

    if intent.preheat is not None:
        preheat = bool(intent.preheat)
    elif intent.food_in:
        preheat = False
    else:
        preheat = benefits
        if benefits:
            notes.append(f"Preheating first: a hot basket helps {match.food.name}.")

    return Plan(
        "cook",
        temp_f=temp,
        minutes=minutes,
        preheat=preheat,
        shake_at=shake_at,
        food=label,
        check=check,
        notes=tuple(notes),
        question=question,
    )
