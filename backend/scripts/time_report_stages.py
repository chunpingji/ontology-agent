"""One-off per-stage timing harness for report generation (feature 016).

Mirrors ``_build_and_save_report`` (extraction.py) exactly, but wraps every
expensive stage with a wall-clock timer and records each local-LLM call
(labeled by ``schema_name``) so we can see where the minutes go.

Run INSIDE the backend container so it uses the live DB + local LLM:
    docker compose exec -T backend python /app/scripts/time_report_stages.py <job_id>
"""

from __future__ import annotations

import sys
import time
from contextlib import contextmanager

JOB_ID = sys.argv[1] if len(sys.argv) > 1 else "7983a5e2-a09c-4fc3-b6cb-02f6cf98c82d"

# ── timing infrastructure ────────────────────────────────────────────────
_STAGES: list[tuple[str, float]] = []
_LLM_CALLS: list[tuple[str, float]] = []


@contextmanager
def stage(name: str):
    t0 = time.perf_counter()
    print(f"  ▶ {name} …", flush=True)
    try:
        yield
    finally:
        dt = time.perf_counter() - t0
        _STAGES.append((name, dt))
        print(f"  ✔ {name}: {dt:.1f}s", flush=True)


def _wrap_llm():
    """Wrap narrative_generator.chat_with_schema to record per-call timing."""
    from app.services.reporting import narrative_generator as ng

    orig = ng.chat_with_schema

    def timed(*args, **kwargs):
        label = kwargs.get("schema_name", "?")
        t0 = time.perf_counter()
        try:
            return orig(*args, **kwargs)
        finally:
            dt = time.perf_counter() - t0
            _LLM_CALLS.append((label, dt))
            print(f"      · LLM[{label}]: {dt:.1f}s", flush=True)

    ng.chat_with_schema = timed


def _wrap_substage(module, attr: str, label: str):
    """Wrap a module-level callable to append its wall time to _STAGES."""
    orig = getattr(module, attr)

    def timed(*args, **kwargs):
        t0 = time.perf_counter()
        try:
            return orig(*args, **kwargs)
        finally:
            _STAGES.append((f"  ├ {label}", time.perf_counter() - t0))

    setattr(module, attr, timed)


def _instrument_substages():
    """Break generate_with_coverage into its internal stages."""
    from app.services.reporting import risk_report_generator as rrg
    from app.services.extraction import llm_gap_filler as gf
    from app.services.reporting import product_report_edges as pre

    _wrap_substage(pre, "product_report_edges_for_template", "enrich/Gap-B (master data)")
    _wrap_substage(rrg, "edges_to_facts", "edges_to_facts")
    _wrap_substage(rrg, "validate_coverage", "validate_coverage")
    _wrap_substage(gf, "fill_coverage_gaps", "llm_merge/fill_coverage_gaps")


def main() -> None:
    import json
    from pathlib import Path

    from app.db import SessionLocal
    from app.config import settings
    from app.services.reporting.ast_template import resolve_template
    from app.services.reporting.docx_renderer import render_risk_report
    from app.services.reporting.risk_report_generator import RiskReportGenerator

    print(f"=== timing report generation for job {JOB_ID} ===", flush=True)
    print(
        f"flags: narrative={settings.llm_report_narrative_enabled} "
        f"merge={settings.llm_report_merge_values} local_llm={settings.local_llm_enabled} "
        f"model={settings.local_llm_model}",
        flush=True,
    )

    _wrap_llm()
    _instrument_substages()

    cache_path = Path("data/uploads") / f"{JOB_ID}.annotated.json"
    result = json.loads(cache_path.read_text(encoding="utf-8"))
    edges = result.get("relationships", [])
    doc_class_iri = (result.get("doc_class") or {}).get("doc_class_iri", "")
    print(f"edges={len(edges)}  doc_class_iri={doc_class_iri}", flush=True)

    db = SessionLocal()
    wall0 = time.perf_counter()
    try:
        with stage("resolve_template"):
            template, match_source, tpl_id = resolve_template(doc_class_iri, db)
        n_sections = len(template.sections)
        n_prompts = sum(1 for s in template.sections if s.prompt)
        n_semantic = sum(
            1 for s in template.sections for g in s.groups for sl in g.slots
            if getattr(sl.source, "kind", "") == "semantic"
        )
        n_coverage = sum(len(s.coverage) for s in template.sections)
        print(
            f"    template={match_source} id={tpl_id} sections={n_sections} "
            f"prompts={n_prompts} semantic_slots={n_semantic} coverage_bindings={n_coverage}",
            flush=True,
        )

        generator = RiskReportGenerator(db, template=template)

        # Replicate generate_with_coverage body with per-stage timers by
        # temporarily instrumenting the generator's internal calls.
        with stage("generate_with_coverage (full: enrich+facts+rules+coverage+llm_merge+narrative)"):
            report, manifest = generator.generate_with_coverage(
                edges,
                source_filename="",
                dismissed_slot_ids=None,
                document_path=None,
            )

        with stage("render_risk_report (docx)"):
            docx_bytes = render_risk_report(report, manifest, template=template)

        wall = time.perf_counter() - wall0

        # ── report content sanity ────────────────────────────────────────
        summ = manifest.summary() if hasattr(manifest, "summary") else {}
        print("\n=== CONTENT SANITY ===", flush=True)
        print(f"docx_bytes={len(docx_bytes)}", flush=True)
        print(f"coverage summary={summ}", flush=True)
        print(f"team_members={len(report.team_members)}", flush=True)
        print(
            f"equipment_tables={{ {', '.join(f'{k}:{len(v)}' for k,v in report.equipment_tables.items())} }}",
            flush=True,
        )
        print(f"section_narratives={len(report.section_narratives)}", flush=True)
        print(f"semantic_slots={len(report.semantic_slots)}", flush=True)
        for ss in report.semantic_slots:
            txt = (ss.get("text") or "").replace("\n", " ")
            print(f"    slot[{ss.get('slot_id')}] len={len(txt)}  {txt[:80]}", flush=True)
        # assessment-team presence check
        team_hit = any(
            "评估小组" in (ss.get("text") or "") or "评估人" in (ss.get("text") or "")
            for ss in report.semantic_slots
        )
        print(f"assessment-team text present in a semantic slot: {team_hit}", flush=True)

    finally:
        db.close()

    # ── timing summary ───────────────────────────────────────────────────
    print("\n=== STAGE TIMING ===", flush=True)
    for name, dt in _STAGES:
        print(f"  {dt:8.1f}s  {name}", flush=True)
    print("\n=== LLM CALLS ===", flush=True)
    llm_total = 0.0
    for label, dt in _LLM_CALLS:
        llm_total += dt
        print(f"  {dt:8.1f}s  {label}", flush=True)
    print(f"\n  LLM total: {llm_total:.1f}s over {len(_LLM_CALLS)} calls", flush=True)
    print(f"  WALL TOTAL: {wall:.1f}s ({wall/60:.1f} min)", flush=True)


if __name__ == "__main__":
    main()
