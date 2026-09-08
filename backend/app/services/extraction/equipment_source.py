"""Explicit raw mock dataset. Identity lookup never infers classes or relationships."""

import json
from pathlib import Path

EQUIPMENT_NS = "https://ontology.pharma-gmp.cn/slpra/equipment/"
PROCESS_EQUIPMENT_IRI = EQUIPMENT_NS + "ProcessEquipment"


class MockEquipmentSource:
    def evidence_record(self, equipment_id):
        from app.services.extraction.external_records import ResolvedRecord, record_version

        path = Path(__file__).parents[2] / "resources" / "equipment_archive.json"
        records = json.loads(path.read_text(encoding="utf-8"))
        fields = next((r for r in records if r["equipment_id"] == equipment_id), None)
        if fields is None:
            return None
        return ResolvedRecord(
            system="mock_equipment",
            dataset="equipment_archive",
            key=equipment_id,
            version=record_version(fields),
            class_iri=PROCESS_EQUIPMENT_IRI,
            label_field="equipment_id",
            fields=fields,
            field_predicates={
                "equipment_id": EQUIPMENT_NS + "equipmentID",
                "name": EQUIPMENT_NS + "equipmentName",
                "specification": EQUIPMENT_NS + "modelSpecification",
            },
        )


def get_equipment_source():
    return MockEquipmentSource()
