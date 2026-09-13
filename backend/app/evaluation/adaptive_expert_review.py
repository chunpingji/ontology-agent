"""Offline human review packages; expert references never enter recognition inputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.evaluation.ontology_guided_scorer import OntologyGuidedReference
from app.services.extraction.document_ir import DocumentIR
from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.ontology_guided.contracts import OntologySnapshot
from app.services.extraction.ontology_guided.records import RecordIndex

EXPOSED_DOCUMENTS = {
    "1156ca7b694c5af10afa393a5df4158ad4f9528901eab560ae2cf96e55b6d44b",
    "2c1174bf616f30dd28c655fef4261c167762de807c8ad79e148fb8ef18e16436",
}


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def prepare_package(ir, ontology, *, root_class_iri, exposure, ranking=None, graph=None):
    if root_class_iri not in ontology.classes:
        raise ValueError("root is outside frozen ontology")
    if exposure not in {"development", "holdout"}:
        raise ValueError("sample exposure must be declared")
    if exposure == "holdout" and ir.document_hash in EXPOSED_DOCUMENTS:
        raise ValueError("HRS-1597 and HRS-5592 are development-exposed samples")
    index = RecordIndex(ir)
    records = [
        {
            "record_id": record.record_id,
            "section_node_id": record.section_node_id,
            "source": [
                {"anchor": ir.anchor(unit.evidence_id).model_dump(mode="json"), "text": unit.text}
                for unit in record.source_units
            ],
            "context": index.source_text(record.record_id, include_context=True),
        }
        for record in index.records
    ]
    manifest = {
        "schema_version": "adaptive-expert-package-v1",
        "document_hash": ir.document_hash,
        "ontology_hash": ontology.ontology_hash,
        "root_class_iri": root_class_iri,
        "exposure": exposure,
        "ir_hash": evidence_hash(ir),
        "ontology_snapshot_hash": evidence_hash(ontology),
        "records_hash": evidence_hash(records),
        "ranking_hash": evidence_hash(ranking),
        "graph_hash": evidence_hash(graph),
        "reference_is_recognition_input": False,
    }
    manifest["package_hash"] = evidence_hash(manifest)
    reference = OntologyGuidedReference(
        reference_id="expert-" + manifest["package_hash"][:16],
        document_hash=ir.document_hash,
        ontology_hash=ontology.ontology_hash,
        root_class_iri=root_class_iri,
        scope_mode="document_graph",
    ).model_dump(mode="json")
    review = {
        "schema_version": "adaptive-expert-review-v1",
        "package_hash": manifest["package_hash"],
        "status": "draft",
        "reviewer": "",
        "reviewed_at": "",
        "annotation_version": "",
        "whole_document_surveyed": False,
        "checks": {
            key: False
            for key in (
                "subject_ownership",
                "relation_direction",
                "entity_level",
                "negation_conditions",
                "missing_facts",
                "complete_paths",
            )
        },
        "records": [
            {"record_id": item["record_id"], "status": "unreviewed", "notes": ""}
            for item in records
        ],
        "pruning_findings": [],
    }
    return {"manifest": manifest, "records": records, "reference": reference, "review": review}


def validate_review(package, *, ir, ontology, reference, review, ranking=None, graph=None):
    """Validate completeness and provenance, not the truth of a human judgment."""
    manifest = package["manifest"]
    expected = prepare_package(
        ir,
        ontology,
        root_class_iri=manifest["root_class_iri"],
        exposure=manifest["exposure"],
        ranking=ranking,
        graph=graph,
    )
    if manifest != expected["manifest"] or package["records"] != expected["records"]:
        raise ValueError("review package source or artifact hashes changed")
    if review.get("package_hash") != manifest["package_hash"]:
        raise ValueError("review belongs to another package")
    if (
        review.get("status") != "approved"
        or not review.get("reviewer", "").strip()
        or not review.get("reviewed_at")
        or not review.get("annotation_version")
    ):
        raise ValueError("expert approval requires reviewer, time and annotation version")
    if review.get("whole_document_surveyed") is not True or review.get("checks") != dict.fromkeys(
        expected["review"]["checks"], True
    ):
        raise ValueError("output-only review cannot establish missing-fact recall")
    records = review.get("records", [])
    if (
        len(records) != len(expected["records"])
        or {r["record_id"] for r in records} != {r["record_id"] for r in expected["records"]}
        or any(r.get("status") != "reviewed" for r in records)
    ):
        raise ValueError("all original records must be surveyed, including pruned and unattempted")
    reference = OntologyGuidedReference.model_validate(reference)
    if (
        not reference.annotation_complete
        or reference.expert_review.status != "approved"
        or reference.expert_review.reference_hash != manifest["package_hash"]
        or reference.document_hash != ir.document_hash
        or reference.ontology_hash != ontology.ontology_hash
        or reference.root_class_iri != manifest["root_class_iri"]
        or reference.scope_mode != "document_graph"
    ):
        raise ValueError("reference scope, approval or completeness mismatch")
    for assertion in [*reference.relationships, *reference.properties]:
        if assertion.expectation != "undetermined" and not assertion.allowed_evidence_sets:
            raise ValueError("expert assertions require replayable source evidence")
        for alternative in assertion.allowed_evidence_sets:
            if not alternative:
                raise ValueError("empty proof alternative")
            for span in alternative:
                unit = ir.unit(span.evidence_id)
                if span.end > len(unit.text):
                    raise ValueError("expert citation outside original text")
    return {
        "schema_version": "adaptive-expert-review-validation-v1",
        "package_hash": manifest["package_hash"],
        "expert_review_hash": evidence_hash(review),
        "reference_hash": evidence_hash(reference),
        "exposure": manifest["exposure"],
        "expert_reference_ready": True,
        "independent_sample": manifest["exposure"] == "holdout",
        "formal_quality_gate": "pending_model_scoring_and_three_new_runs",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--ir", required=True)
    prepare.add_argument("--ontology", required=True)
    prepare.add_argument("--root-class-iri", required=True)
    prepare.add_argument("--exposure", required=True, choices=["development", "holdout"])
    prepare.add_argument("--ranking")
    prepare.add_argument("--graph")
    prepare.add_argument("--output", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--package", required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        ir = DocumentIR.model_validate(read(args.ir))
        ontology = OntologySnapshot.model_validate(read(args.ontology))
        ranking, graph = (
            read(args.ranking) if args.ranking else None,
            (read(args.graph) if args.graph else None),
        )
        # Benchmark result files can also contain a large execution history.
        # Keep the public graph as the human review surface; ranking is an
        # explicitly supplied, separately hashed input.
        if isinstance(graph, dict) and "graph" in graph and "ranking_state" in graph:
            graph = graph["graph"]
        package = prepare_package(
            ir,
            ontology,
            root_class_iri=args.root_class_iri,
            exposure=args.exposure,
            ranking=ranking,
            graph=graph,
        )
        output = Path(args.output)
        output.mkdir(parents=True, exist_ok=False, mode=0o700)
        for name, data in {
            **package,
            "ir": ir.model_dump(mode="json"),
            "ontology": ontology.model_dump(mode="json"),
            "ranking": ranking,
            "graph": graph,
        }.items():
            write(output / f"{name}.json", data)
        (output / "审核说明.md").write_text(
            "先逐条阅读 records.json 与原件，再审核 graph.json、ranking.json 中输出及剪枝。\n"
            "在 reference.json 补齐关系、属性、主体、引用、否定条件和完整路径；遗漏也要标注。\n"
            "每条原文审阅后在 review.json 标记 reviewed，补审阅人与版本、时间及六项核查。\n"
            "专家批准时填写 reference.expert_review，reference_hash 使用 manifest.package_hash。\n"
            "工程校验只证明格式与范围完整；事实正确性由专家裁决，质量结论另由独立评分给出。\n",
            encoding="utf-8",
        )
        print(json.dumps({"package": str(output), "status": "pending_expert_review"}))
    else:
        directory = Path(args.package)
        values = {
            name: read(directory / f"{name}.json")
            for name in (
                "manifest",
                "records",
                "ir",
                "ontology",
                "reference",
                "review",
                "ranking",
                "graph",
            )
        }
        result = validate_review(
            values,
            ir=DocumentIR.model_validate(values["ir"]),
            ontology=OntologySnapshot.model_validate(values["ontology"]),
            reference=values["reference"],
            review=values["review"],
            ranking=values["ranking"],
            graph=values["graph"],
        )
        write(directory / "validation.json", result)
        print(json.dumps(result))


if __name__ == "__main__":
    main()
