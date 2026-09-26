"""What time it is in the house being watched.

Events are stored in UTC, because that is the only sane thing to store. But
every judgement this product makes is about a person's day: a silence that
began at 23:00 is a night silence, and the baseline that explains it is keyed
by the hour of the household's own clock. Reading those hours in UTC would
shift every learned threshold by the offset and quietly change what counts as
normal.

So the engine agrees on one timezone, the home's, set by STILLWATCH_TZ:

    STILLWATCH_TZ=Africa/Douala

Left unset it is UTC, which is correct for a household that is in UTC and
honest about it for one that is not.
"""

from __future__ import annotations

import os
from datetime import datetime, time, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

_ZONES = {}


def household_tz():
    """The home's timezone. Falls back to UTC rather than guessing."""
    name = os.environ.get("STILLWATCH_TZ", "").strip()
    if not name or ZoneInfo is None:
        return timezone.utc
    if name not in _ZONES:
        try:
            _ZONES[name] = ZoneInfo(name)
        except Exception:
            _ZONES[name] = timezone.utc
    return _ZONES[name]


def local(moment):
    """The same instant, read on the household's clock."""
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(household_tz())


def local_date(moment):
    return local(moment).date()


def local_hour(moment):
    return local(moment).hour


def clock(moment):
    return local(moment).strftime("%H:%M")


def midnight_on(day):
    """The start of that date in the household's own clock."""
    return datetime.combine(day, time(0, 0), tzinfo=household_tz())


def midnight_before(moment):
    """The start of the day the moment falls in, locally."""
    return midnight_on(local_date(moment))


def offset_minutes(moment=None):
    """How far the household's clock is from UTC, for showing a visitor."""
    moment = moment or datetime.now(timezone.utc)
    shift = local(moment).utcoffset()
    return int(shift.total_seconds() // 60) if shift else 0
