"""guard publication retries with a durable claim token"""

from alembic import op
import sqlalchemy as sa


revision = "0007_p04_claim_token"
down_revision = "0006_p04_artifacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("publication_claim_token", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_runs", "publication_claim_token")
