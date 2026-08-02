# TMDB API and Letterboxd export: what they provide and what they constrain

Research notes for Anchor's design phase, gathered 2026-07-26.
All claims are grounded in primary sources: TMDB's developer docs and API terms, and Letterboxd's own site, help center, and journal.
Live behavior (Letterboxd film pages, the boxd.it redirect) was verified in a real browser session on 2026-07-26.
Claims that could not be grounded in a primary source are explicitly labeled unverified.

## 1. TMDB endpoints for film needs

### Search: `GET /3/search/movie`

The endpoint's stated purpose is "Search for movies by their original, translated and alternative titles" (https://developer.themoviedb.org/reference/search-movie).
Query parameters: `query` (required), `include_adult` (default false), `language` (default en-US), `primary_release_year`, `year`, `region`, and `page` (default 1) (https://developer.themoviedb.org/reference/search-movie).
The alternative-titles coverage matters for Letterboxd matching: a regional or festival retitle can still hit the right film.
`primary_release_year` and `year` are separate filters, which gives two knobs for the year-mismatch problem described in section 4.

### Metadata: `GET /3/movie/{movie_id}`

Returns the top-level details of a movie by id, including the `overview` plot summary among the standard detail fields (https://developer.themoviedb.org/reference/movie-details).
The `append_to_response` parameter on this endpoint accepts a "comma separated list of endpoints within this namespace, 20 items max" (https://developer.themoviedb.org/reference/movie-details).
The append_to_response doc explains: "This makes it possible to make sub requests within the same namespace in a single HTTP request", and it is supported on "the movie, TV show, TV season, TV episode and person detail methods" (https://developer.themoviedb.org/docs/append-to-response).
So one details call can bundle `credits`, `keywords`, `images`, `release_dates`, `similar`, `recommendations`, `watch/providers`, and more - a single request per film covers everything Anchor needs at ingest time.
Caveat from the same doc: each appended method still responds to its own query parameters, so a `language` value set on the parent call filters appended images; `include_image_language` exists to work around this (https://developer.themoviedb.org/docs/append-to-response).

### Images: `GET /3/movie/{movie_id}/images` plus configuration

The images endpoint returns `backdrops`, `posters`, and `logos` arrays whose entries carry `file_path`, `width`, `height`, `iso_639_1`, and `vote_average`; `language` acts as a filter, and `include_image_language` takes "a comma separated list of ISO-639-1 values to query, for example: `en-US,null`" (https://developer.themoviedb.org/reference/movie-images).
Image responses only contain file paths, never full URLs.
Full URLs are built from three pieces - a `base_url`, a `file_size`, and a `file_path` - where the first two come from `GET /3/configuration` (https://developer.themoviedb.org/docs/image-basics).
The configuration endpoint returns an `images` object with `base_url`, `secure_base_url`, and per-type size lists (`backdrop_sizes`, `logo_sizes`, `poster_sizes`, `profile_sizes`, `still_sizes`) plus `change_keys` (https://developer.themoviedb.org/reference/configuration-details).
The docs' example composed URL is `https://image.tmdb.org/t/p/w500/1E5baAaEse26fej7uHcjOgEE2t2.jpg` (https://developer.themoviedb.org/docs/image-basics).
Unverified: a recommended refresh cadence for the configuration payload; the pages retrieved did not state one, so Anchor should just cache it and re-fetch periodically.

### Discovery: `GET /3/discover/movie`

The endpoint's stated purpose is "Find movies using over 30 filters and sort options" (https://developer.themoviedb.org/reference/discover-movie).
Filters include: `with_genres`/`without_genres`, `with_keywords`/`without_keywords`, `with_cast`, `with_crew`, `with_people`, `with_companies`/`without_companies`, `with_original_language`, `with_origin_country`, `with_runtime.gte`/`.lte`, `vote_average.gte`/`.lte`, `vote_count.gte`/`.lte`, `primary_release_year`, `primary_release_date.gte`/`.lte`, `release_date.gte`/`.lte`, `with_release_type`, `certification` (with `.gte`/`.lte` and `certification_country`), `with_watch_providers`/`without_watch_providers` with `watch_region`, and `with_watch_monetization_types` (flatrate, free, ads, rent, buy) (https://developer.themoviedb.org/reference/discover-movie).
Several accept comma (AND) or pipe (OR) separated value lists (https://developer.themoviedb.org/reference/discover-movie).
`sort_by` defaults to `popularity.desc` and supports `original_title`, `popularity`, `revenue`, `primary_release_date`, `title`, `vote_average`, and `vote_count`, each asc or desc (https://developer.themoviedb.org/reference/discover-movie).

### The pagination cap

TMDB error code 22 states: "Invalid page: Pages start at 1 and max at 500. They are expected to be an integer." (https://developer.themoviedb.org/docs/errors).
So any single paginated query - search, discover, similar, recommendations - can surface at most 500 pages.
Unverified: the fixed page size of 20 results per page; it is universally observed in practice, but neither the reference pages nor the FAQ retrieved for this note state the number, so treat "500 pages x 20 = 10,000 items per query" as the practical ceiling with the multiplier unconfirmed by docs.
The cap is per filter combination, so a catalog-wide sweep must be sliced (for example by `primary_release_date` ranges) to stay under 500 pages per slice.

## 2. Auth model, rate limits, and terms

### Application auth: API key vs bearer token

TMDB v3 supports two application-level credentials: a v3 `api_key` query parameter, or the v4 "API Read Access Token" sent as an `Authorization: Bearer` header (https://developer.themoviedb.org/docs/authentication-application).
The docs call the bearer token "the default method to authenticate" and note it works across both v3 and v4 methods; "Both authentication methods provide the same level of access, and which one you choose is completely up to you" (https://developer.themoviedb.org/docs/authentication-application).
User-level (session) authentication exists only to act on a TMDB user account - it lets users "rate movies, maintain their favourite and watch lists as well as do things like create and edit custom lists - all while staying in sync with their account on TMDB" (https://developer.themoviedb.org/docs/authentication-user).
Anchor keeps its own accounts and only reads TMDB data, so a single application credential suffices; no per-end-user TMDB auth is needed.

### Rate limits

The rate-limiting doc states: "As of December 16, 2019, we have disabled the original API rate limiting (40 requests every 10 seconds.)" (https://developer.themoviedb.org/docs/rate-limiting).
On current limits it says they "sit somewhere in the 40 requests per second range. This limit could change at any time so be respectful of the service we have built and respect the `429` if you receive one." (https://developer.themoviedb.org/docs/rate-limiting).
Note: secondary sources often quote ~50 req/s, but TMDB's own doc as retrieved says the 40 req/s range; the docs' number wins.
Error code 25 is the legacy limiter message: "Your request count (#) is over the allowed limit of (40)." (https://developer.themoviedb.org/docs/errors).
Unverified: whether current enforcement happens at the CDN layer specifically; the doc text retrieved describes soft upper limits and 429 handling but did not attribute them to the CDN.
Practical reading: no hard published quota, generous soft ceiling, and the obligation is to back off on 429.

### Terms of use

The license grant is "worldwide (except as limited below), non-exclusive, non-transferable, non-sublicensable" (https://www.themoviedb.org/api-terms-of-use, Paragraph 1.A).
The restrictions section prohibits, among other things:

- "Cache, for longer than 6 months, any information obtained through or from TMDB or the TMDB APIs" (https://www.themoviedb.org/api-terms-of-use).
- "Make derivatives of the TMDB APIs or TMDB Content" (https://www.themoviedb.org/api-terms-of-use).
- Applications that use excessive bandwidth or degrade access to TMDB's systems (https://www.themoviedb.org/api-terms-of-use).
- "Use the TMDB APIs or TMDB Content in connection with, including for training, a machine learning (ML) or artificial intelligence (AI) based Application" (https://www.themoviedb.org/api-terms-of-use).

The commercial-use section states the free license "does not permit any commercial use", lists as commercial uses things like charging users a fee, selling an application, use with a destination website, search engine, or chatbot, and "Training or validating a machine learning or artificial intelligence system... using TMDB content", and requires "a written agreement with TMDB that expressly permits Your commercial use" (https://www.themoviedb.org/api-terms-of-use, Section 2.A).
The developer FAQ confirms the pricing model: "Our API is free to use for non-commercial purposes as long as you attribute TMDB as the source of the data and/or images", and "Your project is considered commercial if the primary purpose is to create revenue for the benefit of the owner" (https://developer.themoviedb.org/docs/faq).
A personal, non-revenue Anchor is squarely non-commercial under that FAQ definition.
The ML/AI restriction is the ambiguous one for Anchor: the terms define "Application" broadly as any "website, program, service, application, or other product", and they do not define where a statistical taste model ends and an "ML or AI based Application" begins (https://www.themoviedb.org/api-terms-of-use).
The safe reading is that training models on TMDB content (and certainly LLM/embedding-style training) is outside the free license, while using TMDB metadata as lookup features in a hand-rolled scorer is not what the clause's training language targets - but this is an interpretation, not something the terms resolve.

### Attribution

The terms require the TMDB logo: "You must use the TMDB logo to identify Your use of TMDB, the TMDB APIs, or TMDB Content", displayed "less prominent than the logos or marks that primarily describe or identify Your Application", plus a prominent notice: "This [website, program, service, application, product] uses TMDB and the TMDB APIs but is not endorsed, certified, or otherwise approved by TMDB." (https://www.themoviedb.org/api-terms-of-use, Section 3).
The developer FAQ carries an older, shorter form of the notice ("This product uses the TMDB API but is not endorsed or certified by TMDB") (https://developer.themoviedb.org/docs/faq); the terms' longer wording is the authoritative one.
Watch-provider data has a second, separate attribution obligation: "In order to use this data you must attribute the source of the data as JustWatch. If we find any usage not complying with these terms we will revoke access to the API." (https://developer.themoviedb.org/reference/movie-watch-providers).

## 3. Taste-relevant data TMDB exposes

### What each endpoint gives

- Genres: `GET /3/genre/movie/list` returns "the list of official genres for movies" as `{id, name}` pairs (https://developer.themoviedb.org/reference/genre-movie-list); genre ids also ride along on detail responses.
- Keywords: `GET /3/movie/{movie_id}/keywords` returns a flat `keywords` array of `{id, name}` (https://developer.themoviedb.org/reference/movie-keywords).
- Credits: `GET /3/movie/{movie_id}/credits` returns `cast` entries with `id`, `name`, `character`, `order`, `known_for_department`, and `popularity`, and `crew` entries with `id`, `name`, `job`, and `department` (https://developer.themoviedb.org/reference/movie-credits).
- Similar: `GET /3/movie/{movie_id}/similar` is described as "Get the similar movies based on genres and keywords", with the caveat "This method only looks for other items based on genres and plot keywords. As such, the results found here are not always going to be 100%" (https://developer.themoviedb.org/reference/movie-similar).
- Recommendations: `GET /3/movie/{movie_id}/recommendations` exists with the same shape (paginated movie list), but the reference page carries no description of how recommendations are generated (https://developer.themoviedb.org/reference/movie-recommendations); the contrast with `similar` is therefore: `similar` is documented as a static genre+keyword match, `recommendations` is an undocumented black box.
- Watch providers: `GET /3/movie/{movie_id}/watch/providers` returns results keyed by country with `flatrate`, `rent`, and `buy` arrays plus a `link` back to TMDB, under the JustWatch attribution requirement quoted above (https://developer.themoviedb.org/reference/movie-watch-providers).
- Release dates and certifications: `GET /3/movie/{movie_id}/release_dates` returns per-country `release_dates` entries with `certification`, `release_date`, and a `type` from the enumeration "Premiere (1), Theatrical (limited) (2), Theatrical (3), Digital (4), Physical (5), TV (6)" (https://developer.themoviedb.org/reference/movie-release-dates).

### No embeddings API

The v3 reference index enumerates these endpoint groups: Account, Authentication, Certifications, Changes, Collections, Companies, Configuration, Credits, Discover, Find, Genres, Guest Sessions, Keywords, Lists, Movie Lists, Movies, Networks, People Lists, People, Reviews, Search, Trending, TV Series Lists, TV Series, TV Seasons, TV Episodes, TV Episode Groups, and Watch Providers (https://developer.themoviedb.org/reference/intro/getting-started).
There is nothing embeddings-adjacent - no vectors, no semantic similarity, no ML feature endpoints - anywhere in that catalog.
If Anchor wants film embeddings it must build them itself from TMDB's symbolic features, which then collides with the ML/AI terms question in section 2.

### Usability as recommender features (analysis, not sourced)

Genres are dense (every film has them) but coarse - roughly 19 movie genres exist, so they separate broad taste clusters only.
Credits are dense and high-signal: director and top-billed cast (low `order` values) are classic taste features, and person ids are stable join keys into `/discover` (`with_people`, `with_cast`, `with_crew`).
Keywords are the richest thematic signal but are crowd-sourced: popular films carry dozens, obscure films often carry none, so any keyword-based similarity needs a sparsity fallback.
`similar` and `recommendations` are usable as candidate generators (cheap "more like X" pools) but not as ranking signals, since one is a blunt genre+keyword match by TMDB's own admission and the other is undocumented.
Watch providers are a filter, not a taste feature, and are region-keyed and volatile.
Certifications are sparse and country-dependent; treat them as optional display metadata, not model input.
`vote_average`/`vote_count` and `popularity` come free on list responses and are useful as priors and for filtering out barely-rated entries.

## 4. The Letterboxd data export

### What the export is

"There's an account export option in Settings that bundles your entire account (including deleted content, and reviews for deleted films) into a single ZIP file of CSV documents." (https://letterboxd.zendesk.com/hc/en-us/articles/15179196880911-Can-I-get-a-copy-of-my-account-data).
First-party pages confirm by name that the bundle contains `ratings.csv` and `watched.csv`, each with a `Date` column: "Open your ratings.csv (or watched.csv) file and rename the 'Date' column to 'Watched Date'." (https://letterboxd.com/journal/2024-year-in-review-faq/); the help center repeats the same rename instruction (https://letterboxd.zendesk.com/hc/en-us/articles/15178773269263-I-ve-been-marking-films-watched-instead-of-logging-them-to-my-Diary-How-can-I-fix-this).
Exported dates come with a timezone quirk: "our exported dates are in New Zealand time, so they may import one day later than desired (depending on your location)" (https://letterboxd.com/journal/2024-year-in-review-faq/).

The full file inventory was verified against a real account export on 2026-08-02 (recorded in [Obtain a real Letterboxd export](https://github.com/KyleKDang/anchor/issues/16)).
The zip contained `profile.csv`, `ratings.csv`, `watched.csv`, `watchlist.csv`, `diary.csv`, `reviews.csv`, `comments.csv`, a `likes/` folder (`films.csv`, `lists.csv`, `reviews.csv`), a `deleted/` folder (`comments.csv`, `diary.csv`, `reviews.csv`), and an `orphaned/` folder (`comments.csv`, `diary.csv`, `reviews.csv`) that community documentation does not mention.
No `lists/` folder appeared because the account has no lists, so folders are conditional on account content and the importer must not assume a fixed inventory.

The exact column headers were confirmed against the same export: `Date,Name,Year,Letterboxd URI,Rating` for ratings.csv and `Date,Name,Year,Letterboxd URI` for watchlist.csv, exactly as community sources report.
Whole-star ratings serialize without a decimal (`3`, not `3.0`), so `Rating` must be parsed as a decimal rather than pattern-matched on `.5`.
Per-file headers for the whole zip, plus real matcher test cases (an en dash followed by a non-breaking space in exported Star Wars titles, the middle dot in `WALL·E`, comma-bearing titles), are recorded in the ticket resolution.

### Rating scale

Letterboxd's own CSV format documents `Rating` as "decimals from 0.5-5 including 0.5 increments, a rating for the film out of five", and a `Rating10` import column of integers 1-10 is "converted to 0.5-5 scale" (https://letterboxd.com/about/importing-data/).
So the platform scale, and the scale in exported ratings, is 0.5 to 5.0 in half-star steps - 10 distinct values.

### Matching export rows to TMDB

The export carries film Name, Year, and a Letterboxd URI, but no TMDB id.
Letterboxd's import format is the primary evidence that ids are a separate concern: the importer accepts optional `tmdbID` ("matches a film by its numeric TMDB ID, example: 860") and `imdbID` columns precisely because external files may carry them, while `LetterboxdURI` values look like "https://boxd.it/29qU" (https://letterboxd.com/about/importing-data/).
Letterboxd sources everything from TMDB: "Letterboxd sources all film-related data from The Movie Database (TMDb)" (https://letterboxd.zendesk.com/hc/en-us/articles/15269025512847-Where-does-Letterboxd-get-its-film-data-from), so a TMDB entry exists behind essentially every exported row.

The URI-to-TMDB chain was verified live on 2026-07-26:

- Navigating to https://boxd.it/29qU (the example URI from Letterboxd's own import docs) redirects to https://letterboxd.com/film/wargames/.
- That page's HTML carries `<body ... data-tmdb-id="860" data-tmdb-type="movie">` and a "TMDB" link to https://www.themoviedb.org/movie/860/ - the same id 860 Letterboxd's import docs use as their tmdbID example.
- A second check on https://letterboxd.com/film/parasite-2019/ showed `data-tmdb-id="496243"` and a link to https://www.themoviedb.org/movie/496243/.

So the boxd.it short link resolves to the film page, and the film page exposes the TMDB id in its markup - but this is page scraping of undocumented markup, not a supported API, and both the attribute names and Letterboxd's tolerance of automated fetching can change without notice (Letterboxd already serves 403s to non-browser fetchers, observed during this research).
The reverse mapping is first-party documented: `https://letterboxd.com/tmdb/{id}` redirects to (or force-imports) the film page for that TMDB movie id (https://letterboxd.zendesk.com/hc/en-us/articles/15269025512847-Where-does-Letterboxd-get-its-film-data-from).

### Failure cases for title+year matching

If Anchor matches on Name + Year against `/search/movie` (the only fully supported path), these cases will misfire:

- Retitled films: regional and festival titles differ from TMDB's canonical title; TMDB search covering "original, translated and alternative titles" (https://developer.themoviedb.org/reference/search-movie) mitigates but does not eliminate this.
- Year off-by-one: festival-premiere vs wide-release year conventions differ between Letterboxd and TMDB's `primary_release_year`; a strict year filter drops correct matches, so the matcher should retry with year plus or minus 1.
- Duplicate title+year pairs: remakes and same-named films in the same year make Name+Year non-unique; disambiguation needs a second signal (director via credits, or popularity as a tiebreak with manual review).
- Non-film entries: Letterboxd "for historic reasons" hosts "limited or miniseries, TV movies" and named exceptions like Black Mirror episodes and Big Little Lies (https://letterboxd.zendesk.com/hc/en-us/articles/15269096507407-Do-you-support-TV-shows).
- TV-side TMDB entries: "some films on Letterboxd link to TV entries instead of movies" after TMDB moved that content to its TV section (https://letterboxd.zendesk.com/hc/en-us/articles/15269096507407-Do-you-support-TV-shows), so `/search/movie` and `/movie/{id}` will simply miss these rows.
- Missing years: rows without a Year value degrade search precision sharply.
- Deleted films: the export includes "deleted content, and reviews for deleted films" (https://letterboxd.zendesk.com/hc/en-us/articles/15179196880911-Can-I-get-a-copy-of-my-account-data), and such rows may resolve to nothing live.
- Date skew: exported dates are New Zealand time (https://letterboxd.com/journal/2024-year-in-review-faq/), which can shift watch dates by a day but does not affect film identity.

Letterboxd's own importer sets the UX precedent for handling all of this: it does a "best-guess match" on title/year/director and "shows a summary of the import file prior to completing the import, so you can fix any mismatched titles and/or remove any inappropriate entries (such as TV entries that have matched to similarly named films)" (https://letterboxd.com/about/importing-data/).

## Constraints for Anchor's design

- One TMDB application credential (v4 bearer token preferred) covers everything; Anchor's own multi-account layer needs no per-user TMDB auth (https://developer.themoviedb.org/docs/authentication-application, https://developer.themoviedb.org/docs/authentication-user).
- Rate limits are soft (~40 req/s range) but unguaranteed; the client must honor 429 with backoff, and bulk ingest should self-throttle well below the ceiling (https://developer.themoviedb.org/docs/rate-limiting).
- Cached TMDB data must be refreshed at least every 6 months per the terms; Anchor's local film store therefore needs a staleness timestamp and a re-sync path, not a write-once cache (https://www.themoviedb.org/api-terms-of-use).
- Attribution is mandatory: TMDB logo plus the "not endorsed, certified, or otherwise approved" notice in the UI, and a separate JustWatch attribution wherever watch-provider data is shown (https://www.themoviedb.org/api-terms-of-use, https://developer.themoviedb.org/reference/movie-watch-providers).
- Free use is non-commercial only; if Anchor ever charges or drives revenue it needs a written commercial agreement with TMDB (https://www.themoviedb.org/api-terms-of-use).
- The terms bar ML/AI applications and ML training on TMDB content under the free license; Anchor's recommender should stay a transparent scoring/heuristic system over TMDB features, and any embedding-training ambition on TMDB content needs a licensing decision first (https://www.themoviedb.org/api-terms-of-use).
- Catalog-wide browsing through `/discover` is capped at 500 pages per query, so "browse everything" features must be designed as filtered slices, never as one exhaustive enumeration (https://developer.themoviedb.org/docs/errors).
- There is no embeddings API; taste features are genres (coarse, dense), people (dense, high-signal), keywords (rich, sparse), plus vote/popularity priors, with `similar`/`recommendations` usable only as candidate pools (https://developer.themoviedb.org/reference/intro/getting-started).
- `append_to_response` (up to 20 sub-requests) makes per-film ingest a single API call; the ingest pipeline should be designed around one bundled details call per film (https://developer.themoviedb.org/reference/movie-details).
- Letterboxd import arrives with no TMDB ids, so Anchor needs a real matching pipeline: Name+Year search with alternative-title tolerance, plus/minus-1-year retry, director disambiguation, and - decisively - a human review screen for mismatches, mirroring Letterboxd's own importer UX (https://letterboxd.com/about/importing-data/).
- The boxd.it URI to film page to `data-tmdb-id` scrape is a working but unsupported fallback for hard rows; treat it as best-effort, rate-limit it politely, and never make the pipeline depend on it (verified live 2026-07-26; markup undocumented).
- Some Letterboxd rows are structurally unmatchable to TMDB movies (TV-side entries, deleted films); the import design must include an explicit "unmatched" state rather than forcing every row to resolve (https://letterboxd.zendesk.com/hc/en-us/articles/15269096507407-Do-you-support-TV-shows).
- The export headers are confirmed against a real export (2026-08-02): `Date,Name,Year,Letterboxd URI,Rating` for ratings.csv and `Date,Name,Year,Letterboxd URI` for watchlist.csv, so Anchor's import schema can freeze against them ([Obtain a real Letterboxd export](https://github.com/KyleKDang/anchor/issues/16)).
- Ratings arrive on a 0.5-5.0 half-step scale (10 values), which is the input Anchor's pairwise-comparison model must be able to seed from (https://letterboxd.com/about/importing-data/).
