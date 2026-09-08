"""One semantic model identity for extraction, template design and replay."""

from copy import deepcopy

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.ontology_meta import OntologyRelease
from app.models.reporting import ContractRevision, OntologySchemaSnapshot
from app.services.extraction.evidence_identity import evidence_hash
from app.services.reporting.template_v2 import ReportingError

MODEL_PREFIX = "urn:ontology:snapshot:"


def capture_schema(db, classes, actor="ontology-runtime"):
    """Content address, no lifecycle decision and no commit of the caller's work."""
    identity = evidence_hash(classes)
    row = db.get(OntologySchemaSnapshot, identity)
    if row is None:
        try:
            with db.begin_nested():
                row = OntologySchemaSnapshot(
                    id=identity, payload=deepcopy(classes), content_hash=identity, actor=actor
                )
                db.add(row)
                db.flush()
        except IntegrityError:
            row = db.get(OntologySchemaSnapshot, identity)
            if row is None:
                raise
    if row.content_hash != identity or evidence_hash(row.payload) != identity:
        raise ReportingError("ONTOLOGY_SNAPSHOT_HASH_MISMATCH")
    return identity


class ModelContext:
    def __init__(self, db, classes=None):
        self.db, self.classes = db, classes

    def current_classes(self):
        if callable(self.classes):
            self.classes = self.classes()
        if self.classes is None:
            from app.dependencies import get_ontology_engine
            from app.services.extraction.extraction_tasks import semantic_schema_from_engine

            self.classes = semantic_schema_from_engine(get_ontology_engine())
        return self.classes

    def resolve(self, ref="auto:ontology"):
        from app.services.reporting.contract_registry import ContractRegistry

        registry = ContractRegistry(self.db)
        if ref not in {"auto:ontology", "unresolved:ontology"}:
            if ref.startswith(MODEL_PREFIX):
                return self.snapshot_contract(ref[len(MODEL_PREFIX) :])
            # Explicit invalid/unpublished references must never silently upgrade.
            contract = registry.load(ref)
            if contract["kind"] != "ontology":
                raise ReportingError("ONTOLOGY_RELEASE_MISMATCH")
            return contract
        classes = self.current_classes()
        identity = capture_schema(self.db, classes)
        for row in self.db.scalars(
            select(ContractRevision)
            .where(ContractRevision.kind == "ontology")
            .order_by(ContractRevision.id)
        ):
            record = registry.load(row.id, published=False)
            if (
                record["status"] == "published"
                and evidence_hash(record["definition"].get("classes")) == identity
            ):
                return record
        return self.snapshot_contract(identity)

    def snapshot_contract(self, identity):
        row = self.db.get(OntologySchemaSnapshot, identity)
        if row is None:
            raise ReportingError("ONTOLOGY_SNAPSHOT_NOT_FOUND", actual=identity)
        if evidence_hash(row.payload) != identity or row.content_hash != identity:
            raise ReportingError("ONTOLOGY_SNAPSHOT_HASH_MISMATCH")
        release = self.db.scalar(
            select(OntologyRelease)
            .where(
                OntologyRelease.semantic_snapshot_ref == identity,
                OntologyRelease.status == "published",
            )
            .order_by(OntologyRelease.published_at.desc())
        )
        definition = {"classes": deepcopy(row.payload)}
        return {
            "contract_id": MODEL_PREFIX + identity,
            "kind": "ontology",
            "family_id": "ontology-model",
            "revision_no": 1,
            "status": "published" if release else "captured",
            "is_disabled": False,
            "definition": definition,
            "definition_hash": evidence_hash(definition),
            "origin": "ontology_model_service",
            "schema_hash": identity,
            "release_id": str(release.id) if release else None,
            "decision_refs": [],
        }

    def classes_at(self, identity):
        row = self.db.get(OntologySchemaSnapshot, identity)
        if row is not None:
            if row.content_hash != identity or evidence_hash(row.payload) != identity:
                raise ReportingError("ONTOLOGY_SNAPSHOT_HASH_MISMATCH")
            return row.payload
        # Compatibility with snapshots registered before model capture existed.
        for row in self.db.scalars(
            select(ContractRevision).where(ContractRevision.kind == "ontology")
        ):
            if evidence_hash(row.payload) != row.content_hash:
                raise ReportingError("CONTRACT_HASH_MISMATCH")
            classes = row.payload.get("classes", {})
            if evidence_hash(classes) == identity:
                return classes
        return None

    def compatibility(self, candidates, template, classes, contracts):
        """Compare only the model dependencies used by this template/rule plan.

        A missing historical schema is unknown, never equal by class IRI alone.
        Labels and unrelated fields are deliberately excluded from comparison.
        """
        from app.services.reporting.template_compiler import Compiler

        current = evidence_hash(classes)
        versions = sorted(
            {c.get("ontology_release") for c in candidates if c.get("ontology_release")}
        )
        results = []

        def dependencies(schema):
            compiler = Compiler(template, schema, contracts)
            used = {}
            original_class = compiler.require_class
            original_properties = compiler.properties

            def require_class(iri, path):
                if iri in schema:
                    used["class:" + iri] = sorted(schema[iri].get("parents", []))
                return original_class(iri, path)

            class TrackedProperties(dict):
                def get(self, key, default=None):
                    result = super().get(key, default)
                    if result is not None:
                        used["property:" + key] = {
                            k: v
                            for k, v in result.items()
                            if k not in {"label", "comment", "description"}
                        }
                    return result

            def properties(iri, kind):
                return TrackedProperties(original_properties(iri, kind))

            compiler.require_class, compiler.properties = require_class, properties
            plan = compiler.compile()
            # Rule paths/parameters and path traversal use iteration instead of get().
            iris = set()

            def references(value):
                if isinstance(value, dict):
                    for key, child in value.items():
                        if (
                            key in {"predicate_iri", "property_iri", "display_property_iri"}
                            and child
                        ):
                            iris.add(child)
                        references(child)
                elif isinstance(value, list):
                    for child in value:
                        references(child)

            references(plan["template"])
            for check in plan["template"]["calculation_checks"]:
                rule = contracts.get(check["contract_ref"], {}).get("definition", {})
                iris.update(rule.get("predicate_path", []))
                references(rule)
            for iri in list(schema):
                for kind in ("properties", "relationships"):
                    for key, prop in original_properties(iri, kind).items():
                        if key in iris:
                            used[f"{iri}:{key}"] = {
                                k: v
                                for k, v in prop.items()
                                if k not in {"label", "comment", "description"}
                            }
            return {
                "used": used,
                "input_types": plan["input_types"],
                "binding_types": plan["binding_types"],
                "errors": [d for d in plan["diagnostics"] if d["severity"] == "error"],
            }

        expected = None
        for version in versions:
            if version == current:
                results.append({"ontology_release": version, "status": "identical"})
                continue
            previous = self.classes_at(version)
            if previous is None:
                results.append(
                    {
                        "ontology_release": version,
                        "status": "unknown",
                        "code": "SOURCE_ONTOLOGY_VERSION_UNKNOWN",
                    }
                )
                continue
            if expected is None:
                expected = dependencies(classes)
            actual = dependencies(previous)
            changed = sorted(
                k
                for k in expected["used"].keys() | actual["used"].keys()
                if expected["used"].get(k) != actual["used"].get(k)
            )
            results.append(
                {
                    "ontology_release": version,
                    "status": "compatible" if actual == expected else "incompatible",
                    "code": None if actual == expected else "SOURCE_ONTOLOGY_INCOMPATIBLE",
                    "affected_fields": changed,
                }
            )
        return results
