"""文件源连接器（integration 页面 · file system_type）。

把本地文件（Excel / Word / PDF）接入连接器管理页面，提供探活（文件可读性检查）
和结构预览能力。复用 `ExternalSystemConnector` 协议与工厂分发（`connector_for`），
与既有 rest_api/database/doc_repo/aps 连接器同构。

配置项：
- `file_path`：文件绝对路径（或 glob 模式，如 `/data/imports/*.xlsx`）
- `format`：文件格式（excel / word / pdf），用于选择解析器
- `column_mapping`：列名 → 本体属性 IRI 映射（Excel/Word 表格适用）
- `sheet_name`：Excel 工作表名（可选，默认第一个）
"""

from __future__ import annotations

import glob
import logging
from datetime import datetime
from pathlib import Path

from app.services.integration.base import (
    EquipmentStatus,
    ExternalSystemConnector,
    LabResult,
    MaterialStock,
    ProductionBatch,
    TrialInfo,
)

logger = logging.getLogger(__name__)

SUPPORTED_FORMATS = {"excel", "word", "pdf"}

_FORMAT_EXTENSIONS: dict[str, set[str]] = {
    "excel": {".xlsx", ".xls"},
    "word": {".docx", ".doc"},
    "pdf": {".pdf"},
}


def _guess_format(path: str) -> str | None:
    suffix = Path(path).suffix.lower()
    for fmt, exts in _FORMAT_EXTENSIONS.items():
        if suffix in exts:
            return fmt
    return None


def _resolve_paths(pattern: str) -> list[Path]:
    if "*" in pattern or "?" in pattern:
        return [Path(p) for p in sorted(glob.glob(pattern)) if Path(p).is_file()]
    p = Path(pattern)
    return [p] if p.is_file() else []


class FileConnector(ExternalSystemConnector):
    """`file` 连接器：本地文件（Excel/Word/PDF）的探活 + 结构预览。"""

    def __init__(
        self,
        connection_config: dict | None,
        field_mapping: dict | None = None,
        timeout: float = 5.0,
    ) -> None:
        self.config = connection_config or {}
        self.field_mapping = field_mapping or {}
        self.timeout = timeout
        self.last_status: str | None = None
        self.last_error: str | None = None

    @property
    def file_path(self) -> str:
        return str(self.config.get("file_path", ""))

    @property
    def format(self) -> str:
        fmt = self.config.get("format", "")
        return fmt if fmt else (_guess_format(self.file_path) or "")

    async def test_connection(self) -> bool:
        try:
            paths = _resolve_paths(self.file_path)
            if not paths:
                raise FileNotFoundError(f"文件不存在或模式无匹配：{self.file_path}")
            for p in paths:
                if not p.is_file():
                    raise FileNotFoundError(f"不是文件：{p}")
                if not p.stat().st_size:
                    raise ValueError(f"文件为空：{p}")
            self.last_status = "ok"
            self.last_error = None
            return True
        except Exception as exc:  # noqa: BLE001
            self.last_status = "error"
            self.last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("文件连接器探活失败：%s", self.last_error)
            return False

    def preview(self) -> list[dict]:
        """预览文件结构/内容（首几行）。"""
        paths = _resolve_paths(self.file_path)
        result = []
        for p in paths:
            fmt = self.config.get("format") or _guess_format(str(p)) or ""
            entry: dict = {"file": str(p), "format": fmt}
            if fmt == "excel":
                entry["preview"] = self._preview_excel(p)
            elif fmt == "word":
                entry["preview"] = self._preview_word(p)
            elif fmt == "pdf":
                entry["preview"] = self._preview_pdf(p)
            else:
                entry["preview"] = {"note": f"未知格式：{p.suffix}"}
            result.append(entry)
        return result

    def _preview_excel(self, path: Path) -> dict:
        try:
            import openpyxl
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            sheets = wb.sheetnames
            ws = wb.active
            rows = list(ws.iter_rows(max_row=6, values_only=True))
            wb.close()
            headers = [str(c) if c else "" for c in rows[0]] if rows else []
            sample = [[str(c) if c is not None else "" for c in r] for r in rows[1:5]]
            return {"sheets": sheets, "headers": headers, "sample_rows": sample}
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}

    def _preview_word(self, path: Path) -> dict:
        try:
            import docx
            doc = docx.Document(path)
            tables = len(doc.tables)
            paragraphs = len(doc.paragraphs)
            first_paras = [p.text.strip() for p in doc.paragraphs[:5] if p.text.strip()]
            table_headers = []
            if doc.tables:
                table_headers = [c.text.strip() for c in doc.tables[0].rows[0].cells]
            return {
                "tables": tables,
                "paragraphs": paragraphs,
                "first_paragraphs": first_paras,
                "first_table_headers": table_headers,
            }
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}

    def _preview_pdf(self, path: Path) -> dict:
        try:
            return {"pages": "PDF preview not yet implemented", "file_size": path.stat().st_size}
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}

    # --- ExternalSystemConnector 抽象方法（文件源不供业务域实时数据 → 最小实现）---
    async def fetch_production_schedule(
        self, start_date: datetime, end_date: datetime
    ) -> list[ProductionBatch]:
        return []

    async def fetch_equipment_status(
        self, equipment_ids: list[str]
    ) -> list[EquipmentStatus]:
        return []

    async def fetch_material_inventory(
        self, material_ids: list[str]
    ) -> list[MaterialStock]:
        return []

    async def fetch_lab_results(self, batch_ids: list[str]) -> list[LabResult]:
        return []

    async def fetch_clinical_trial_info(
        self, trial_ids: list[str]
    ) -> list[TrialInfo]:
        return []
