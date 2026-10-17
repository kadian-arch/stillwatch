**What this changes**

**Why**

If it changes a judgement, say which household this reads differently now, and
why the new reading is the right one.

**Checks**

- [ ] `python check.py` passes
- [ ] If behaviour changed, there is a check that fails without this change
- [ ] Nothing new requests, stores or analyses video
- [ ] Any new state still says why, in a sentence
- [ ] A failure in anything added reports that it cannot tell, rather than
      reporting calm
