"""initial schema: wallets, transactions, processed_requests

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-19
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wallets",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("customer_id", sa.String(255), nullable=True),
        sa.Column("balance_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("balance_paise >= 0", name="ck_wallet_balance_non_negative"),
    )

    op.create_table(
        "transactions",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("wallet_id", UUID(as_uuid=False), sa.ForeignKey("wallets.id"), nullable=False),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("balance_after_paise", sa.BigInteger(), nullable=False),
        sa.Column("reference_id", sa.String(255), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("amount_paise > 0", name="ck_transaction_amount_positive"),
        sa.CheckConstraint(
            "balance_after_paise >= 0", name="ck_transaction_balance_after_non_negative"
        ),
    )
    op.create_index("ix_transactions_wallet_id", "transactions", ["wallet_id"])
    op.create_index("ix_transactions_reference_id", "transactions", ["reference_id"])

    op.create_table(
        "processed_requests",
        sa.Column("idempotency_key", sa.String(255), primary_key=True),
        sa.Column("wallet_id", UUID(as_uuid=False), sa.ForeignKey("wallets.id"), nullable=False),
        sa.Column("endpoint", sa.String(64), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("response_body", sa.Text(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_processed_requests_wallet_id", "processed_requests", ["wallet_id"])


def downgrade() -> None:
    op.drop_table("processed_requests")
    op.drop_index("ix_transactions_reference_id", table_name="transactions")
    op.drop_index("ix_transactions_wallet_id", table_name="transactions")
    op.drop_table("transactions")
    op.drop_table("wallets")
