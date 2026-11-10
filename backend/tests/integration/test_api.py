"""HTTP API through FastAPI's TestClient (real database, offline AI, demo data)."""
import uuid

import pytest

pytestmark = pytest.mark.integration


def _signup(client, name="Api"):
    r = client.post("/v1/auth/signup", json={"email": f"{name}-{uuid.uuid4().hex[:6]}@test.example",
                                             "password": "correct-horse-1", "full_name": name,
                                             "company_name": f"{name} Co"})
    assert r.status_code == 201, r.text
    return r.json()


def _auth(session):
    return {"Authorization": f"Bearer {session['access_token']}"}


def test_health_and_metrics(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").json()["status"] == "ready"
    assert "sn_http_requests_total" in client.get("/metrics").text


def test_auth_flow_and_problem_json(client):
    s = _signup(client)
    me = client.get("/v1/me", headers=_auth(s)).json()
    assert me["role"] == "owner"
    bad = client.post("/v1/auth/login", json={"email": s["user"]["email"], "password": "wrong-password"})
    assert bad.status_code == 401
    body = bad.json()
    assert body["code"] == "bad_credentials" and "request_id" in body
    assert bad.headers["content-type"].startswith("application/problem+json")
    assert client.get("/v1/me").status_code == 401
    dup = client.post("/v1/auth/signup", json={"email": s["user"]["email"], "password": "correct-horse-1",
                                               "full_name": "x", "company_name": "y"})
    assert dup.status_code == 409 and dup.json()["code"] == "email_taken"


def test_icp_and_search_planning(client):
    s = _signup(client)
    icp = client.post("/v1/icp/draft", headers=_auth(s), json={
        "business_name": "BlueCart", "description": "Inventory SaaS for D2C brands on Shopify and Amazon",
        "seeds": ["pawpantry.example"]}).json()
    assert icp["version"] == 1 and "pawpantry.example" in icp["body"]["exclusions"]
    # Editing creates version 2.
    body = icp["body"] | {"personas": ["Founder"]}
    v2 = client.put(f"/v1/icp/{icp['id']}", headers=_auth(s), json=body).json()
    assert v2["version"] == 2 and v2["lineage_id"] == icp["lineage_id"]

    search = client.post("/v1/searches", headers=_auth(s), json={
        "icp_id": v2["id"], "region_text": "Maharashtra", "target_count": 10}).json()
    assert search["status"] == "ESTIMATED"
    assert search["plan"]["paths"][0]["path"] == "B2B_COMPANY"
    assert search["estimate"]["cost_paise"] > 0 and search["budget_paise"] > 0

    unresolved = client.post("/v1/searches", headers=_auth(s), json={
        "icp_id": v2["id"], "region_text": "Baner near Balewadi", "target_count": 10})
    assert unresolved.status_code == 422 and unresolved.json()["code"] == "region_unresolved"


def test_cross_tenant_access_is_not_found(client):
    a, b = _signup(client, "A"), _signup(client, "B")
    icp = client.post("/v1/icp/draft", headers=_auth(a), json={
        "business_name": "A", "description": "Software for D2C brands"}).json()
    assert client.get(f"/v1/icp/{icp['id']}", headers=_auth(b)).status_code == 404


def test_quota_exceeded_is_402(client):
    s = _signup(client)
    icp = client.post("/v1/icp/draft", headers=_auth(s), json={
        "business_name": "X", "description": "Software for D2C brands"}).json()
    search = client.post("/v1/searches", headers=_auth(s), json={
        "icp_id": icp["id"], "region_text": "Maharashtra", "target_count": 100}).json()
    r = client.post(f"/v1/searches/{search['id']}/run", headers=_auth(s))
    assert r.status_code == 402 and r.json()["code"] == "quota_exceeded"   # trial = 25 leads


def test_admin_observability_endpoints_after_a_search(client):
    """Every admin view must work on real data (regression: NUMERIC sums broke the overview)."""
    from app.workers.worker import run_pending

    s = _signup(client, "Obs")
    icp = client.post("/v1/icp/draft", headers=_auth(s), json={
        "business_name": "Obs", "description": "Inventory SaaS for D2C brands on Shopify and Amazon"}).json()
    search = client.post("/v1/searches", headers=_auth(s), json={
        "icp_id": icp["id"], "region_text": "Pune", "target_count": 5}).json()
    assert client.post(f"/v1/searches/{search['id']}/run", headers=_auth(s)).status_code == 200
    run_pending()
    for path in ("overview", "ai", "cost", "providers", "errors"):
        r = client.get(f"/v1/admin/{path}", headers=_auth(s))
        assert r.status_code == 200, (path, r.text)
    overview = client.get("/v1/admin/overview", headers=_auth(s)).json()
    assert overview["leads"]["delivered"] > 0 and overview["spend_inr"]["total"] > 0
    timeline = client.get(f"/v1/searches/{search['id']}/timeline", headers=_auth(s)).json()
    assert any(t["type"] == "provider" for t in timeline) and any(t["type"] == "ai" for t in timeline)


def test_unhandled_error_is_problem_json_with_cors(client, monkeypatch):
    """A crash must still reach the browser as readable problem+json (CORS headers present)."""
    from app.modules.admin import service as admin

    def boom(*_a, **_k):
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(admin, "overview", boom)
    s = _signup(client, "Crash")
    r = client.get("/v1/admin/overview", headers=_auth(s) | {"Origin": "http://localhost:3000"})
    assert r.status_code == 500 and r.json()["code"] == "internal_error" and r.json()["request_id"]
    assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_vague_profile_is_refused_before_any_spend(client):
    from app.core.db import as_json, execute, tenant_tx
    from app.core.ids import new_id
    from app.modules.icp.schemas import IcpBody

    s = _signup(client, "Vague")
    icp_id = new_id()
    with tenant_tx(__import__("uuid").UUID(s["workspace_id"])) as conn:
        execute(conn, """INSERT INTO icp (id, workspace_id, lineage_id, version, body, source)
                         VALUES (:id, :ws, :id, 1, CAST(:b AS jsonb), '{}'::jsonb)""",
                {"id": icp_id, "ws": s["workspace_id"], "b": as_json(IcpBody(industries=["General businesses"]).model_dump())})
    r = client.post("/v1/searches", headers=_auth(s), json={"icp_id": str(icp_id), "region_text": "global", "target_count": 10})
    assert r.status_code == 422 and r.json()["code"] == "profile_too_vague"
    assert client.get("/v1/searches", headers=_auth(s)).json() == []        # nothing saved


def test_live_mode_without_web_search_is_refused(client, monkeypatch):
    from app.modules.search import service as searches

    class NoWebSearch:
        available_sources = {"website"}
    monkeypatch.setattr(searches, "build_gateway", lambda: NoWebSearch())
    s = _signup(client, "NoBrave")
    icp = client.post("/v1/icp/draft", headers=_auth(s), json={
        "business_name": "X", "description": "Inventory SaaS for D2C brands on Shopify and Amazon"}).json()
    r = client.post("/v1/searches", headers=_auth(s), json={"icp_id": icp["id"], "region_text": "India", "target_count": 5})
    assert r.status_code == 503 and r.json()["code"] == "no_data_source"
    assert client.get("/v1/searches", headers=_auth(s)).json() == []
