# TMDB licensing posture: full feature use under a good-faith non-commercial reading

TMDB's free license prohibits using TMDB content "in connection with, including for training, a machine learning (ML) or artificial intelligence (AI) based Application", and classifies training an ML/AI system on TMDB content as commercial use requiring a written agreement.
The clause does not define where a statistical scorer ends and an "ML or AI based Application" begins, so Anchor must pick a reading and stand on it.

Anchor adopts a good-faith non-commercial posture: the app is personal, unmarketed, non-revenue software (at most a portfolio piece shared with a few friends), and TMDB metadata may be used fully to make it good - as lookup features in the pairwise-trained scorer and as prompt context in runtime LLM calls.
The scorer's training signal is the owner's own comparisons; TMDB fields enter only as per-film covariates, not as a training corpus.
This is recorded honestly as a risk-accepted interpretation, not a settled compliance fact: the ML/AI clause sits in the restrictions section of the free license and applies regardless of commerciality, and the realistic worst case - TMDB revoking the API credential - is accepted in exchange for full data use.

Rejected: the strict reading (recommender built only on non-TMDB features), which trades real quality for a theoretical risk at personal scale; and asking TMDB for written clarification, which blocks design on an external party unlikely to bless a nuanced reading in writing.

## The no-training provider rule

TMDB content (and the owner's taste profile, which rides along in discovery prompts) may be sent only to AI providers whose terms bar training on customer API inputs by default.
Verified 2026-08-02 against first-party terms (see [llm-provider-data-use.md](../research/llm-provider-data-use.md)): the Anthropic API, the OpenAI API (including embeddings), and Gemini's paid tier qualify; Gemini's free tier (trains on inputs, with human review) and Voyage AI at default settings (perpetual training license, prospective-only opt-out) do not.
Any future provider is checked against the same test before being wired in.
The rule keeps Anchor's usage genuinely inference-over rather than training-on, and it unlocks the embed-TMDB-text experiment under the same gate; whether embeddings are actually used is a quality call for the recommender design, not a licensing question anymore.

## Bright lines kept even under the loose posture

- No training runs on TMDB content as corpus: no fine-tuning an LLM or training any reusable model on TMDB text or catalog data.
- No public redistribution of TMDB-derived data: the repo and portfolio may be public with all the code, but no bulk TMDB dumps or TMDB-derived embedding sets are published. The owner's own ratings and ordering are theirs to publish.
- Attribution stays on: the TMDB logo and the "not endorsed, certified, or otherwise approved by TMDB" notice appear in the app and wherever the portfolio shows it.
- The 6-month cache ceiling is honored as a rolling refresh policy for cached TMDB metadata.

Together with the provider rule, the posture in one sentence: nothing is ever trained on TMDB content, nothing TMDB-derived is redistributed, attribution and cache rules are followed; the only interpretive liberty taken is using TMDB metadata as features and prompt context in a personal, non-commercial recommender.

## Named fallback, not pre-built

If the strict reading ever wins (TMDB objects, revokes access, or the ambiguity stops being worth carrying): recommender features refill from the MovieLens tag genome (which joins to Anchor's films via links.csv tmdbId) plus Wikidata (CC0); display metadata moves to Wikidata/OMDb; posters are partially recoverable from Wikimedia.
The scorer architecture is unchanged; only the feature pipeline refills from elsewhere.
No abstraction layers or dual pipelines are built now: imported TMDB metadata already lives in Anchor's own tables, so revocation stops refreshes and new lookups without bricking the catalog.
Accepted cost: the tag genome is a frozen snapshot (~2021), so the fallback discovery feed over new releases degrades to Wikidata-only features.

## Consequences

- Recommender tickets design with full TMDB features and LLM prompt context; the embedding experiment is unlocked, gated only by the no-training provider rule.
- Provider selection carries a standing compliance check: terms must bar training on API inputs by default (re-verify at integration time; terms move).
- The provider rule doubles as a privacy measure for the owner's taste profile.
- Spec assembly carries the attribution requirement and the rolling cache refresh into the implementation spec.
