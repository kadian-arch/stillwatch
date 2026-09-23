"""What the web server runs in production.

Configuration comes from the environment, never from a file in the repository:

    STILLWATCH_RING_CLIENT_ID          from the Ring developer console
    STILLWATCH_RING_CLIENT_SECRET      from the Ring developer console
    STILLWATCH_RING_WEBHOOK_SECRET     the HMAC signature key, shown once
    STILLWATCH_PERSON                  whose home this is, for the wording
    DATABASE_URL                       Postgres, set by Heroku automatically
    STILLWATCH_DB                      fallback store when there is no Postgres
    STILLWATCH_SNS_TOPIC_ARN           optional, to send real notifications
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "stillwatch"))

from stillwatch.service import LiveSource, create_app
from stillwatch.store import EventStore

# Heroku sets DATABASE_URL. A container's own disk is wiped on every
# restart, and a baseline needs weeks of history, so Postgres is not
# optional in production.
store = EventStore(os.environ.get("DATABASE_URL")
                   or os.environ.get("STILLWATCH_DB", "events.db"))
app = create_app(
    LiveSource(store, person=os.environ.get("STILLWATCH_PERSON", "The household")),
    store=store,
    webhook_secret=os.environ.get("STILLWATCH_RING_WEBHOOK_SECRET", "").strip(),
)
