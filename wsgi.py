"""What the web server runs in production.

Configuration comes from the environment, never from a file in the repository:


    STILLWATCH_RING_CLIENT_ID          from the Ring developer console
    STILLWATCH_RING_CLIENT_SECRET      from the Ring developer console
    STILLWATCH_RING_WEBHOOK_SECRET     the HMAC signature key, shown once
    STILLWATCH_PERSON                  whose home this is, for the wording
    STILLWATCH_TZ                      the home's timezone, eg Europe/London
    DATABASE_URL                       Postgres, set by Heroku automatically
    STILLWATCH_DB                      fallback store when there is no Postgres
    STILLWATCH_SNS_TOPIC_ARN           optional, to send real notifications
    STILLWATCH_NOTIFY                  1 to judge on a timer and send messages
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "stillwatch"))

from stillwatch.service import CompositeSource, LiveSource, ReplaySource, create_app
from stillwatch.store import EventStore

# Heroku sets DATABASE_URL. A container's own disk is wiped on every
# restart, and a baseline needs weeks of history, so Postgres is not
# optional in production.
store = EventStore(os.environ.get("DATABASE_URL")
                   or os.environ.get("STILLWATCH_DB", "events.db"))

source = LiveSource(store, person=os.environ.get("STILLWATCH_PERSON", "The household"))

# Off unless a folder is named. A live deployment shows live events or it says
# it is standing by; recorded days are for looking at the engine offline, and
# are labelled as demonstrations wherever they do appear.
_demo_folder = os.environ.get("STILLWATCH_DEMO_DATA", "").strip()
if _demo_folder:
    demo = ReplaySource(_demo_folder)
    if demo.scenarios():
        source = CompositeSource(source, demo)

app = create_app(
    source,
    store=store,
    webhook_secret=os.environ.get("STILLWATCH_RING_WEBHOOK_SECRET", "").strip(),
)

# Nobody opens a dashboard at four in the morning, which is when it matters, so
# the judging runs on a timer inside the web process. It is off unless asked
# for, because a deployment with no events would otherwise start alerting about
# its own silence. This assumes a single worker; more than one would each hold
# their own notifier and a caregiver would hear everything twice.
if os.environ.get("STILLWATCH_NOTIFY", "").strip() == "1":
    from stillwatch.notify import channels_from_env
    from stillwatch.watch import LiveWatcher

    LiveWatcher(
        store,
        os.environ.get("STILLWATCH_PERSON", "The household"),
        channels_from_env(),
    ).start()
