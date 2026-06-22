"""FastAPI dependencies: ontology engine, KG store, RBAC identity, meta store.

RBAC (R7, FR-033): identity is injected by a trusted gateway via `X-User` /
`X-Role` headers. Write/publish endpoints require the `senior_analyst` role.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.kg_store import KGStore
from app.services.ontology_engine import OntologyEngine, ontology_engine

ROLE_SENIOR_ANALYST = "senior_analyst"
ROLE_OPERATOR = "operator"
ROLE_QA = "qa"
VALID_ROLES = (ROLE_SENIOR_ANALYST, ROLE_OPERATOR, ROLE_QA)


@dataclass
class Identity:
    username: str
    role: str


def get_ontology_engine() -> OntologyEngine:
    if not ontology_engine.is_loaded:
        raise RuntimeError("Ontology not loaded")
    return ontology_engine


def get_kg_store(
    db: Session = Depends(get_db),
    engine: OntologyEngine = Depends(get_ontology_engine),
) -> KGStore:
    return KGStore(db=db, onto_engine=engine)


def get_current_user(
    x_user: str | None = Header(default=None, alias="X-User"),
    x_role: str | None = Header(default=None, alias="X-Role"),
) -> Identity:
    """Resolve the caller identity from trusted gateway headers."""
    if not x_user or not x_role:
        raise HTTPException(status_code=403, detail="缺少身份头：X-User / X-Role")
    if x_role not in VALID_ROLES:
        raise HTTPException(status_code=403, detail=f"未知角色：{x_role}")
    return Identity(username=x_user, role=x_role)


def require_role(*allowed_roles: str):
    """Dependency factory enforcing that the caller holds one of the roles."""

    def _checker(identity: Identity = Depends(get_current_user)) -> Identity:
        if identity.role not in allowed_roles:
            raise HTTPException(
                status_code=403,
                detail=f"角色不足：需要 {' / '.join(allowed_roles)}，当前为 {identity.role}",
            )
        return identity

    return _checker


def get_ontology_meta_store(
    db: Session = Depends(get_db),
    engine: OntologyEngine = Depends(get_ontology_engine),
):
    """Inject the metadata store (editable T-Box projection + dual-store sync)."""
    from app.services.ontology_meta_store import OntologyMetaStore

    return OntologyMetaStore(db=db, engine=engine)
