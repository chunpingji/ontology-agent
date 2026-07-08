"""0020_mock_data_tables

Revision ID: 0020_mock_data_tables
Revises: 0019_tpl_src_job
Create Date: 2026-07-07

Mock 外部事实源数据表 —— 支持运行时编辑部门、角色、设备、生产区域、团队成员 mock 数据。
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0020_mock_data_tables'
down_revision = '0019_tpl_src_job'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'mock_departments',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('code', sa.String(50), nullable=False),
        sa.Column('iri', sa.String(500), nullable=False),
        sa.Column('label', sa.String(200), nullable=False),
        sa.Column('description', sa.Text(), nullable=False, server_default=''),
        sa.Column('data_properties', postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default='[]'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('code', name='uq_mock_departments_code'),
    )

    op.create_table(
        'mock_roles',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('code', sa.String(50), nullable=False),
        sa.Column('iri', sa.String(500), nullable=False),
        sa.Column('label', sa.String(200), nullable=False),
        sa.Column('role_class_iri', sa.String(500), nullable=False),
        sa.Column('description', sa.Text(), nullable=False, server_default=''),
        sa.Column('data_properties', postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default='[]'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('code', name='uq_mock_roles_code'),
    )

    op.create_table(
        'mock_equipment',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('equipment_id', sa.String(100), nullable=False),
        sa.Column('iri', sa.String(500), nullable=False),
        sa.Column('label', sa.String(200), nullable=False),
        sa.Column('equipment_class_iri', sa.String(500), nullable=False),
        sa.Column('workshop_code', sa.String(50), nullable=False),
        sa.Column('data_properties', postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default='[]'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('equipment_id', name='uq_mock_equipment_id'),
    )

    op.create_table(
        'mock_production_areas',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('code', sa.String(50), nullable=False),
        sa.Column('iri', sa.String(500), nullable=False),
        sa.Column('label', sa.String(200), nullable=False),
        sa.Column('description', sa.Text(), nullable=False, server_default=''),
        sa.Column('data_properties', postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default='[]'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('code', name='uq_mock_production_areas_code'),
    )

    op.create_table(
        'mock_team_members',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('team_type', sa.String(20), nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('role_label', sa.String(200), nullable=False),
        sa.Column('department', sa.String(200), nullable=False),
        sa.Column('role_class_iri', sa.String(500), nullable=False),
        sa.Column('role_code', sa.String(50), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('team_type', 'role_code', name='uq_team_role'),
    )


def downgrade() -> None:
    op.drop_table('mock_team_members')
    op.drop_table('mock_production_areas')
    op.drop_table('mock_equipment')
    op.drop_table('mock_roles')
    op.drop_table('mock_departments')
