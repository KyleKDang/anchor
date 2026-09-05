from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process configuration, read from ``ANCHOR_*`` environment variables."""

    model_config = SettingsConfigDict(env_prefix="ANCHOR_")

    database_url: str = "postgresql://anchor:anchor@localhost:5432/anchor"
    """libpq connection URL; shared by SQLAlchemy and the job queue."""

    health_worker_timeout: float = 5.0
    """Seconds the health check waits for the worker to answer its probe."""

    stalled_job_seconds: float = 30.0
    """Silence from a worker after which the job it was running is reclaimed.

    Thirty seconds is procrastinate's own bar: its workers beat every ten and call each
    other stalled at thirty, so a live worker has missed two beats before this fires.
    Lowering it without lowering those is what makes a busy worker look dead.
    """

    public_url: str = "http://localhost"
    """Where the app is reached from a browser; the base of every emailed link."""

    resend_api_key: str | None = None
    """Resend API key. Unset (the dev default), mail is logged instead of sent."""

    resend_base_url: str = "https://api.resend.com"
    mail_from: str = "Anchor <anchor@localhost>"

    sentry_dsn: str | None = None
    """Sentry error reporting. Unset (the dev default), errors are not reported."""

    tmdb_access_token: str | None = None
    """TMDB v4 read access token. Unset, every catalog call fails outright."""

    tmdb_base_url: str = "https://api.themoviedb.org/3"

    tmdb_requests_per_second: float = 4.0
    """The shared client's self-throttle, far under TMDB's ~40/s soft limit."""

    tmdb_max_attempts: int = 3
    """Tries per TMDB call, counting the first; the retries are the 429 backoff."""

    anthropic_api_key: str | None = None
    """Anthropic API key. Unset (the dev default), every LLM operation is skipped.

    Skipped rather than failed: the whole LLM layer degrades to cached results by design
    (ADR 0004), so a box without a key runs the app with prose that never refreshes
    rather than a worker whose jobs all fail.
    """

    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_version: str = "2023-06-01"

    llm_provider: str = "anthropic"
    """Which adapter the seam dispatches to; refused unless it is on the no-training
    allowlist (ADR 0003). v1 ships Anthropic only."""

    llm_max_attempts: int = 3
    """Tries per provider call, counting the first; the retries are the 429 backoff."""

    llm_cheap_model: str = "claude-haiku-4-5"
    llm_cheap_input_usd_per_mtok: float = 1.00
    llm_cheap_output_usd_per_mtok: float = 5.00
    """The cheap tier: listwise reranking and quality tags (taste-profile.md)."""

    llm_mid_model: str = "claude-sonnet-5"
    llm_mid_input_usd_per_mtok: float = 2.00
    llm_mid_output_usd_per_mtok: float = 10.00
    """The mid tier: prose regeneration, the one job whose output the owner reads."""

    llm_mid_tier_operations: str = "regenerate_prose_profile"
    """Comma-separated operations that run on the mid tier; the rest run on the cheap one.

    The assignment is spec (taste-profile.md) and this is where it is written down, so
    re-tuning which job deserves which tier is an environment variable rather than a
    code change - and the prices above move with the models without a migration.
    """

    llm_batched_operations: str = "rerank_candidates,tag_film_qualities"
    """Comma-separated operations dispatched through Message Batches rather than inline.

    Batches are half price and asynchronous, which is exactly the shape of a refresh
    that fans out over many films and that nothing is waiting on. Prose is deliberately
    not here: it is one call for one account, so a batch of one would buy a discount at
    the price of a poll loop.
    """

    llm_batch_poll_seconds: float = 5.0
    llm_batch_timeout_seconds: float = 3600.0
    """How the adapter waits out a batch. The API allows a batch 24 hours; a worker job
    holding a connection that long is a wedge, so this gives up and the refresh is simply
    missed - the next accumulated change asks again."""

    llm_account_monthly_cap_usd: float = 2.00
    llm_global_monthly_cap_usd: float = 10.00
    """The two caps of architecture.md, checked month-to-date before every dispatch.

    Hitting either skips the work and serves cached results: never a broken feed, never a
    runaway bill. Both are config because the right number is an operator's call about
    what this box is worth, not a design decision.
    """

    prose_placements_trigger: int = 10
    """New placements since the live prose that make it worth rewriting.

    Ten is roughly a session's work: enough that the ordering has visibly moved, far
    enough above one that no comparison can ever be what triggers a regeneration.
    """

    prose_drift_trigger: int = 3
    """Drift resolutions since the live prose that count as a wave rather than a one-off."""

    prose_staleness_comparisons: int = 40
    """The backstop: answered comparisons since the live prose, whatever else moved.

    An owner who settles films they already placed - keep-comparing, drift checks, the
    settling door - accumulates real evidence that lands no new placement, so without
    this their prose would describe a library they have since re-judged. Denominated in
    answers rather than days, because spend is earned by engagement (ADR 0004): a
    dormant account never reaches it.
    """

    film_refresh_days: int = 150
    """~5 months: still-referenced films re-sync at this age, inside ADR 0003's 6-month ceiling."""

    session_ttl_hours: int = 24 * 30
    """How long a login session lives."""

    verification_ttl_hours: int = 24
    """How long an emailed verification link stays valid."""

    cookie_secure: bool = True
    """Mark the session cookie Secure (browsers still accept it on http://localhost)."""

    readiness_forming_films: int = 20
    readiness_forming_bands: int = 3
    """Forming: enough rated films across enough bands for a stable weight-vector fit.

    The dimensions are spec (taste-profile.md); these numbers are its indicative ones,
    and moving them is tuning rather than a design change.
    """

    readiness_ready_films: int = 50
    readiness_ready_comparisons_per_film: float = 3.0
    readiness_ready_settled_share: float = 0.5
    """Ready: a real library that the owner has actually answered their way through.

    The two bars are the two halves of the spec's sentence - the vector must not be
    dominated by implied pairs (so answers have to accumulate faster than films do) nor
    by provisional ones (so half the library must rest on real judgments).
    """

    tier_swap_budget: int = 3
    """Engine-initiated swaps one session-boundary refresh may make.

    Damping is spec and the numbers are tuning (watchlist.md). Three is small enough that
    a wholesale change of taste rolls in over several sessions rather than arriving as a
    tier the owner does not recognise, and large enough that a real shift is visible the
    next time they look. Vacancy refills and a newly backlogged film are not counted
    against it - neither is churn.
    """

    tier_hysteresis: float = 0.05
    """How far a challenger must beat an incumbent, as a share of the backlog's score spread.

    Scores are a dot product against a per-account feature space, so their scale means
    nothing between two accounts and an absolute margin would damp one library to a
    standstill and another not at all. Measuring the margin against the spread of the
    scores actually on offer is the one reading that behaves the same everywhere.
    """

    tier_enter_cooldown: int = 3
    """Watches a film keeps a fresh seat for before the engine may drop it: no immediate drops."""

    tier_reentry_cooldown: int = 5
    """Watches a dropped film waits before it may return: no bounce-backs."""

    tier_staleness_watches: int = 10
    """Watches a tier film may be passed over before it rotates out.

    watchlist.md's indicative number. Denominated in the watch clock like every other
    measure here, so a dormant account never rotates anything.
    """

    import_max_upload_bytes: int = 20 * 1024 * 1024
    """A real export is a few hundred kilobytes; this is generous, not a target."""

    import_popularity_dominance: float = 5.0
    """How far an exact-title hit must outrank the runner-up to be accepted unasked.

    Five times is a landslide, not a lead: two plausible films of the same name sit far
    closer than this, so the rule fires on the unique-title case and stays out of the
    remake case, which is exactly the review screen's job.
    """

    import_review_candidates: int = 6
    """Candidates offered per review row. A page of choices is not a decision aid."""

    import_reset_confirm_comparisons: int = 10
    """Overall comparisons above which re-importing demands the typed confirmation.

    Below it the enumerated counts carry the whole warning; above it the owner has
    answered enough questions that the log is worth making them stop and type.
    """

    letterboxd_rescue_rate_limit: int = 20
    """Per-row Letterboxd scrapes per IP per window; the rescue is never bulk."""

    warmup_comparisons: int = 10
    warmup_placements: int = 5
    """How much evidence the warmup asks for, per path. Advisory targets, not gates.

    Both are the spec's own indicative numbers and both are tuning knobs: the warmup is
    skippable at every point, so these decide when it stops asking rather than when the
    owner is allowed to leave. Five placements at a handful of comparisons each roughly
    matches the import path's ten, which is why the two differ (onboarding-and-import.md).
    """

    warmup_candidates_per_band: int = 5
    """Anchor candidates offered per band on the import path. A page is not a decision aid."""

    rate_limit_window_seconds: float = 15 * 60
    """The sliding window the per-IP limits below count within."""

    signup_rate_limit: int = 5
    login_rate_limit: int = 10
    verify_rate_limit: int = 10
    debug_error_rate_limit: int = 3
    """Caps the Sentry check endpoint so nobody can loop it to burn the error quota."""

    @property
    def sqlalchemy_url(self) -> str:
        return self.database_url.replace("postgresql://", "postgresql+psycopg://", 1)

    @property
    def mid_tier_operations(self) -> frozenset[str]:
        return _names(self.llm_mid_tier_operations)

    @property
    def batched_operations(self) -> frozenset[str]:
        return _names(self.llm_batched_operations)


def _names(setting: str) -> frozenset[str]:
    """A comma-separated setting as a set. Blanks and stray spaces are the operator's."""
    return frozenset(name.strip() for name in setting.split(",") if name.strip())
