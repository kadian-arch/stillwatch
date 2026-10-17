# Reporting a vulnerability

Please do not open a public issue.

Report it through [GitHub's private security advisories][advisory] on this
repository, or to the maintainer at <donalsienkum@gmail.com>. Say what you
found, how to reproduce it and what it lets somebody do. You will get an
acknowledgement within a week, and an honest answer about whether and when it
will be fixed.

This is a small project maintained by one person, so there is no bounty and no
service level agreement. What there is, is a direct line and a straight answer.

[advisory]: https://github.com/kadian-arch/stillwatch/security/advisories/new

## What is worth looking at

The service holds a record of when somebody's home was empty, and it can send
messages to their family. Both are worth attacking.

[docs/SECURITY.md](../docs/SECURITY.md) sets out what is already defended
against, including the webhook signing, the session cookies, the sign in
throttle and what the deployment refuses to do. Reading it first will save you
time on anything already covered.

## Out of scope

Reports about the recorded demonstration days, which are generated from a
seed and contain no real household, and anything requiring physical access to
the machine the service runs on.
