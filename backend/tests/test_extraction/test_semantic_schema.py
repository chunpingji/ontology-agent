from types import SimpleNamespace

from app.services.extraction.extraction_tasks import semantic_schema_from_engine


def test_multiple_inheritance_closes_parent_menus_without_executable_annotations():
    class Engine:
        def get_modules(self):
            return [SimpleNamespace(key="example")]

        def get_class_hierarchy(self, key):
            child = SimpleNamespace(iri="urn:Child", label="Child", children=[])
            return [
                SimpleNamespace(iri=iri, label=iri, children=[child]) for iri in ("urn:A", "urn:B")
            ]

        def get_class_detail(self, iri):
            return SimpleNamespace(comment="definition")

        def get_data_properties_by_domain(self, iri):
            return (
                [{"iri": "urn:value", "datatype": "decimal", "pattern": "forbidden"}]
                if iri == "urn:A"
                else []
            )

        def get_object_properties_by_domain(self, iri):
            return [{"iri": "urn:uses", "range": ["urn:A"]}] if iri == "urn:B" else []

    schema = semantic_schema_from_engine(Engine())
    assert schema["urn:Child"]["properties"] == [{"iri": "urn:value", "datatype": "decimal"}]
    assert schema["urn:Child"]["relationships"] == [{"iri": "urn:uses", "range": ["urn:A"]}]
    assert schema["urn:Child"]["parents"] == ["urn:A", "urn:B"]
