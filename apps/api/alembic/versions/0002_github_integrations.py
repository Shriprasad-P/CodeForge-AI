"""phase3 github integrations

Revision ID: 0002_github_integrations
Revises: 0001_phase2_auth
Create Date: 2026-08-10

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_github_integrations"
down_revision: Union[str, Sequence[str], None] = "0001_phase2_auth"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "github_accounts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("github_user_id", sa.BigInteger(), nullable=False),
        sa.Column("github_login", sa.String(length=255), nullable=False),
        sa.Column("account_type", sa.String(length=32), nullable=False),
        sa.Column("avatar_url", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_github_accounts_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_github_accounts")),
        sa.UniqueConstraint("github_user_id", name="uq_github_accounts_github_user_id"),
        sa.UniqueConstraint("user_id", name=op.f("uq_github_accounts_user_id")),
    )
    op.create_index(op.f("ix_github_accounts_user_id"), "github_accounts", ["user_id"], unique=False)

    op.create_table(
        "github_installations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("github_account_id", sa.UUID(), nullable=True),
        sa.Column("github_installation_id", sa.BigInteger(), nullable=False),
        sa.Column("account_login", sa.String(length=255), nullable=False),
        sa.Column("account_type", sa.String(length=32), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("repository_selection", sa.String(length=32), nullable=False),
        sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["github_account_id"],
            ["github_accounts.id"],
            name=op.f("fk_github_installations_github_account_id_github_accounts"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_github_installations_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_github_installations")),
        sa.UniqueConstraint(
            "github_installation_id",
            name="uq_github_installations_github_installation_id",
        ),
    )
    op.create_index(op.f("ix_github_installations_user_id"), "github_installations", ["user_id"], unique=False)

    op.create_table(
        "repository_connections",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("installation_id", sa.UUID(), nullable=False),
        sa.Column("github_repository_id", sa.BigInteger(), nullable=False),
        sa.Column("owner", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=511), nullable=False),
        sa.Column("default_branch", sa.String(length=255), nullable=False),
        sa.Column("private", sa.Boolean(), nullable=False),
        sa.Column("html_url", sa.String(length=512), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["installation_id"],
            ["github_installations.id"],
            name=op.f("fk_repository_connections_installation_id_github_installations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_repository_connections_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_repository_connections")),
        sa.UniqueConstraint("user_id", "github_repository_id", name="uq_repository_connections_user_repo"),
    )
    op.create_index(
        op.f("ix_repository_connections_github_repository_id"),
        "repository_connections",
        ["github_repository_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_repository_connections_installation_id"),
        "repository_connections",
        ["installation_id"],
        unique=False,
    )
    op.create_index(op.f("ix_repository_connections_user_id"), "repository_connections", ["user_id"], unique=False)

    op.create_table(
        "github_webhook_deliveries",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("delivery_id", sa.String(length=64), nullable=False),
        sa.Column("event", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_github_webhook_deliveries")),
        sa.UniqueConstraint("delivery_id", name=op.f("uq_github_webhook_deliveries_delivery_id")),
    )
    op.create_index(
        op.f("ix_github_webhook_deliveries_delivery_id"),
        "github_webhook_deliveries",
        ["delivery_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_github_webhook_deliveries_delivery_id"), table_name="github_webhook_deliveries")
    op.drop_table("github_webhook_deliveries")
    op.drop_index(op.f("ix_repository_connections_user_id"), table_name="repository_connections")
    op.drop_index(op.f("ix_repository_connections_installation_id"), table_name="repository_connections")
    op.drop_index(op.f("ix_repository_connections_github_repository_id"), table_name="repository_connections")
    op.drop_table("repository_connections")
    op.drop_index(op.f("ix_github_installations_user_id"), table_name="github_installations")
    op.drop_table("github_installations")
    op.drop_index(op.f("ix_github_accounts_user_id"), table_name="github_accounts")
    op.drop_table("github_accounts")
