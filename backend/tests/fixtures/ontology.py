"""T001 — shared ontology test double for feature 014.

A lightweight, deterministic stand-in for ``OntologyEngine`` that answers only
the *read* surface the dynamic-mapping feature consults: hierarchy
(``get_subclasses`` / ``get_class_detail().parent_iris``), property domains
(``get_data_properties_by_domain`` / ``get_object_properties_by_domain``),
existing individuals (``get_individuals``) and class-level external alignments
(``get_class_alignments``). It mirrors the real engine's method signatures and
returns the real ``ClassInfo`` / ``IndividualInfo`` dataclasses, so production
code cannot tell it apart — but it needs no TTL, no BFO, no World.

The seeded test T-Box (managed namespace ``…/slpra/drug/``):

    DrugProduct                         批准文号/风险等级/药品名称 (data) · manufacturedBy → Manufacturer
    └── BiologicalDrugProduct           (subclass — inherits nothing by domain; see note)
    Manufacturer

Note: ``get_data_properties_by_domain`` is *domain-literal* (matches the real
engine, which checks ``cls in prop.domain`` and does NOT walk ancestors) — a
data property declared on ``DrugProduct`` is NOT returned for ``Manufacturer``.
This is exactly what US3 domain-gating (FR-013) relies on.
"""

from __future__ import annotations

from app.services.ontology_engine import ClassInfo, IndividualInfo

SLPRA = "https://ontology.pharma-gmp.cn/slpra/"
DRUG_NS = SLPRA + "drug/"

# --- classes ---------------------------------------------------------------
DRUG_PRODUCT = DRUG_NS + "DrugProduct"
BIOLOGIC = DRUG_NS + "BiologicalDrugProduct"  # subclass of DrugProduct
MANUFACTURER = DRUG_NS + "Manufacturer"

# --- data properties (domain: DrugProduct) ---------------------------------
APPROVAL_NUMBER = DRUG_NS + "approvalNumber"
RISK_LEVEL = DRUG_NS + "riskLevel"
DRUG_NAME = DRUG_NS + "drugName"

# --- data properties (domain: Manufacturer) --------------------------------
MFR_NAME = DRUG_NS + "manufacturerName"

# --- object properties (domain: DrugProduct → Manufacturer) ----------------
MANUFACTURED_BY = DRUG_NS + "manufacturedBy"

_LABELS = {
    DRUG_PRODUCT: "药品",
    BIOLOGIC: "生物制品",
    MANUFACTURER: "生产企业",
}


def _local(iri: str) -> str:
    return iri.rsplit("/", 1)[-1].rsplit("#", 1)[-1]


