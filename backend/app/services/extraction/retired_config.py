"""Reject historical executable extraction annotations at the loading boundary."""

from rdflib import URIRef

RETIRED_ANNOTATIONS = frozenset(
    URIRef("https://ontology.pharma-gmp.cn/slpra/integration/" + name)
    for name in ("extractionMethod", "extractionPattern", "extractionProfile", "extractionAnchor")
)


def reject_execution_annotations(graph):
    for predicate in RETIRED_ANNOTATIONS:
        if any(graph.triples((None, predicate, None))):
            raise ValueError("EXECUTABLE_EXTRACTION_CONFIG_RETIRED: " + str(predicate))
