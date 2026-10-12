"""Reports Stillwatch's own vital signs to Amazon CloudWatch.

The product's worst failure is not a wrong judgement. It is a judge that has
stopped running, because a service that has died and a household where nothing
is wrong produce exactly the same thing: nothing.

`stillwatch watchdog` already guards against that, but it is not truly outside
the system it watches. It runs on the same scheduler, against the same
database, inside the same deployment. If the deployment is the thing that
broke, the watchdog is as likely to be broken with it, and the silence it was
built to catch is the silence it produces.

So the heartbeat is also published out of the deployment entirely, as a
CloudWatch metric. An alarm set on *missing data* then fires when nothing
arrives at all, which is precisely the case nothing inside the process can
report, and it reaches the same Amazon SNS topic the household notices go to.
The chain closes outside anything that can die quietly:

    judge runs -> CloudWatch metric -> alarm on missing data -> SNS -> a person

Nothing here is on the detection path. Every call is best effort: if
CloudWatch is unreachable, slow, or misconfigured, the judgement that produced
the numbers has already been made and delivered, and the reporting failure is
logged and dropped. Monitoring that can take down the thing it monitors is
worse than no monitoring.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

log = logging.getLogger("stillwatch.metrics")

NAMESPACE = "Stillwatch"

# The ladder as a number, so an alarm can be set on it without parsing words.
# Ordered by how much attention a person should pay, not alphabetically.
LADDER = {
    "NORMAL": 0,
    "SETTLED": 0,
    "QUIET": 1,
    "AWAY": 1,
    "UNKNOWN": 2,
    "CONCERN": 3,
    "ALERT": 4,
}


def _minutes_since(moment, now):
    if moment is None:
        return None
    return max(0.0, (now - moment).total_seconds() / 60.0)


class Reporter:
    """Publishes a few numbers per tick. Never raises, never blocks a decision."""

    def __init__(self, household, client=None, region=None, namespace=NAMESPACE,
                 endpoint_url=None):
        if client is None:
            import boto3

            client = boto3.client("cloudwatch", region_name=region,
                                  endpoint_url=endpoint_url)
        self.client = client
        self.household = household or "default"
        self.namespace = namespace or NAMESPACE

    def _datum(self, name, value, unit, at):
        return {
            "MetricName": name,
            "Dimensions": [{"Name": "Household", "Value": self.household}],
            "Timestamp": at,
            "Value": float(value),
            "Unit": unit,
        }

    def tick(self, state, at=None, last_event_at=None, last_delivery_at=None,
             stored_events=None):
        """One heartbeat, plus what the judgement was and how fresh its inputs are.

        `JudgeRan` is the one that matters. Its absence is the alarm, so it is
        published on every tick whatever else is or is not known.
        """
        at = at or datetime.now(timezone.utc)
        data = [
            self._datum("JudgeRan", 1, "Count", at),
            self._datum("StateLevel", LADDER.get(state, 2), "None", at),
        ]

        since_event = _minutes_since(last_event_at, at)
        if since_event is not None:
            data.append(self._datum("MinutesSinceEvent", since_event, "Count", at))

        since_delivery = _minutes_since(last_delivery_at, at)
        if since_delivery is not None:
            data.append(
                self._datum("MinutesSinceDelivery", since_delivery, "Count", at))

        if stored_events is not None:
            data.append(self._datum("StoredEvents", stored_events, "Count", at))

        return self._send(data)

    def delivery_failed(self, channel, at=None):
        """A notice that could not be sent. Worth an alarm of its own, because
        an undelivered alarm is indistinguishable from no alarm to the family."""
        at = at or datetime.now(timezone.utc)
        datum = self._datum("DeliveryFailed", 1, "Count", at)
        datum["Dimensions"] = datum["Dimensions"] + [
            {"Name": "Channel", "Value": str(channel)[:64]}]
        return self._send([datum])

    def _send(self, data):
        try:
            self.client.put_metric_data(Namespace=self.namespace, MetricData=data)
            return True
        except Exception as error:
            # Deliberately swallowed. The judgement has already been made and
            # sent; losing its telemetry must not cost anybody an alarm.
            log.warning("could not report to CloudWatch: %s", error)
            return False


def reporter_from_env(environ=None):
    """A reporter if one is configured, otherwise None.

    Off unless asked for, so a laptop, a test run and a fresh clone never reach
    for AWS credentials they do not have.
    """
    environ = os.environ if environ is None else environ
    if (environ.get("STILLWATCH_CLOUDWATCH") or "").strip().lower() not in (
            "1", "true", "yes", "on"):
        return None

    household = (environ.get("STILLWATCH_HOUSEHOLD") or "").strip()
    region = ((environ.get("STILLWATCH_CLOUDWATCH_REGION") or "").strip()
              or (environ.get("AWS_REGION") or "").strip() or None)
    namespace = (environ.get("STILLWATCH_CLOUDWATCH_NAMESPACE") or "").strip() or NAMESPACE

    try:
        return Reporter(household or "default", region=region, namespace=namespace)
    except Exception as error:
        # A missing boto3, or credentials that will not resolve. Same rule: the
        # product runs without its telemetry rather than refusing to start.
        log.warning("CloudWatch reporting asked for but not available: %s", error)
        return None