class FakeFeatureEngine:
    """Duck-typed ``OntologyEngine`` covering the 014 read surface.

    Tests may seed extra individuals (``add_individual``) and class-level
    external alignments (``set_alignments``) before running alignment / fact
    building. Any other engine method resolves to a no-op via ``__getattr__``
    (matching the tolerant fake in ``tests/conftest.py``).
    """

    is_loaded = True

    def __init__(self) -> None:
        # class IRI -> direct parent IRIs
        self._parents: dict[str, list[str]] = {
            DRUG_PRODUCT: [],
            BIOLOGIC: [DRUG_PRODUCT],
            MANUFACTURER: [],
        }
        # domain class IRI -> [{iri, name, label}]
        self._data_props: dict[str, list[dict]] = {
            DRUG_PRODUCT: [
                {"iri": APPROVAL_NUMBER, "name": "approvalNumber", "label": "批准文号"},
                {"iri": RISK_LEVEL, "name": "riskLevel", "label": "风险等级"},
                {"iri": DRUG_NAME, "name": "drugName", "label": "药品名称"},
            ],
            MANUFACTURER: [
                {"iri": MFR_NAME, "name": "manufacturerName", "label": "企业名称"},
            ],
        }
        # domain class IRI -> [{iri, name, label, range}]
        self._obj_props: dict[str, list[dict]] = {
            DRUG_PRODUCT: [
                {
                    "iri": MANUFACTURED_BY,
                    "name": "manufacturedBy",
                    "label": "生产者",
                    "range": [MANUFACTURER],
                },
            ],
        }
        # class IRI -> [IndividualInfo]
        self._individuals: dict[str, list[IndividualInfo]] = {}
        # class IRI -> [external alignment IRI]
        self._alignments: dict[str, list[str]] = {}

    # --- test seeding helpers ---------------------------------------------
    def add_individual(
        self,
        class_iri: str,
        name: str,
        properties: dict | None = None,
        extra_class_iris: list[str] | None = None,
        label_zh: str | None = None,
    ) -> IndividualInfo:
        ind = IndividualInfo(
            iri=DRUG_NS + name,
            name=name,
            class_iris=[class_iri, *(extra_class_iris or [])],
            label_zh=label_zh,
            properties=properties or {},
        )
        self._individuals.setdefault(class_iri, []).append(ind)
        return ind

    def set_alignments(self, class_iri: str, iris: list[str]) -> None:
        self._alignments[class_iri] = list(iris)

    # --- read surface (mirror OntologyEngine) -----------------------------
    def get_subclasses(self, class_iri: str, recursive: bool = True) -> list[dict]:
        out: list[dict] = []
        seen: set[str] = set()

        def walk(parent: str) -> None:
            for cls, parents in self._parents.items():
                if parent in parents and cls not in seen:
                    seen.add(cls)
                    out.append({"iri": cls, "label": _LABELS.get(cls, _local(cls))})
                    if recursive:
                        walk(cls)

        walk(class_iri)
        return out

    def get_data_properties_by_domain(self, class_iri: str) -> list[dict]:
        return [dict(p) for p in self._data_props.get(class_iri, [])]

    def get_object_properties_by_domain(self, class_iri: str) -> list[dict]:
        return [dict(p) for p in self._obj_props.get(class_iri, [])]

    def get_relation_schema(self, class_iri: str, max_hops: int = 4) -> list[dict]:
        """Read-only BFS over object properties → relationship edges (016, T004).

        Mirrors ``OntologyEngine.get_relation_schema`` (``ontology_engine.py:483``):
        one edge per discovered ``(predicate, range)`` carrying the range type's own
        data-property checklist, with the real engine's **global first-discovery
        dedup** — each range IRI is emitted **at most once** across the whole BFS
        (``visited_ranges``). Edge shape is byte-compatible with the production engine:
        ``hop, predicate_iri, predicate_label, domain_class_iri, domain_class_label,
        range_class_iri, range_class_label, range_subclasses, range_data_properties``.

        This MUST be a real method: ``__getattr__`` returns a ``None``-yielding no-op,
        which would make relationship expansion silently vanish (memory
        ``ontology-aware-paths-legacy-in-tests``).
        """
        edges: list[dict] = []
        visited_ranges: set[str] = {class_iri}
        frontier: list[tuple[str, int]] = [(class_iri, 0)]
        while frontier:
            domain, hop = frontier.pop(0)
            if hop >= max_hops:
                continue
            for op in self._obj_props.get(domain, []):
                for rng in op.get("range", []):
                    if rng in visited_ranges:  # global first-discovery dedup (D8)
                        continue
                    visited_ranges.add(rng)
                    edges.append(
                        {
                            "hop": hop + 1,
                            "predicate_iri": op["iri"],
                            "predicate_label": op.get("label", _local(op["iri"])),
                            "domain_class_iri": domain,
                            "domain_class_label": _LABELS.get(domain, _local(domain)),
                            "range_class_iri": rng,
                            "range_class_label": _LABELS.get(rng, _local(rng)),
                            "range_subclasses": self.get_subclasses(rng, recursive=True),
                            "range_data_properties": [
                                {"iri": p["iri"], "label": p["label"]}
                                for p in self._data_props.get(rng, [])
                            ],
                        }
                    )
                    frontier.append((rng, hop + 1))
        return edges

    def get_individuals(self, class_iri: str) -> list[IndividualInfo]:
        return list(self._individuals.get(class_iri, []))

    def get_class_detail(self, class_iri: str) -> ClassInfo | None:
        if class_iri not in self._parents:
            return None
        return ClassInfo(
            iri=class_iri,
            name=_local(class_iri),
            label_zh=_LABELS.get(class_iri),
            parent_iris=list(self._parents[class_iri]),
            children_iris=[s["iri"] for s in self.get_subclasses(class_iri, recursive=False)],
        )

    def get_class_alignments(self, class_iri: str) -> list[str]:
        return list(self._alignments.get(class_iri, []))

    # tolerate any other engine call the code under test may make
    def __getattr__(self, name):  # pragma: no cover - defensive
        def _noop(*args, **kwargs):
            return None

        return _noop


def build_drug_ontology() -> FakeFeatureEngine:
    """Return a fresh, fully seeded engine for the DrugProduct test T-Box."""
    return FakeFeatureEngine()
