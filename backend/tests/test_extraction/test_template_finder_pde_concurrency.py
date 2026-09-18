"""Source-level PDE review CAS across independently owned Finder executions."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.pde_conflict import PdeConflictDecision
from app.schemas.template_finder import FinderPdeDecisionRequest
from app.services.template_finder.pde_review import review
from tests.test_api.test_template_finder import finder_setup  # noqa: F401
from tests.test_api.test_template_finder_pde import PdeOntology, pde_setup, start  # noqa: F401
from tests.test_extraction.test_annotation_execution import db  # noqa: F401


@pytest.mark.parametrize("version", [0, 1])
def test_different_owners_cannot_both_save_the_same_review_version(
    client, db, analyst_headers, pde_setup, version,  # noqa: F811
):
    template, job, _ = pde_setup
    executions = {
        owner: start(client, {**analyst_headers, "X-User": owner}, template, job)[0]
        for owner in ("first-reviewer", "second-reviewer")
    }
    if version:
        db.add(PdeConflictDecision(job_id=job.id, chosen="pending", version=version))
        db.commit()
    barrier = Barrier(2)
    template_id, job_id, bind = template.id, job.id, db.get_bind()

    def decide(owner):
        with Session(bind) as other:
            barrier.wait(timeout=10)
            try:
                review(other, owner, template_id, job_id, executions[owner],
                       body=FinderPdeDecisionRequest(chosen="derived", expected_version=version),
                       engine=PdeOntology())
                return 200
            except HTTPException as exc:
                return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(decide, executions))
    assert sorted(results) == [200, 409]
    db.expire_all()
    rows = list(db.scalars(select(PdeConflictDecision)))
    assert len(rows) == 1
    assert rows[0].version == version + 1
