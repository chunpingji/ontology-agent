"""Local SHACL execution shared by callers with independent representation profiles."""

from rdflib import RDF, RDFS, SH, Graph

VALIDATION_OPTIONS = {
    "inference": "none",
    "advanced": False,
    "js": False,
    "inplace": False,
    "do_owl_imports": False,
    "meta_shacl": False,
    "abort_on_first": False,
    "allow_infos": False,
    "allow_warnings": False,
}


def report_rows(graph):
    fields = (
        "focusNode",
        "resultPath",
        "value",
        "sourceShape",
        "sourceConstraintComponent",
        "resultSeverity",
    )
    rows = []
    for node in graph.subjects(RDF.type, SH.ValidationResult):
        row = {
            name: str(v) if (v := graph.value(node, SH[name])) is not None else None
            for name in fields
        }
        row["message"] = sorted(str(v) for v in graph.objects(node, SH.resultMessage))
        rows.append(row)
    return sorted(rows, key=lambda row: tuple(str(row[key]) for key in fields))


def focus_nodes(data, shapes):
    actual = set()
    for shape in set(shapes.subjects(SH.targetNode)) | set(shapes.subjects(SH.targetClass)):
        if shapes.value(shape, SH.deactivated):
            continue
        actual.update(
            node
            for node in shapes.objects(shape, SH.targetNode)
            if (node, None, None) in data or (None, None, node) in data
        )
        for target in shapes.objects(shape, SH.targetClass):
            classes, pending = {target}, [target]
            while pending:
                for subclass in data.subjects(RDFS.subClassOf, pending.pop()):
                    if subclass not in classes:
                        classes.add(subclass)
                        pending.append(subclass)
            actual.update(node for cls in classes for node in data.subjects(RDF.type, cls))
    return {str(node) for node in actual}


def run_local_shacl(data_graph, shapes_graph, *, expected_focus_nodes):
    result = {
        "execution_status": "failed",
        "evaluated": False,
        "conforms": None,
        "report": [],
        "report_text": None,
        "issues": [],
        "coverage": {
            "expected_focus_nodes": sorted(set(expected_focus_nodes)),
            "actual_focus_nodes": [],
            "complete": False,
        },
    }
    try:
        if not all(
            isinstance(g, Graph) and g.store.__class__.__name__ == "Memory"
            for g in (data_graph, shapes_graph)
        ):
            raise ValueError("shacl_requires_memory_graphs")
        actual = focus_nodes(data_graph, shapes_graph)
        expected = set(expected_focus_nodes)
        result["coverage"].update(
            actual_focus_nodes=sorted(actual), complete=bool(expected) and actual == expected
        )
        from pyshacl import validate

        conforms, report, report_text = validate(
            data_graph,
            shacl_graph=shapes_graph,
            **VALIDATION_OPTIONS,
        )
        if not isinstance(report, Graph):
            raise RuntimeError(f"SHACL validation failure: {report}")
        result.update(
            execution_status="completed",
            evaluated=True,
            conforms=bool(conforms),
            report=report_rows(report),
            report_text=report_text,
        )
        if not result["coverage"]["complete"]:
            result["issues"] = ["shacl_coverage_incomplete"]
    except Exception as exc:
        result["issues"] = [f"{type(exc).__name__}: {exc}"]
    return result
