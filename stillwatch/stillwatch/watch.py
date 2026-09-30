"""Judges the house on a timer and lets the notifier decide who hears.

The dashboard works out a judgement when somebody opens it. Nobody opens a
dashboard at four in the morning, which is when it matters, so this runs
whether anyone is looking or not.

The rhythm is relearned at most once an hour, because it is the expensive part
and a person's habits do not change in five minutes. The judgement itself runs
on every tick.

Notifier state lives in memory, so a restart can repeat a message that was
already sent. That is the safer direction: a caregiver hearing twice is a
nuisance, a caregiver never hearing is the thing this product exists to
prevent.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone

from .clock import local_date, midnight_before
from .monitor import assess
from .notify import Notifier, restore, state_of
from .rhythm import learn

TICK_MINUTES = 5
RELEARN_MINUTES = 60

log = logging.getLogger("stillwatch.watch")


class LiveWatcher:
    def __init__(self, store, person, channels, narrator=None, link=None,
                 tick_minutes=TICK_MINUTES, relearn_minutes=RELEARN_MINUTES):
        self.store = store
        self.notifier = restore(
            Notifier(person, channels, link=link, narrator=narrator,
                     answered=store.answer_for),
            store.load_state("notifier"))
        self.tick = timedelta(minutes=tick_minutes)
        self.relearn = timedelta(minutes=relearn_minutes)
        self._baseline = None
        self._roster = None
        self._learned_at = None
        self._stop = threading.Event()
        self._thread = None

    @property
    def channels(self):
        return [channel.name for channel in self.notifier.channels]

    def _refresh(self, now):
        stale = (self._learned_at is None
                 or now - self._learned_at >= self.relearn
                 or local_date(now) != local_date(self._learned_at))
        if not stale:
            return
        events = self.store.events()
        self._roster = self.store.roster()
        midnight = midnight_before(now)
        self._baseline = learn(events, self._roster, until=midnight,
                               confirmed=self.store.confirmed_quiet())
        self._learned_at = now
        log.info("relearned the rhythm from %d events", len(events))

    def once(self, now=None):
        """One judgement. Returns whatever it decided to send."""
        now = now or datetime.now(timezone.utc)
        self._refresh(now)
        if self._roster is None or not len(self._roster):
            return []

        # Events are read fresh every tick. The baseline is not, because it
        # describes weeks and cannot have moved since the last one.
        reading = assess(self.store.events(), self._baseline, self._roster, now,
                         last_contact=self.store.last_delivery_at())
        notices = self.notifier.observe(reading)
        # Written every time, not only when something was sent, so a restart
        # picks up exactly where this left off.
        self.store.save_state("notifier", state_of(self.notifier))
        for notice in notices:
            if notice.delivered:
                log.info("sent %s to %s", notice.kind, ", ".join(notice.delivered_to))
            else:
                log.warning("could not send %s: %s", notice.kind, "; ".join(notice.errors))
        return notices

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.once()
            except Exception:
                # A watcher that dies silently is worse than one that complains.
                log.exception("a judgement failed, carrying on")
            self._stop.wait(self.tick.total_seconds())

    def start(self):
        if self._thread is not None:
            return self
        self._thread = threading.Thread(target=self._loop, name="stillwatch-watch",
                                        daemon=True)
        self._thread.start()
        return self

    def stop(self, timeout=5):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
