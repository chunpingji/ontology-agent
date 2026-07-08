"""0021_pde_conflict_decision

Revision ID: 0021_pde_conflict_decision
Revises: 0020_mock_data_tables
Create Date: 2026-07-07

CMCReport「推导 vs 原文」PDE 冲突的人工决策表：(job_id, conflict_key) 唯一，version 乐观并发。
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0021_pde_conflict_decision'
down_revision = '0020_mock_data_tables'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'pde_conflict_decisions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('job_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('conflict_key', sa.String(64), nullable=False, server_default='shared_line_pde'),
        sa.Column('chosen', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('note', sa.Text(), nullable=False, server_default=''),
        sa.Column('actor', sa.String(100), nullable=False, server_default=''),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('job_id', 'conflict_key', name='uq_pde_conflict_job_key'),
    )
    op.create_index(
        'ix_pde_conflict_decisions_job_id', 'pde_conflict_decisions', ['job_id']
    )


def downgrade() -> None:
    op.drop_index('ix_pde_conflict_decisions_job_id', table_name='pde_conflict_decisions')
    op.drop_table('pde_conflict_decisions')
