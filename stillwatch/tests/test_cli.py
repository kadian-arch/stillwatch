"""Checks on the commands a person actually types.

Everything underneath the command line had a suite. The command line did not,
and a wrong attribute name in a line that only runs when Amazon Bedrock is
unavailable sat there through a green run of everything else. It reached the
person using it, in the middle of setting Bedrock up, which is the one moment
that line exists for.

The cheap paths are run as real subprocesses, because the thing worth proving
is that the command works when typed. The paths that would otherwise reach the
network are called in process with the narrator stood in for.
"""

from __future__ import annotations

import argparse
import io
import os
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["STILLWATCH_TZ"] = "UTC"

from stillwatch import cli, narrate
from stillwatch.narrate import Narration

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


def run(args, **environ):
    """The command as somebody would type it, in its own process."""
    env = dict(os.environ)
    # Nothing here may reach AWS, and nothing may read a developer's own keys.
    for name in ("STILLWATCH_SNS_TOPIC_ARN", "STILLWATCH_SMTP_HOST",
                 "STILLWATCH_EMAIL_TO", "STILLWATCH_BEDROCK_MODEL_ID",
                 "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_PROFILE"):
        env.pop(name, None)
    env.update(environ)
    finished = subprocess.run(
        [sys.executable, "-m", "stillwatch"] + args,
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=120)
    return finished.returncode, finished.stdout + finished.stderr


def say(narrator):
    """Run the test message with this narrator, and give back what it printed."""
    was = narrate.narrator_from_env
    narrate.narrator_from_env = lambda environ=None: narrator
    out = io.StringIO()
    try:
        with redirect_stdout(out):
            code = cli.cmd_notify_test(argparse.Namespace(person="Margarette"))
    finally:
        narrate.narrator_from_env = was
    return code, out.getvalue()


def test_it_runs_from_where_people_run_it():
    section("the command works from either folder")

    # Production runs from the repository root, where the name `stillwatch`
    # finds the outer folder rather than the package inside it. That answered
    # a correct command with a message about a package that cannot be
    # executed, on the deployed service, with no local way to notice.
    env = dict(os.environ)
    env["STILLWATCH_NOTICE_CONSOLE"] = "1"
    for name in ("STILLWATCH_SNS_TOPIC_ARN", "AWS_ACCESS_KEY_ID",
                 "AWS_SECRET_ACCESS_KEY", "STILLWATCH_BEDROCK_MODEL_ID"):
        env.pop(name, None)

    for where, label in ((ROOT.parent, "the repository root"),
                         (ROOT, "the package folder")):
        done = subprocess.run(
            [sys.executable, "-m", "stillwatch", "notify-test", "--person", "Margarette"],
            cwd=str(where), env=env, capture_output=True, text=True, timeout=120)
        check("it runs from %s" % label,
              done.returncode == 0 and "console  sent" in done.stdout,
              (done.stdout + done.stderr).strip()[:200])


def test_the_message_test_runs():
    section("the command that proves messages get out")

    code, out = run(["notify-test", "--person", "Margarette"])
    check("with nowhere to send, it refuses and says what to set",
          code == 2 and "STILLWATCH_SNS_TOPIC_ARN" in out, out.strip()[:160])

    code, out = run(["notify-test", "--person", "Margarette"],
                    STILLWATCH_NOTICE_CONSOLE="1")
    check("with a channel, it sends", code == 0 and "console  sent" in out,
          out.strip()[:160])
    check("and says plainly that no model was asked",
          "no model configured" in out, out.strip()[:160])
    check("the message names the household", "Margarette" in out)
    check("it says plainly that nothing has happened",
          "Nothing has happened" in out, out.strip()[:200])
    check("it says what a real notice would look like instead",
          "names what was last seen" in out, out.strip()[:200])
    check("and it does not call itself urgent, next to real alerts",
          "urgent" not in out.lower(), out.strip()[:200])


def test_it_reports_a_model_it_could_not_use():
    section("Bedrock unavailable, which is most of the time")

    class Refused:
        """A narrator that cannot reach the model, the commonest case of all."""

        def narrate_facts(self, kind, facts, fallback):
            return Narration(fallback, "us.amazon.nova-lite-v1:0", False,
                             "bedrock failed: the security token is invalid")

    os.environ["STILLWATCH_NOTICE_CONSOLE"] = "1"
    try:
        code, out = say(Refused())
    finally:
        os.environ.pop("STILLWATCH_NOTICE_CONSOLE", None)

    # This is the line that crashed. It read narration.note, and the field has
    # always been called reason.
    check("it says the model was not used", code == 0 and "not used" in out,
          out.strip()[:200])
    check("and gives the reason, which is the only way to fix it",
          "security token is invalid" in out, out.strip()[:200])
    check("the message still goes out in the engine's own words",
          "console  sent" in out and "Stillwatch is checking" in out,
          out.strip()[:200])


