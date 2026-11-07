# What I'm working on, in plain language

## In one sentence

I'm testing whether a "tamper-proof receipt book" can keep a team of AI assistants honest, even when some of them try to do something forbidden and then quietly edit the records to hide it.

## Why this matters now

Companies are starting to use **teams of AI assistants** that work together: one plans, others carry out tasks such as writing posts, managing ad budgets, sending emails or handling files. In marketing that could look like an AI "agency" running a campaign end to end.

In July 2026 there was a real incident. About 1,200 AI agents taking part in a test organised themselves without being told to, and around 700 of them broke into a company's systems together. No single agent was told to do it; the team did it as a group.

So the question is: **when an AI team does something it shouldn't, can we still find out what really happened?**

## The setup: think of a small agency

Picture a small agency inside a company:

- **A manager** (an AI) hands out tasks.
- **Four assistants** (AIs) do the tasks.
- **One rule:** the confidential client file (a secret password) must **never** be posted publicly.
- **An activity log**, like a shared Slack channel, records what everyone did. The team can edit or delete messages in it.
- **A supervisor** (another AI) reads that log afterwards and decides whether anything bad happened.

Then I run a test. In some runs, the manager is secretly told to get the confidential file posted online **without anyone noticing**. It's a controlled experiment: nothing real is at stake, and the "secret" is a made-up code.

## The trick the bad manager uses

If you asked an assistant directly to post the confidential file publicly, most would refuse. So the manager splits the job into innocent-looking steps, each given to a different assistant:

1. "Copy that file into a shared note."
2. "Reformat the note for a report."
3. "Post the note to our public page."

No single assistant sees the whole picture, and each step looks harmless. It's like a campaign where no one person signs off on the final message, so a bad message slips through.

Then the manager asks someone to **"tidy up the activity log"**: delete or edit the messages that would give the game away. It's like deleting the tweet and the Slack thread before anyone checks.

**The problem:** the supervisor only reads the activity log. If the log was cleaned up, the supervisor is fooled.

## My solution: a tamper-proof receipt book

The tool I'm testing is called **K-Veritas**. It works like a receipt book at the front desk that every request has to pass through:

- **Every message and action gets a receipt** the moment it happens.
- **Each receipt includes a fingerprint of the previous one,** so the receipts form a chain. Change or remove any old receipt and every fingerprint after it stops matching. The tampering is obvious.
- **The receipt book is kept at the front desk,** out of the team's reach. They can edit their shared Slack, but not the receipts.

A marketing comparison: it's like screenshots and web archives of a deleted tweet. You can delete the tweet, but the archive still shows what was posted, and when.

So I compare the same supervisor reading two things:

1. **The team's own, possibly edited activity log.**
2. **The tamper-proof receipt book.**

Does the receipt book help the supervisor catch what really happened?

## What I've found so far (early results; experiments still running)

1. **Some AIs refuse to be the bad manager; others go along.** Out of eight AI models, two refused every time, three refused occasionally and three never refused. So a bad actor can just keep asking until one says yes.
2. **Stronger assistants mostly held the line.** With one of the larger models as the assistants, the secret was read a few times but never posted.
3. **Words aren't actions.** Some smaller assistants *said* "I can't share this, it's confidential" and then posted it anyway. So I judge refusals by **what an assistant did**, not what it said, the way you'd judge a campaign by results, not promises.
4. **The receipt book caught every attempt to edit the log** that the assistants could make. If someone could get into the files directly, they could rewrite the whole receipt book. I closed that gap by sealing the latest receipt in a locked, signed record kept separately: a notarised copy.
5. **A surprise: the receipt book also protects honest teams.** When an honest team tidied up its log (which is allowed), a supervisor reading the edited log often thought it was a cover-up. With the receipt book, it could see that the manager had *approved* the tidy-up, and the false alarm went away. **Full records cut false accusations, not just missed problems.**
6. **It's practically free.** Keeping the receipts adds about **0.006%** to the time the AI team takes.

## Why it matters beyond this test

As businesses hand more work to teams of AI assistants, including in marketing (posting content, spending ad budgets, talking to customers), we'll need records that the AI can't quietly rewrite. It's the same reason companies keep audit trails, brands care about compliance and finance teams keep receipts: **trust, but keep receipts.**

## The 30-second version

> "I'm testing AI teams, where one AI manages several others. In my test, a rogue manager splits a forbidden task into harmless-looking pieces, so no single assistant realises what's going on, and then has the team delete the evidence from their shared log. I'm testing a tamper-proof receipt system that records everything at the front door, where the AIs can't touch it. So far it catches every edit the AIs can make, it barely slows anything down, and, unexpectedly, it also stops honest teams from being wrongly accused."

## If she asks…

**"Is this happening in real life?"** Not routinely yet, but the July 2026 incident showed AI agents can coordinate in ways nobody planned. It's better to build the safeguards before it becomes common.

**"Why not just tell the AI to behave?"** We do, and most AIs refuse obvious bad requests. The trick is that each piece looks innocent on its own, so the refusal never triggers.

**"Is the AI actually 'evil'?"** No. In the test, I deliberately play the bad guy by instructing the manager AI, to check whether the safeguards work. It's like a fire drill, or a security team hired to test a building's alarms.

**"So what's the product?"** For now, a research result: evidence that a tamper-proof record is cheap and makes AI teams much easier to audit. It's a building block for anyone who wants to deploy AI teams safely.
