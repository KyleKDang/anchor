# Recommendation techniques for a pairwise-ordering taste engine: survey and candidates

Research notes for Anchor's design phase, gathered 2026-07-26, as input for ticket #7 (taste profile and scoring design).
The question: which recommendation approaches fit Anchor's shape - a personal (single-owner) taste engine whose training signal is a pairwise-comparison-derived total ordering over a few hundred to low thousands of films, with TMDB metadata as the feature source, no cross-user data, and two distinct scoring jobs (ranking the backlog into the ranked tier, and scoring never-rated films for the discovery feed).
All claims are grounded in primary sources: original papers (arXiv, publisher DOI pages, author- or university-hosted full texts), official vendor docs, and official dataset READMEs.
Where a paywall blocked the original text, the claim is grounded in the closest attributing primary source and flagged.
Claims that could not be grounded first-party are explicitly labeled unverified; Anchor-specific reasoning is labeled analysis.
Companion note: [tmdb-letterboxd-data.md](tmdb-letterboxd-data.md) establishes the data constraints this survey builds on - TMDB has no embeddings API, usable taste features are genres/credits/keywords/votes, TMDB's terms restrict ML/AI use and impose a 6-month cache ceiling, and the Letterboxd seed import matches on title+year.

## 1. The shape of the problem

