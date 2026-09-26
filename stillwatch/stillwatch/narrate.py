"""Turns the reasoning into something a person would actually say.

Stillwatch already knows exactly why it is worried. What it produces on its own
reads like a system: correct, complete, and cold. A daughter reading it at three
in the morning does not need a report, she needs a sentence.

Amazon Bedrock writes that sentence. It is given the facts and nothing else, and
what it returns is checked before anyone sees it. If the model invents a number,
wanders off, goes quiet, or takes too long, the deterministic sentence is used
instead and nobody notices.

The model makes the message human. It can never make it wrong.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from .clock import clock
from .rhythm import human_duration

DEFAULT_REGION = "us-east-1"
DEFAULT_MAX_WORDS = 45
DEFAULT_TIMEOUT = 6

VOICE = """You write the message a family gets about an older relative living alone.

Write like a calm person who knows the family, not like a system. Short words.
Say the thing itself.

Rules you must not break:
- Use only the facts given to you. Never add a time, a number, a room or an
  event that is not in them.
- Never guess what happened. You do not know whether she has fallen. You know
  she has not moved.
- Two sentences at most.
- No greeting, no sign off, no exclamation marks, no capitals for emphasis.
- Do not tell them how to feel, and do not tell them it will be fine.
- Do not use the words alert, system, detected, monitoring, anomaly, status,
  or trigger.
- Plain contractions are fine. This is a message, not a letter."""

TONE = {
    "concern": "Nothing is certainly wrong. Say what you have noticed and let them decide.",
    "alert": "This needs checking now. Be direct and calm, never dramatic.",
    "reminder": "They already know. Say only what has changed, which is how long it has been.",
    "all_clear": "Good news. One short sentence is plenty.",
    "blind": "The cameras are down, so nothing can be judged. Be plain that this is a fault.",
}

BANNED = re.compile(
    r"\b(alert|system|detected|monitoring|anomaly|status|trigger|dear|regards)\b", re.I)
NUMBERS = re.compile(r"\d+")


@dataclass
class Narration:
    text: str
    model_id: str = None
    used_model: bool = False
    reason: str = None

    def to_dict(self):
        return {
            "text": self.text,
            "model_id": self.model_id,
            "used_model": self.used_model,
            "reason": self.reason,
        }


def facts_for(person, reading):
    """Everything the model is allowed to know, written out plainly."""
    lines = ["Who: %s, who lives alone." % person]
    if reading.silence_began is not None:
        lines.append("Last movement: %s, in the %s." % (
            clock(reading.silence_began),
            reading.last_device_name or reading.last_device or "house"))
    if reading.silence_seconds is not None:
        lines.append("Still since then: %s." % human_duration(reading.silence_seconds))
    if reading.threshold_seconds:
        lines.append("Normal for her at that hour: quiet spells up to %s."
                     % human_duration(reading.threshold_seconds))
    if reading.departure_at is not None:
        lines.append("A door opened at %s, so she may be out."
                     % clock(reading.departure_at))
    else:
        lines.append("No door has opened since, so she is at home.")
    for anchor in reading.missed_anchors[:2]:
        lines.append("Usually active by %s and has not been." % anchor.clock())
    if reading.devices_down:
        lines.append("Cameras not reporting: %s." % ", ".join(reading.devices_down))
    return "\n".join(lines)


def _numbers_in(text):
    return set(NUMBERS.findall(text))


def check(text, facts, max_words=DEFAULT_MAX_WORDS):
    """Why this text cannot be sent, or None if it can."""
    if not text or not text.strip():
        return "the model returned nothing"

    text = text.strip()
    if len(text.split()) > max_words:
        return "too long at %d words" % len(text.split())
    if BANNED.search(text):
        return "used a word we do not use: %s" % BANNED.search(text).group(0)
    if "\n\n" in text:
        return "more than one paragraph"

    invented = _numbers_in(text) - _numbers_in(facts)
    if invented:
        return "invented a number that is not in the facts: %s" % ", ".join(sorted(invented))
    return None


class BedrockNarrator:
    """Asks Amazon Bedrock for the sentence, and refuses it if it is not right."""

    def __init__(self, model_id, client=None, region=None, max_words=DEFAULT_MAX_WORDS,
                 timeout=DEFAULT_TIMEOUT, temperature=0.2):
        if not model_id:
            raise ValueError("a Bedrock model id is required")
        self.model_id = model_id
        self.max_words = max_words
        self.temperature = temperature
        if client is None:
            import boto3
            from botocore.config import Config

            client = boto3.client(
                "bedrock-runtime",
                region_name=region or os.environ.get("AWS_REGION", DEFAULT_REGION),
                config=Config(connect_timeout=timeout, read_timeout=timeout,
                              retries={"max_attempts": 1}),
            )
        self.client = client

    def _ask(self, facts, kind):
        instruction = "%s\n\n%s\n\nFacts:\n%s\n\nWrite the message." % (
            VOICE, TONE.get(kind, ""), facts)
        reply = self.client.converse(
            modelId=self.model_id,
            messages=[{"role": "user", "content": [{"text": instruction}]}],
            inferenceConfig={"maxTokens": 200, "temperature": self.temperature},
        )
        blocks = reply["output"]["message"]["content"]
        return "".join(block.get("text", "") for block in blocks).strip()

    def narrate(self, kind, person, reading, fallback):
        facts = facts_for(person, reading)
        try:
            text = self._ask(facts, kind)
        except Exception as error:
            return Narration(fallback, self.model_id, False, "bedrock failed: %s" % error)

        complaint = check(text, facts, self.max_words)
        if complaint:
            return Narration(fallback, self.model_id, False, complaint)
        return Narration(text, self.model_id, True)


def narrator_from_env(environ=None):
    """A narrator only if one is configured. Silence is the default."""
    environ = os.environ if environ is None else environ
    model_id = environ.get("STILLWATCH_BEDROCK_MODEL_ID", "").strip()
    if not model_id:
        return None
    return BedrockNarrator(model_id, region=environ.get("AWS_REGION"))