def test_it_uses_a_model_it_could_reach():
    section("Bedrock available")

    class Wrote:
        def narrate_facts(self, kind, facts, fallback):
            return Narration("All quiet at her home, and nothing to do.",
                             "us.amazon.nova-lite-v1:0", True)

    os.environ["STILLWATCH_NOTICE_CONSOLE"] = "1"
    try:
        code, out = say(Wrote())
    finally:
        os.environ.pop("STILLWATCH_NOTICE_CONSOLE", None)

    check("it shows what the model wrote", code == 0 and "wrote:" in out,
          out.strip()[:200])
    check("and that sentence is the one that gets sent",
          "All quiet at her home" in out, out.strip()[:200])
    check("the reasoning underneath is still the engine's",
          "names what was last seen" in out, out.strip()[:200])


def ask(answer, model="us.amazon.nova-lite-v1:0", region="us-east-1"):
    """Run the availability check against a stubbed Amazon."""

    class Stub:
        asked = None

        def get_foundation_model_availability(self, modelId):
            Stub.asked = modelId
            if isinstance(answer, Exception):
                raise answer
            return answer

    out = io.StringIO()
    stub = Stub()
    with redirect_stdout(out):
        code = cli.cmd_bedrock_check(argparse.Namespace(
            model=model, region=region, client=stub))
    return code, out.getvalue(), Stub.asked


def test_it_asks_amazon_why_the_model_refuses():
    section("the check that says why Bedrock will not answer")

    check("a geo profile is asked about as the model underneath it",
          cli.base_model("us.amazon.nova-lite-v1:0") == "amazon.nova-lite-v1:0")
    check("and a plain model id is left alone",
          cli.base_model("amazon.nova-lite-v1:0") == "amazon.nova-lite-v1:0")

    code, out, asked = ask({
        "regionAvailability": "AVAILABLE",
        "agreementAvailability": {"status": "AVAILABLE"},
        "entitlementAvailability": "AVAILABLE",
        "authorizationStatus": "NOT_AUTHORIZED",
    })
    check("it asks about the foundation model, not the routing label",
          asked == "amazon.nova-lite-v1:0", str(asked))
    check("an unauthorized account is named as the account's problem",
          code == 1 and "account itself is not authorized" in out,
          out.strip()[:200])
    check("and it says what clears it",
          "limited state" in out, out.strip()[:200])

    code, out, asked = ask({
        "regionAvailability": "AVAILABLE",
        "agreementAvailability": {"status": "NOT_AVAILABLE"},
        "entitlementAvailability": "NOT_AVAILABLE",
        "authorizationStatus": "AUTHORIZED",
    })
    check("terms not accepted is told apart from the account being blocked",
          code == 1 and "has not taken up this" in out, out.strip()[:200])

    code, out, asked = ask({
        "regionAvailability": "NOT_AVAILABLE",
        "agreementAvailability": {"status": "NOT_AVAILABLE"},
        "entitlementAvailability": "NOT_AVAILABLE",
        "authorizationStatus": "AUTHORIZED",
    })
    check("a model missing from the region is told apart from both",
          code == 1 and "not offered in us-east-1" in out, out.strip()[:200])

    code, out, asked = ask({
        "regionAvailability": "AVAILABLE",
        "agreementAvailability": {"status": "AVAILABLE"},
        "entitlementAvailability": "AVAILABLE",
        "authorizationStatus": "AUTHORIZED",
    })
    check("and when nothing is blocking, it says so and blames the key",
          code == 0 and "Nothing is blocking" in out, out.strip()[:200])


def test_the_watchdog_runs():
    section("the command that watches the watcher")

    with tempfile.TemporaryDirectory() as folder:
        db = str(Path(folder) / "events.db")

        code, out = run(["watchdog", "--db", db], STILLWATCH_NOTICE_CONSOLE="1")
        check("a service that has never judged anything is a fault",
              code == 1 and "stopped judging" in out, out.strip()[:200])
        check("so is a feed that has never arrived",
              "Nothing is reaching Stillwatch" in out, out.strip()[:200])

        code, out = run(["watchdog", "--db", db], STILLWATCH_NOTICE_CONSOLE="1")
        check("saying it again straight away is held back",
              "waiting for the" in out, out.strip()[:200])

        code, out = run(["watchdog", "--db", db, "--times", "1", "--force"],
                        STILLWATCH_NOTICE_CONSOLE="1")
        check("and forcing it says it anyway", "told console" in out,
              out.strip()[:200])


def main():
    for test in (
        test_it_runs_from_where_people_run_it,
        test_the_message_test_runs,
        test_it_reports_a_model_it_could_not_use,
        test_it_uses_a_model_it_could_reach,
        test_it_asks_amazon_why_the_model_refuses,
        test_the_watchdog_runs,
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
