#!/usr/bin/env python3
"""Run a real local-model template acceptance in an isolated SQL/World workspace.

Never touches application data or labels automated verification as human sign-off.
No credentials, source documents or model weights are copied into tracked files.
"""

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def stage_workflow(args, engine, ir, runner, run, template_schema):
    """Persist genuine candidates; only supplied, version-matching decisions review them."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    import app.models  # noqa: F401
    from app.db import Base
    from app.models.extraction import AstTemplate, ExtractionJob
    from app.services.extraction.candidate_store import CandidateStore
    from app.services.extraction.evidence_identity import evidence_hash
    from app.services.fact_commit import FactCommitService
    from app.services.ontology_instance_writer import EvidenceInstanceWriter
    from app.services.reporting.snapshot_report import build_report_inputs

    sql = create_engine(f"sqlite:///{args.work_dir / 'acceptance.sqlite3'}")
    Base.metadata.create_all(sql)
    job_id = uuid5(NAMESPACE_URL, "template-acceptance:" + ir.analysis_id)
    template_hash = evidence_hash(template_schema)
    template_id = uuid5(job_id, "evidence-template:" + template_hash)
    with Session(sql, expire_on_commit=False) as db:
        if db.get(AstTemplate, template_id) is None:
            db.add(
                AstTemplate(
                    id=template_id,
                    name="风险评估文档-隔离验收-" + template_hash[:8],
                    version="v19",
                    status="draft",
                    iri_pattern=args.input_class,
                    schema_json=template_schema,
                    sample_docx_path=str(args.sample) if args.sample else None,
                )
            )
        job = db.get(ExtractionJob, job_id)
        if job is None:
            job = ExtractionJob(
                id=job_id,
                source_type="word",
                source_filename=args.source.name,
                document_path=str(args.source),
                source_config={
                    "template_id": str(template_id),
                    "doc_class_iri": args.input_class,
                },
            )
            db.add(job)
        else:
            job.source_config = {**(job.source_config or {}), "template_id": str(template_id)}
        db.commit()
        store = CandidateStore(db)
        store.save_analysis(job_id, ir, run.model_dump(mode="json", exclude={"candidates"}))
        values = store.persist_validated(job_id, run.candidates, actor="acceptance-import")
        # This file is a review input, NOT an automatically approved decision list.
        (args.work_dir / "candidates-for-review.json").write_text(
            json.dumps(
                {
                    "analysis_id": ir.analysis_id,
                    "model_identity": runner.model_identity,
                    "candidates": [c.model_dump(mode="json") for c in values],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        if args.review_file:
            review = json.loads(args.review_file.read_text())
            if (
                review.get("analysis_id") != ir.analysis_id
                or review.get("model_identity") != runner.model_identity
            ):
                raise ValueError("review belongs to a different source/model revision")
            if not review.get("actor") or not review.get("decisions"):
                raise ValueError("an explicit reviewer and individual decisions are required")
            for decision in review["decisions"]:
                candidate = store.get(decision["candidate_id"])
                if candidate.candidate_id not in {c.candidate_id for c in values}:
                    raise ValueError("review candidate is outside this acceptance run")
                store.review(
                    candidate.candidate_id,
                    decision["revision"],
                    decision["decision"],
                    decision["reason"],
                    review["actor"],
                )
            approved = [
                c
                for c in store.list(job_id)
                if c.review_status == "confirmed" and c.validation_status == "passed"
            ]
            if approved:
                service = FactCommitService(
                    db, EvidenceInstanceWriter(engine, args.work_dir / "fact-worlds")
                )
                items = [{"candidate_id": c.candidate_id, "revision": c.revision} for c in approved]
                receipt = service.request(job_id, evidence_hash(items), items, review["actor"])
                if service.apply(receipt.id).status != "succeeded":
                    raise ValueError("isolated fact commit failed; no report is published")
        inputs = build_report_inputs(db, job, schema=runner.schema)
        (args.work_dir / "coverage.json").write_text(
            json.dumps(
                {
                    key: value
                    for key, value in inputs.items()
                    if key not in {"fact_snapshot", "semantic_schema", "template_schema", "rules"}
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return {
            "job_id": str(job_id),
            "template_id": str(template_id),
            "candidate_persistence": "passed",
            "snapshot_id": inputs["snapshot_id"],
            "required_gaps": inputs["required_gaps"],
            "template_status": "draft",
            "report_generation": "blocked_template_publication_and_frozen_rules_required",
            "gold_template_passed": False,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--input-class", required=True)
    parser.add_argument("--model-url", default="http://localhost:8080/v1")
    parser.add_argument("--model", default="Qwen3.6-35B-A3B")
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--server-model-path", required=True)
    parser.add_argument("--max-tasks", type=int, default=128)
    parser.add_argument("--inspect-only", action="store_true")
    parser.add_argument(
        "--retry-failed", action="store_true",
        help="Explicitly retry failed tasks within the original total attempt budget",
    )
    parser.add_argument(
        "--capture-model-responses", action="store_true",
        help="Keep confidential model requests/responses in this isolated directory only",
    )
    parser.add_argument(
        "--pause-after", type=int,
        help="Pause after this many new tasks without changing checkpoint budget identity",
    )
    parser.add_argument(
        "--reuse-run",
        action="store_true",
        help="Reuse an exact matching local run without new inference",
    )
    parser.add_argument("--sample", type=Path)
    parser.add_argument(
        "--template", type=Path, default=ROOT / "backend/tests/fixtures/risk_template_dea037a2.json"
    )
    parser.add_argument(
        "--upgrade-plan",
        type=Path,
        default=ROOT / "specs/019-evidence-semantic-extraction/risk-template-upgrade.json",
    )
    parser.add_argument(
        "--review-file", type=Path, help="Explicit versioned reviewer decisions; never inferred"
    )
    args = parser.parse_args()
    if not args.source.is_file() or not args.work_dir.is_dir():
        parser.error("source and an existing isolated work directory are required")
    if args.pause_after is not None and args.pause_after < 1:
        parser.error("pause-after must be positive")
    from app.config import settings
    from app.services.extraction.local_semantic_model import configured_generic_runner
    from app.services.extraction.word_analysis import analyze_word_core
    from app.services.ontology_engine import OntologyEngine

    settings.local_llm_enabled = True
    settings.local_llm_base_url = args.model_url
    settings.local_llm_model = args.model
    settings.local_llm_model_revision = args.model_revision
    settings.local_llm_tokenizer_backend = "llama_server"
    settings.local_llm_server_model_path = args.server_model_path
    settings.evidence_max_input_tokens = 32768
    settings.evidence_max_tasks = args.max_tasks
    engine = OntologyEngine(ROOT / "ontology/slpra", args.work_dir / "schema.sqlite3")
    engine.load()
    ir = analyze_word_core(args.source, role="default_source").ir
    runner = configured_generic_runner(engine)
    if args.capture_model_responses:
        trace_dir = args.work_dir / "model-calls"
        trace_dir.mkdir(mode=0o700, exist_ok=True)
        trace_index = len(list(trace_dir.glob("*.json")))

        def capture(value):
            nonlocal trace_index
            trace_index += 1
            (trace_dir / f"{time.time_ns()}-{trace_index}.json").write_text(
                json.dumps(value, ensure_ascii=False, indent=2)
            )

        runner.trace_fn = capture
    from app.services.reporting.template_upgrade import prepare_template_upgrade

    template_schema = prepare_template_upgrade(
        json.loads(args.template.read_text()),
        json.loads(args.upgrade_plan.read_text()),
        runner.schema,
    )
    (args.work_dir / "template-v19-draft.json").write_text(
        json.dumps(template_schema, ensure_ascii=False, indent=2)
    )
    print(
        json.dumps(
            {
                "analysis_id": ir.analysis_id,
                "evidence_units": len(ir.evidence_units),
                "ontology_classes": len(runner.schema),
                "model_identity": runner.model_identity,
                "input_class": args.input_class,
                "scope": "isolated_engineering_acceptance",
            }
        ),
        flush=True,
    )
    for name, content in (("ir.json", ir.model_dump(mode="json")), ("schema.json", runner.schema)):
        (args.work_dir / name).write_text(json.dumps(content, ensure_ascii=False, indent=2))
    if args.inspect_only:
        return
    checkpoint_file = args.work_dir / "checkpoint.json"
    checkpoint = json.loads(checkpoint_file.read_text()) if checkpoint_file.is_file() else None
    execute = runner.execute_task
    index = 0

    def progress(task, *rest):
        nonlocal index
        index += 1
        print(
            json.dumps(
                {
                    "task": index,
                    "kind": task.task_kind,
                    "predicate": task.predicate_iri,
                    "stage": "started",
                }
            ),
            flush=True,
        )
        started = time.monotonic()
        try:
            values = execute(task, *rest)
        except (ValueError, RuntimeError, TimeoutError) as exc:
            # Avoid logging source text embedded in structured validation errors.
            reason = str(exc)
            if not reason or any(c not in "abcdefghijklmnopqrstuvwxyz_" for c in reason):
                reason = type(exc).__name__
            print(json.dumps({
                "task": index, "stage": "incomplete", "reason": reason,
                "seconds": round(time.monotonic() - started, 2),
            }), flush=True)
            raise
        print(
            json.dumps(
                {
                    "task": index,
                    "candidates": len(values),
                    "seconds": round(time.monotonic() - started, 2),
                }
            ),
            flush=True,
        )
        return values

    runner.execute_task = progress
    started = time.monotonic()
    if args.reuse_run:
        from app.services.extraction.extraction_tasks import ExtractionRun

        previous_result = json.loads((args.work_dir / "result.json").read_text())
        if (
            previous_result["model_revision"] != args.model_revision
            or previous_result["source_analysis"] != ir.analysis_id
        ):
            raise ValueError("saved run does not match this source/model")
        run = ExtractionRun.model_validate_json((args.work_dir / "run.json").read_text())
        if run.input_id != runner.input_id(ir, args.input_class):
            raise ValueError("saved run policy/tokenizer/budget changed; rerun extraction")
        if any(c.ontology_release != runner.ontology_release for c in run.candidates):
            raise ValueError("ontology changed; rerun extraction before reusing candidates")
    else:
        run = runner.run(
            ir,
            effective_class=args.input_class,
            checkpoint=checkpoint,
            retry_failed=args.retry_failed,
            should_pause=lambda: args.pause_after is not None and index >= args.pause_after,
            checkpoint_fn=lambda value: checkpoint_file.write_text(
                json.dumps(value, ensure_ascii=False)
            ),
        )
    (args.work_dir / "run.json").write_text(run.model_dump_json(indent=2))
    summary = {
        "completion": run.completion,
        "counts": dict(Counter(c.kind for c in run.candidates)),
        "validation": dict(Counter(c.validation_status for c in run.candidates)),
        "diagnostics": dict(Counter(run.diagnostics)),
        "seconds": previous_result["seconds"]
        if args.reuse_run
        else round(time.monotonic() - started, 2),
        "reused_run": args.reuse_run,
        "model_revision": args.model_revision,
        "source_analysis": ir.analysis_id,
        "independent_human_gold": False,
        "production_signoff": False,
    }
    summary.update(stage_workflow(args, engine, ir, runner, run, template_schema))
    (args.work_dir / "result.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    if not summary["gold_template_passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
