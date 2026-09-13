"""Freeze configured calibration in a new run; never reload it during resume."""

from pathlib import Path

from app.services.extraction.ontology_guided.adaptive_retrieval import (
    AdaptivePolicy,
    CalibrationProfile,
)


def configured_adaptive_policy(settings):
    mode = settings.document_analysis_adaptive_retrieval_mode
    if mode == "disabled":
        return None
    if not settings.document_analysis_evidence_repair_enabled:
        raise ValueError("adaptive retrieval requires evidence repair")
    profile = None
    if mode in {"trial", "enforce"}:
        path = settings.document_analysis_adaptive_calibration_path
        if not path:
            raise ValueError(
                "adaptive enforcement requires an expert-reviewed calibration file"
                if mode == "enforce" else "trial pruning requires a development threshold file"
            )
        profile = CalibrationProfile.model_validate_json(Path(path).read_text(encoding="utf-8"))
    return AdaptivePolicy(mode=mode, calibration=profile)
