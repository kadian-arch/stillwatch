"""Checks on when caregivers are told, and how. Run directly, exits non zero on failure."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Clocks are pinned, so a suite cannot pass or fail depending on where the
# machine running it happens to be.
os.environ["STILLWATCH_TZ"] = "UTC"
sys.path.insert(0, str(ROOT.parent / "ring-event-simulator"))

from ringsim import Simulator
from ringsim.devices import DEFAULT_DEVICES, manifest
from ringsim.scenarios import SCENARIOS

from stillwatch.model import Device, DeviceRoster, Event, normalise
from stillwatch.monitor import ALERT, CONCERN, NORMAL, Assessment, assess, walk
from stillwatch.notify import (
    ALERT_NOTICE,
    ALL_CLEAR_NOTICE,
    BLIND_NOTICE,
    CONCERN_NOTICE,
    INFO,
    LOW,
    REMINDER_GAPS,
    REMINDER_NOTICE,
    SUBJECT_LIMIT,
    URGENT,
    ConsoleChannel,
    EmailChannel,
    JsonLinesChannel,
    Notice,
    MemoryChannel,
    Notifier,
    SNSChannel,
    channels_from_env,
    compose,
    restore,
    state_of,
)
from stillwatch.rhythm import Baseline, learn

START = date(2026, 8, 24)
DAYS = 28
SEED = 7

PASSED = 0
FAILED = []


def check(label, condition, detail=""):
    global PASSED
    if condition:
        PASSED += 1
        print("  pass  %s" % label)
    else:
        FAILED.append(label)
        print("  FAIL  %s  %s" % (label, detail))


def section(title):
    print("\n%s" % title)


def roster():
    return DeviceRoster(
        Device(entry["device_id"], entry["name"], entry["zone_class"])
        for entry in manifest(DEFAULT_DEVICES)
    )


def at(day, hour, minute=0):
    return datetime.combine(day, time(hour, minute), tzinfo=timezone.utc)


def day_of(key):
    events, meta = SCENARIOS[key].build(Simulator(seed=SEED), START, DAYS)
    stream = [normalise(event.to_record()) for event in events]
    target = date.fromisoformat(meta["target_day"])
    baseline = learn(stream, roster(), until=at(target, 0))
    readings = walk(stream, baseline, roster(), at(target, 0), at(target, 23, 55), 5)
    return stream, baseline, target, meta, readings


def replay(readings, channels=None, **options):
    channel = MemoryChannel()
    notifier = Notifier("Margarette", channels or [channel], **options)
    for reading in readings:
        notifier.observe(reading)
    return notifier, channel


class FakeSNS:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def publish(self, **kwargs):
        if self.fail:
            raise ConnectionError("network unreachable")
        self.calls.append(kwargs)
        return {"MessageId": "m-%d" % len(self.calls)}


class Flaky:
    name = "flaky"

    def __init__(self, failures):
        self.failures = failures
        self.accepted = []

    def send(self, notice):
        if self.failures > 0:
            self.failures -= 1
            raise ConnectionError("try again")
        self.accepted.append(notice)


def reading(state, minute, began_minute, day=date(2026, 9, 20), **extra):
    return Assessment(
        at=at(day, 0) + timedelta(minutes=minute),
        state=state,
        headline="%s at %d." % (state, minute),
        reasons=["a reason"],
        silence_seconds=(minute - began_minute) * 60.0,
        silence_began=at(day, 0) + timedelta(minutes=began_minute),
        threshold_seconds=2100.0,
        **extra,
    )


def test_collapse():
    section("a collapse tells someone, then keeps telling them")
    _, _, _, _, readings = day_of("fall")
    notifier, channel = replay(readings)
    kinds = [notice.kind for notice in channel.notices]

    check("the first message is the low urgency one", kinds[:1] == [CONCERN_NOTICE], str(kinds[:3]))
    check("then an urgent alert", kinds[1:2] == [ALERT_NOTICE], str(kinds[:3]))
    check("then only reminders", set(kinds[2:]) <= {REMINDER_NOTICE}, str(kinds))
    check("there is exactly one concern and one alert",
          kinds.count(CONCERN_NOTICE) == 1 and kinds.count(ALERT_NOTICE) == 1, str(kinds))

    reminders = [n for n in channel.notices if n.kind == REMINDER_NOTICE]
    gaps = [round((b.at - a.at).total_seconds() / 60)
            for a, b in zip(channel.notices[1:], channel.notices[2:])]

    # An alarm that repeats hourly for two days is noise, and the sixth
    # identical message is read less carefully than the first.
    check("she is chased, but not endlessly", 0 < len(reminders) <= len(REMINDER_GAPS),
          str(len(reminders)))
    check("each reminder waits longer than the last",
          all(b >= a for a, b in zip(gaps, gaps[1:])), str(gaps))
    check("the first comes about an hour later", gaps and 55 <= gaps[0] <= 65, str(gaps[:1]))
    check("a whole day produces a handful of messages, not dozens",
          len(channel.notices) <= 8, str(len(channel.notices)))
    check("it has not run out of reminders within one day",
          "will not keep repeating" not in reminders[-1].body)
    check("urgency matches the kind",
          channel.notices[0].urgency == LOW
          and all(n.urgency == URGENT for n in channel.notices[1:]))

    concern_at = channel.notices[0].at
    first_concern = next(r for r in readings if r.state == CONCERN)
    first_alert = next(r for r in readings if r.state == ALERT)
    held = (concern_at - first_concern.at).total_seconds() / 60
    check("the low urgency message waits for the concern to hold", 15 <= held < 20, "%.0f min" % held)
    check("the urgent one goes the moment alert is reached",
          channel.notices[1].at == first_alert.at,
          "%s vs %s" % (channel.notices[1].at, first_alert.at))
    check("nothing is sent before she stopped moving",
          all(n.at >= readings[0].at for n in channel.notices))
    check("no all clear, because she never moves again",
          ALL_CLEAR_NOTICE not in kinds)


def test_nothing_needed_saying():
    section("days that must not reach a phone")
    for key in ("away", "long_sleep", "visitor_while_out", "normal"):
        _, _, _, _, readings = day_of(key)
        _, channel = replay(readings)
        check("%s sends nothing" % key, not channel.notices,
              ", ".join("%s at %s" % (n.kind, n.at.strftime("%H:%M")) for n in channel.notices))


def test_broken_camera():
    section("a broken camera is at most a low urgency message")
    _, _, _, _, readings = day_of("camera_offline")
    _, channel = replay(readings)
    check("nothing urgent is sent", all(n.urgency != URGENT for n in channel.notices),
          str([n.kind for n in channel.notices]))
    if channel.notices:
        check("what is sent names the camera",
              all("Kitchen" in n.body for n in channel.notices if n.kind == CONCERN_NOTICE))


def test_all_clear():
    section("she moves again")
    sequence = [
        reading(NORMAL, 600, 590),
        reading(CONCERN, 660, 590),
        reading(CONCERN, 670, 590),
        reading(CONCERN, 680, 590),
        reading(ALERT, 700, 590),
        reading(NORMAL, 710, 706),
        reading(NORMAL, 715, 712),
    ]
    _, channel = replay(sequence)
    kinds = [n.kind for n in channel.notices]
    check("concern, alert, then all clear", kinds == [CONCERN_NOTICE, ALERT_NOTICE, ALL_CLEAR_NOTICE],
          str(kinds))
    check("the all clear is informational", channel.notices[-1].urgency == INFO)
    check("the all clear goes as soon as she moves", channel.notices[-1].at == sequence[5].at)

    blip = [reading(CONCERN, 660, 600), reading(CONCERN, 665, 600), reading(NORMAL, 670, 668)]
    _, channel = replay(blip)
    check("a concern that clears within the hold sends nothing", not channel.notices,
          str([n.kind for n in channel.notices]))

    quiet_only = [reading(NORMAL, 600, 590), reading("QUIET", 640, 590), reading(NORMAL, 650, 648)]
    _, channel = replay(quiet_only)
    check("no all clear when nobody was worried", not channel.notices,
          str([n.kind for n in channel.notices]))


def test_second_spell_same_day():
    section("a second quiet spell starts fresh")
    sequence = [
        reading(CONCERN, 660, 600),
        reading(CONCERN, 680, 600),
        reading(NORMAL, 690, 688),
        reading(CONCERN, 900, 840),
        reading(CONCERN, 920, 840),
    ]
    _, channel = replay(sequence)
    kinds = [n.kind for n in channel.notices]
    check("the second spell gets its own message",
          kinds == [CONCERN_NOTICE, ALL_CLEAR_NOTICE, CONCERN_NOTICE], str(kinds))

    sudden = [reading(ALERT, 700, 590), reading(ALERT, 900, 850)]
    _, channel = replay(sudden)
    kinds = [n.kind for n in channel.notices]
    check("a new alert straight after an old one is cleared first, then sent",
          kinds == [ALERT_NOTICE, ALL_CLEAR_NOTICE, ALERT_NOTICE], str(kinds))


def test_failed_delivery_is_retried():
    section("a failed send is not a sent message")
    flaky = Flaky(failures=2)
    sequence = [reading(ALERT, 700 + step * 5, 590) for step in range(5)]
    notifier = Notifier("Margarette", [flaky])
    outcomes = [notifier.observe(item) for item in sequence]

    check("the first two attempts fail and are reported",
          outcomes[0] and not outcomes[0][0].delivered and outcomes[0][0].errors
          and outcomes[1] and not outcomes[1][0].delivered)
    check("the third attempt gets through", len(flaky.accepted) == 1 and flaky.accepted[0].kind == ALERT_NOTICE)
    check("once through it is not sent again", len(flaky.accepted) == 1 and not outcomes[3] and not outcomes[4])

    log = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
    log.close()
    dead = FakeSNS(fail=True)
    notifier = Notifier("Margarette", [SNSChannel("arn:aws:sns:eu-west-1:1:t", client=dead),
                                     JsonLinesChannel(log.name)])
    result = notifier.observe(reading(ALERT, 700, 590))[0]
    check("a log accepting it does not count as a person told", not result.delivered)
    check("the failure names the channel", any(error.startswith("sns") for error in result.errors),
          str(result.errors))
    Path(log.name).unlink()


def test_blind_house():
    section("every camera down")
    sequence = [
        Assessment(at=at(date(2026, 9, 20), 10) + timedelta(minutes=5 * step), state="UNKNOWN",
                   headline="Cannot tell. Every camera inside the house is offline.",
                   reasons=["Offline: everything."], devices_down=["kitchen"], unknown_reason="blind")
        for step in range(4)
    ]
    _, channel = replay(sequence)
    check("one message saying we cannot see", [n.kind for n in channel.notices] == [BLIND_NOTICE])

    unlearned = [Assessment(at=at(date(2026, 9, 20), 10), state="UNKNOWN", headline="Cannot tell yet.",
                            reasons=["no baseline"], unknown_reason="no_baseline")]
    _, channel = replay(unlearned)
    check("not having learned yet is not news", not channel.notices)


def test_sns_channel():
    section("the Amazon SNS channel")
    fake = FakeSNS()
    channel = SNSChannel("arn:aws:sns:eu-west-1:123456789012:stillwatch", client=fake)
    _, _, _, _, readings = day_of("fall")
    notifier = Notifier("Margarette", [channel], link="https://example.invalid/view")
    for item in readings:
        notifier.observe(item)

    check("every notice was published", len(fake.calls) == len(notifier.sent) and fake.calls)
    first = fake.calls[0]
    check("it publishes to the configured topic", first["TopicArn"].endswith(":stillwatch"))
    check("subjects fit the SNS limit", all(len(call["Subject"]) <= SUBJECT_LIMIT for call in fake.calls))
    check("urgency travels as a message attribute",
          first["MessageAttributes"]["urgency"] == {"DataType": "String", "StringValue": LOW})
    check("the urgent one is marked urgent",
          fake.calls[1]["MessageAttributes"]["urgency"]["StringValue"] == URGENT)
    check("the body carries the reasoning and the link",
          "- " in first["Message"] and "https://example.invalid/view" in first["Message"])
    check("an empty topic is refused", _raises(lambda: SNSChannel("", client=fake)))


def _raises(action):
    try:
        action()
    except ValueError:
        return True
    return False


def test_configuration():
    section("configuration")
    channels = channels_from_env({})
    check("with nothing configured it only prints", [c.name for c in channels] == ["console"])

    with tempfile.TemporaryDirectory() as folder:
        log = str(Path(folder) / "sent.jsonl")
        channels = channels_from_env({"STILLWATCH_NOTICE_LOG": log})
        check("a log alone still prints, so nothing is silently swallowed",
              [c.name for c in channels] == ["log", "console"])

        stream = io.StringIO()
        notifier = Notifier("Margarette", [JsonLinesChannel(log), ConsoleChannel(stream)])
        notifier.observe(reading(ALERT, 700, 590))
        record = json.loads(Path(log).read_text(encoding="utf-8").splitlines()[0])
        check("the log holds the notice", record["kind"] == ALERT_NOTICE)
        check("the console shows it", "urgent" in stream.getvalue())


class FakeSMTP:
    """Stands in for a mail server, so the tests never touch the network."""

    def __init__(self, log):
        self.log = log
        self.logged_in = None

    def login(self, user, password):
        self.logged_in = user

    def send_message(self, message):
        self.log.append(message)

    def quit(self):
        self.log.append("quit")


def test_email_channel():
    section("email reaches a caregiver")

    sent = []
    channel = EmailChannel(
        "smtp.example.invalid", 587, "watch@example.invalid",
        ["daughter@example.invalid", "son@example.invalid"],
        user="watch@example.invalid", password="secret",
        connect=lambda: FakeSMTP(sent),
    )

    judged = reading(ALERT, 560, 420)
    subject, body, urgency = compose(ALERT_NOTICE, "Margarette", judged)
    channel.send(Notice(at=judged.at, kind=ALERT_NOTICE, urgency=urgency,
                        subject=subject, body=body, state=ALERT))

    messages = [item for item in sent if item != "quit"]
    check("one message went out", len(messages) == 1, str(len(messages)))
    check("both caregivers are on it",
          "daughter@example.invalid" in messages[0]["To"]
          and "son@example.invalid" in messages[0]["To"])
    check("the subject is the notice subject", messages[0]["Subject"] == subject)
    check("the urgency travels as a header",
          messages[0]["X-Stillwatch-Urgency"] == urgency)
    check("the reasoning is in the body",
          judged.reasons[0] in messages[0].get_content())
    check("the connection is always closed", sent[-1] == "quit")

    check("email counts as reaching somebody",
          getattr(channel, "reaches_people", True) is True)


def test_email_from_environment():
    section("email is configured from the environment")

    channels = channels_from_env({
        "STILLWATCH_SMTP_HOST": "smtp.example.invalid",
        "STILLWATCH_SMTP_PORT": "587",
        "STILLWATCH_SMTP_USER": "watch@example.invalid",
        "STILLWATCH_SMTP_PASSWORD": "secret",
        "STILLWATCH_EMAIL_TO": "daughter@example.invalid, son@example.invalid",
    })
    names = [channel.name for channel in channels]
    check("an email channel is built", "email" in names, str(names))
    check("no console fallback is added once somebody is reachable",
          "console" not in names, str(names))

    email = [c for c in channels if c.name == "email"][0]
    check("both addresses are split out", len(email.recipients) == 2)
    check("the sender falls back to the account",
          email.sender == "watch@example.invalid")

    check("nothing is built without recipients",
          "email" not in [c.name for c in channels_from_env(
              {"STILLWATCH_SMTP_HOST": "smtp.example.invalid"})])


def test_live_watcher():
    section("the watcher judges with nobody looking")

    from stillwatch.store import EventStore
    from stillwatch.watch import LiveWatcher

    stream, _baseline, target, _meta, _readings = day_of("fall")

    folder = tempfile.mkdtemp()
    store = EventStore(str(Path(folder) / "watch.db"))
    store.remember_devices(roster())
    store.add_many(stream)

    channel = MemoryChannel()
    watcher = LiveWatcher(store, "Margarette", [channel])

    quiet_morning = at(target, 9, 30)
    watcher.once(now=quiet_morning)
    check("nothing is sent while the quiet is still normal", not channel.notices,
          str([n.kind for n in channel.notices]))

    # Well past anything she normally does at this hour.
    for minutes in range(0, 200, 5):
        watcher.once(now=quiet_morning + timedelta(minutes=minutes))

    kinds = [notice.kind for notice in channel.notices]
    check("a caregiver is told", bool(kinds), str(kinds))
    check("and told it is urgent", ALERT_NOTICE in kinds, str(kinds))
    check("the message names the room she was last seen in",
          any("Kitchen" in notice.body for notice in channel.notices))

    before = len(channel.notices)
    watcher.once(now=quiet_morning + timedelta(minutes=205))
    check("it does not repeat itself every five minutes",
          len(channel.notices) == before, str(len(channel.notices)))

    check("the rhythm is learned once, not every tick",
          watcher._learned_at is not None)


def test_a_silence_that_lasts_for_days():
    section("an alarm nobody answers")

    # Two days of unbroken alert, which is what a stopped feed or an
    # unanswered alarm actually looks like. This is the case that sent 80
    # emails before reminders were capped.
    channel = MemoryChannel()
    notifier = Notifier("Margarette", [channel])
    began = 0
    for step in range(0, 48 * 60, 5):
        notifier.observe(reading(ALERT, 60 + step, began))

    kinds = [n.kind for n in channel.notices]
    reminders = [n for n in channel.notices if n.kind == REMINDER_NOTICE]

    check("the alert itself is sent once", kinds.count(ALERT_NOTICE) == 1, str(kinds))
    check("reminders stop at the cap", len(reminders) == len(REMINDER_GAPS),
          str(len(reminders)))
    check("two days produce six messages, not eighty",
          len(channel.notices) == len(REMINDER_GAPS) + 1, str(len(channel.notices)))
    check("the last one says it will stop, without sounding curt",
          "will not keep repeating" in reminders[-1].body
          and "still watching" in reminders[-1].body, reminders[-1].body[:110])

    spread = (channel.notices[-1].at - channel.notices[0].at).total_seconds() / 3600
    # The gaps add up to 27 hours, so the last nudge lands just over a day in
    # and the second day is silent unless something changes.
    check("they are spread across roughly the first day",
          26 <= spread <= 28, "%.1f hours" % spread)


def test_answering_ends_the_chasing():
    section("somebody looks, and the chasing stops")

    said = {}
    channel = MemoryChannel()
    notifier = Notifier("Margarette", [channel], answered=said.get)

    began = 0
    for step in range(0, 6 * 60, 5):
        notifier.observe(reading(ALERT, 60 + step, began))
    before = len(channel.notices)
    check("without an answer she is chased", before >= 3, str(before))

    # The daughter phones, finds her well, and says so.
    episode = reading(ALERT, 60, began).silence_began.isoformat()
    said[episode] = {"episode": episode, "outcome": "fine"}

    for step in range(6 * 60, 48 * 60, 5):
        notifier.observe(reading(ALERT, 60 + step, began))
    check("after an answer nothing more is sent", len(channel.notices) == before,
          str(len(channel.notices)))

    # She gets up the next morning. That is still worth saying.
    notifier.observe(reading(NORMAL, 48 * 60 + 65, 48 * 60 + 60))
    kinds = [n.kind for n in channel.notices]
    check("but moving again is still announced", kinds[-1] == ALL_CLEAR_NOTICE, str(kinds[-1]))


def test_it_remembers_across_a_restart():
    section("a restart does not tell everybody twice")

    channel = MemoryChannel()
    notifier = Notifier("Margarette", [channel])
    began = 0
    for step in range(0, 3 * 60, 5):
        notifier.observe(reading(ALERT, 60 + step, began))
    before = len(channel.notices)
    check("messages went out", before >= 2, str(before))

    # The dyno restarts. Everything the notifier knew was in memory.
    saved = state_of(notifier)
    fresh = restore(Notifier("Margarette", [channel]), saved)

    check("it comes back knowing which stretch it was worrying about",
          fresh.episode == notifier.episode)
    check("and how many reminders it had already sent",
          fresh.reminders == notifier.reminders,
          "%d vs %d" % (fresh.reminders, notifier.reminders))

    for step in range(3 * 60, 4 * 60, 5):
        fresh.observe(reading(ALERT, 60 + step, began))
    after = [n.kind for n in channel.notices[before:]]
    check("the alert itself is never raised a second time",
          ALERT_NOTICE not in after, str(after))
    check("the reminder schedule simply carries on",
          set(after) <= {REMINDER_NOTICE}, str(after))


def main():
    for test in (
        test_collapse,
        test_nothing_needed_saying,
        test_broken_camera,
        test_all_clear,
        test_second_spell_same_day,
        test_failed_delivery_is_retried,
        test_blind_house,
        test_sns_channel,
        test_configuration,
        test_email_channel,
        test_email_from_environment,
        test_live_watcher,
        test_a_silence_that_lasts_for_days,
        test_answering_ends_the_chasing,
        test_it_remembers_across_a_restart,
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