Anchor's training signal is richer than star ratings: a total ordering of every rated film, produced by pairwise comparisons, with owner-designated anchors marking half-star band boundaries.
The rated set is small (hundreds to low thousands), accounts are isolated, and there is no in-app user base for classic cross-user collaborative filtering; public interaction datasets (MovieLens) are the only substitute if an approach needs one.
Film features come from TMDB: genres (coarse, dense), credits (dense, high-signal), keywords (rich, sparse), and vote/popularity priors ([tmdb-letterboxd-data.md](tmdb-letterboxd-data.md), section 3).
TMDB's API terms prohibit use "in connection with, including for training, a machine learning (ML) or artificial intelligence (AI) based Application" under the free license, and cap caching at 6 months ([TMDB API terms](https://www.themoviedb.org/api-terms-of-use)); any approach that feeds TMDB content into a model must be checked against that clause.
Cold start is handled by a one-time Letterboxd seed import that yields provisional placements in the ordering.
Paid LLM API calls are acceptable, favored especially for discovery over never-rated films.
A future multi-criteria comparison feature (ticket #10) may add per-dimension comparison signals; each approach below notes how it would absorb them.

## 2. Content-based methods over TMDB metadata

### The canonical architecture

The defining survey describes content-based recommenders as "systems that recommend an item to a user based upon a description of the item and a profile of the user's interests", sharing "a means for describing the items that may be recommended, a means for creating a profile of the user that describes the types of items the user likes, and a means of comparing items to the user profile to determine what to recommend" ([Pazzani and Billsus 2007](https://doi.org/10.1007/978-3-540-72079-9_10), [full text](https://cs.fit.edu/~pkc/apweb/related/pazzani07aw.pdf)).
The Recommender Systems Handbook chapter decomposes this into three components: a Content Analyzer ("represent the content of items ... in a form suitable for the next processing steps", e.g. keyword vectors), a Profile Learner ("collects data representative of the user preferences and tries to generalize this data, in order to construct the user profile", usually via machine learning), and a Filtering Component ("exploits the user profile to suggest relevant items by matching the profile representation against that of items to be recommended", e.g. cosine similarity between a prototype vector and item vectors) ([Lops, de Gemmis, Semeraro 2011](https://doi.org/10.1007/978-0-387-85820-3_3), [full text](http://www.ise.bgu.ac.il/faculty/liorr/recsyshb/chContent.pdf)).
The same chapter lists the structural advantages that matter for Anchor: user independence, transparency, and "Content-based recommenders are capable of recommending items not yet rated by any user" (no first-rater problem) - which is exactly the open-world discovery requirement; and the structural drawbacks: limited content analysis and over-specialization (the "serendipity problem") ([Lops et al. 2011](http://www.ise.bgu.ac.il/faculty/liorr/recsyshb/chContent.pdf)).

### Feature construction and similarity

The canonical item representation is a term vector weighted by tf\*idf, "term-frequency times inverse document frequency", normalized to unit length ([Pazzani and Billsus 2007](https://cs.fit.edu/~pkc/apweb/related/pazzani07aw.pdf), section 10.2).
The idf idea originates in Spärck Jones: "terms should be weighted according to collection frequency, so that matches on less frequent, more specific, terms are of greater value than matches on frequent terms" ([Spärck Jones 1972](https://doi.org/10.1108/eb026526)).
For similarity, "the cosine similarity measure is often used", and it is appropriate when two items should be similar because they share topics rather than because they jointly lack them ([Pazzani and Billsus 2007](https://cs.fit.edu/~pkc/apweb/related/pazzani07aw.pdf), section 10.5).
Mapping onto TMDB (analysis, not sourced): genres and credits are naturally binary/categorical features (genre ids, director id, top-billed cast ids by low `order`), keywords are the tf\*idf-shaped signal (idf weighting matters because popular keywords like "based on novel" carry little taste information), and vote/popularity priors are numeric side features.
Keyword sparsity on obscure films (companion note, section 3) means any keyword-similarity path needs a fallback to genres+credits.

### Learning a profile - and the ranked-list mismatch

Classical profile learning is framed as classification: "Creating a model of the user's preference from the user history is a form of classification learning", with binary categories such as items the user likes and dislikes ([Pazzani and Billsus 2007](https://cs.fit.edu/~pkc/apweb/related/pazzani07aw.pdf), section 10.3).
The chapter covers decision trees, nearest neighbor, linear classifiers, naive Bayes, and Rocchio's relevance feedback, which "forms two document prototypes by taking the vector sum over all relevant and non-relevant documents" and classifies by proximity to the prototypes ([Pazzani and Billsus 2007](https://cs.fit.edu/~pkc/apweb/related/pazzani07aw.pdf), section 10.6).
All of these presume class labels or ratings, not a total ordering.
Anchor could flatten its ordering into classes (e.g. above/below a band threshold) to reuse them, but that throws away most of the signal (analysis).
The clean fit is to keep the content-based architecture - TMDB feature vectors as the Content Analyzer output, score-against-profile as the Filtering Component - and replace the Profile Learner with a preference model trained directly on pairs from the ordering (section 3).
The chapter's standing warning applies regardless of learner: "no content-based recommendation system can give good recommendations if the content does not contain enough information to distinguish items the user likes from items the user doesn't like" ([Pazzani and Billsus 2007](https://cs.fit.edu/~pkc/apweb/related/pazzani07aw.pdf), section 10.10) - if the owner's taste hinges on qualities absent from genres/credits/keywords (tone, pacing, formal style), a purely symbolic-feature model will plateau.

### Taste-profile representation and multi-criteria fit

The taste profile implied here is a weight vector over metadata features (or Rocchio-style positive/negative prototypes).
It is transparent and inspectable: "why is this recommended" reduces to which features carried the score.
Multi-criteria absorption: keep one weight vector (or prototype pair) per criterion, and combine per-criterion scores with an aggregation function (section 6); per-dimension comparisons simply train the matching per-criterion vector.

## 3. Learning from the total ordering: pairwise and listwise preference models

This is the approach family that treats Anchor's ordering as what it actually is - preference data - rather than flattening it into pseudo-ratings.

### Bradley-Terry

The Bradley-Terry model ([Bradley and Terry 1952](https://doi.org/10.1093/biomet/39.3-4.324), Biometrika 39, pp. 324-345) assigns each item a positive strength and models P(i beats j) as strength_i / (strength_i + strength_j).
Unverified against the 1952 text itself (paywalled at OUP; JSTOR blocked automated access); the formula is confirmed by two attributing primary sources: the RankNet paper states the model as "P(Ai|Ai or Aj) = P̂i/(P̂i + P̂j)" citing Bradley and Terry ([Burges et al. 2005](https://www.microsoft.com/en-us/research/wp-content/uploads/2005/08/icml_ranking.pdf), p. 4), and the choix library docs give "p(i > j) = e^θi / (e^θi + e^θj)" as "the Bradley-Terry model" ([choix docs](https://choix.lum.li/en/latest/data.html)).
The decisive property for Anchor is the structured variant: the BradleyTerry2 reference documentation shows "logit[pr(i beats j)] = λi - λj" and that abilities can be "related through a linear predictor to explanatory variables", λi = Σ βr x_ir, estimable "by maximum likelihood using standard software for generalized linear models" ([Turner and Firth, BradleyTerry2 vignette](https://cran.r-project.org/web/packages/BradleyTerry2/vignettes/BradleyTerry.html); [Turner and Firth 2012, JSS](https://www.jstatsoft.org/article/view/v048i09)).
Substituting the linear predictor into the logit gives logit P(i beats j) = β·(x_i - x_j): Bradley-Terry with features is exactly logistic regression on feature differences (this one-line derivation is ours; both premises are quoted above).
That is the bridge from "ordering over rated films" to "score any film": the learned β scores arbitrary feature vectors, including films the owner has never seen.
By contrast, per-item-strength inference (what [choix](https://choix.lum.li/en/latest/index.html) implements: `ilsr_pairwise`, `opt_pairwise`, Bayesian `ep_pairwise`, plus Plackett-Luce ranking variants) fits one parameter per film and cannot generalize to unseen films; its API has no covariate regression ([choix API](https://choix.lum.li/en/latest/api.html)).
choix-style per-item inference is still useful inside Anchor for the ordering layer itself (probabilistic placement confidence, drift detection), just not as the taste model (analysis).

### Plackett-Luce

The listwise generalization factorizes a full ranking into sequential choices: "p(i > j > ... > k) = [e^θi/(e^θi + e^θj + ... + e^θk)] · [e^θj/(e^θj + ... + e^θk)] ...", "an independent sequence of top-1 lists", "usually referred to as the Plackett-Luce model" ([choix docs](https://choix.lum.li/en/latest/data.html)).
Original sources: [Plackett 1975](https://doi.org/10.2307/2346567) (Applied Statistics 24(2), "The Analysis of Permutations"; bibliographic record confirmed via CrossRef, full text paywalled) and Luce's choice axiom (Luce, Individual Choice Behavior, Wiley 1959; [bibliographic record](https://archive.org/details/individualchoice0000luce), book text not verified first-party).
For Anchor the practical difference from pairwise is small: the ordering is already a consistent total order, and Plackett-Luce mostly matters when learning from many partial rankings; the pairwise reduction below is simpler and equivalent in signal (analysis).

### RankNet, LambdaRank, LambdaMART

RankNet is the same pairwise-logistic idea with an arbitrary learned scoring function: "We consider models f : R^d → R such that the rank order of a set of test samples is specified by the real values that f takes", trained with "the cross entropy cost function" on score differences o_ij = f(x_i) - f(x_j) mapped through a logistic ([Burges et al. 2005](https://www.microsoft.com/en-us/research/wp-content/uploads/2005/08/icml_ranking.pdf), ICML 2005; [publication page](https://www.microsoft.com/en-us/research/publication/learning-to-rank-using-gradient-descent/)).
With a linear f, RankNet reduces to the feature-parameterized Bradley-Terry above; with a neural f it is the nonlinear upgrade path.
LambdaMART: "LambdaMART is the boosted tree version of LambdaRank, which is based on RankNet", combining gradient-boosted regression trees with lambda gradients scaled by the NDCG change from swapping two items ([Burges 2010, MSR-TR-2010-82](https://www.microsoft.com/en-us/research/wp-content/uploads/2016/02/MSR-TR-2010-82.pdf); [publication page](https://www.microsoft.com/en-us/research/publication/from-ranknet-to-lambdarank-to-lambdamart-an-overview/)).
The practical implementation is LightGBM's `lambdarank` objective, which requires query/group-structured data and exposes `lambdarank_truncation_level` (default 30, "controls the number of top-results to focus on during training") and `lambdarank_norm` ([LightGBM parameters](https://lightgbm.readthedocs.io/en/latest/Parameters.html)).
The LightGBM docs give no lambdarank-specific small-dataset guidance, only general overfitting controls (`num_leaves`, `min_data_in_leaf`, L1/L2) ([LightGBM tuning docs](https://lightgbm.readthedocs.io/en/latest/Parameters-Tuning.html)); at Anchor's scale (one "query" of a few hundred to low thousands of items) a tree ensemble is at real risk of memorizing the ordering rather than learning taste (analysis).

### Turning the ordering into training signal

The primary sources directly sanction Anchor's data shape.
Burges 2010 defines training data as a set I of ordered pairs and notes: "since RankNet learns from probabilities and outputs probabilities, it does not require that the urls be labeled; it just needs the set I, which could also be determined by gathering pairwise preferences" ([Burges 2010](https://www.microsoft.com/en-us/research/wp-content/uploads/2016/02/MSR-TR-2010-82.pdf)).
So comparison-derived orderings are first-class training input for this entire model family - no conversion to ratings needed.
On how many pairs to extract: RankNet's Section 3.1 theorem shows that "specifying any set of adjacency posteriors is necessary and sufficient to uniquely identify a target posterior ... for every pair of samples" ([Burges et al. 2005](https://www.microsoft.com/en-us/research/wp-content/uploads/2005/08/icml_ranking.pdf)) - a total order over n films is fully captured, at the level of target probabilities, by its n-1 adjacent pairs.
The full pair set of a strict total order is n(n-1)/2 (the paper's per-label pair-count formula degenerates to this when all labels differ - our derivation); at 2,000 films that is ~2M pairs, so pair subsampling is needed in practice.
Flag: neither the RankNet paper nor the 2010 report gives an explicit pair-sampling recipe; the mitigations actually present in primary sources are the lambda-sum factorization ("training time dropped from close to quadratic in the number of urls per query, to close to linear", [Burges 2010](https://www.microsoft.com/en-us/research/wp-content/uploads/2016/02/MSR-TR-2010-82.pdf)) and LightGBM's truncation level.
Any specific sampling scheme (all adjacent pairs plus k random long-range pairs per film, extra weight on owner-made explicit comparisons vs pairs merely implied by the ordering, down-weighting pairs involving provisional placements from the seed import) is design folklore to be validated empirically, not literature (analysis).
Anchor's anchors add something the literature's orderings lack: absolute calibration points.
A trained scorer produces relative scores; mapping scores to half-star bands can be done by locating each film's score between the anchor films' scores, which keeps the band semantics owned by the ordering layer rather than the model (analysis).

### Related: comparison-based rating systems and active selection

Elo and TrueSkill are the same model family applied online: the TrueSkill paper notes "the Elo system addresses the problem of estimating from paired comparison data ... with the Gaussian variant corresponding to the Thurstone Case V model and the logistic variant to the Bradley-Terry model", and presents "a new Bayesian skill rating system which can be viewed as a generalisation of the Elo system" that "tracks the uncertainty about player skills" ([Herbrich, Minka, Graepel 2006, NIPS](https://proceedings.neurips.cc/paper/2006/hash/f44ee263952e65b3610b8ba51229d1f9-Abstract.html)).
Their matchmaking motivation (pairing players of similar skill for informative matches) is the template for Anchor's comparison UX: the most informative next comparison is between films with similar and uncertain positions, which uncertainty-tracking models make explicit (analysis; Elo's own 1978 book was not verifiable online, so Elo claims here are sourced to the TrueSkill paper's account).

### Taste-profile representation and multi-criteria fit

Linear pairwise model: the profile is a weight vector β over film features - compact, inspectable, cheap to retrain from scratch on every ordering change.
LambdaMART: the profile is a tree ensemble - more expressive, opaque, and overfit-prone at this scale.
Multi-criteria absorption is natural: a comparison tagged with a criterion is a training pair for that criterion's model, so ticket #10 yields per-criterion weight vectors β_c trained by the same machinery, plus an aggregation step (section 6) to combine per-criterion scores into an overall score.

## 4. Embedding-based approaches

TMDB offers no embeddings ([companion note](tmdb-letterboxd-data.md), section 3), so film embeddings must come from somewhere concrete.
The realistic sources, checked against current official docs on 2026-07-26:

### Hosted text-embedding APIs

Anthropic has no embeddings API: "Anthropic does not offer its own embedding model", and its docs point to Voyage AI while advising to "assess a variety of embeddings vendors" ([Anthropic embeddings docs](https://platform.claude.com/docs/en/docs/build-with-claude/embeddings)).
OpenAI: `text-embedding-3-small` (1536 dims) and `text-embedding-3-large` (3072 dims), 8192-token max input, with a `dimensions` parameter to shorten vectors ("developers can shorten embeddings ... without the embedding losing its concept-representing properties"); pricing $0.02 and $0.13 per 1M tokens respectively ([OpenAI embeddings guide](https://developers.openai.com/api/docs/guides/embeddings); model pages for [3-small](https://developers.openai.com/api/docs/models/text-embedding-3-small) and [3-large](https://developers.openai.com/api/docs/models/text-embedding-3-large)).
Unverified: OpenAI's batch-API price for embeddings (the model pages read as same-as-standard, which contradicts the historical 50% batch discount; treat as unconfirmed).
Voyage AI (now first-party branded "Voyage AI by MongoDB"; [acquisition announced by MongoDB 2025-02-24](https://www.mongodb.com/press/mongodb-announces-acquisition-of-voyage-ai)): current generation is the voyage-4 family - voyage-4-large ($0.12/1M tokens), voyage-4 ($0.06), voyage-4-lite ($0.02), all with 32,000-token context and 1024-dim default (256/512/2048 options), plus an open-weight Apache-2.0 voyage-4-nano; the first 200M tokens on current-generation models are free per account ([Voyage embeddings docs](https://docs.voyageai.com/docs/embeddings); [Voyage pricing](https://docs.voyageai.com/docs/pricing)).
Google Gemini: `gemini-embedding-001` and the newer multimodal `gemini-embedding-2`, both trained with Matryoshka Representation Learning; "By default, both models output a 3072-dimensional embedding, but you can truncate it to a smaller size without losing quality", recommended sizes 768/1536/3072, 8192-token input; paid-tier text pricing extracted as $0.15/1M (001) and $0.20/1M (embedding-2), both with a genuine free tier ([Gemini embeddings docs](https://ai.google.dev/gemini-api/docs/embeddings); [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing); exact paid-tier row labels not reproduced verbatim, flag as extraction-level confidence).
Cost at Anchor's scale is a non-issue (analysis): a film's text blob (title, overview, genres, keywords, top credits) is a few hundred tokens; embedding 2,000 rated films is under 1M tokens, and even a 50,000-film catalog slice is ~10-15M tokens, i.e. cents to a few dollars at any provider, one-time plus incremental updates.

### Local open-source models

Sentence-transformers (SBERT) is the standard local option: "a modification of the pretrained BERT network that use siamese and triplet network structures to derive semantically meaningful sentence embeddings that can be compared using cosine-similarity" ([Reimers and Gurevych 2019](https://arxiv.org/abs/1908.10084)).
The official docs recommend: "The all-mpnet-base-v2 model provides the best quality, while all-MiniLM-L6-v2 is 5 times faster and still offers good quality" ([sbert.net pretrained models](https://www.sbert.net/docs/sentence_transformer/pretrained_models.html)).
all-MiniLM-L6-v2 maps text to a 384-dim vector (input truncated beyond 256 word pieces) and all-mpnet-base-v2 to 768 dims (truncated at 384 word pieces), both Apache 2.0 ([MiniLM model card](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2); [mpnet model card](https://huggingface.co/sentence-transformers/all-mpnet-base-v2)).
The truncation limits fit film blobs comfortably; a local model removes per-call cost and any third-party data-sharing question, at the price of running inference infrastructure Anchor otherwise would not need (analysis).

### Public-dataset-derived vectors: the MovieLens tag genome

The tag genome "encodes an item in an information space based on its relationship to a common set of tags", computed by "a machine learning approach" over community tagging data ([Vig, Sen, Riedl 2012](https://doi.org/10.1145/2362394.2362395), ACM TiiS; [GroupLens-hosted PDF](https://files.grouplens.org/papers/tag_genome.pdf)).
In dataset form it is "a dense matrix: each movie in the genome has a value for every tag in the genome" ([ml-25m README](https://files.grouplens.org/datasets/movielens/ml-25m-README.html)).
ml-25m ships genome data with "15 million relevance scores across 1,129 tags" ([GroupLens datasets page](https://grouplens.org/datasets/movielens/25m/)); unverified: the count of movies the ml-25m genome covers (commonly cited ~13.8k) is not stated on the first-party pages, so confirm against genome-scores.csv before relying on coverage.
A newer standalone release exists: Tag Genome 2021, "10.5 million computed tag-movie relevance scores from a pool of 1,084 tags applied to 9,734 movies" ([Tag Genome 2021 page](https://grouplens.org/datasets/movielens/tag-genome-2021/)).
Decisively for Anchor, MovieLens ships the join key: `links.csv` maps `movieId` to `imdbId` and `tmdbId` ("tmdbId is an identifier for movies used by https://www.themoviedb.org") ([ml-25m README](https://files.grouplens.org/datasets/movielens/ml-25m-README.html)), so genome vectors attach directly to Anchor's TMDB-keyed films.
License constraints ([ml-25m README](https://files.grouplens.org/datasets/movielens/ml-25m-README.html), verbatim bullets): no commercial/revenue-bearing use without GroupLens permission, no redistribution without separate permission, and acknowledgment required (citing [Harper and Konstan 2015](https://doi.org/10.1145/2827872)).
Genome limits (analysis): the datasets are frozen snapshots (ml-25m data ends 2019-11-21 per its README; Tag Genome 2021 ends 2021), so new releases have no genome vector, which rules it out as the sole discovery-feed representation and positions it as offline enrichment for older films.

### The TMDB terms flag, and training-on vs inference-over

Any embedding built from TMDB text (overview, keywords, credits) feeds TMDB content into an ML system, which collides with the terms' ML/AI clause quoted in section 1.
Two distinctions matter (analysis, building on the companion note's reading).
First, training-on vs inference-over: training or fine-tuning any model on TMDB content is explicitly named in the commercial-use section ("Training or validating a machine learning or artificial intelligence system... using TMDB content", [TMDB API terms](https://www.themoviedb.org/api-terms-of-use)) and is out under the free license; sending TMDB text to a hosted embedding endpoint at inference time is not literally training, but the restrictions clause's broader "use ... in connection with ... a machine learning (ML) or artificial intelligence (AI) based Application" wording does not resolve it, so this remains a judgment call to record in an ADR rather than a settled fact.
Second, provider-side data use: an embedding provider that trains on API inputs would convert inference-over into training-on; provider data-use terms were not surveyed here (unverified) and must be checked before wiring TMDB text into any hosted endpoint.
Routes that sidestep TMDB content entirely: tag-genome vectors (MovieLens community data joined via links.csv) and local models applied to non-TMDB text; the symbolic-feature models of sections 2-3 sit on the companion note's safe reading (TMDB metadata as lookup features in a hand-rolled scorer).

### Taste-profile representation and multi-criteria fit

With embeddings, the taste profile is either a dense taste vector (a rank-weighted centroid of embeddings - Rocchio's prototype construction lifted into embedding space, cf. section 2) or, better, the section-3 machinery applied to embedding dimensions: a pairwise-trained linear scorer over the embedding space, so the ordering remains the training signal.
Exemplar films (anchors, top and bottom of the ordering) double as an interpretable companion to the opaque vector.
Multi-criteria absorption: per-criterion taste vectors or per-criterion linear scorers over the same film embeddings.

## 5. LLM-as-scorer and LLM-as-recommender

### What the literature actually found

Zero-shot ranking works, framed as reranking over retrieved candidates: Hou et al. "formalize the recommendation problem as a conditional ranking task, considering sequential interaction histories as conditions and the items retrieved by other candidate generation models as candidates", and find "LLMs have promising zero-shot ranking abilities but (1) struggle to perceive the order of historical interactions, and (2) can be biased by popularity or item positions in the prompts", with mitigations via "specially designed prompting and bootstrapping strategies" ([Hou et al. 2023/2024, ECIR](https://arxiv.org/abs/2305.08845)).
Prompting granularity: comparing point-wise, pair-wise, and list-wise prompting, Dai et al. "identify that ChatGPT with list-wise ranking achieves the best trade-off between cost and performance compared to point-wise and pair-wise ranking", and note "ChatGPT shows the potential for mitigating the cold start problem and explainable recommendation" ([Dai et al. 2023, RecSys](https://arxiv.org/abs/2305.02182)).
Cold start with language-based profiles: Sanner et al. "find that LLMs provide competitive recommendation performance for pure language-based preferences (no item preferences) in the near cold-start case in comparison to item-based CF methods, despite having no supervised training for this specific task (zero-shot) or only a few labels (few-shot)", adding that "language-based preference representations are more explainable and scrutable than item-based or vector-based representations" ([Sanner et al. 2023, RecSys](https://arxiv.org/abs/2307.14225)).
Note the scope: the claim is for near cold-start with pure language preferences, not for LLMs beating CF once item history is plentiful.
Listwise reranking mechanics: RankGPT inputs a group of candidates with identifiers and asks the model "to generate the permutation of passages in descending order based on their relevance", explicitly "without producing an intermediate relevance score", and handles long candidate lists with a sliding window applied "in back-to-first order"; "properly instructed LLMs can deliver competitive, even superior results to state-of-the-art supervised methods on popular IR benchmarks" ([Sun et al. 2023, EMNLP](https://arxiv.org/abs/2304.09542)).
Position bias is real beyond recommendation: the LLM-as-judge study names "position, verbosity, and self-enhancement biases, as well as limited reasoning ability" as limitations and proposes mitigations ([Zheng et al. 2023, NeurIPS D&B](https://arxiv.org/abs/2306.05685)); the specific swap-both-orders mitigation is described in the paper body (section 3), not the abstract.
Calibration: for RLHF-tuned models, "verbalized confidences emitted as output tokens are typically better-calibrated than the model's conditional probabilities", often halving expected calibration error - but on factual-QA benchmarks ([Tian et al. 2023, EMNLP](https://arxiv.org/abs/2305.14975)); this does not license treating a verbalized 0-10 movie score as a calibrated rating, only preferring verbalized over logprob-derived scores if a numeric score is needed at all.
The survey framing of why LLMs help here: conventional recommenders suffer from "lacking open-world knowledge, and difficulties in comprehending users' underlying preferences and motivations" ([Lin et al., ACM TOIS survey](https://arxiv.org/abs/2306.05817)) - which is precisely the discovery-feed gap for a single-owner system with no user base.

### Practical patterns for Anchor (analysis, with sourced cost facts)

Candidate generation is not the LLM's job: TMDB `/discover`, `/similar`, and `/recommendations` are the cheap candidate pools (companion note, section 3); the LLM reranks or scores a bounded candidate set, mirroring the Hou et al. framing.
Prefer relative judgments to absolute scores: listwise permutation ranking (Dai et al., Sun et al.) both performs best per cost and matches Anchor's comparison-based ethos; where a film must be judged alone (discovery triage), a coarse verdict schema (e.g. strong-fit / plausible / poor-fit plus rationale) is safer than a pseudo-calibrated number (Tian et al. caveat).
Mitigate position bias mechanically: randomize candidate order per call, and for high-stakes orderings run the swapped order and keep only stable wins (Zheng et al.; Hou et al. bootstrapping).
Structured outputs keep the pipeline mechanical: the Claude API's structured outputs "guarantee schema-compliant responses through constrained decoding" so responses parse without retries ([Anthropic structured outputs docs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)).
Cost control is well-supported first-party: prompt caching prices cache reads at 0.1x base input (5-minute-TTL writes at 1.25x, 1-hour at 2x) ([Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing)), so a stable prose taste profile placed as a cached prefix makes per-film scoring calls cheap; the Message Batches API processes asynchronous request volumes "cutting costs by 50%" with most batches finishing under an hour ([Anthropic batch docs](https://platform.claude.com/docs/en/build-with-claude/batch-processing)); and model tiers span Haiku 4.5 at $1/$5 per MTok input/output, Sonnet-tier at $3/$15, Opus-tier at $5/$25 ([Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing)).
Back-of-envelope (analysis): listwise-ranking a 500-film backlog in windows of ~20 films at ~2K tokens per call is on the order of 100K tokens, i.e. well under a dollar even on Opus-tier and near-negligible on Haiku; a nightly batch refresh of discovery verdicts over a few hundred candidates is similar.
Precompute and cache verdicts keyed by (film id, taste-profile version); re-score only when the profile version changes or new candidates appear, and never inside an interactive request path.
LLM verdicts should also respect the TMDB 6-month cache ceiling insofar as they embed TMDB metadata in stored rationales (companion note, section 2).

### Taste-profile representation and multi-criteria fit

The implied representation is a prose taste profile (plus exemplar films: anchors, recent highs and lows, seed-import favorites), maintained as a versioned document - the "explainable and scrutable" representation Sanner et al. highlight, and one the owner can read and correct.
Multi-criteria absorption is the most natural of any approach: per-criterion preferences become sections of the prose profile, and per-criterion comparison outcomes become few-shot evidence; the LLM can be asked to judge fit on named criteria directly.

## 6. Hybrids and cold start

### Hybridization vocabulary

Burke's survey defines the standard combination methods: weighted ("The scores (or votes) of several recommendation techniques are combined together"), switching, mixed, feature combination ("Features from different recommendation data sources are thrown together into a single recommendation algorithm"), cascade ("One recommender refines the recommendations given by another"), feature augmentation ("Output from one technique is used as an input feature to another"), and meta-level ([Burke 2002](https://doi.org/10.1023/A:1021240730564), Table III; [author preprint](https://pzs.dstu.dp.ua/DataMining/recom/bibl/Hybrid_Recommender_Systems_Survey_and_Experiments.pdf)).
The combinations that fit Anchor (analysis): feature augmentation (embedding similarities or tag-genome features feed the pairwise-trained scorer as extra features), cascade (cheap scorer shortlists candidates, LLM refines the shortlist), and weighted (blend scorer score with LLM verdict for the discovery feed).

### Cold start

Burke defines the ramp-up problems: "New User: ... a user with few ratings becomes difficult to categorize. New Item: Similarly, a new item that has not had many ratings also cannot be easily recommended" ([Burke 2002 preprint](https://pzs.dstu.dp.ua/DataMining/recom/bibl/Hybrid_Recommender_Systems_Survey_and_Experiments.pdf), section 2).
The canonical cold-start study benchmarks recommending "items that no one in the community has yet rated" under a combined content/collaborative probabilistic framework ([Schein, Popescul, Ungar, Pennock 2002, SIGIR](https://doi.org/10.1145/564376.564421); abstract confirmed via the OpenAlex record for the DOI because ACM and the Penn repository block automated fetch - re-verify in a browser before quoting further).
Anchor's content-based core is structurally immune to new-item cold start (Lops et al.'s "new item" advantage, section 2); its real cold start is the new-account case, and the seed import addresses it directly (analysis): imported ratings yield provisional placements, provisional placements yield training pairs (down-weighted until refined by real comparisons), and the import list plus its rating distribution seeds the first prose taste profile - the configuration Sanner et al. found LLMs competitive in (near cold-start, language- and item-based preferences).
How each approach handles the seed import (summary): content-based/pairwise models train immediately on provisional pairs at reduced weight; embedding centroids compute immediately from imported favorites; the LLM profile is drafted from the import in one call; only per-item-strength models (choix-style) gain nothing transferable to unseen films.

### If cross-user signal is ever wanted: MovieLens as the missing user base

Item-item collaborative filtering "first analyze[s] the user-item matrix to identify relationships between different items, and then use[s] these relationships to indirectly compute recommendations", with item-item cosine/correlation/adjusted-cosine similarities precomputable offline, and "item-based algorithms provide dramatically better performance than user-based algorithms" ([Sarwar, Karypis, Konstan, Riedl 2001, WWW10](https://doi.org/10.1145/371920.372071); [official conference full text](https://archives.iw3c2.org/www10/cdrom/papers/519/index.html)).
Anchor could precompute item-item similarities from ml-25m ratings (25,000,095 ratings across 62,423 movies, [ml-25m README](https://files.grouplens.org/datasets/movielens/ml-25m-README.html)) and join them to TMDB ids via links.csv, giving a "people who liked X also liked Y" signal with zero in-app users.
Caveats (analysis): the data ends 2019-11-21 (README, verbatim date range), so post-2019 films have no co-rating signal; and the license's non-commercial and no-redistribution terms (section 4) apply.
This is an optional feature-augmentation input, not a foundation.

### Multi-criteria recommendation

The multi-criteria literature frames exactly what ticket #10 would add: "The overall rating that users give to an item provides the information regarding how much they like the item, and multicriteria ratings provide some insights regarding why they like it" ([Adomavicius and Kwon 2007](https://doi.org/10.1109/MIS.2007.58), IEEE Intelligent Systems; abstract via the [authors' institutional record](https://experts.umn.edu/en/publications/new-recommendation-techniques-for-multicriteria-rating-systems/)).
Their two families, as attributed in the authors' own handbook chapter ([Adomavicius, Manouselis, Kwon](https://www.ise.bgu.ac.il/faculty/liorr/recsyshb/chmulticriteria.pdf); exact in-paper wording unverified because IEEE blocks automated fetch): similarity-based approaches that aggregate per-criterion similarities, and the aggregation-function approach, which "assumes that the overall rating serves as an aggregate of multi-criteria ratings ... r0 = f(r1, ..., rk)" - predict per-criterion scores with any technique, learn or choose f, and compute the overall score.
The aggregation-function pattern is the absorption path for every architecture in this note: per-criterion pairwise models (section 3), per-criterion taste vectors (section 4), or per-criterion prose sections (section 5), combined by a learned or owner-tuned f (analysis).

## 7. Recommended candidates and trade-offs (input for ticket #7)

### Backlog ranking (the ranked tier): pairwise linear scorer over TMDB features

Recommendation: a feature-parameterized Bradley-Terry model - logistic regression on feature differences - over TMDB symbolic features (genres, director, top-billed cast, idf-weighted keywords, vote/popularity priors), trained on pairs sampled from the ordering (adjacent pairs plus sampled long-range pairs, explicit comparisons weighted above implied pairs, provisional seed placements down-weighted).
Why: it consumes the ordering natively (Burges 2010's "it just needs the set I"), scores unseen backlog films by construction, retrains from scratch in milliseconds at this scale after every comparison, needs no ML infrastructure beyond a logistic-regression fit, stays inspectable (the taste profile is a readable weight vector), and sits on the safest reading of TMDB's terms (metadata as lookup features in a hand-rolled scorer, companion note).
Anchors calibrate its relative scores into bands.
Trade-offs: linear in the features, so it cannot capture interaction effects (director-genre combinations) or qualities absent from TMDB metadata (Pazzani and Billsus's content-sufficiency warning); keyword sparsity degrades it on obscure films.
Second candidate, deliberately not recommended first: LightGBM lambdarank (LambdaMART) adds nonlinearity but is overfit-prone on a single few-hundred-item "query", has no primary-source small-data guidance, and trades away inspectability; revisit only if the linear scorer measurably plateaus.

### Open-world discovery (the discovery feed): candidate pools + cheap prefilter + LLM listwise reranking

Recommendation: a cascade (Burke's term): (1) candidate generation from TMDB `/discover` slices steered by the learned weight vector (top-weighted people/genres/keywords via `with_people`/`with_genres`/`with_keywords`) plus `/similar`/`/recommendations` pools seeded from the ordering's top films; (2) prefilter and shortlist with the same linear scorer (zero marginal cost); (3) LLM listwise reranking of the shortlist against a versioned prose taste profile, sliding-window style, with randomized candidate order, structured outputs, the profile as a cached prefix, and nightly Batch-API refresh on a cheap model tier.
Why: this is exactly the configuration the literature supports - LLMs as zero-shot rerankers over retrieved candidates (Hou et al.), listwise as the best cost/performance granularity (Dai et al., Sun et al.), and language-based profiles competitive precisely in the low-data regime Anchor permanently lives in (Sanner et al.) - and the cost profile is trivial (sub-dollar per full refresh with caching and batching).
Trade-offs: position and popularity bias require the mechanical mitigations above; LLM absolute scores must not be trusted as calibrated (use relative verdicts and coarse buckets); stored LLM rationales inherit the TMDB 6-month staleness obligation; discovery quality is bounded by candidate-pool quality since the LLM never sees the open catalog.

### The combined system and the taste profile artifact

Pragmatically, the taste profile that ticket #7 should design is three linked artifacts, all derived from the one ordering: (1) the learned feature-weight vector (drives backlog ranking, discover-slice steering, and prefiltering), (2) the exemplar set (anchors plus ordering extremes; drives comparisons in prompts and UX explanations), and (3) the prose profile (versioned, LLM-maintained, owner-editable; drives discovery reranking) - regenerated or retrained on ordering changes rather than incrementally patched, which the small scale makes affordable.
Multi-criteria (ticket #10) extends each artifact per criterion with an aggregation function combining them (Adomavicius and Kwon's r0 = f(r1, ..., rk) pattern), without changing any of the machinery.
Deliberately out of scope at this scale: any model training on TMDB content (terms), fine-tuning or embedding-model training (terms and scale), cross-user collaborative filtering as a foundation (no user base; MovieLens item-item similarity and tag-genome vectors remain optional feature-augmentation inputs with non-commercial-license and staleness caveats), and vector infrastructure as a hard dependency (embeddings are an optional later feature-augmentation experiment, cheapest via Voyage/Gemini free tiers or a local MiniLM, pending the TMDB inference-over-terms decision recorded as an ADR).

### Findings that should shape or spawn tickets

- The TMDB ML/AI clause needs an explicit ADR before any embedding of TMDB-derived text: training-on is clearly out under the free license; inference-over via hosted embedding APIs is unresolved by the terms, and provider-side data-use terms are unchecked (section 4).
- Pair extraction from the ordering has no primary-source recipe (section 3); the sampling and weighting scheme (adjacent + sampled long-range, explicit vs implied, provisional down-weighting) is a design decision ticket #7 must own and validate empirically.
- Anchors are a genuine asset the literature lacks: they turn relative scores into calibrated bands and supply high-quality exemplars for LLM prompts (sections 3, 5).
- Comparison selection can be made active: uncertainty-aware placement (TrueSkill lineage) points at asking the owner the most informative comparisons, worth a note in the comparison-UX design (section 3).
- LLM discovery scoring should be specified as relative-verdict, order-randomized, schema-constrained, cached-prefix, batch-refreshed - each element traceable to a sourced finding or first-party pricing fact (section 5).
- MovieLens (ratings and tag genome) is usable and TMDB-joinable via links.csv, but non-commercial-licensed, non-redistributable, and frozen in time; treat as optional enrichment, never a foundation (sections 4, 6).
