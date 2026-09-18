"""Finder claim and fencing across independent connections."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy.orm import Session

from app.models.extraction import AnnotationExecution, AstTemplate
from app.schemas.template_finder import RecognitionEngineUpdate, StartFinderRequest
from app.services.extraction.annotation_execution import now
from app.services.template_finder.configuration import save_configuration
from app.services.template_finder.service import FinderService, private_job_id, worker
from tests.test_api.test_template_finder import FinderOntology, finder_setup  # noqa: F401
from tests.test_extraction.test_annotation_execution import db  # noqa: F401


def test_concurrent_engine_editor_does_not_overwrite_saved_selection(db, finder_setup):  # noqa: F811
    template, *_ = finder_setup
    # Both editors read the file-bound configuration before either saves.
    with Session(db.bind, expire_on_commit=False) as first, Session(db.bind) as second:
        stale = first.get(AstTemplate, template.id)
        fresh = second.get(AstTemplate, template.id)
        save_configuration(second, fresh, RecognitionEngineUpdate(
            recognition_mode="ontology_guided", finder_profile_id=None,
            expected_recognition_mode="finder_legacy",
            expected_finder_profile_id="cmc_baseline_v1",
        ), "analyst")
        with pytest.raises(HTTPException) as failure:
            save_configuration(first, stale, RecognitionEngineUpdate(
                recognition_mode="finder_legacy", finder_profile_id="cmc_baseline_v1",
                expected_recognition_mode="finder_legacy",
                expected_finder_profile_id="cmc_baseline_v1",
            ), "analyst")
        assert failure.value.status_code == 409
        assert first.get(AstTemplate, template.id).recognition_mode == "ontology_guided"


def test_concurrent_replay_starts_only_one_worker(db, finder_setup):  # noqa: F811
    template, job, _, _ = finder_setup
    barrier = Barrier(2)

    def start():
        with Session(db.bind, expire_on_commit=False) as other:
            tasks = BackgroundTasks()
            barrier.wait(timeout=10)
            response = FinderService(other, FinderOntology()).start(
                "analyst", template.id, job.id, StartFinderRequest(request_key="same"), tasks
            )
            return response, tasks

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(start) for _ in range(2)]
        results = [future.result(timeout=20) for future in futures]
    assert len({result[0]["execution_id"] for result in results}) == 1
    assert sum(len(result[1].tasks) for result in results) == 1
    with pytest.raises(HTTPException) as failure:
        FinderService(db, FinderOntology()).start(
            "analyst",
            template.id,
            job.id,
            StartFinderRequest(
                request_key="other", expected_execution_id=results[0][0]["execution_id"]
            ),
            BackgroundTasks(),
        )
    assert failure.value.status_code == 409


def test_expired_worker_cannot_publish_over_new_generation(db, finder_setup):  # noqa: F811
    template, job, _, _ = finder_setup
    service = FinderService(db, FinderOntology())
    first_tasks, second_tasks = BackgroundTasks(), BackgroundTasks()
    first = service.start(
        "analyst", template.id, job.id, StartFinderRequest(request_key="one"), first_tasks
    )
    private = private_job_id("analyst", template.id, job.id)
    head = db.get(AnnotationExecution, private, populate_existing=True)
    head.lease_expires_at = now() - timedelta(seconds=1)
    db.commit()
    assert service.status("analyst", template.id, job.id)["status"] == "interrupted"
    second = service.start(
        "analyst",
        template.id,
        job.id,
        StartFinderRequest(request_key="two", expected_execution_id=first["execution_id"]),
        second_tasks,
    )
    worker(*first_tasks.tasks[0].args)
    assert service.status("analyst", template.id, job.id)["status"] == "queued"
    worker(*second_tasks.tasks[0].args)
    result = service.status("analyst", template.id, job.id)
    assert result["status"] == "completed"
    assert result["execution_id"] == second["execution_id"]
    worker(*first_tasks.tasks[0].args)
    assert service.status("analyst", template.id, job.id) == result
