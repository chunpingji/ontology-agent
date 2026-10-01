"""Human questions for slash-ambiguous relation groups and scoped answers."""

import re
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.document_interpretation import DocumentInterpretationAnswer
from app.services import audit
from app.services.document_analysis.run_store import content_hash
from app.services.document_harness.application import HarnessError, require_harness

MEANING_OPTIONS = (
    ("alternatives", "备选，择一"),
    ("parallel", "均参与且并行"),
    ("joint_unspecified", "均参与，先后或时间未说明"),
    ("unresolved", "仍无法判断"),
)
SCOPE_OPTIONS = (
    ("occurrence", "仅此处"),
    ("document", "整篇文档中的同类表达"),
    ("platform", "整个平台中的同类表达"),
)


def _slash_connects_members(ref, objects):
    text = ref["text"]
    if "/" not in text and "／" not in text:
        return False
    labels = [
        (obj.get("referent") or {}).get("text") or obj["label"]
        for obj in objects
    ]
    for left in labels:
        for right in labels:
            if left == right or not left or not right:
                continue
            if re.search(re.escape(left) + r"\s*[/／]\s*" + re.escape(right), text):
                return True
    return False


def _scope_keys(run, task):
    signature = task["signature"]
    return {
        "occurrence": content_hash([
            "slash-answer", "occurrence", str(run.recognition_run_id), task["id"],
        ]),
        "document": content_hash(["slash-answer", "document", run.owner_id,
                                  run.document_hash, signature]),
        "platform": content_hash(["slash-answer", "platform", signature]),
    }


def _candidates(run, state):
    entities = state.get("entities", {})
    candidates = {}
    accepted = set()
    for group in state.get("relation_groups", {}).values():
        base = (group["subject_id"], group["predicate_iri"],
                tuple(sorted(group["object_ids"])))
        if group["state"] == "accepted":
            accepted.add(base)
        if group["state"] != "unresolved" or len(group["object_ids"]) < 2:
            continue
        reason = group.get("reason", "") + group.get("proposal_reason", "")
        if (group["participation"] != "unknown"
                and "斜杠" not in reason and "slash" not in reason.lower()):
            continue
        ids = [group["subject_id"], *group["object_ids"]]
        if any(key not in entities or entities[key]["state"] != "accepted" for key in ids):
            continue
        subject, *objects = [entities[key] for key in ids]
        refs = [ref for ref in group.get("evidence", [])
                if _slash_connects_members(ref, objects)]
        if not refs:
            continue
        signature = content_hash([
            "slash-relation-v1", group["predicate_iri"], subject["class_iri"],
            sorted(obj["class_iri"] for obj in objects),
        ])
        location = sorted((ref["source_id"], ref["start"], ref["end"]) for ref in refs)
        task_id = content_hash(["slash-task", base, location])
        candidates[task_id] = {
            "id": task_id, "base": base, "signature": signature,
            "subject_label": subject["label"],
            "object_labels": [obj["label"] for obj in objects],
            "relation_label": group["label"], "evidence": refs,
        }
    return [row for row in candidates.values() if row["base"] not in accepted]


def _public_task(db, run, task, answers=None):
    keys = _scope_keys(run, task)
    rows = ({scope: db.get(DocumentInterpretationAnswer, key) for scope, key in keys.items()}
            if answers is None else {scope: answers.get(key) for scope, key in keys.items()})
    answer = next((rows[scope] for scope in ("occurrence", "document", "platform")
                   if rows[scope] is not None), None)
    return {
        "id": task["id"],
        "subject_label": task["subject_label"],
        "object_labels": task["object_labels"],
        "relation_label": task["relation_label"],
        "evidence": task["evidence"],
        "questions": [
            {"prompt": "此处斜杠表示这些对象之间的什么含义？",
             "options": [{"value": value, "label": label} for value, label in MEANING_OPTIONS]},
            {"prompt": "这个解释适用于哪个范围？",
             "options": [{"value": value, "label": label} for value, label in SCOPE_OPTIONS]},
        ],
        "answer": ({"meaning": answer.meaning, "scope": answer.scope,
                    "revision": answer.revision} if answer else None),
        "scope_revisions": {scope: row.revision if row else 0 for scope, row in rows.items()},
    }


def interpretation_tasks(db, run, state):
    require_harness(db, run)
    return attach_interpretation_answers(db, run, _candidates(run, state))


def attach_interpretation_answers(db, run, task_seeds):
    """Resolve all current answers in one query, retaining the three-scope precedence."""
    keys = {key for task in task_seeds for key in _scope_keys(run, task).values()}
    answers = {row.scope_key: row for row in db.scalars(
        select(DocumentInterpretationAnswer).where(DocumentInterpretationAnswer.scope_key.in_(keys))
    )} if keys else {}
    return [_public_task(db, run, task, answers) for task in task_seeds]


def submit_answer(db, run, identity, task_id, request):
    from app.services.document_harness.runtime import read_rows

    require_harness(db, run)
    if request.scope == "platform" and identity.role != "senior_analyst":
        raise HarnessError("ROLE_FORBIDDEN", "仅高级分析师可设置整个平台的解释", status_code=403)
    if request.meaning == "unresolved" and request.scope != "occurrence":
        raise HarnessError("INVALID_REQUEST", "无法判断只适用于此处", status_code=400)
    state = read_rows(db, run, domains={"entities", "relation_groups"})
    task = next((item for item in _candidates(run, state) if item["id"] == task_id), None)
    if task is None:
        raise HarnessError("RUN_STATE_CONFLICT", "该歧义任务已不适用", status_code=409)
    key = _scope_keys(run, task)[request.scope]
    row = db.scalar(select(DocumentInterpretationAnswer).where(
        DocumentInterpretationAnswer.scope_key == key,
    ).with_for_update())
    if (row.revision if row else 0) != request.expected_revision:
        raise HarnessError("RUN_REVISION_CONFLICT", "解释已更新，请刷新后重试", status_code=409)
    if row is None:
        row = DocumentInterpretationAnswer(
            scope_key=key, scope=request.scope, meaning=request.meaning,
            actor_id=identity.username, revision=1,
        )
        db.add(row)
    else:
        row.meaning = request.meaning
        row.actor_id = identity.username
        row.revision += 1
        row.updated_at = datetime.now(UTC)
    audit.append(db, "document_analysis.interpretation_answer", actor=identity.username,
                 details={"run_id": str(run.recognition_run_id), "task_id": task_id,
                          "scope": request.scope, "meaning": request.meaning}, commit=False)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HarnessError("RUN_REVISION_CONFLICT", "解释已更新，请刷新后重试",
                           status_code=409) from exc
    return _public_task(db, run, task)
