"""认证层测试：口令哈希、自签令牌、登录端点、强制态中间件。

默认（auth_required=False）下头认证路径保持不变——由 test_enforced_mode_* 之外的
既有测试隐式覆盖；此处仅补验令牌路径与强制态。
"""

from __future__ import annotations

from app.auth import hash_password, issue_token, verify_password, verify_token
from app.config import settings
from app.models.ontology_meta import AppRole, AppUser


def _seed_user(db, username="admin", password="admin1234!", role="senior_analyst"):
    r = db.query(AppRole).filter_by(name=role).first()
    u = AppUser(
        username=username,
        display_name=username,
        role_id=r.id,
        password_hash=hash_password(password),
    )
    db.add(u)
    db.commit()
    return u


# --- primitives -------------------------------------------------------------
def test_password_hash_roundtrip():
    h = hash_password("admin1234!")
    assert h != "admin1234!"  # 绝不明文
    assert h.startswith("pbkdf2_sha256$")
    assert verify_password("admin1234!", h)
    assert not verify_password("wrong", h)
    assert not verify_password("admin1234!", None)


def test_token_roundtrip():
    tok = issue_token("admin", "senior_analyst")
    assert verify_token(tok) == ("admin", "senior_analyst")
    assert verify_token(tok + "x") is None  # 签名被篡改
    assert verify_token("garbage") is None
    assert verify_token(None) is None


def test_expired_token_rejected():
    expired = issue_token("admin", "senior_analyst", ttl_seconds=-10)
    assert verify_token(expired) is None


# --- login endpoint ---------------------------------------------------------
def test_login_success_and_me(client, db):
    _seed_user(db)
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin1234!"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["username"] == "admin"
    assert body["role"] == "senior_analyst"
    token = body["token"]
    assert token

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json() == {"username": "admin", "role": "senior_analyst"}


def test_login_wrong_password(client, db):
    _seed_user(db)
    r = client.post("/api/auth/login", json={"username": "admin", "password": "nope"})
    assert r.status_code == 401


def test_login_unknown_user(client):
    r = client.post("/api/auth/login", json={"username": "ghost", "password": "x"})
    assert r.status_code == 401


# --- header path preserved when enforcement off (default) -------------------
def test_header_identity_accepted_by_default(client, analyst_headers):
    r = client.get("/api/auth/me", headers=analyst_headers)
    assert r.status_code == 200
    assert r.json()["role"] == "senior_analyst"


# --- enforced mode: middleware guards even dependency-free endpoints ---------
def test_enforced_mode_blocks_without_token(client, monkeypatch, analyst_headers):
    monkeypatch.setattr(settings, "auth_required", True)
    # 无认证依赖的开放端点在强制态下亦被中间件拦截：
    assert client.get("/api/system-config").status_code == 401
    # 伪造头在强制态下被拒（无有效令牌）：
    assert client.get("/api/system-config", headers=analyst_headers).status_code == 401
    # 有效令牌放行：
    token = issue_token("admin", "senior_analyst")
    ok = client.get("/api/system-config", headers={"Authorization": f"Bearer {token}"})
    assert ok.status_code == 200


def test_enforced_mode_allows_login_and_health(client, db, monkeypatch):
    monkeypatch.setattr(settings, "auth_required", True)
    _seed_user(db)
    assert client.get("/api/health").status_code == 200
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin1234!"})
    assert r.status_code == 200
