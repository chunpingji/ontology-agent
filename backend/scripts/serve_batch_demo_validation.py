"""Serve a disposable, memory-only CMC demo backend on loopback, without app startup.

For frontend/tests/batch-demo-browser.mjs. Never connects to the configured application DB.
The supplied original must match the packaged demo hash. No models or migrations are started.
"""

import argparse
import hashlib
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--layout-sample", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18769)
    args = parser.parse_args()

    # Fixture pins DATABASE_URL=sqlite:// before importing any app modules.
    import tests.conftest as fixture

    # isort: split

    import uvicorn
    from fastapi import Depends, FastAPI
    from sqlalchemy import func, select
    from sqlalchemy.orm import Session

    from app.api import ast_templates, batch_demo, document_analysis, extraction
    from app.config import settings
    from app.db import Base, get_db
    from app.dependencies import Identity, get_current_user, get_ontology_engine
    from app.models.document_analysis import DocumentAnalysisRun
    from app.models.entity_shadow import EntityShadow
    from app.models.extraction import (
        AnnotationExecution,
        AstTemplate,
        ExtractionJob,
        GeneratedReport,
    )
    from app.services.reporting import batch_demo as service
    from app.services.reporting.batch_demo_layout import REFERENCE_ID
    from app.services.reporting.batch_demo_template import specialize
    from app.services.reporting.report_run_service import schema_hash
    from app.services.reporting.template_v2 import TemplateV2

    graph, template = service.definitions()
    if hashlib.sha256(args.source.read_bytes()).hexdigest() != graph["source_sha256"]:
        parser.error("Source does not match the versioned HRS-5592 demo fixture")
    assert str(fixture.test_engine.url) == "sqlite://"
    with TemporaryDirectory(prefix="cmc-batch-demo-validation-") as directory:
        root = Path(directory)
        service.ARTIFACT_ROOT = root / "reports"
        settings.document_analysis_storage_dir = root / "analysis"
        Base.metadata.create_all(fixture.test_engine)
        with fixture.TestSessionLocal() as db:
            job = ExtractionJob(
                source_type="word", source_filename=graph["source_filename"],
                document_path=str(args.source.resolve()), status="paused", source_config={},
            )
            db.add(job)
            db.flush()
            db.add(EntityShadow(
                iri="urn:demo:browser:hrs5592", module="document",
                class_iri=template["root_class_iri"], label_zh=graph["source_filename"],
                properties_json={"job_id": str(job.id)},
            ))
            schema = TemplateV2(schema_version=2, template_family_id="demo-browser-family",
                                template_revision_id=str(service.TEMPLATE_ID), revision_no=2)
            template_row = AstTemplate(
                id=service.TEMPLATE_ID, name="批记录", version="v2.2", schema_version=2,
                schema_json=schema.model_dump(mode="json"), iri_pattern=template["root_class_iri"],
                status="draft", is_default=False,
            )
            db.add(AstTemplate(
                id=REFERENCE_ID, name="批记录", version="v1", status="draft",
                schema_version=2, schema_json={"sections": []},
                sample_docx_path=str(args.layout_sample.resolve()),
            ))
            db.add(template_row)
            db.commit()
            specialize(db, expected_hash=schema_hash(template_row.schema_json), actor="analyst",
                       document_iri="urn:demo:browser:hrs5592", apply=True)

        def database():
            with fixture.TestSessionLocal() as db:
                yield db

        # This app deliberately has no production lifespan and binds only loopback.
        app = FastAPI()
        app.include_router(batch_demo.router, prefix="/api/reports")
        app.include_router(ast_templates.router, prefix="/api/ast-templates")
        app.include_router(extraction.router, prefix="/api/extraction")
        app.include_router(document_analysis.router, prefix="/api/document-analysis")
        app.dependency_overrides[get_db] = database
        app.dependency_overrides[get_current_user] = lambda: Identity(
            username="analyst", role="senior_analyst",
        )
        app.dependency_overrides[get_ontology_engine] = lambda: fixture.FakeOntologyEngine()

        @app.get("/verification")
        def verification(db: Session = Depends(get_db)):
            return {name: db.scalar(select(func.count()).select_from(model))
                    for name, model in [("reports", GeneratedReport),
                                        ("analyses", DocumentAnalysisRun),
                                        ("annotations", AnnotationExecution)]}

        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
