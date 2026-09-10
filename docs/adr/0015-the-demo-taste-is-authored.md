# The demo's taste is authored for the demo, not the owner's

Amends the "What it is made of" and "The taste on display" sections of [demo-account.md](../design/demo-account.md), and the neutral-presentation bullet of its "Read-only enforcement".
Decided 2026-09-09, while [#42](https://github.com/KyleKDang/anchor/issues/42) was waiting to merge and before [#108](https://github.com/KyleKDang/anchor/issues/108) had started.

The demo account was specified to run on the owner's own ratings: a default-out review pass over the real 592-row export, cut for embarrassment and keep for edge, landing somewhere in the hundreds of films.
Two problems with that surfaced once the enforcement was built and the account could actually be looked at.

The first is privacy, and it is the one that decides this.
A rating is a judgment, and several hundred of them together are a fairly complete picture of what somebody likes, is bored by, and thinks is overrated.
The owner does not want that published, and "allowlisted" does not fix it: a default-out pass still ships several hundred of their real opinions to anybody with the link, and the ones that survive an embarrassment filter are still theirs.
The spec had treated authenticity as free. It is not; it costs the owner their privacy on the one surface most likely to be read by strangers, including people deciding whether to employ them.

The second is cost, and it is why the size changed too.
The curation pass was written as the owner's own work and could not be delegated, since only they know which of their judgments are embarrassing.
That put an afternoon of the owner's time in front of the demo shipping at all, and it was the single largest unfunded cost left in the project.

We decided the demo's taste is invented for the demo, and small.

- **One authored sensibility, belonging to nobody.**
  Not the owner's ratings, not a subset of them, and not derived from them.
  No row in the fixture is traceable to a real judgment of the owner's, so there is nothing to allowlist and nothing to leak.
- **Generic and consensus taste stay ruled out**, for exactly the reason they always were: averaged opinion has no edges, so the prose profile reads like a horoscope and every surface collapses into a popularity list.
  This decision does not reopen that; it adds a third option the original framing missed, which is a specific taste that happens to be fictional.
- **Sixty to eighty films**, down from the hundreds a full pass would have produced.
  Enough to fill the ten bands, seat a ranked tier, and give the discovery feed something to work from; few enough that every film is chosen on purpose and the fixture stays readable and reviewable.
- **The sensibility must have edges and must be nameable.**
  It should be describable in one sentence, and it should carry a handful of judgments on widely recognizable films that a visitor would argue with.
  A demo whose ordering nobody would dispute has failed at the only thing that makes the surfaces cohere.
- **Authoring it is the agent's job**, which is what removes the owner from the critical path.

Chosen because the demo's purpose is to prove the engine works on a real, specific taste, and nothing about that proof requires the taste to be a real person's.
A visitor cannot tell whose judgments they are looking at and has no reason to care; what they can tell is whether the ordering is coherent, whether the prose profile sounds like it was written about that ordering, and whether the feed's suggestions follow from both.
All three survive the taste being invented, because they are properties of the pipeline rather than of the person.
The cost is the loss of a nice story - "these are really his ratings" - that was never told in-product anyway, since the account is presented without owner identity.

## Consequences

- The presentation copy changes.
  "Demo account - a real, lived-in account you can explore" claimed something that is no longer true, and the account is still genuinely lived-in - every row is real engine output from a real replay - so the fix is to drop the word that overclaims rather than to hedge the whole sentence.
- The curation rule "cut for embarrassment, keep for edge" is gone, because an invented taste has nothing to be embarrassed about.
  Only "keep for edge" survives, promoted from a filter to a design requirement.
- The recognizability rule simplifies.
  It was written to say that the extremes and the anchors should be recognizable while the middle stayed as obscure as the real taste happened to be.
  With sixty to eighty chosen films there is no accidental middle; most of the ordering should be recognizable, with a few deep cuts kept deliberately for texture.
- The owner leaves the critical path for #108 entirely, and the ticket's model choice is reinforced: inventing a coherent, edged sensibility is taste work, which is Fable's.
- Nothing changes in [#42](https://github.com/KyleKDang/anchor/issues/42)'s enforcement, which never cared where the rows came from, and nothing changes in the build-and-replay approach, which is unaffected by who authored the outcome.
- The real export keeps its existing role as import-matcher test fixtures ([testing.md](../design/testing.md)), which is a different thing: those rows are titles and dates exercising a parser, never opinions on display.

## Considered options

- **Keep the owner's taste, allowlisted as specified**: rejected on privacy, which no amount of filtering addresses, and on the undelegable afternoon it costs.
- **The owner's taste with the films anonymized or shuffled**: rejected; shuffling destroys the coherence that is the entire point, and anonymizing is meaningless when the judgments themselves are the private thing.
- **Generic or consensus taste**: still rejected, unchanged from the original decision.
- **Drop the demo and let the landing page ([#113](https://github.com/KyleKDang/anchor/issues/113)) carry it alone**: rejected.
  A landing page is a claim and its screenshots are indistinguishable from mockups; the demo is the evidence that the engine runs, which is the question a recruiter is actually asking.
  The two are a funnel, not substitutes.
- **A much larger invented library, in the hundreds**: rejected; inventing several hundred coherent judgments is a great deal of work for a visitor who will see perhaps forty of them, and a smaller fixture is easier to keep coherent.
