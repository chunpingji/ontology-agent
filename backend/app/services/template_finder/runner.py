"""Freeze a small read-only ontology menu and run Finder on one canonical Word IR."""

from copy import deepcopy
from dataclasses import asdict

from .finders import DP, extract_relationships
from .policy import digest
from .profiles import parse_profile
from .sources import attach_sources, locate_structure


class OntologyView:
    def __init__(self, snapshot, profile):
        self.snapshot, self.profile = snapshot, profile

    def get_relation_schema(self, _root, max_hops=4):
        return self.snapshot["edges"]

    def get_class_label(self, iri):
        return self.snapshot["classes"][iri]["label"]

    def get_data_properties_by_domain(self, iri):
        return self.snapshot["classes"][iri]["properties"]

    def get_subclass_synonyms(self, iri):
        return self.snapshot["classes"][iri]["synonyms"]

    def get_extraction_hints(self, iri):
        return self.profile["hints"].get(iri, {})

    def get_data_property_patterns(self, iri):
        return [
            {**prop, "pattern": self.profile["patterns"][prop["iri"]]}
            for prop in self.get_data_properties_by_domain(iri)
            if prop["iri"] in self.profile["patterns"]
        ]


def freeze_ontology(engine, binding):
    with engine.lexical_read_scope():
        if engine.get_class_detail(binding.root_class_iri) is None:
            raise ValueError("Finder 根类不在当前本体中")
        edges = deepcopy(engine.get_relation_schema(binding.root_class_iri, max_hops=4))
        edges.sort(
            key=lambda edge: (
                edge["domain_class_iri"],
                edge["predicate_iri"],
                edge["range_class_iri"],
            )
        )
        classes = {binding.root_class_iri}
        for edge in edges:
            for key in ("range_subclasses", "range_data_properties"):
                edge[key] = sorted(edge.get(key, []), key=lambda item: item["iri"])
            classes.update((edge["domain_class_iri"], edge["range_class_iri"]))
            classes.update(sub["iri"] for sub in edge.get("range_subclasses", []))
            hints = deepcopy(binding.profile["hints"].get(edge["range_class_iri"], {}))
            if hints.get("profile"):
                parse_profile(hints["profile"])
            edge["range_extraction_hints"] = hints
        snapshot = {
            "edges": edges,
            # Legacy risk finders explicitly use these declared data properties.
            # No rdfs:domain means by-domain menus omit them, not that they were removed.
            "unscoped_data_properties": sorted(
                set(DP.values()) & {
                    row["col_0"]["iri"] for row in engine.sparql_query("""
                        SELECT ?property WHERE {
                            ?property a <http://www.w3.org/2002/07/owl#DatatypeProperty> .
                            FILTER NOT EXISTS {
                                ?property <http://www.w3.org/2000/01/rdf-schema#domain> ?domain
                            }
                        }
                    """)
                }
            ),
            "classes": {
                iri: {
                    "label": engine.get_class_label(iri) or iri,
                    "properties": sorted(
                        deepcopy(engine.get_data_properties_by_domain(iri)),
                        key=lambda prop: prop["iri"],
                    ),
                    "synonyms": deepcopy(engine.get_subclass_synonyms(iri)),
                }
                for iri in sorted(classes)
            },
        }
    return snapshot


def render_source(analysis, path, filename):
    from app.services.extraction.document_annotator import annotate_word

    content, warnings, _, _ = annotate_word(
        path,
        engine=None,
        structure_only=True,
        rich_style=True,
        structure=analysis.structure,
        ir=analysis.ir,
    )
    return {
        "filename": filename,
        "content": content,
        "document_hash": analysis.ir.document_hash,
        "parser_version": analysis.ir.parser_version,
        "structure_hash": analysis.ir.structure_hash,
        "analysis_id": analysis.ir.analysis_id,
        "section_tree": analysis.structure.section_tree.to_dict(),
        "pagination": asdict(analysis.structure.pagination),
        "warnings": list(dict.fromkeys([*analysis.structure.warnings, *warnings])),
    }


def run(analysis, binding, snapshot):
    graph = extract_relationships(
        OntologyView(snapshot, binding.profile),
        locate_structure(analysis.structure),
        binding.root_class_iri,
    )
    # Reject a ported strategy's removed ontology vocabulary, never repair the T-Box.
    classes = snapshot["classes"]
    predicates = {edge["predicate_iri"] for edge in snapshot["edges"]}
    properties = {prop["iri"] for cls in classes.values() for prop in cls["properties"]}
    properties.update(snapshot["unscoped_data_properties"])

    def validate(nodes):
        for node in nodes:
            if node["object_class_iri"] not in classes or node["predicate_iri"] not in predicates:
                raise ValueError("Finder 关系不在当前本体菜单中")
            for prop in node["object_data_properties"]:
                if prop.get("iri") and prop["iri"] not in properties:
                    raise ValueError(f"Finder 属性不在当前本体菜单中：{prop['iri']}")
            validate(node.get("sub_relationships", []))

    validate(graph["relationships"])
    graph["counts"] = attach_sources(analysis.ir, graph["relationships"])
    graph["ontology_hash"] = digest(snapshot)
    return graph
