"""008 gliner-ner-extraction: extraction_configs 增 ner_columns（自由文本列白名单）

US3（FR-008，data-model §3.2）：声明为自由文本的列，其原文经本地零样本 NER 富化
本行属性（仅补空缺、结构化权威）。与既有 column_mapping 同为可空 JSON。沿用 0002
的防御式 ``op.add_column``（``sa.inspect`` 查列幂等），既有库可重复应用、不破坏遗留行。

Revision ID: 0005_add_ner_columns
Revises: 0004_declarative_rule_layer
Create Date: 2026-06-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_add_ner_columns"
down_revision = "0004_declarative_rule_layer"
branch_labels = None
depends_on = None

_TABLE = "extraction_configs"
_COLUMN = "ner_columns"


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if _TABLE not in set(insp.get_table_names()):
        return
    present = {c["name"] for c in insp.get_columns(_TABLE)}
    if _COLUMN in present:
        return
    op.add_column(_TABLE, sa.Column(_COLUMN, sa.JSON(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if _TABLE not in set(insp.get_table_names()):
        return
    present = {c["name"] for c in insp.get_columns(_TABLE)}
    if _COLUMN not in present:
        return
    op.drop_column(_TABLE, _COLUMN)
