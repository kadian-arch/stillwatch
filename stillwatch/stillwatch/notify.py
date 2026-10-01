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
import smtplib
import sys
from email.message import EmailMessage
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .clock import clock
from .monitor import ALERT, BLIND, CONCERN, NO_CONTACT, SETTLED, UNKNOWN, when_text
from .narrate import Narration
from .rhythm import human_duration

CONCERN_NOTICE = "concern"
ALERT_NOTICE = "alert"
REMINDER_NOTICE = "reminder"
ALL_CLEAR_NOTICE = "all_clear"
BLIND_NOTICE = "blind"
ANSWERED_NOTICE = "answered"

LOW = "low"
URGENT = "urgent"
INFO = "info"

SUBJECT_LIMIT = 100

# How long after the last message before reminding again, in minutes, and then
# no more. An unanswered alarm that repeats hourly for two days is not urgency,
# it is noise, and the sixth identical message is read less carefully than the
# first. Stillwatch says it once, chases it a few times over the first day, and
# then stops repeating itself while it keeps watching.
REMINDER_GAPS = (60, 120, 240, 480, 720)
REPEAT_MINUTES = REMINDER_GAPS[0]

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
    return clock(moment) if moment else "an unknown time"


def _subject(text):
    text = "Stillwatch: " + text
    return text if len(text) <= SUBJECT_LIMIT else text[: SUBJECT_LIMIT - 3].rstrip() + "..."


def _body(lead, reading, link):
    lines = [lead, ""]
    lines.extend("- " + reason for reason in reading.reasons)
    if link:
        lines.extend(["", "See what Stillwatch sees: " + link])
    return "\n".join(lines)


def compose(kind, person, reading, link=None, last=False):
    """The words a caregiver reads. One place, so it can be rewritten later."""
    since = when_text(reading.silence_began, reading.at)
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
        if last:
            lead += (" Stillwatch will not keep repeating this. It is still watching, and"
                     " will write again as soon as %s moves." % person)
        urgency = URGENT
    elif kind == ALL_CLEAR_NOTICE:
        subject = _subject("all clear, %s is moving again" % person)
        lead = "%s is moving again. %s" % (person, reading.headline)
        urgency = INFO
    else:
        subject = _subject("cannot see inside %s's home" % person)
        lead = reading.headline + " Nothing can be judged until the cameras are reachable again."
        urgency = LOW

    return subject, _body(lead, reading, link), urgency


def deliver(channels, notice):
    """Hand one notice to every channel, and record what became of it."""
    for channel in channels:
        try:
            channel.send(notice)
        except Exception as error:
            notice.errors.append("%s: %s" % (channel.name, error))
            continue
        notice.delivered_to.append(channel.name)
        # A log file accepting the message is not a caregiver hearing it.
        if getattr(channel, "reaches_people", True):
            notice.reached_people = True
    return notice


def answer_notice(person, outcome, by, at, note=None, episode=None):
    """What everybody else is told once one of them has looked.

    A family of three all get the alert, and without this all three phone. The
    name matters more than the words: knowing which of them has already been is
    the whole point of saying anything.
    """
    who = (by or "").strip() or "Somebody at the dashboard"

    if outcome == "fine":
        lead = "%s has checked on %s, and all is well." % (who, person)
    elif outcome == "away":
        lead = "%s says %s was out of the house, not still." % (who, person)
    elif outcome == "expected":
        lead = ("%s says this is normal for %s now, so Stillwatch will expect quiet"
                " like it at that hour from here on." % (who, person))
    elif outcome == "helped":
        lead = ("%s says something was wrong and it has been dealt with."
                % who)
    else:
        lead = "%s has answered." % who

    lines = [lead, ""]
    if note:
        lines.append("- They added: %s" % note)
    lines.append("- Nobody else needs to check. Stillwatch has stopped asking about this.")
    lines.append("- It is still watching, and will write again if anything changes.")

    return Notice(at=at, kind=ANSWERED_NOTICE, urgency=INFO,
                  subject=_subject("%s has been checked on" % person),
                  body="\n".join(lines), state=SETTLED, episode=episode)


def announce_answer(channels, person, outcome, by, at, note=None, episode=None):
    """Tell everyone an answer has come in. Returns the channels that took it."""
    if not channels:
        return []
    notice = answer_notice(person, outcome, by, at, note=note, episode=episode)
    deliver(channels, notice)
    return list(notice.delivered_to)


