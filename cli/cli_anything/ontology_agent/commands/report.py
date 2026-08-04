"""``oa report`` — the Report Center "生成风险评估报告" flow and its siblings.

``generate`` is the primary command: it mirrors the page action exactly —
document IRI → resolve extraction job → ``POST /risk-report`` → (sync docx | async
poll+download) → save ``.docx`` locally. ``--job-id`` is an escape hatch for when
you already know the job.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import click

from cli_anything.ontology_agent.commands._ctx import build_client, handle, print_result
from cli_anything.ontology_agent.core import documents as documents_mod
from cli_anything.ontology_agent.core import reports as reports_mod
from cli_anything.ontology_agent.core.errors import NotFoundError, PreconditionError, UsageError
from cli_anything.ontology_agent.core.files import resolve_output_path, write_atomic


def _validate_positive_float(ctx: click.Context, param: click.Parameter, value: float) -> float:
    if not math.isfinite(value) or value <= 0:
        raise click.BadParameter(f"必须是正有限数，实际值：{value}", ctx=ctx, param=param)
    return value


@click.group()
def report() -> None:
    """Risk-assessment reports."""


# --------------------------------------------------------------------------- #
# generate — the primary, document-centric flow
# --------------------------------------------------------------------------- #


@report.command("generate")
@click.argument("doc_iri", required=False)
@click.option("--job-id", "job_id", default=None, help="Generate for an extraction job directly (skips doc resolution)")
@click.option("-o", "--output", default=None, help="Output path (file, or dir to keep the server filename)")
@click.option("--wait/--no-wait", default=True, help="Wait for async generation to finish (default: wait)")
@click.option("--poll-interval", type=float, default=reports_mod.DEFAULT_POLL_INTERVAL, show_default=True,
              callback=_validate_positive_float, help="Seconds between status polls")
@click.option("--wait-timeout", type=float, default=reports_mod.DEFAULT_WAIT_TIMEOUT, show_default=True,
              callback=_validate_positive_float, help="Max seconds to wait before giving up (report keeps generating server-side)")
@click.option("--force", is_flag=True, help="Overwrite the output file if it already exists")
@click.pass_context
def report_generate(
    ctx: click.Context,
    doc_iri: str | None,
    job_id: str | None,
    output: str | None,
    wait: bool,
    poll_interval: float,
    wait_timeout: float,
    force: bool,
) -> None:
    """Generate a risk-assessment report for a document (DOC_IRI) or --job-id.

    Not idempotent: each successful call creates a new report server-side.
    """

    def _run() -> dict[str, Any]:
        if bool(doc_iri) == bool(job_id):
            raise UsageError("请二选一：提供文档 IRI（位置参数）或 --job-id")
        if not wait and output:
            raise UsageError("--no-wait 与 -o/--output 不兼容：报告尚未生成，无法写入文件")

        # Preflight: if an explicit file path was given, check for collision before POST.
        if output:
            preflight = Path(output)
            if preflight.exists() and not preflight.is_dir() and not force:
                raise UsageError(f"文件已存在：{preflight}（加 --force 覆盖）")

        client = build_client(ctx)
        resolved_job = job_id
        if doc_iri:
            # Distinguish "doc not found" (exit 6) from "doc has no job" (exit 7).
            result_page = documents_mod.list_documents(client)
            shadow = next(
                (s for s in (result_page.get("items") or []) if s.get("iri") == doc_iri),
                None,
            )
            if shadow is None:
                raise NotFoundError(f"文档不存在：{doc_iri}")
            props = shadow.get("properties_json") or {}
            for key in documents_mod.DOCUMENT_JOB_KEYS:
                value = props.get(key)
                if isinstance(value, str) and value:
                    resolved_job = value
                    break
            if not resolved_job:
                raise PreconditionError(f"该文档未关联抽取任务：{doc_iri}")

        result = reports_mod.generate_report(
            client,
            resolved_job,
            wait=wait,
            poll_interval=poll_interval,
            wait_timeout=wait_timeout,
        )

        if result.mode == "async-pending":
            return {
                "mode": result.mode,
                "job_id": resolved_job,
                "report_id": result.report_id,
                "status": result.status,
            }

        fallback = f"risk_report_{resolved_job}.docx"
        path = resolve_output_path(output, result.filename, fallback)
        digest = write_atomic(result.content, path, force=force)
        return {
            "mode": result.mode,
            "job_id": resolved_job,
            "report_id": result.report_id,
            "path": str(path),
            "filename": path.name,
            "bytes": len(result.content or b""),
            "sha256": digest,
        }

    print_result(ctx, handle(ctx, _run))


# --------------------------------------------------------------------------- #
# list / status / download / delete / coverage
# --------------------------------------------------------------------------- #


@report.command("list")
@click.option("--job-id", "job_id", default=None, help="One job's reports; omit to fan out across all documents")
@click.pass_context
def report_list(ctx: click.Context, job_id: str | None) -> None:
    """List completed reports for a job, or across every document."""

    def _run() -> list[dict[str, Any]]:
        client = build_client(ctx)
        if job_id:
            return reports_mod.list_reports(client, job_id)

        # Report-Center fan-out: resolve job IDs inline from the single fetched page
        # (no per-doc re-fetch), then list reports per unique job.
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        docs_result = documents_mod.list_documents(client)
        items = docs_result.get("items") or []
        for shadow in items:
            props = shadow.get("properties_json") or {}
            jid: str | None = None
            for key in documents_mod.DOCUMENT_JOB_KEYS:
                value = props.get(key)
                if isinstance(value, str) and value:
                    jid = value
                    break
            if not jid or jid in seen:
                continue
            seen.add(jid)
            try:
                for rep in reports_mod.list_reports(client, jid):
                    rows.append({**rep, "job_id": jid, "document_iri": shadow.get("iri")})
            except NotFoundError:
                continue  # job vanished; skip rather than fail the whole listing
        if len(items) >= 100:
            click.echo(
                "警告：文档列表已截断至 100 条，可能有报告未列出。请用 --job-id 查询特定任务。",
                err=True,
            )
        return rows

    print_result(ctx, handle(ctx, _run))


@report.command("status")
@click.argument("job_id")
@click.argument("report_id")
@click.pass_context
def report_status(ctx: click.Context, job_id: str, report_id: str) -> None:
    """Show one report's generation status (report_status / report_error / …)."""
    data = handle(ctx, reports_mod.get_report_status, build_client(ctx), job_id, report_id)
    print_result(ctx, data)


