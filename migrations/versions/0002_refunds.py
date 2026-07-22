"""refunds: link refund ledger entries to the transaction they reverse

Revision ID: 0002_refunds
Revises: 0001_initial
Create Date: 2026-07-22
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0002_refunds"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "transactions",
        sa.Column("original_transaction_id", UUID(as_uuid=False), nullable=True),
    )
    op.add_column("transactions", sa.Column("reason", sa.String(500), nullable=True))
    op.create_foreign_key(
        "fk_transactions_original_transaction_id",
        "transactions",
        "transactions",
        ["original_transaction_id"],
        ["id"],
    )
    # NULLs are distinct in Postgres, so non-refund rows are unaffected while at most one
    # refund can reference a given original transaction.
    op.create_unique_constraint(
        "uq_transaction_original_transaction_id",
        "transactions",
        ["original_transaction_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_transaction_original_transaction_id", "transactions", type_="unique")
    op.drop_constraint(
        "fk_transactions_original_transaction_id", "transactions", type_="foreignkey"
    )
    op.drop_column("transactions", "reason")
    op.drop_column("transactions", "original_transaction_id")
