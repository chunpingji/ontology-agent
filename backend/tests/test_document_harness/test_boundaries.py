import ast
from pathlib import Path


def test_new_engine_has_no_old_protocol_import_or_evaluation_dependency():
    root = Path(__file__).parents[2] / "app/services/document_harness"
    forbidden = (
        "app.services.extraction.ontology_guided", "app.evaluation",
        "app.services.document_analysis.current_state",
        "app.services.document_analysis.state_artifacts",
        "app.services.document_analysis.execution",
        "app.services.document_analysis.public_projection",
    )
    violations = []
    for file in root.glob("*.py"):
        for node in ast.walk(ast.parse(file.read_text())):
            imports = ([node.module or ""] if isinstance(node, ast.ImportFrom)
                       else [x.name for x in node.names] if isinstance(node, ast.Import) else [])
            violations.extend((file.name, name) for name in imports if name.startswith(forbidden))
    assert violations == []
