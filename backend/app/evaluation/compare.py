"""Read-only comparison of completed experiment artifacts, with explicit limits.

Example: python -m app.evaluation.compare --prepared PREPARED --runs RUN_A RUN_B
--output NEW_REPORT_DIRECTORY [--reference FROZEN_REFERENCE_JSON]

The command never invokes a model or changes a run/checkpoint. It independently
replays saved scoring against the supplied reference before reporting metrics.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from app.evaluation.graph_metrics import evaluate_graph

IDENTITIES = ("document_hash", "runtime_hash", "ontology_hash")
KINDS = ("entity", "property", "relationship")


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _number(value):
    return f"{value:.2f}" if isinstance(value, (int, float)) else "—"


def _rate(value):
    return f"{value:.1%}" if isinstance(value, (int, float)) else "—"


def _counts(candidates):
    counts = Counter(candidate["kind"] for candidate in candidates)
    return {kind: counts[kind] for kind in KINDS}


def _primary(metrics, key):
    if not metrics.get("semantic_metrics_available"):
        return None
    primary = metrics.get(key, {}).get("extracted_only")
    if primary is None:
        raise ValueError("metrics lack extracted_only: re-score without supplied-root inflation")
    return {
        kind: {
            name: value
            for name, value in primary[kind].items()
            if name
            in {
                "tp",
                "fp",
                "fn",
                "precision",
                "recall",
                "f1",
                "predictions",
                "reference_count",
                "unscored_count",
                "scored_prediction_fraction",
                "negative_cases",
            }
        }
        for kind in (*KINDS, "micro")
    }


def compare_runs(prepared, run_directories, *, reference_path=None):
    """Validate one frozen experiment and return comparisons, never update inputs."""
    prepared = Path(prepared).resolve()
    manifest = _read(prepared / "manifest.json")
    ir_path = prepared / "ir.json"
    if manifest.get("ir_hash") and _digest(ir_path) != manifest["ir_hash"]:
        raise ValueError("prepared IR hash differs from manifest")
    ir = _read(ir_path)
    if ir.get("document_hash") != manifest["document_hash"]:
        raise ValueError("prepared IR belongs to a different document")
    if reference_path is None:
        default = (
            prepared
            / "runtime"
            / "app"
            / "evaluation"
            / "fixtures"
            / "cmc_upload_23c872fb_reference.json"
        )
        reference_path = default if default.is_file() else None
    reference = _read(reference_path) if reference_path else None
    paths = [Path(path).resolve() for path in run_directories]
    if len(paths) < 2 or len(set(paths)) != len(paths):
        raise ValueError("comparison requires at least two distinct run directories")

    rows, common_budget, common_metric_version = [], None, None
    common_limits = _read(paths[0] / "result.json").get("execution_limits")
    for path in paths:
        result = _read(path / "result.json")
        run = _read(path / "run.json")
        metrics = _read(path / "metrics.json")
        checkpoint_path = path / "checkpoint.json"
        checkpoint = _read(checkpoint_path) if checkpoint_path.is_file() else {}
        for key in IDENTITIES:
            if result.get(key) != manifest.get(key) or not result.get(key):
                raise ValueError(f"not comparable: {path.name} has different {key}")
        if result.get("model_settings") != manifest.get("settings"):
            raise ValueError(f"not comparable: {path.name} has different model settings")
        if result.get("execution_limits") != common_limits:
            raise ValueError(f"not comparable: {path.name} has different execution limits")
        if not result.get("budget"):
            raise ValueError(f"not comparable: {path.name} has no recorded budget")
        if common_budget is None:
            common_budget = result["budget"]
        elif result["budget"] != common_budget:
            raise ValueError(f"not comparable: {path.name} has different task/model budget")
        if common_metric_version is None:
            common_metric_version = metrics.get("metric_version")
        elif metrics.get("metric_version") != common_metric_version:
            raise ValueError("not comparable: scoring versions differ")
        if run.get("completion") != result.get("completion"):
            raise ValueError(f"stale result: completion differs in {path.name}")
        replayed = evaluate_graph(run, reference, ir=ir)
        if replayed != metrics:
            raise ValueError(
                f"saved metrics differ from supplied reference/run: {path.name}; re-score"
            )
        candidates = run.get("candidates", [])
        validated = [c for c in candidates if c.get("validation_status") == "passed"]
        for key, values in (("candidate_counts", candidates), ("validated_counts", validated)):
            if {kind: result.get(key, {}).get(kind, 0) for kind in KINDS} != _counts(values):
                raise ValueError(f"stale result: {key} differs in {path.name}")
        attempts = checkpoint.get("attempt_count")
        if attempts is not None and (type(attempts) is not int or attempts < 0):
            raise ValueError(f"invalid checkpoint attempt_count in {path.name}")
        elapsed_total = result["elapsed_seconds_total"]
        elapsed_segment = result["elapsed_seconds_this_segment"]
        resumed = elapsed_total > elapsed_segment + 0.000001
        metadata_roots = [c for c in candidates if c.get("identity", {}).get("document_root")]
        first = result.get("first_seconds_this_segment", {})
        rows.append(
            {
                "run_directory": str(path),
                "run_name": path.name,
                "mode": result["mode"],
                "completion": result["completion"],
                "execution_limits": result.get("execution_limits"),
                "task_event_count": result.get("task_count"),
                "attempted_tasks_total": attempts,
                "model_calls_this_segment": result.get("model_calls_this_segment"),
                "model_calls_total": checkpoint.get("model_calls"),
                "elapsed_seconds_this_segment": elapsed_segment,
                "elapsed_seconds_total": elapsed_total,
                "summary_generation_seconds_separate": result.get(
                    "summary_generation_seconds_separate", 0
                ),
                "planner_seconds_this_segment": result.get("planner_seconds_this_segment"),
                "cold_metadata_accounted_seconds": result.get("cold_metadata_accounted_seconds"),
                "summary_cache": result.get("summary_cache"),
                "resumed": resumed,
                "first_validated_result_this_segment_seconds": first.get("validated_result"),
                "first_validated_relationship_this_segment_seconds": first.get(
                    "validated_relationship"
                ),
                "first_reference_correct_relationship_seconds": None,
                "first_reference_correct_relationship_reason": (
                    "Saved first-result timestamps attest production verification only; "
                    "no chronological reference-scored publication history was recorded."
                ),
                "raw_candidate_counts": _counts(candidates),
                "validated_candidate_counts": _counts(validated),
                "supplied_metadata_root_count": len(metadata_roots),
                "raw_extracted_candidate_counts": _counts(
                    [c for c in candidates if c not in metadata_roots]
                ),
                "validated_extracted_candidate_counts": _counts(
                    [c for c in validated if c not in metadata_roots]
                ),
                "raw_extracted_only_metrics": _primary(metrics, "raw"),
                "validated_extracted_only_metrics": _primary(metrics, "validated"),
                "scope_count": metrics.get("scope_count", 0),
                "provenance_replay": metrics.get("provenance_replay"),
                "variant_statistics": result.get("variant_statistics", {}),
                "diagnostics": result.get("diagnostics", []),
                "slots_before": result.get("slots_before"),
                "slots_after": result.get("slots_after"),
                "input_hashes": {
                    name: _digest(path / name)
                    for name in ("result.json", "run.json", "metrics.json")
                },
            }
        )
    baselines = [row for row in rows if row["mode"] == "baseline"]
    baseline = baselines[0] if baselines else None
    comparisons = []
    for row in rows:
        if baseline is None or row is baseline:
            continue
        both_complete = baseline["completion"] == row["completion"] == "complete"
        same_attempts = (
            baseline["attempted_tasks_total"] is not None
            and baseline["attempted_tasks_total"] > 0
            and baseline["attempted_tasks_total"] == row["attempted_tasks_total"]
        )
        ratio = (
            baseline["elapsed_seconds_total"] / row["elapsed_seconds_total"]
            if row["elapsed_seconds_total"] > 0
            else None
        )
        comparisons.append(
            {
                "baseline_run": baseline["run_name"],
                "variant_run": row["run_name"],
                "ratio_direction": "baseline_elapsed_total / variant_elapsed_total",
                "same_attempted_task_budget": same_attempts,
                "same_task_budget_observed_elapsed_ratio": ratio if same_attempts else None,
                "complete_run_observed_elapsed_ratio": ratio if both_complete else None,
                "complete_run_ratio_withheld_reason": None
                if both_complete
                else "one_or_both_runs_incomplete",
                "equal_quality_speedup_established": False,
            }
        )
    return {
        "comparison_version": "cmc-readonly-comparison-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "prepared_directory": str(prepared),
        "manifest_sha256": _digest(prepared / "manifest.json"),
        "identities": {key: manifest[key] for key in IDENTITIES},
        "model_settings": manifest["settings"],
        "common_budget": common_budget,
        "input_comparability_verified": True,
        "annotation_level": reference.get("annotation_level") if reference else "none",
        "reference_path": str(Path(reference_path).resolve()) if reference_path else None,
        "reference_sha256": _digest(reference_path) if reference_path else None,
        "metrics_recomputed_and_verified": True,
        "rows": rows,
        "baseline_comparisons": comparisons,
        "limitations": [
            "Each row is one observed run, not a statistical confidence estimate; "
            "no aggregate causal speedup claim.",
            "Shared model service load and prompt/weight caches were not controlled or reset.",
            "Identical attempted-task counts do not imply identical source coverage, "
            "model calls, or semantic quality.",
            "Incomplete runs support bounded-progress comparisons only, "
            "never full-document speedup.",
            "Cold-metadata accounted time adds a separately measured summary cost; "
            "it is not an observed cold end-to-end run.",
            "First validated relationship timing is not independent-reference correctness timing; "
            "resume times are segment-local.",
            "Raw/validated candidate counts are output volume; accuracy uses independently scored "
            "extracted_only metrics.",
            "Assistant silver measures agreement within explicitly annotated scopes, "
            "not human-verified full-document accuracy.",
        ],
    }


def render_markdown(report):
    lines = [
        "# CMCReport 图谱识别对照实测",
        "",
        "所有组已核对相同文档、代码、本体、模型配置及任务预算。",
        "参考类型：`"
        + report["annotation_level"]
        + "`。下列数值是各次运行观测，不构成等质量提速结论。",
        "",
        "## 执行量与时间",
        "",
        "| 运行/模式 | 完成状态 | 累计尝试任务 | 调用：本段/累计 | 时间：本段/累计 秒 "
        "| 摘要 秒 | 冷摘要计入 秒 | 首条已验证关系：本段 秒 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report["rows"]:
        lines.append(
            f"| {row['run_name']} / {row['mode']} | {row['completion']} "
            f"| {row['attempted_tasks_total']} "
            f"| {row['model_calls_this_segment']} / {row['model_calls_total']} "
            f"| {_number(row['elapsed_seconds_this_segment'])} "
            f"/ {_number(row['elapsed_seconds_total'])} "
            f"| {_number(row['summary_generation_seconds_separate'])} "
            f"| {_number(row['cold_metadata_accounted_seconds'])} "
            f"| {_number(row['first_validated_relationship_this_segment_seconds'])} |"
        )
    lines += [
        "",
        "首条通过独立参考验证的关系时间未记录；不能由生产验证时间推断。",
        "",
        "## 输出数量与独立评分",
        "",
        "E/P/R 分别为实体、属性、关系；数量排除外部提供的文档根实体。"
        "准确性采用 extracted_only 微平均，范围外未评分项另列。",
        "",
        "| 运行 | 原始 E/P/R | 已验证 E/P/R | 原始 P/R/F1 | 已验证 P/R/F1 "
        "| 未评分：原始/已验证 | 标注范围数 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report["rows"]:
        raw = row["raw_extracted_only_metrics"]
        validated = row["validated_extracted_only_metrics"]
        raw_micro, valid_micro = (raw or {}).get("micro", {}), (validated or {}).get("micro", {})
        lines.append(
            f"| {row['run_name']} "
            f"| {' / '.join(str(row['raw_extracted_candidate_counts'][k]) for k in KINDS)} "
            f"| {' / '.join(str(row['validated_extracted_candidate_counts'][k]) for k in KINDS)} "
            f"| {' / '.join(_rate(raw_micro.get(k)) for k in ('precision', 'recall', 'f1'))} "
            f"| {' / '.join(_rate(valid_micro.get(k)) for k in ('precision', 'recall', 'f1'))} "
            f"| {raw_micro.get('unscored_count', '—')} / {valid_micro.get('unscored_count', '—')} "
            f"| {row['scope_count']} |"
        )
    lines += [
        "",
        "## 可报告的时间比值",
        "",
        "比值方向为基线累计时间 / 方案累计时间。"
        "相同任务数仍可能处理不同原文并产生不同调用数，不能直接解释为等质量收益。",
        "",
    ]
    for item in report["baseline_comparisons"]:
        lines.append(
            f"- `{item['variant_run']}` 对 `{item['baseline_run']}`：同尝试任务数时间比 "
            f"{_number(item['same_task_budget_observed_elapsed_ratio'])}；完整运行时间比 "
            f"{_number(item['complete_run_observed_elapsed_ratio'])}。"
        )
    lines += [
        "",
        "未完成组不提供完整运行时间比。冷摘要计入值为分别测量后相加的成本核算，并非一次冷启动实测。",
        "",
        "单次运行受共享模型负载与缓存影响；银标只覆盖声明范围，尚未经过领域专家复核。",
        "恢复运行的首结果时间属于当前片段，可能对应已有候选的重新发布。"
        "逐类型指标、诊断及输入哈希见 `comparison.json`。",
        "",
    ]
    return "\n".join(lines)


def write_report(report, output):
    """Create a new report directory; never overwrite a run or previous report."""
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / "comparison.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "comparison.md").write_text(render_markdown(report), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", required=True)
    parser.add_argument("--runs", nargs="+", required=True)
    parser.add_argument("--reference")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = compare_runs(args.prepared, args.runs, reference_path=args.reference)
    write_report(report, args.output)
    print(json.dumps({"output": str(Path(args.output).resolve()), "runs": len(report["rows"])}))


if __name__ == "__main__":
    main()
