from app.models.mock_data import MockEquipment


def test_update_daily_product_occupancy_and_read_it_back(client, db, analyst_headers):
    db.add(
        MockEquipment(
            equipment_id="PF64216",
            iri="http://slpra.org/equipment/PF64216",
            label="钛棒过滤器",
            equipment_class_iri="http://slpra.org/ontology/TitaniumRodFilter",
            workshop_code="642",
            data_properties=[],
        )
    )
    db.commit()

    update = client.put(
        "/api/mock-sources/equipment-schedules/occupancy",
        headers=analyst_headers,
        json={
            "equipment_id": "PF64216",
            "schedule_date": "2026-06-10",
            "product_code": "HRS-1597",
        },
    )
    assert update.status_code == 200
    assert update.json()["product_code"] == "HRS-1597"

    result = client.get(
        "/api/mock-sources/equipment-schedules",
        headers=analyst_headers,
        params={
            "equipment_id": "PF64216",
            "start_date": "2026-06-10",
            "end_date": "2026-06-10",
        },
    )
    assert result.status_code == 200
    records = result.json()
    assert len(records) == 1
    assert records[0]["product_code"] == "HRS-1597"
    assert records[0]["start_at"] == "2026-06-10T00:00:00+08:00"
    assert records[0]["end_at"] == "2026-06-11T00:00:00+08:00"
