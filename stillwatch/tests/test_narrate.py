"""Checks on the Bedrock narration. Run directly, exits non zero on failure.

Nothing here reaches AWS. The Bedrock client is stood in for, so the checks run
on any machine with no credentials and no network.
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Clocks are pinned, so a suite cannot pass or fail depending on where the
# machine running it happens to be.
os.environ["STILLWATCH_TZ"] = "UTC"

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
    facts = facts_for("Margarette", collapsed())
    report("it names who this is about", "Margarette" in facts)
    report("it gives the time she was last seen", "09:14" in facts)
    report("it names the room", "Kitchen" in facts)
    report("it gives how long she has been still", "1h 46m" in facts)
    report("it gives what is normal for her", "35 min" in facts)
    report("it says no door opened", "No door has opened" in facts, facts)

    out = facts_for("Margarette", collapsed(departure_at=at(8, 10)))
    report("when a door did open, it says so instead", "A door opened at 08:10" in out, out)


def test_refusals():
    section("what the model is not allowed to say")
    facts = facts_for("Margarette", collapsed())

    report("a made up time is refused",
           check("Margarette has not moved since 07:30.", facts) is not None)
    report("and the refusal names the invented number",
           "07" in check("Margarette has not moved since 07:30.", facts))
    report("a real time from the facts is fine",
           check("Margarette has not moved since 09:14.", facts) is None)

    for word in ("alert", "system", "detected", "monitoring", "anomaly", "status"):
        report("it will not say %s" % word,
               check("The %s is raised." % word, facts) is not None)

    report("nothing at all is refused", check("", facts) is not None)
    report("whitespace only is refused", check("   \n  ", facts) is not None)
    report("a speech is refused", check("word " * 60, facts) is not None)
    report("two paragraphs are refused",
           check("One thing.\n\nAnother thing.", facts) is not None)

    good = "Margarette hasn't moved since 09:14, which is much longer than her usual quiet."
    report("a plain human sentence passes", check(good, facts) is None, str(check(good, facts)))


def test_the_model_writes_it():
    section("Bedrock writes the message")
    written = "Margarette hasn't moved since 09:14. No door has opened, so she's at home."
    bedrock = FakeBedrock(written)
    narrator = BedrockNarrator("some.model.id", client=bedrock)

    result = narrator.narrate("alert", "Margarette", collapsed(), "the fallback sentence")
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
    result = liar.narrate("alert", "Margarette", collapsed(), fallback)
    report("an invented time is thrown away", result.text == fallback, result.text)
    report("the fallback is not credited to the model", result.used_model is False)
    report("and the reason is recorded", "invented a number" in result.reason, result.reason)

    robot = BedrockNarrator("m", client=FakeBedrock("The system detected an anomaly."))
    result = robot.narrate("alert", "Margarette", collapsed(), fallback)
    report("robot language is thrown away", result.text == fallback)
    report("the reason names the word", "do not use" in result.reason, result.reason)

    silent = BedrockNarrator("m", client=FakeBedrock(""))
    report("an empty reply falls back",
           silent.narrate("alert", "Margarette", collapsed(), fallback).text == fallback)

    broken = BedrockNarrator("m", client=FakeBedrock(fail=TimeoutError("read timed out")))
    result = broken.narrate("alert", "Margarette", collapsed(), fallback)
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
    written = "Margarette hasn't moved since 09:14. No door has opened, so she's at home."
    channel = MemoryChannel()
    notifier = Notifier("Margarette", [channel],
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
    Notifier("Margarette", [plain]).observe(collapsed())
    report("with no narrator the message is unchanged",
           plain.notices[0].body.startswith("Please check on Margarette."),
           plain.notices[0].body[:60])
    report("and it is not credited to a model",
           plain.notices[0].written_by_model is False)

    lying = MemoryChannel()
    Notifier("Margarette", [lying],
             narrator=BedrockNarrator("m", client=FakeBedrock("She fell at 07:30."))
             ).observe(collapsed())
    report("a refused sentence sends our own words instead",
           lying.notices[0].body.startswith("Please check on Margarette."))
    report("and says the model did not write it",
           lying.notices[0].written_by_model is False)


def test_a_model_that_wants_its_other_name():
    section("the model that refuses its own id")

    class Picky:
        """Refuses the plain id the way Bedrock does, takes the profile."""

        def __init__(self):
            self.tried = []

        def converse(self, **kwargs):
            self.tried.append(kwargs["modelId"])
            if not kwargs["modelId"].startswith("us."):
                raise RuntimeError(
                    "Invocation of model ID amazon.nova-lite-v1:0 with on-demand"
                    " throughput isn't supported. Retry your request with the ID"
                    " or ARN of an inference profile that contains this model.")
            return {"output": {"message": {"content": [{"text": "All quiet at her home."}]}}}

    client = Picky()
    narrator = BedrockNarrator("amazon.nova-lite-v1:0", client=client, region="us-east-1")
    first = narrator.narrate_facts("all_clear", "Who lives here: Margarette", "fallback")

    report("it tries the plain id first", client.tried[0] == "amazon.nova-lite-v1:0")
    report("then the cross region profile", client.tried[1] == "us.amazon.nova-lite-v1:0")
    report("and the message is written", first.used_model and first.text == "All quiet at her home.")
    report("the second name is remembered", narrator.model_id == "us.amazon.nova-lite-v1:0")

    narrator.narrate_facts("all_clear", "Who lives here: Margarette", "fallback")
    report("so the failing call is never paid for twice",
          client.tried[2:] == ["us.amazon.nova-lite-v1:0"], str(client.tried))

    # The other way round, which is the one that actually happened. The
    # account was authorized for the model and could not use the profile, and
    # Bedrock said only "Operation not allowed", naming neither.
    class Reverse:
        """Refuses the profile, takes the plain id."""

        def __init__(self):
            self.tried = []

        def converse(self, **kwargs):
            self.tried.append(kwargs["modelId"])
            if kwargs["modelId"].startswith("us."):
                raise RuntimeError("An error occurred (ValidationException) when"
                                   " calling the Converse operation: Operation"
                                   " not allowed")
            return {"output": {"message": {"content": [{"text": "All quiet at her home."}]}}}

    back = Reverse()
    narrator = BedrockNarrator("us.amazon.nova-lite-v1:0", client=back,
                               region="us-east-1")
    said = narrator.narrate_facts("all_clear", "Who lives here: Margarette", "fallback")
    report("a profile that is refused falls back to the plain id",
          back.tried == ["us.amazon.nova-lite-v1:0", "amazon.nova-lite-v1:0"],
          str(back.tried))
    report("and the message is written after all",
          said.used_model and said.text == "All quiet at her home.")

    # When neither name works the engine's own sentence goes out, and the
    # reason names both attempts, because one of them is the real complaint.
    dead = BedrockNarrator("amazon.nova-lite-v1:0",
                           client=FakeBedrock(fail=RuntimeError("no credentials")),
                           region="us-east-1")
    said = dead.narrate_facts("all_clear", "facts", "the engine's own sentence")
    report("with neither name working the engine's sentence goes out",
          not said.used_model and said.text == "the engine's own sentence")
    report("and the reason says both names were tried",
          "no credentials" in said.reason and "us.amazon.nova-lite-v1:0" in said.reason,
          said.reason)

    # A region with no geography of its own gets no second guess.
    far = BedrockNarrator("amazon.nova-lite-v1:0", client=Picky(), region="ap-south-1")
    report("and a region with no profile prefix does not invent one",
          far._other_id() is None)
    near = BedrockNarrator("us.amazon.nova-lite-v1:0", client=Picky(), region="ap-south-1")
    report("while a profile gives up its prefix whatever the region",
          near._other_id() == "amazon.nova-lite-v1:0")


def test_it_must_be_written_like_a_person_wrote_it():
    section("a message that looks unproof read is not sendable")

    facts = "Who lives here: Margarette\nWhat has happened: nothing."

    report("a lower case opening is refused",
           check("she is moving again.", facts) == "does not start with a capital letter")
    report("her name in lower case is refused",
           check("Stillwatch says margarette is fine.", facts)
           == "wrote Margarette as margarette")
    report("and so is the product's own name",
           check("Margarette is fine, stillwatch is still watching.", facts)
           == "wrote Stillwatch as stillwatch")
    report("shouting a name is refused too",
           check("MARGARETTE is moving again.", facts)
           == "wrote Margarette as MARGARETTE")
    report("a properly written sentence passes",
           check("Stillwatch has not seen Margarette move.", facts) is None)
    report("a name the facts never gave is not policed",
           check("Stillwatch has not seen her move.", facts) is None)

    # The whole point of refusing it: the engine's sentence goes instead.
    sloppy = BedrockNarrator(
        "amazon.nova-lite-v1:0", region="us-west-2",
        client=FakeBedrock(text="stillwatch is confirming it can reach you"
                                " and nothing is wrong with margarette."))
    said = sloppy.narrate_facts("all_clear", facts, "the engine's own sentence")
    report("so the real thing Bedrock wrote on the day is refused",
           not said.used_model and said.text == "the engine's own sentence")
    report("and the reason says what was wrong with it",
           said.reason == "does not start with a capital letter", said.reason)


def test_configuration():
    section("configuration")
    report("with nothing configured there is no narrator", narrator_from_env({}) is None)
    report("bedrock can be pointed at its own region",
           narrator_from_env({"STILLWATCH_BEDROCK_MODEL_ID": "amazon.nova-lite-v1:0",
                              "AWS_REGION": "us-east-1",
                              "STILLWATCH_BEDROCK_REGION": "us-west-2"}).region
           == "us-west-2")
    report("and falls back to the account's region when it is not",
           narrator_from_env({"STILLWATCH_BEDROCK_MODEL_ID": "amazon.nova-lite-v1:0",
                              "AWS_REGION": "us-east-1"}).region == "us-east-1")
    report("an empty model id is the same as none",
           narrator_from_env({"STILLWATCH_BEDROCK_MODEL_ID": "  "}) is None)


def main():
    for test in (
        test_facts,
        test_refusals,
        test_the_model_writes_it,
        test_the_model_cannot_make_it_wrong,
        test_it_reaches_the_message,
        test_a_model_that_wants_its_other_name,
        test_it_must_be_written_like_a_person_wrote_it,
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
