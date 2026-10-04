---
name: Something is wrong with the output
about: Events that are not what they should be, or a run that does not reproduce
labels: bug
---

**The command.** The exact one, including the seed, so the stream can be
reproduced:

```
python -m ringsim --scenario ... --days ... --seed ...
```

**What you expected.**

**What you got.** A few of the offending lines is usually enough.

**Does it reproduce?** Running the same command twice should produce
byte-identical output. If it does not, say so first, because that is a
different and more serious problem than a wrong-looking day.

**Python version and operating system.**
