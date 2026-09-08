"""Default approval for existing validated document extraction; retain human decisions."""

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa

from alembic import op

revision = "0028_automatic_evidence_review"
down_revision = "0027_report_output_semantics"
branch_labels = None
depends_on = None

REASON = "文档识别结果通过系统校验，按默认审核规则自动通过；用户可提出异议。"


def upgrade():
    bind = op.get_bind()
    candidates = sa.table(
        "evidence_candidates",
        sa.column("id"),
        sa.column("job_id"),
        sa.column("revision"),
        sa.column("review_status"),
        sa.column("payload", sa.JSON()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    reviews = sa.table(
        "evidence_reviews",
        sa.column("id"),
        sa.column("candidate_id"),
        sa.column("expected_revision"),
        sa.column("decision"),
        sa.column("actor"),
        sa.column("reason"),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    rows = {row["id"]: row for row in bind.execute(sa.select(candidates)).mappings()}
    reviewed = set(bind.execute(sa.select(reviews.c.candidate_id)).scalars())
    eligible = {
        identity
        for identity, row in rows.items()
        if row["review_status"] == "pending"
        and identity not in reviewed
        and row["revision"] == 1
        and row["payload"].get("validation_status") == "passed"
        and row["payload"].get("provenance")
        and all(
            source.get("kind") == "document"
            and source.get("document_role") in {"analysis_source", "default_source"}
            for source in row["payload"]["provenance"]
        )
    }
    # Exclude stale/rejected dependencies transitively, regardless of row order.
    changed = True
    while changed:
        changed = False
        for identity in list(eligible):
            row = rows[identity]
            payload = row["payload"]
            for ref in [
                payload.get("subject"),
                payload.get("object"),
                *payload.get("dependency_refs", []),
            ]:
                if not ref:
                    continue
                target = rows.get(ref["candidate_id"])
                if (
                    target is None
                    or target["job_id"] != row["job_id"]
                    or target["revision"] != ref["revision"]
                    or target["payload"].get("validation_status") != "passed"
                    or (target["review_status"] != "confirmed" and target["id"] not in eligible)
                ):
                    eligible.remove(identity)
                    changed = True
                    break
    for identity in sorted(eligible):
        row = rows[identity]
        payload = {
            **row["payload"],
            "review_status": "confirmed",
            "review_source": "automatic",
            "review_reason": REASON,
        }
        now = datetime.now(timezone.utc)
        result = bind.execute(
            candidates.update()
            .where(
                candidates.c.id == identity,
                candidates.c.revision == row["revision"],
                candidates.c.review_status == "pending",
            )
            .values(review_status="confirmed", payload=payload, updated_at=now)
        )
        if result.rowcount == 1:
            bind.execute(
                reviews.insert().values(
                    id=uuid.uuid4().hex,
                    candidate_id=identity,
                    expected_revision=row["revision"],
                    decision="confirmed",
                    actor="system:automatic-review",
                    reason=REASON,
                    created_at=now,
                )
            )


def downgrade():
    # Audit decisions are business history. Rolling back code must not erase them.
    pass
