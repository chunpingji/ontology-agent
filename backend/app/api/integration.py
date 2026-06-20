"""Integration gateway API routes — interface specifications only."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.integration import IntegrationConnector

router = APIRouter()


class ConnectorCreate(BaseModel):
    system_type: str  # mes, erp, lims, ctms
    name: str
    connection_config: dict | None = None
    field_mapping: dict | None = None


class ConnectorResponse(BaseModel):
    id: UUID
    system_type: str
    name: str
    connection_config: dict | None = None
    field_mapping: dict | None = None
    is_active: bool = False

    class Config:
        from_attributes = True


class IntegrationSpec(BaseModel):
    system_type: str
    description: str
    endpoints: list[dict]


@router.get("/connectors", response_model=list[ConnectorResponse])
def list_connectors(db: Session = Depends(get_db)):
    return db.query(IntegrationConnector).all()


@router.post("/connectors", response_model=ConnectorResponse, status_code=201)
def create_connector(req: ConnectorCreate, db: Session = Depends(get_db)):
    connector = IntegrationConnector(**req.model_dump())
    db.add(connector)
    db.commit()
    db.refresh(connector)
    return connector


@router.delete("/connectors/{connector_id}", status_code=204)
def delete_connector(connector_id: UUID, db: Session = Depends(get_db)):
    c = db.get(IntegrationConnector, connector_id)
    if not c:
        raise HTTPException(404)
    db.delete(c)
    db.commit()


@router.post("/connectors/{connector_id}/test")
def test_connector(connector_id: UUID, db: Session = Depends(get_db)):
    c = db.get(IntegrationConnector, connector_id)
    if not c:
        raise HTTPException(404)
    return {"status": "mock_success", "message": f"Stub connector {c.name} responded OK"}


@router.get("/specs", response_model=list[IntegrationSpec])
def list_integration_specs():
    return [
        IntegrationSpec(
            system_type="mes",
            description="Manufacturing Execution System — production batch data, equipment status",
            endpoints=[
                {"method": "GET", "path": "/production-schedule", "params": "date_range"},
                {"method": "GET", "path": "/equipment-status", "params": "equipment_ids[]"},
                {"method": "POST", "path": "/batch-record", "params": "batch_data"},
            ],
        ),
        IntegrationSpec(
            system_type="erp",
            description="Enterprise Resource Planning — material inventory, production orders",
            endpoints=[
                {"method": "GET", "path": "/material-inventory", "params": "material_ids[]"},
                {"method": "GET", "path": "/production-orders", "params": "date_range, status"},
            ],
        ),
        IntegrationSpec(
            system_type="lims",
            description="Laboratory Information Management System — QC results, CoA",
            endpoints=[
                {"method": "GET", "path": "/lab-results", "params": "batch_ids[]"},
                {"method": "GET", "path": "/specifications", "params": "product_id"},
            ],
        ),
        IntegrationSpec(
            system_type="ctms",
            description="Clinical Trial Management System — trial metadata, batch allocation",
            endpoints=[
                {"method": "GET", "path": "/trials", "params": "trial_ids[]"},
                {"method": "GET", "path": "/batch-allocation", "params": "trial_id"},
            ],
        ),
    ]