class Notifier:
    def __init__(self, person, channels, repeat_minutes=REPEAT_MINUTES, link=None,
                 hold_minutes=CONCERN_HOLD_MINUTES, narrator=None, answered=None):
        self.person = person
        self.narrator = narrator
        # Asked whether somebody has already looked into this stretch. A
        # caregiver who has phoned and found everyone well should not be
        # chased about it again.
        self.answered = answered or (lambda episode: None)
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
        self.reminders = 0
        self.sent = []

    def _link_for(self, reading):
        if callable(self.link):
            return self.link(reading)
        return self.link

    def _deliver(self, kind, reading, episode, last=False):  # noqa: C901
        subject, body, urgency = compose(kind, self.person, reading,
                                         self._link_for(reading), last=last)
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
        deliver(self.channels, notice)
        if notice.delivered:
            self.sent.append(notice)
        return notice

    def _decide(self, reading, episode):
        if episode is not None and self.level and self.answered(episode):
            # Somebody has looked. Stay quiet about it, but still say so when
            # she moves again, because that is the message people wait for.
            return ALL_CLEAR_NOTICE if episode != self.episode else None

        if reading.state == UNKNOWN:
            # Losing sight of the house is worth saying once. Not knowing yet,
            # because there is no history, is not.
            lost = reading.unknown_reason in (BLIND, NO_CONTACT)
            return BLIND_NOTICE if lost and not self.blind_told else None

        if self.level and episode != self.episode:
            return ALL_CLEAR_NOTICE

        if reading.state == ALERT:
            if self.level != ALERT_NOTICE or episode != self.episode:
                return ALERT_NOTICE
            if self.reminders >= len(REMINDER_GAPS) or self.last_sent is None:
                return None
            gap = timedelta(minutes=REMINDER_GAPS[self.reminders])
            return REMINDER_NOTICE if reading.at - self.last_sent >= gap else None

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
            self.reminders = 0
            return
        if episode != self.episode:
            self.reminders = 0
        if kind == REMINDER_NOTICE:
            self.reminders += 1
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
            last = (kind == REMINDER_NOTICE
                    and self.reminders == len(REMINDER_GAPS) - 1)
            notice = self._deliver(kind, reading, episode, last=last)
            notices.append(notice)
            if not notice.delivered:
                break
            self._commit(kind, reading, episode)
            # An all clear can be followed at once by news about a new spell.
            if kind != ALL_CLEAR_NOTICE:
                break
        return notices


def state_of(notifier):
    """Everything the notifier would forget if the process stopped."""
    return {
        "episode": notifier.episode,
        "level": notifier.level,
        "last_sent": notifier.last_sent.isoformat() if notifier.last_sent else None,
        "reminders": notifier.reminders,
        "blind_told": notifier.blind_told,
        "concern_episode": notifier.concern_episode,
        "concern_since": (notifier.concern_since.isoformat()
                          if notifier.concern_since else None),
    }


def restore(notifier, saved):
    """Put it back, so a restart does not tell everybody twice."""
    if not saved:
        return notifier
    moment = lambda value: datetime.fromisoformat(value) if value else None
    notifier.episode = saved.get("episode")
    notifier.level = saved.get("level")
    notifier.last_sent = moment(saved.get("last_sent"))
    notifier.reminders = int(saved.get("reminders") or 0)
    notifier.blind_told = bool(saved.get("blind_told"))
    notifier.concern_episode = saved.get("concern_episode")
    notifier.concern_since = moment(saved.get("concern_since"))
    return notifier


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
            clock(notice.at), notice.urgency, notice.subject, notice.body))
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


class EmailChannel:
    """Sends the notice as ordinary email.

    Amazon SNS is the better channel because a caregiver subscribes themselves
    and we never hold their address. This exists so a household is not left
    without notifications while that is unavailable.
    """

    name = "email"

    def __init__(self, host, port, sender, recipients, user=None, password=None,
                 starttls=True, timeout=20, connect=None):
        if not host or not sender or not recipients:
            raise ValueError("email needs a host, a sender and at least one recipient")
        self.host = host
        self.port = int(port)
        self.sender = sender
        self.recipients = list(recipients)
        self.user = user
        self.password = password
        self.starttls = starttls
        self.timeout = timeout
        self._open = connect

    def _connect(self):
        if self._open is not None:
            return self._open()
        if self.port == 465:
            return smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout)
        server = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
        if self.starttls:
            server.starttls()
        return server

    def send(self, notice):
        message = EmailMessage()
        message["From"] = self.sender
        message["To"] = ", ".join(self.recipients)
        message["Subject"] = notice.subject[:SUBJECT_LIMIT]
        message["X-Stillwatch-Urgency"] = notice.urgency
        message.set_content(notice.body)

        server = self._connect()
        try:
            if self.user:
                server.login(self.user, self.password or "")
            server.send_message(message)
        finally:
            try:
                server.quit()
            except Exception:
                pass


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
    host = environ.get("STILLWATCH_SMTP_HOST", "").strip()
    to = [a.strip() for a in environ.get("STILLWATCH_EMAIL_TO", "").split(",") if a.strip()]
    if host and to:
        channels.append(EmailChannel(
            host,
            environ.get("STILLWATCH_SMTP_PORT", "587"),
            environ.get("STILLWATCH_EMAIL_FROM", "").strip()
            or environ.get("STILLWATCH_SMTP_USER", "").strip(),
            to,
            user=environ.get("STILLWATCH_SMTP_USER", "").strip() or None,
            password=environ.get("STILLWATCH_SMTP_PASSWORD", ""),
        ))

    log = environ.get("STILLWATCH_NOTICE_LOG", "").strip()
    if log:
        channels.append(JsonLinesChannel(log))
    reaches_someone = any(getattr(c, "reaches_people", True) for c in channels)
    if environ.get("STILLWATCH_NOTICE_CONSOLE", "") == "1" or not reaches_someone:
        channels.append(ConsoleChannel())
    return channels
