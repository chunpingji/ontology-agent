from collections import OrderedDict
from copy import deepcopy

from app.services.reporting import report_run_service as module
from tests.test_reporting.test_calculation_pipeline import source_case


def test_compilation_cache_validates_live_versions_and_detaches_callers(db, monkeypatch):
    plan, source = source_case()
    schema, contracts = deepcopy(source["schema"]), deepcopy(source["contracts"])
    calls, original = [], module.compile_template

    def compile_plan(*args):
        calls.append(1)
        return original(*args)

    monkeypatch.setattr(module, "_compiled_plans", OrderedDict())
    monkeypatch.setattr(module, "compile_template", compile_plan)
    monkeypatch.setattr(
        module.ReportRunService, "load_contracts", lambda *args: (schema, contracts)
    )
    service = module.ReportRunService(db)
    first, _, _ = service.compile(plan["template"], "analyst")
    identity = first["compilation_id"]
    first["diagnostics"] = ["caller-local corruption"]
    second, _, _ = service.compile(plan["template"], "analyst")
    assert len(calls) == 1
    assert second["compilation_id"] == identity
    assert second["diagnostics"] != first["diagnostics"]
    next(iter(schema.values()))["label"] = "new ontology release content"
    third, _, _ = service.compile(plan["template"], "analyst")
    assert len(calls) == 2
    assert third["compilation_id"] != identity
    next(iter(contracts.values()))["status"] = "draft"
    service.compile(plan["template"], "analyst")
    assert len(calls) == 3
