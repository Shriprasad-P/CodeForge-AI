"""normalize unique column indexes to match SQLAlchemy metadata

Revision ID: 0010_normalize_unique_indexes
Revises: 0009_workflow_correlation

The original migrations created both a unique constraint and a separate
non-unique index for columns declared ``unique=True, index=True``.  The ORM
metadata represents those declarations as one unique index, so Alembic
reported drift on every check.  This small, deterministic migration preserves
the uniqueness invariant while removing the redundant indexes/constraints.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0010_normalize_unique_indexes"
down_revision: Union[str, Sequence[str], None] = "0009_workflow_correlation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table, index_name, constraint_name, column in (
        ("users", "ix_users_email", "uq_users_email", "email"),
        ("auth_sessions", "ix_auth_sessions_token_hash", "uq_auth_sessions_token_hash", "token_hash"),
        ("github_accounts", "ix_github_accounts_user_id", "uq_github_accounts_user_id", "user_id"),
        (
            "github_webhook_deliveries",
            "ix_github_webhook_deliveries_delivery_id",
            "uq_github_webhook_deliveries_delivery_id",
            "delivery_id",
        ),
    ):
        op.drop_index(index_name, table_name=table)
        op.drop_constraint(constraint_name, table_name=table, type_="unique")
        op.create_index(index_name, table, [column], unique=True)


def downgrade() -> None:
    for table, index_name, constraint_name, column in (
        ("users", "ix_users_email", "uq_users_email", "email"),
        ("auth_sessions", "ix_auth_sessions_token_hash", "uq_auth_sessions_token_hash", "token_hash"),
        ("github_accounts", "ix_github_accounts_user_id", "uq_github_accounts_user_id", "user_id"),
        (
            "github_webhook_deliveries",
            "ix_github_webhook_deliveries_delivery_id",
            "uq_github_webhook_deliveries_delivery_id",
            "delivery_id",
        ),
    ):
        op.drop_index(index_name, table_name=table)
        op.create_unique_constraint(constraint_name, table, [column])
        op.create_index(index_name, table, [column], unique=False)