@report.command("download")
@click.argument("job_id")
@click.argument("report_id", required=False)
@click.option("-o", "--output", default=None, help="Output path (file, or dir to keep the server filename)")
@click.option("--force", is_flag=True, help="Overwrite the output file if it already exists")
@click.pass_context
def report_download(ctx: click.Context, job_id: str, report_id: str | None, output: str | None, force: bool) -> None:
    """Download a report's ``.docx``.

    With REPORT_ID: that exact report (409 if it isn't completed yet). Without:
    the job's latest completed report (404 if the job has none).
    """

    def _run() -> dict[str, Any]:
        client = build_client(ctx)
        if report_id:
            content, filename = reports_mod.download_report(client, job_id, report_id)
            fallback = f"risk_report_{job_id}_{report_id}.docx"
        else:
            content, filename = reports_mod.download_latest(client, job_id)
            fallback = f"risk_report_{job_id}_latest.docx"
        path = resolve_output_path(output, filename, fallback)
        digest = write_atomic(content, path, force=force)
        return {
            "job_id": job_id,
            "report_id": report_id,
            "path": str(path),
            "filename": path.name,
            "bytes": len(content),
            "sha256": digest,
        }

    print_result(ctx, handle(ctx, _run))


@report.command("delete")
@click.argument("job_id")
@click.argument("report_id")
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt")
@click.pass_context
def report_delete(ctx: click.Context, job_id: str, report_id: str, yes: bool) -> None:
    """Soft-delete a report (requires the senior_analyst role)."""
    if not yes:
        click.confirm(f"确认删除报告 {report_id}？", abort=True)
    handle(ctx, reports_mod.delete_report, build_client(ctx), job_id, report_id)
    print_result(ctx, {"deleted": True, "job_id": job_id, "report_id": report_id})


_COVERAGE_SUMMARY_KEYS = (
    "template_id",
    "template_name",
    "template_version",
    "total_slots",
    "filled",
    "inferred",
    "missing_required",
    "blank_optional",
    "manual",
    "dismissed",
)


@report.command("coverage")
@click.argument("job_id")
@click.option("--template-id", "template_id", default=None, help="Override template resolution")
@click.option("--fail-on-missing", is_flag=True, help="Exit non-zero (7) when required slots are unfilled")
@click.pass_context
def report_coverage(ctx: click.Context, job_id: str, template_id: str | None, fail_on_missing: bool) -> None:
    """Show the no-omission coverage manifest for a job's report."""

    def _run() -> dict[str, Any]:
        payload = reports_mod.get_coverage(build_client(ctx), job_id, template_id=template_id)
        missing = int(payload.get("missing_required") or 0)
        if fail_on_missing and missing > 0:
            raise PreconditionError(
                f"覆盖不完整：{missing} 个必填槽位未填充",
                detail={k: payload.get(k) for k in _COVERAGE_SUMMARY_KEYS},
            )
        if ctx.obj.get("json"):
            return payload  # agents get the full manifest (incl. sections)
        summary = {k: payload.get(k) for k in _COVERAGE_SUMMARY_KEYS}
        summary["section_count"] = len(payload.get("sections") or [])
        return summary

    print_result(ctx, handle(ctx, _run))
