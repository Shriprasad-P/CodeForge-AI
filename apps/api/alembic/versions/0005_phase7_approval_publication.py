"""phase7 approval and publication metadata"""

from alembic import op
import sqlalchemy as sa

revision = "0005_phase7_approval_publication"
down_revision = "0004_agent_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("approval_status", sa.String(32), server_default="pending", nullable=False))
    op.add_column("agent_runs", sa.Column("approved_by_user_id", sa.UUID(), nullable=True))
    op.add_column("agent_runs", sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("agent_runs", sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("agent_runs", sa.Column("rejection_reason", sa.String(1024), nullable=True))
    op.add_column("agent_runs", sa.Column("base_commit_sha", sa.String(64), nullable=True))
    op.add_column("agent_runs", sa.Column("diff_hash", sa.String(64), nullable=True))
    op.add_column("agent_runs", sa.Column("publication_status", sa.String(32), server_default="pending", nullable=False))
    op.add_column("agent_runs", sa.Column("branch_name", sa.String(255), nullable=True))
    op.add_column("agent_runs", sa.Column("commit_sha", sa.String(64), nullable=True))
    op.add_column("agent_runs", sa.Column("github_pr_number", sa.Integer(), nullable=True))
    op.add_column("agent_runs", sa.Column("github_pr_id", sa.Integer(), nullable=True))
    op.add_column("agent_runs", sa.Column("github_pr_url", sa.String(1024), nullable=True))
    op.create_foreign_key("fk_agent_runs_approved_by_user_id_users", "agent_runs", "users", ["approved_by_user_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    op.drop_constraint("fk_agent_runs_approved_by_user_id_users", "agent_runs", type_="foreignkey")
    for name in ("github_pr_url", "github_pr_id", "github_pr_number", "commit_sha", "branch_name", "publication_status", "diff_hash", "base_commit_sha", "rejection_reason", "rejected_at", "approved_at", "approved_by_user_id", "approval_status"):
        op.drop_column("agent_runs", name)
