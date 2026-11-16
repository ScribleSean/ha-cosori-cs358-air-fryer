"""The shipped copy of the shared planner, on the sentences Assist can now reach.

The full planner suite lives with the planner's source; this checks the copy here.
"""

import pytest

from custom_components.cosori_fryer.planner import PlanError, plan

# utterance -> (intent, temp °F, minutes, needs an answer)
SPOKEN = [
    ("air fry for 12 minutes at 380", ("cook", 380, 12, False)),
    ("air fry for 12 and a half minutes at 380", ("cook", 380, 13, False)),
    ("air fry for one and a half minutes at 380", ("cook", 380, 2, False)),
    ("air fry for 20 minutes and 30 seconds at 380", ("cook", 380, 21, False)),
    ("air fry for 12 minutes at three eighty", ("cook", 380, 12, False)),
    ("keep it warm, I'm 10 minutes out", ("keep_warm", 170, 15, False)),
    ("keep it warm, I'm an hour out", ("keep_warm", 170, 60, False)),
    ("keep warm for 100 minutes", ("keep_warm", 170, 60, False)),
    ("air fry for 12 minutes at 380 then keep it warm", ("cook", None, None, True)),
    ("cook 12 minutes at 380 or 15 minutes", ("cook", None, None, True)),
    ("keep it warm for 120", ("keep_warm", 170, 60, False)),
    ("air fry for 12 min at 380 and shake after 6 min", ("cook", 380, 12, False)),
    ("air fry for 10 minutes then 5 more minutes at 400", ("cook", 400, 15, False)),
]


@pytest.mark.parametrize(("utterance", "expected"), SPOKEN, ids=[u for u, _ in SPOKEN])
def test_spoken(utterance, expected):
    p = plan(utterance)
    assert (p.intent, p.temp_f, p.minutes, not p.runnable) == expected, p.describe()


def test_rounding_is_stated():
    assert "12.5 min rounds up to 13 min" in plan("air fry for 12 and a half minutes at 380").describe()
    hour = plan("keep it warm, I'm an hour out").describe()
    assert "60 min out + 5 min spare = 65 min" in hour
    assert "Capped at 60" in hour


def test_added_times_over_the_limit_are_refused():
    with pytest.raises(PlanError, match="60-minute limit"):
        plan("air fry for 1 hour 30 minutes at 380")


def test_shake_and_more_minutes_are_not_food():
    assert plan("air fry for 12 min at 380 and shake after 6 min").food is None
    assert plan("air fry for 10 minutes then 5 more minutes at 400").food is None
    assert "capped at 60" in plan("keep it warm for 120").describe()


# Fail closed: a time or temperature that was said but not understood starts
# nothing and never falls back to a default (review, 2026-11-15).
UNCLEAR = [
    # A: a number left over, or a temperature dropped
    ("fries 3 more", "I didn't follow '3 more'"),
    ("chicken 3 extra", "I didn't follow '3 extra'"),
    ("air fry fries for 10 minutes at 400 then 5 more at 350", "I didn't follow"),
    # B: a time was said but can't be read as one total
    ("fries 12 minutes then 3 minutes", "Did you mean 12 or 3 minutes?"),
    (
        "cook fries 400 for 12 minutes then shake after 6 and 4 more minutes",
        "How long in total?",
    ),
    # C: a top-up with no base time
    ("air fry 5 more minutes at 400", "5 more minutes on top of what? Say the total time."),
    ("cook 10 more at 380 then 5 more", "on top of what? Say the total time."),
]


@pytest.mark.parametrize(("utterance", "said"), UNCLEAR, ids=[u for u, _ in UNCLEAR])
def test_unclear_requests_start_nothing(utterance, said):
    p = plan(utterance)
    assert not p.runnable, p.describe()
    assert said in p.question
    assert p.minutes is None


@pytest.mark.parametrize(
    ("utterance", "temp_f", "minutes"),
    [
        ("air fry for 10 minutes then 5 more minutes at 400", 400, 15),
        ("fries 10 minutes then 5 more minutes", 400, 15),
        ("air fry for 12 min at 380 and shake after 6 min", 380, 12),
        ("air fry frozen fries", 400, 15),
    ],
)
def test_clear_requests_still_run(utterance, temp_f, minutes):
    p = plan(utterance)
    assert p.runnable, p.describe()
    assert (p.temp_f, p.minutes) == (temp_f, minutes)
    assert f"{temp_f}°F for {minutes} min" in p.describe()


def test_keep_warm_still_caps():
    p = plan("keep it warm for 120")
    assert p.minutes == 60 and "capped at 60" in p.describe()


@pytest.mark.parametrize(
    ("utterance", "said"),
    [
        ("fries 12 min and 7 min", "Did you mean 12 or 7 minutes?"),
        ("air fry for 12 minutes and 7 minutes at 400", "Did you mean 12 or 7 minutes?"),
        ("fries 12 to 15 minutes", "Did you mean 12 or 15 minutes?"),
        ("keep it warm for 20 minutes and 10 minutes", "Did you mean 20 or 10 minutes?"),
    ],
)
def test_two_times_for_one_item_ask(utterance, said):
    p = plan(utterance)
    assert not p.runnable and p.minutes is None
    assert said in p.question


@pytest.mark.parametrize(
    ("utterance", "minutes"),
    [
        ("air fry for 10 minutes then 5 more minutes at 400", 15),
        ("air fry for 2 minutes 30 seconds at 380", 3),
        ("air fry for 20 minutes and 30 seconds at 380", 21),
    ],
)
def test_one_duration_still_adds_up(utterance, minutes):
    p = plan(utterance)
    assert p.runnable and p.minutes == minutes


# Everyday requests: (intent, temp °F, minutes, needs an answer)
EVERYDAY = [
    ("frozen fries", ("cook", 400, 15, False)),
    ("chicken tenders x6 at 400 for 12 min", ("cook", 400, 12, False)),
    ("2 lb wings 25 minutes", ("cook", 380, 25, False)),
    ("salmon 8 oz 400 for 10", ("cook", 400, 10, False)),
    ("reheat pizza", ("cook", 350, 4, False)),
    ("keep warm I'm 10 min out", ("keep_warm", 170, 15, False)),
    ("preheat to 380", ("preheat", 380, None, False)),
    ("stop", ("stop", None, None, False)),
]


@pytest.mark.parametrize(("utterance", "expected"), EVERYDAY, ids=[u for u, _ in EVERYDAY])
def test_everyday_requests_unchanged(utterance, expected):
    p = plan(utterance)
    assert (p.intent, p.temp_f, p.minutes, not p.runnable) == expected, p.describe()


@pytest.mark.parametrize(
    ("utterance", "said"),
    [
        ("fries at 400 for 12 minutes and keep warm for 10 minutes", "one at a time"),
        ("air fry fries 12 minutes then keep warm 10 minutes", "one at a time"),
    ],
)
def test_cook_and_keep_warm_is_named_before_two_times(utterance, said):
    p = plan(utterance)
    assert not p.runnable and said in p.question


@pytest.mark.parametrize(
    "utterance",
    ["air fry at 380 - 12 minutes", "air fry at 380-12 minutes", "air fry 380 to 12 minutes"],
)
def test_a_temperature_next_to_a_time_is_not_a_range(utterance):
    p = plan(utterance)
    assert p.runnable, p.describe()
    assert (p.temp_f, p.minutes) == (380, 12)
