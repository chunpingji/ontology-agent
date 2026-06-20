"""Abstract integration connector interface for MES/ERP/LIMS/CTMS systems."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ProductionBatch:
    batch_id: str
    product_name: str
    equipment_ids: list[str] = field(default_factory=list)
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
    status: str = "planned"


@dataclass
class EquipmentStatus:
    equipment_id: str
    status: str  # idle, running, cleaning, maintenance
    current_product: str | None = None
    last_cleaned: datetime | None = None


@dataclass
class MaterialStock:
    material_id: str
    name: str
    quantity: float
    unit: str
    lot_number: str | None = None


@dataclass
class LabResult:
    batch_id: str
    test_name: str
    result: str
    specification: str
    passed: bool


@dataclass
class TrialInfo:
    trial_id: str
    title: str
    phase: str
    status: str
    products: list[str] = field(default_factory=list)


class ExternalSystemConnector(ABC):
    """Abstract interface for external system integration.

    Each concrete implementation connects to a specific system instance
    (e.g., a particular SAP ERP or Siemens MES deployment).
    """

    @abstractmethod
    async def test_connection(self) -> bool:
        """Test connectivity to the external system."""

    @abstractmethod
    async def fetch_production_schedule(
        self, start_date: datetime, end_date: datetime
    ) -> list[ProductionBatch]:
        """Pull upcoming batch schedule."""

    @abstractmethod
    async def fetch_equipment_status(
        self, equipment_ids: list[str]
    ) -> list[EquipmentStatus]:
        """Pull real-time equipment status."""

    @abstractmethod
    async def fetch_material_inventory(
        self, material_ids: list[str]
    ) -> list[MaterialStock]:
        """Pull material inventory."""

    @abstractmethod
    async def fetch_lab_results(
        self, batch_ids: list[str]
    ) -> list[LabResult]:
        """Pull QC results."""

    @abstractmethod
    async def fetch_clinical_trial_info(
        self, trial_ids: list[str]
    ) -> list[TrialInfo]:
        """Pull trial metadata."""


class StubConnector(ExternalSystemConnector):
    """Mock implementation returning sample data for prototype testing."""

    async def test_connection(self) -> bool:
        return True

    async def fetch_production_schedule(self, start_date, end_date):
        return [
            ProductionBatch(
                batch_id="B2026-001", product_name="DrugX Clinical Batch",
                equipment_ids=["CT64201", "DE64203"], status="planned",
            ),
        ]

    async def fetch_equipment_status(self, equipment_ids):
        return [
            EquipmentStatus(equipment_id=eid, status="idle")
            for eid in equipment_ids
        ]

    async def fetch_material_inventory(self, material_ids):
        return []

    async def fetch_lab_results(self, batch_ids):
        return []

    async def fetch_clinical_trial_info(self, trial_ids):
        return []
