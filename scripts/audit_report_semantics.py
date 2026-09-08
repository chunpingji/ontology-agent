#!/usr/bin/env python3
"""Audit owned production ASTs, imports, packaged resources and retired entrypoints.

Third-party internals are inventoried separately and are not a zero-regex claim.
Run with backend/.venv/bin/python scripts/audit_report_semantics.py --output FILE.
"""

import argparse
import ast
import hashlib
import importlib.metadata
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def audit():
    findings, dynamic, files = [], [], []
    retired = json.loads(
        (
            ROOT / "specs/020-report-output-semantics/retirement/removed-modules.json"
        ).read_text()
    )
    removed = {row["path"] for row in retired}
    for base in ("backend/app", "cli/cli_anything"):
        for path in sorted((ROOT / base).rglob("*.py")):
            if "tests" in path.parts or "__pycache__" in path.parts:
                continue
            relative = str(path.relative_to(ROOT))
            files.append(
                {
                    "path": relative,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
            tree = ast.parse(path.read_text(), filename=relative)
            for node in ast.walk(tree):
                code = None
                if isinstance(node, ast.Import) and any(
                    a.name.split(".")[0] in {"re", "regex", "sre_parse", "sre_compile"}
                    for a in node.names
                ):
                    code = "REGEX_IMPORT"
                elif isinstance(node, ast.ImportFrom):
                    if (node.module or "").split(".")[0] in {
                        "re",
                        "regex",
                        "sre_parse",
                        "sre_compile",
                    }:
                        code = "REGEX_IMPORT"
                    if node.module and node.module.startswith("app."):
                        target = "backend/" + node.module.replace(".", "/") + ".py"
                        if target in removed:
                            code = "RETIRED_IMPORT"
                elif isinstance(node, ast.Call):
                    name = ast.unparse(node.func)
                    if name in {"eval", "exec", "__import__", "compile"}:
                        code = "DYNAMIC_EXECUTION"
                    if name.endswith(".import_module"):
                        dynamic.append(
                            {
                                "path": relative,
                                "line": node.lineno,
                                "call": ast.unparse(node),
                            }
                        )
                if code:
                    findings.append(
                        {"path": relative, "line": node.lineno, "code": code}
                    )
    for path in removed:
        if (ROOT / path).exists():
            findings.append({"path": path, "code": "RETIRED_MODULE_PACKAGED"})
    result = subprocess.run(
        ["node", str(ROOT / "scripts/audit_report_semantics.mjs")],
        check=True,
        text=True,
        capture_output=True,
    )
    frontend = json.loads(result.stdout)
    findings.extend(frontend["findings"])
    files.extend(frontend["files"])
    from rdflib import Graph
    from app.services.extraction.retired_config import reject_execution_annotations

    ontology = []
    for path in sorted((ROOT / "ontology/slpra").glob("*.ttl")):
        graph = Graph().parse(path, format="turtle")
        try:
            reject_execution_annotations(graph)
        except ValueError as exc:
            findings.append({"path": str(path.relative_to(ROOT)), "code": str(exc)})
        ontology.append(
            {
                "path": str(path.relative_to(ROOT)),
                "triples": len(graph),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    third_party = {}
    for package in (
        "fastapi",
        "pydantic",
        "sqlalchemy",
        "rdflib",
        "python-docx",
        "owlready2",
    ):
        third_party[package] = importlib.metadata.version(package)
    return {
        "status": "pass" if not findings and not dynamic else "needs_review",
        "findings": findings,
        "dynamic_imports": dynamic,
        "owned_source_manifest": files,
        "ontology": ontology,
        "retired_modules": retired,
        "third_party": {
            "versions": third_party,
            "scope": "Internals excluded from owned-code zero-regex gate",
        },
        "limitations": [
            "Database config rows and built deployment image require separate runtime audit",
            "Business dispatch review accompanies this syntax audit; AST absence alone is not semantic proof",
        ],
    }


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(ROOT / "backend"))
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit()
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered)
    print(
        json.dumps(
            {
                "status": result["status"],
                "findings": result["findings"],
                "dynamic_imports": result["dynamic_imports"],
                "owned_files": len(result["owned_source_manifest"]),
            }
        )
    )
    raise SystemExit(0 if result["status"] == "pass" else 1)
