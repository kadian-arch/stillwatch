"""Checks on the Bedrock narration. Run directly, exits non zero on failure.

Nothing here reaches AWS. The Bedrock client is stood in for, so the checks run
on any machine with no credentials and no network.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from stillwatch.monitor import ALERT, CONCERN, Assessment
from stillwatch.narrate import (
    BedrockNarrator,
    check,
    facts_for,
    narrator_from_env,
)
from stillwatch.notify import ALERT_NOTICE, CONCERN_NOTICE, MemoryChannel, Notifier
from stillwatch.rhythm import Anchor

PASSED = 0
FAILED = []


def report(label, condition, detail=""):
    global PASSED
    if condition:
        PASSED += 1
        print("  pass  %s" % label)
    else:
        FAILED.append(label)
        print("  FAIL  %s  %s" % (label, detail))


def section(title):
    print("\n%s" % title)


def at(hour, minute=0):
    return datetime(2026, 9, 20, hour, minute, tzinfo=timezone.utc)


def collapsed(**extra):
    defaults = dict(
        at=at(11, 0),
        state=ALERT,
        headline="No movement since 09:14, now 1h 46m, where 35 min is usual for a weekend at that hour.",
        reasons=["Last movement was in the Kitchen at 09:14, 1h 46m ago.",
                 "No door has been used since the house went quiet, so she is at home."],
        silence_seconds=6360.0,
        silence_began=at(9, 14),
        threshold_seconds=2100.0,
        last_device="kitchen",
        last_device_name="Kitchen",
        missed_anchors=[Anchor("living_room", "weekend", "morning", 589.0, 25.0, 1.0, 8)],
    )
    defaults.update(extra)
    return Assessment(**defaults)


class FakeBedrock:
    """Stands in for bedrock-runtime, returning whatever the test wants."""

    def __init__(self, text="", fail=None):
        self.text = text
        self.fail = fail
        self.calls = []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise self.fail
        return {"output": {"message": {"content": [{"text": self.text}]}}}


def test_facts():
    section("what the model is allowed to know")
    facts = facts_for("Margaret", collapsed())
    report("it names who this is about", "Margaret" in facts)
    report("it gives the time she was last seen", "09:14" in facts)
    report("it names the room", "Kitchen" in facts)
    report("it gives how long she has been still", "1h 46m" in facts)
    report("it gives what is normal for her", "35 min" in facts)
    report("it says no door opened", "No door has opened" in facts, facts)

    out = facts_for("Margaret", collapsed(departure_at=at(8, 10)))
    report("when a door did open, it says so instead", "A door opened at 08:10" in out, out)


def test_refusals():
    section("what the model is not allowed to say")
    facts = facts_for("Margaret", collapsed())

    report("a made up time is refused",
           check("Margaret has not moved since 07:30.", facts) is not None)
    report("and the refusal names the invented number",
           "07" in check("Margaret has not moved since 07:30.", facts))
    report("a real time from the facts is fine",
           check("Margaret has not moved since 09:14.", facts) is None)

    for word in ("alert", "system", "detected", "monitoring", "anomaly", "status"):
        report("it will not say %s" % word,
               check("The %s is raised." % word, facts) is not None)

    report("nothing at all is refused", check("", facts) is not None)
    report("whitespace only is refused", check("   \n  ", facts) is not None)
    report("a speech is refused", check("word " * 60, facts) is not None)
    report("two paragraphs are refused",
           check("One thing.\n\nAnother thing.", facts) is not None)

    good = "Margaret hasn't moved since 09:14, which is much longer than her usual quiet."
    report("a plain human sentence passes", check(good, facts) is None, str(check(good, facts)))


def test_the_model_writes_it():
    section("Bedrock writes the message")
    written = "Margaret hasn't moved since 09:14. No door has opened, so she's at home."
    bedrock = FakeBedrock(written)
    narrator = BedrockNarrator("some.model.id", client=bedrock)

    result = narrator.narrate("alert", "Margaret", collapsed(), "the fallback sentence")
    report("the model's words are used", result.text == written, result.text)
    report("and it is recorded as the model's", result.used_model is True)
    report("the model id is recorded", result.model_id == "some.model.id")

    sent = bedrock.calls[0]
    report("it asks the configured model", sent["modelId"] == "some.model.id")
    report("it keeps the reply short", sent["inferenceConfig"]["maxTokens"] <= 300)
    report("it keeps the model close to the facts",
           sent["inferenceConfig"]["temperature"] <= 0.3)

    asked = sent["messages"][0]["content"][0]["text"]
    report("the facts are given to it", "09:14" in asked and "Kitchen" in asked)
    report("the rules are given to it", "Use only the facts" in asked)
    report("it is told not to guess what happened", "do not know whether she has fallen" in asked)
    report("the urgency shapes the ask", "needs checking now" in asked, asked[-200:])


def test_the_model_cannot_make_it_wrong():
    """The point of the whole guard: a bad sentence never reaches the family."""
    section("when the model gets it wrong")
    fallback = "No movement since 09:14, now 1h 46m."

    liar = BedrockNarrator("m", client=FakeBedrock("She fell at 07:30 in the bathroom."))
    result = liar.narrate("alert", "Margaret", collapsed(), fallback)
    report("an invented time is thrown away", result.text == fallback, result.text)
    report("the fallback is not credited to the model", result.used_model is False)
    report("and the reason is recorded", "invented a number" in result.reason, result.reason)

    robot = BedrockNarrator("m", client=FakeBedrock("The system detected an anomaly."))
    result = robot.narrate("alert", "Margaret", collapsed(), fallback)
    report("robot language is thrown away", result.text == fallback)
    report("the reason names the word", "do not use" in result.reason, result.reason)

    silent = BedrockNarrator("m", client=FakeBedrock(""))
    report("an empty reply falls back",
           silent.narrate("alert", "Margaret", collapsed(), fallback).text == fallback)

    broken = BedrockNarrator("m", client=FakeBedrock(fail=TimeoutError("read timed out")))
    result = broken.narrate("alert", "Margaret", collapsed(), fallback)
    report("a timeout falls back rather than raising", result.text == fallback)
    report("the failure is recorded", "bedrock failed" in result.reason, result.reason)

    report("a narrator needs a model id", _raises(lambda: BedrockNarrator("", client=object())))


def _raises(action):
    try:
        action()
    except ValueError:
        return True
    return False


def test_it_reaches_the_message():
    section("the message a family actually gets")
    written = "Margaret hasn't moved since 09:14. No door has opened, so she's at home."
    channel = MemoryChannel()
    notifier = Notifier("Margaret", [channel],
                        narrator=BedrockNarrator("m", client=FakeBedrock(written)))

    notifier.observe(collapsed())
    notice = channel.notices[0]

    report("something was sent", notice.kind == ALERT_NOTICE, notice.kind)
    report("the model's sentence leads the message",
           notice.body.startswith(written), notice.body[:80])
    report("the reasoning still follows it", "Last movement was in the Kitchen" in notice.body)
    report("it is marked as written by the model", notice.written_by_model is True)
    report("the subject is still ours, not the model's",
           notice.subject.startswith("Stillwatch:"), notice.subject)

    plain = MemoryChannel()
    Notifier("Margaret", [plain]).observe(collapsed())
    report("with no narrator the message is unchanged",
           plain.notices[0].body.startswith("Please check on Margaret."),
           plain.notices[0].body[:60])
    report("and it is not credited to a model",
           plain.notices[0].written_by_model is False)

    lying = MemoryChannel()
    Notifier("Margaret", [lying],
             narrator=BedrockNarrator("m", client=FakeBedrock("She fell at 07:30."))
             ).observe(collapsed())
    report("a refused sentence sends our own words instead",
           lying.notices[0].body.startswith("Please check on Margaret."))
    report("and says the model did not write it",
           lying.notices[0].written_by_model is False)


def test_configuration():
    section("configuration")
    report("with nothing configured there is no narrator", narrator_from_env({}) is None)
    report("an empty model id is the same as none",
           narrator_from_env({"STILLWATCH_BEDROCK_MODEL_ID": "  "}) is None)


def main():
    for test in (
        test_facts,
        test_refusals,
        test_the_model_writes_it,
        test_the_model_cannot_make_it_wrong,
        test_it_reaches_the_message,
        test_configuration,
    ):
        test()

    print("\n%d checks passed, %d failed" % (PASSED, len(FAILED)))
    if FAILED:
        for label in FAILED:
            print("  failed: %s" % label)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
