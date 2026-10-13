"""Checks Stillwatch from outside, on a schedule, and reports what it found.

Everything else that watches Stillwatch watches it from the inside. The
in-process watchdog runs in the same deployment. The CloudWatch heartbeat is
published outward from the same dyno. Both of those go quiet together if the
dyno goes quiet, which is the case they were built for, and an alarm on
missing data catches it.

What none of them catch is the deployment being perfectly healthy while the
site is unreachable: a certificate that expired, a DNS or CDN change, a router
that stopped sending traffic to the right place. The process is alive and
still publishing its pulse, and a caregiver opening the dashboard sees
nothing. The only way to find that is to ask from the outside, over the public
internet, the way a real visitor does.

So this runs on EventBridge, calls the public health endpoint, and publishes
what it saw to CloudWatch. It makes no judgement about the household, holds no
credentials for it, and reads nothing private: the health endpoint is open by
design and deliberately says nothing about whether anybody is home.

Runs on the Python 3.12 Lambda runtime with no packaging. `urllib` and
`boto3` are both already there.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

NAMESPACE = os.environ.get("NAMESPACE", "Stillwatch")
HOUSEHOLD = os.environ.get("HOUSEHOLD", "default")
URL = os.environ.get("HEALTH_URL", "https://stillwatch.tech/api/health")
TIMEOUT = float(os.environ.get("TIMEOUT_SECONDS", "10"))


def fetch(url, timeout):
    """Returns (status, body text, milliseconds). Raises nothing of its own."""
    started = time.time()
    request = urllib.request.Request(url, headers={"User-Agent": "stillwatch-check"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:
            body = reply.read().decode("utf-8", "replace")
            return reply.status, body, (time.time() - started) * 1000.0
    except urllib.error.HTTPError as error:
        # A 503 is an answer, and a useful one: the service is up and telling
        # us its database is unreachable. Worth reading rather than discarding.
        body = ""
        try:
            body = error.read().decode("utf-8", "replace")
        except Exception:
            pass
        return error.code, body, (time.time() - started) * 1000.0
    except Exception:
        return None, "", (time.time() - started) * 1000.0


def assess(status, body, millis):
    """What the metrics should say. Pure, so it can be tested without a network."""
    reachable = 0
    store_ok = 0
    answered = 1 if status is not None else 0

    payload = None
    if body:
        try:
            payload = json.loads(body)
        except ValueError:
            payload = None

    if status == 200 and isinstance(payload, dict):
        reachable = 1 if payload.get("ok") is True else 0
        store_ok = 1 if payload.get("store_ok") is not False else 0
    elif isinstance(payload, dict):
        # It answered and said it was unwell, which is still an answer.
        store_ok = 1 if payload.get("store_ok") is not False else 0

    return {
        "SiteReachable": reachable,
        "SiteAnswered": answered,
        "SiteStoreOk": store_ok,
        "SiteLatencyMs": round(millis, 1),
    }


def metric_data(values, household=None):
    household = household or HOUSEHOLD
    dimensions = [{"Name": "Household", "Value": household}]
    units = {"SiteLatencyMs": "Milliseconds"}
    return [
        {
            "MetricName": name,
            "Dimensions": dimensions,
            "Value": float(value),
            "Unit": units.get(name, "Count"),
        }
        for name, value in sorted(values.items())
    ]


def handler(event=None, context=None):
    status, body, millis = fetch(URL, TIMEOUT)
    values = assess(status, body, millis)

    import boto3

    boto3.client("cloudwatch").put_metric_data(
        Namespace=NAMESPACE, MetricData=metric_data(values))

    print(json.dumps({"url": URL, "status": status, **values}))
    return values


# Lambda's default handler setting is `lambda_function.lambda_handler`. Pasting
# this file into the console as-is then needs no configuration change.
lambda_handler = handler
