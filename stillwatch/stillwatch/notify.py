"""Decides when a caregiver hears about it, and delivers the message.

The monitor judges the house every few minutes, and almost none of those
judgements should reach a phone. A quiet spell produces at most one low urgency
message, one urgent one if it gets worse, a reminder each hour while it lasts,
and an all clear when she moves again. Being out, being quiet, and being
normal never send anything.

Nothing counts as sent until a channel has accepted it. If delivery fails the
notifier keeps its old state, so the next assessment tries again rather than
an alert being lost to a network blip.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .monitor import ALERT, BLIND, CONCERN, UNKNOWN
from .narrate import Narration
from .rhythm import human_duration

CONCERN_NOTICE = "concern"
ALERT_NOTICE = "alert"
REMINDER_NOTICE = "reminder"
ALL_CLEAR_NOTICE = "all_clear"
BLIND_NOTICE = "blind"

LOW = "low"
URGENT = "urgent"
INFO = "info"

REPEAT_MINUTES = 60
SUBJECT_LIMIT = 100

# A low urgency message can wait a quarter of an hour to be sure the concern
# is holding. Sitting still a little too long after lunch should not reach a
# phone. An alert is never held back.
CONCERN_HOLD_MINUTES = 15


@dataclass
class Notice:
    at: datetime
    kind: str
    urgency: str
    subject: str
    body: str
    state: str
    episode: str = None
    delivered_to: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    reached_people: bool = False
    written_by_model: bool = False

    @property
    def delivered(self):
        return self.reached_people

    def to_dict(self):
        return {
            "at": self.at.isoformat(),
            "kind": self.kind,
            "urgency": self.urgency,
            "subject": self.subject,
            "body": self.body,
            "state": self.state,
            "episode": self.episode,
            "delivered_to": list(self.delivered_to),
            "errors": list(self.errors),
            "written_by_model": self.written_by_model,
        }


def _clock(moment):
    return moment.strftime("%H:%M") if moment else "an unknown time"


def _subject(text):
    text = "Stillwatch: " + text
    return text if len(text) <= SUBJECT_LIMIT else text[: SUBJECT_LIMIT - 3].rstrip() + "..."


def _body(lead, reading, link):
    lines = [lead, ""]
    lines.extend("- " + reason for reason in reading.reasons)
    if link:
        lines.extend(["", "See what Stillwatch sees: " + link])
    return "\n".join(lines)


def compose(kind, person, reading, link=None):
    """The words a caregiver reads. One place, so it can be rewritten later."""
    since = _clock(reading.silence_began)
    quiet = human_duration(reading.silence_seconds)

    if kind == CONCERN_NOTICE:
        subject = _subject("%s has been still since %s" % (person, since))
        lead = "Not urgent yet. " + reading.headline
        urgency = LOW
    elif kind == ALERT_NOTICE:
        subject = _subject("urgent, no movement from %s since %s" % (person, since))
        lead = "Please check on %s. %s" % (person, reading.headline)
        urgency = URGENT
    elif kind == REMINDER_NOTICE:
        subject = _subject("still no movement from %s, %s now" % (person, quiet))
        lead = "Still no movement. " + reading.headline
        urgency = URGENT
    elif kind == ALL_CLEAR_NOTICE:
        subject = _subject("all clear, %s is moving again" % person)
        lead = "%s is moving again. %s" % (person, reading.headline)
        urgency = INFO
    else:
        subject = _subject("cannot see inside %s's home" % person)
        lead = reading.headline + " Stillwatch cannot judge anything until a camera is back."
        urgency = LOW

    return subject, _body(lead, reading, link), urgency


class Notifier:
    def __init__(self, person, channels, repeat_minutes=REPEAT_MINUTES, link=None,
                 hold_minutes=CONCERN_HOLD_MINUTES, narrator=None):
        self.person = person
        self.narrator = narrator
        self.channels = list(channels)
        self.repeat = timedelta(minutes=repeat_minutes)
        self.hold = timedelta(minutes=hold_minutes)
        self.concern_since = None
        self.concern_episode = None
        self.link = link
        self.episode = None
        self.level = None
        self.last_sent = None
        self.blind_told = False
        self.sent = []

    def _link_for(self, reading):
        if callable(self.link):
            return self.link(reading)
        return self.link

    def _deliver(self, kind, reading, episode):
        subject, body, urgency = compose(kind, self.person, reading, self._link_for(reading))
        written_by_model = False

        if self.narrator is not None:
            # The model rewrites the opening sentence only. The reasoning
            # underneath it is ours and stays exactly as the engine produced it.
            lead, separator, rest = body.partition("\n")
            narration = self.narrator.narrate(kind, self.person, reading, lead)
            written_by_model = narration.used_model
            body = narration.text + separator + rest

        notice = Notice(at=reading.at, kind=kind, urgency=urgency, subject=subject,
                        body=body, state=reading.state, episode=episode,
                        written_by_model=written_by_model)
        for channel in self.channels:
            try:
                channel.send(notice)
            except Exception as error:
                notice.errors.append("%s: %s" % (channel.name, error))
                continue
            notice.delivered_to.append(channel.name)
            # A log file accepting the message is not a caregiver hearing it.
            if getattr(channel, "reaches_people", True):
                notice.reached_people = True
        if notice.delivered:
            self.sent.append(notice)
        return notice

    def _decide(self, reading, episode):
        if reading.state == UNKNOWN:
            blind = reading.unknown_reason == BLIND
            return BLIND_NOTICE if blind and not self.blind_told else None

        if self.level and episode != self.episode:
            return ALL_CLEAR_NOTICE

        if reading.state == ALERT:
            if self.level != ALERT_NOTICE or episode != self.episode:
                return ALERT_NOTICE
            if self.last_sent is not None and reading.at - self.last_sent >= self.repeat:
                return REMINDER_NOTICE
            return None

        if reading.state == CONCERN:
            held = self.concern_since is not None and reading.at - self.concern_since >= self.hold
            if held and (self.level is None or episode != self.episode):
                return CONCERN_NOTICE
        return None

    def _commit(self, kind, reading, episode):
        if kind == BLIND_NOTICE:
            self.blind_told = True
            return
        if kind == ALL_CLEAR_NOTICE:
            self.episode = None
            self.level = None
            self.last_sent = None
            return
        self.episode = episode
        self.last_sent = reading.at
        if kind in (ALERT_NOTICE, REMINDER_NOTICE):
            self.level = ALERT_NOTICE
        elif self.level is None:
            self.level = CONCERN_NOTICE

    def observe(self, reading):
        """Take one assessment, and return any notices it caused."""
        if reading.state != UNKNOWN:
            self.blind_told = False
        episode = reading.silence_began.isoformat() if reading.silence_began else None

        if reading.state in (CONCERN, ALERT):
            if self.concern_since is None or self.concern_episode != episode:
                self.concern_since = reading.at
                self.concern_episode = episode
        else:
            self.concern_since = None
            self.concern_episode = None

        notices = []
        for _ in range(2):
            kind = self._decide(reading, episode)
            if kind is None:
                break
            notice = self._deliver(kind, reading, episode)
            notices.append(notice)
            if not notice.delivered:
                break
            self._commit(kind, reading, episode)
            # An all clear can be followed at once by news about a new spell.
            if kind != ALL_CLEAR_NOTICE:
                break
        return notices


class MemoryChannel:
    name = "memory"

    def __init__(self):
        self.notices = []

    def send(self, notice):
        self.notices.append(notice)


class ConsoleChannel:
    name = "console"

    def __init__(self, stream=None):
        self.stream = stream or sys.stdout

    def send(self, notice):
        self.stream.write("[%s %s] %s\n%s\n\n" % (
            notice.at.strftime("%H:%M"), notice.urgency, notice.subject, notice.body))
        self.stream.flush()


class JsonLinesChannel:
    """Appends every notice to a file, which doubles as the sent log."""

    name = "log"
    reaches_people = False

    def __init__(self, path):
        self.path = path

    def send(self, notice):
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(notice.to_dict()) + "\n")


class SNSChannel:
    """Publishes to an Amazon SNS topic. Caregivers subscribe to the topic by
    email or text message, so Stillwatch never holds their contact details."""

    name = "sns"

    def __init__(self, topic_arn, client=None, region=None, endpoint_url=None):
        if not topic_arn:
            raise ValueError("an SNS topic ARN is required")
        if client is None:
            import boto3

            client = boto3.client("sns", region_name=region, endpoint_url=endpoint_url)
        self.topic_arn = topic_arn
        self.client = client

    def send(self, notice):
        self.client.publish(
            TopicArn=self.topic_arn,
            Subject=notice.subject[:SUBJECT_LIMIT],
            Message=notice.body,
            MessageAttributes={
                "urgency": {"DataType": "String", "StringValue": notice.urgency},
                "kind": {"DataType": "String", "StringValue": notice.kind},
            },
        )


def channels_from_env(environ=None):
    """Channels named by configuration. Nothing is sent anywhere unless asked."""
    environ = os.environ if environ is None else environ
    channels = []
    topic = environ.get("STILLWATCH_SNS_TOPIC_ARN", "").strip()
    if topic:
        channels.append(SNSChannel(
            topic,
            region=environ.get("AWS_REGION") or environ.get("AWS_DEFAULT_REGION"),
            endpoint_url=environ.get("STILLWATCH_SNS_ENDPOINT") or None,
        ))
    log = environ.get("STILLWATCH_NOTICE_LOG", "").strip()
    if log:
        channels.append(JsonLinesChannel(log))
    reaches_someone = any(getattr(c, "reaches_people", True) for c in channels)
    if environ.get("STILLWATCH_NOTICE_CONSOLE", "") == "1" or not reaches_someone:
        channels.append(ConsoleChannel())
    return channels
