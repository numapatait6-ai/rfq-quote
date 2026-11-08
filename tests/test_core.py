import os, sys, tempfile, json
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ["DB_PATH"] = tempfile.mktemp(suffix=".db")
import pytest
from fastapi.testclient import TestClient
from costing import cost_part, resolve_rates
import app as appmod

R = {"material:EN8": 100, "machine:CNC Turning": 600, "overhead:scrap_pct": 0, "overhead:overhead_pct": 10, "packing:per_pc": 5,
     "freight:per_kg": 2, "setup:per_lot": 1200, "margin:margin_pct": 20, "tax:gst_pct": 18}
R = {k: {"value": v, "effective_from": "2026-01-01"} for k, v in R.items()}
PART = {"material": "EN8", "gross_wt": 1.0, "annual_volume": 12000, "routing": [{"machine": "CNC Turning", "cycle_min": 2}]}

def test_costing_by_hand():
    v = cost_part(PART, R)["volumes"][1]  # 12,000/yr -> lot of 1,000 -> setup 1.2/pc
    # material 100 + conversion 20 + overhead 12 + packing 5 + freight 2 + setup 1.2 = 140.2; price = 168.24
    assert v["cost"] == 140.2 and v["price"] == 168.24 and v["gst"] == 30.28

def test_higher_volume_lowers_price():
    p = [v["price"] for v in cost_part(PART, R)["volumes"]]
    assert p[0] > p[1] > p[2]

def test_missing_rate_is_an_error():
    with pytest.raises(ValueError): cost_part({**PART, "material": "XYZ"}, R)

def test_rates_effective_dates():
    rows = [("material", "EN8", 68, "2026-04-01"), ("material", "EN8", 74, "2026-10-01")]
    assert resolve_rates(rows, "2026-09-30")["material:EN8"]["value"] == 68
    assert resolve_rates(rows, "2026-10-01")["material:EN8"]["value"] == 74
    assert "material:EN8" not in resolve_rates(rows, "2026-03-31")

def login(c, email, pw):
    t = c.post("/api/login", json={"email": email, "password": pw}).json()["token"]; return {"Authorization": f"Bearer {t}"}
def rfq_id(c, h, no): return [r for r in c.get("/api/rfqs", headers=h).json() if r["rfq_no"] == no][0]["id"]

def test_workflow_and_access_control():
    c = TestClient(appmod.app)
    assert c.get("/api/rfqs").status_code == 401
    est = login(c, "estimator@demo.test", "Estimate#2026"); head = login(c, "saleshead@demo.test", "Approve#2026")
    assert c.post("/api/rates", headers=est, json={"kind": "material", "key": "X", "value": 1, "effective_from": "2026-11-01"}).status_code == 403
    rid = rfq_id(c, est, "RFQ-2611-014")
    assert c.post(f"/api/rfqs/{rid}/submit", headers=est).status_code == 422  # parts not accepted yet
    for p in c.get(f"/api/rfqs/{rid}", headers=est).json()["parts"]: assert c.post(f"/api/parts/{p['id']}/accept", headers=est).status_code == 200
    assert c.post(f"/api/rfqs/{rid}/submit", headers=est).json()["status"] == "pending_approval"  # > Rs 25 lakh
    assert c.post(f"/api/rfqs/{rid}/send", headers=est).status_code == 409  # cannot skip approval
    assert c.post(f"/api/rfqs/{rid}/approve", headers=est).status_code == 403
    assert c.post(f"/api/rfqs/{rid}/approve", headers=head).status_code == 200
    assert c.get(f"/api/rfqs/{rid}/pdf", headers=est).headers["content-type"] == "application/pdf"

def test_old_quote_keeps_old_rates():
    c = TestClient(appmod.app); est = login(c, "estimator@demo.test", "Estimate#2026"); head = login(c, "saleshead@demo.test", "Approve#2026")
    rid = rfq_id(c, est, "RFQ-2611-023")
    for p in c.get(f"/api/rfqs/{rid}", headers=est).json()["parts"]: c.post(f"/api/parts/{p['id']}/accept", headers=est)
    c.post(f"/api/rfqs/{rid}/submit", headers=est)
    before = appmod.q("select snapshot from rfqs where id=?", (rid,), one=True)["snapshot"]
    c.post("/api/rates", headers=head, json={"kind": "material", "key": "20MnCr5", "value": 999, "effective_from": "2026-11-02"})
    assert appmod.q("select snapshot from rfqs where id=?", (rid,), one=True)["snapshot"] == before

def test_intake_parser():
    rows = appmod.parse_lines("Part | Material | Wt | Vol\nRear spindle | EN8 | 0.6 | 180,000\nrandom chatter")
    assert rows == [{"name": "Rear spindle", "material": "EN8", "gross_wt": 0.6, "annual_volume": 180000, "source": "Line 2 of pasted text"}]
