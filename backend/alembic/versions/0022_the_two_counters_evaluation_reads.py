"""The counters the named indicator set is denominated in, and where they start.

Rotation rate and the accept and dismissal rates are counted per opportunity, and none of
those opportunities left a countable trace. A staleness rotation writes a re-entry mark
that the next one overwrites, that a displacement and a not-now write identically, and
that is cleared outright when the film comes back. A restock stamps the profile version it
ran for, which answers whether another is due and keeps no history. An accept writes an
account-film that removing the film from the backlog deletes outright.

So each is counted where it happens. A counter that starts mid-life needs to say so, or
the rate built on it divides a numerator that starts today by a denominator that runs back
to the account's first week: the tier's rotations get a baseline of the watch clock they
started at, and the feed's three counters start together, so its rates cover one span on
both sides.

They are read by the operator's SQL and by nothing else: evaluation reads but never feeds
(ADR 0012).

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-07

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | Sequence[str] | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tier_states",
        sa.Column("staleness_rotations", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "tier_states",
        sa.Column("rotations_counted_from", sa.Integer(), server_default="0", nullable=False),
    )
    # Where counting begins for an account that already has a history: the watches Anchor
    # itself logged, which is the same denominator the rate is read against. Watches that
    # arrived with an import carry their real diary date and sit before the account
    # existed, so they are no more an opportunity here than they were then.
    op.execute(
        """
        UPDATE tier_states t
        SET rotations_counted_from = (
            SELECT count(*)
            FROM watch_events w
            JOIN accounts a ON a.id = w.account_id
            WHERE w.account_id = t.account_id AND w.watched_at > a.created_at
        )
        """
    )
    for counter in ("restock_counter", "accept_counter", "dismissal_counter"):
        op.add_column(
            "feed_states",
            sa.Column(counter, sa.Integer(), server_default="0", nullable=False),
        )
    # The feed's three start at zero together and are never backfilled. The restocks and
    # accepts that already happened cannot be recovered - a restock left only the version
    # it last ran for, and an accept the owner has since undone left nothing - and a rate
    # whose two sides ran from different days would be worse than one that starts today.


def downgrade() -> None:
    for counter in ("dismissal_counter", "accept_counter", "restock_counter"):
        op.drop_column("feed_states", counter)
    op.drop_column("tier_states", "rotations_counted_from")
    op.drop_column("tier_states", "staleness_rotations")
