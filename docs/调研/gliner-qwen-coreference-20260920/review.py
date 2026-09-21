"""Offline artifact checks and aggregation; never calls either model."""
import argparse
import json
from pathlib import Path

from probe import CASES, digest, validate_output, write


parser = argparse.ArgumentParser()
parser.add_argument("--input", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
root = args.input
bundle = {"cases": CASES, "arms": {}, "review": {}}

for arm in ("gliner1", "gliner25", "qwen-ready", "diagnostic"):
    directory = root / arm
    bundle["arms"][arm] = {
        str(path.relative_to(directory)): json.loads(path.read_text())
        for path in sorted(directory.rglob("*.json"))
    }

schema = bundle["arms"]["qwen-ready"]["schema.json"]
qwen_rows = []
for case in CASES:
    directory = root / "qwen-ready" / case["id"]
    response = json.loads((directory / "http-01-response.json").read_text())
    request = json.loads((directory / "http-01-request.json").read_text())
    assert request["messages"][1]["content"] == json.dumps({
        "text": case["text"],
        "entity_types": bundle["arms"]["qwen-ready"]["protocol.json"]["entity_types"],
        "relation_definitions": bundle["arms"]["qwen-ready"]["protocol.json"]["relations"],
        "product_attribute_fields": bundle["arms"]["qwen-ready"]["protocol.json"]["record_fields"],
    }, ensure_ascii=False)
    assert response["choices"][0]["finish_reason"] == "stop"
    parsed = json.loads(response["choices"][0]["message"]["content"])
    validate_output(parsed, schema)
    checks = []
    for collection, fields in {
        "entities": ["text"],
        "coreferences": ["mention", "antecedent", "evidence"],
        "relations": ["head", "tail", "evidence"],
        "attributes": ["owner", "value", "evidence"],
        "comparisons": ["subject", "target", "evidence"],
    }.items():
        for index, item in enumerate(parsed[collection]):
            for field in fields:
                value = item[field]
                checks.append({"path": f"{collection}/{index}/{field}", "text": value,
                               "literal_source_match": bool(value) and value in case["text"]})
    qwen_rows.append({"case_id": case["id"], "model_response_status": "completed",
                      "schema_validated_offline": True, "result": parsed,
                      "source_checks": checks,
                      "usage": response["usage"],
                      "original_runner_status": json.loads((directory / "result.json").read_text())})

span_checks = []
for arm in ("gliner1", "gliner25", "diagnostic"):
    for filename, value in bundle["arms"][arm].items():
        if isinstance(value, dict) and "span_checks" in value:
            span_checks.extend({"arm": arm, "file": filename, **check}
                               for check in value["span_checks"])

bundle["review"] = {
    "kind": "assistant_case_review_not_a_benchmark_or_expert_gold",
    "qwen": qwen_rows,
    "gliner_span_checks": {"total": len(span_checks),
                           "failed": [check for check in span_checks if not check["valid"]]},
    "qwen_source_check_failures": [
        {"case_id": row["case_id"], **check}
        for row in qwen_rows for check in row["source_checks"]
        if not check["literal_source_match"]
    ],
    "qwen_requests": sum(len(row["original_runner_status"]["calls"]) for row in qwen_rows),
    "qwen_usage": {key: sum(row["usage"][key] for row in qwen_rows)
                   for key in ("prompt_tokens", "completion_tokens", "total_tokens")},
    "review_script_sha256": digest(__file__),
    "runner_issue": "Initial jsonschema import failed before any HTTP request. The subsequent "
                    "3 successful HTTP calls were marked failed by a reused validator that "
                    "required maxItems absent from this schema. Saved responses were validated "
                    "offline without changing outputs or calling Qwen again.",
}
write(args.output, bundle)
print(json.dumps({key: value for key, value in bundle["review"].items() if key != "qwen"},
                 ensure_ascii=False, indent=2))
