"""Scoped PID searches for Connect, measured in ADR-0017; no event rewrite."""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for field in ("pid", "parent_pid"):
        op.execute(
            f"CREATE INDEX ix_events_process_{field} ON events "
            f"(host_id, boot_id, json_extract(attributes_json, '$.{field}'), subject_id)"
        )


def downgrade() -> None:
    for field in ("pid", "parent_pid"):
        op.drop_index(f"ix_events_process_{field}", table_name="events")
