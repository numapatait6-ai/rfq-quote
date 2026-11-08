import os, io, re, json, sqlite3, hashlib, secrets, urllib.request
from datetime import date, datetime, timedelta
from typing import Optional, List
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from costing import cost_part, resolve_rates

DB = os.environ.get("DB_PATH", "quotes.db")
SCHEMA = """
create table if not exists users(id integer primary key, email text unique, name text, role text, salt text, pw text);
create table if not exists sessions(token text primary key, user_id int, expires text);
create table if not exists rates(id integer primary key, kind text, key text, value real, effective_from text, note text, created_by text);
create table if not exists rfqs(id integer primary key, rfq_no text unique, customer text not null, due_date text, owner_id int,
  status text default 'estimating', lost_reason text, snapshot text, annual_value real, approved_by text, created_at text);
create table if not exists parts(id integer primary key, rfq_id int, name text, material text, gross_wt real, annual_volume int,
  routing text default '[]', sources text default '{}', accepted int default 0);
create table if not exists past_parts(id integer primary key, quote_no text, customer text, name text, material text, routing text, price real, quoted_on text);
"""
def db():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row; return c
def q(sql, a=(), one=False):
    c = db()
    try:
        rows = c.execute(sql, a).fetchall(); c.commit()
        return (rows[0] if rows else None) if one else rows
    finally: c.close()
def ex(sql, a=()):
    c = db(); cur = c.execute(sql, a); c.commit(); i = cur.lastrowid; c.close(); return i

def hp(pw, salt): return hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 120000).hex()
def now(): return datetime.utcnow().isoformat()

app = FastAPI(title="RFQ-to-Quote")

def user(authorization: str = Header(None)):
    t = (authorization or "").removeprefix("Bearer ").strip()
    u = q("select u.* from sessions s join users u on u.id=s.user_id where s.token=? and s.expires>?", (t, now()), one=True)
    if not u: raise HTTPException(401, "Please sign in")
    return u
def head(u=Depends(user)):
    if u["role"] != "sales_head": raise HTTPException(403, "Sales head only")
    return u

class Login(BaseModel): email: str = Field(max_length=100); password: str = Field(max_length=100)
@app.post("/api/login")
def login(b: Login):
    u = q("select * from users where email=?", (b.email.lower().strip(),), one=True)
    if not u or not secrets.compare_digest(hp(b.password, u["salt"]), u["pw"]): raise HTTPException(401, "Wrong email or password")
    t = secrets.token_hex(24)
    ex("insert into sessions values(?,?,?)", (t, u["id"], (datetime.utcnow() + timedelta(hours=12)).isoformat()))
    return {"token": t, "name": u["name"], "role": u["role"]}

# ---------- rates ----------
def rate_rows(): return [(r["kind"], r["key"], r["value"], r["effective_from"]) for r in q("select * from rates")]
def rates_on(d): return resolve_rates(rate_rows(), d)
@app.get("/api/rates")
def list_rates(u=Depends(user)): return [dict(r) for r in q("select * from rates order by kind,key,effective_from desc")]
class NewRate(BaseModel):
    kind: str = Field(pattern=r"^[a-z_]+$", max_length=20); key: str = Field(min_length=1, max_length=60)
    value: float = Field(ge=0, le=1e9); effective_from: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$"); note: str = Field("", max_length=200)
@app.post("/api/rates")
def add_rate(b: NewRate, u=Depends(head)):
    # Rates are append-only: a change is a new row with a new effective date, so old quotes keep old rates.
    if q("select 1 from rates where kind=? and key=? and effective_from=?", (b.kind, b.key, b.effective_from), one=True):
        raise HTTPException(409, "A rate already exists for that date")
    ex("insert into rates(kind,key,value,effective_from,note,created_by) values(?,?,?,?,?,?)", (b.kind, b.key, b.value, b.effective_from, b.note, u["email"]))
    return {"ok": True}

# ---------- RFQs ----------
class NewRfq(BaseModel): customer: str = Field(min_length=2, max_length=80); due_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
def rfq_or_404(i):
    r = q("select r.*, u.name owner from rfqs r left join users u on u.id=r.owner_id where r.id=?", (i,), one=True)
    if not r: raise HTTPException(404, "RFQ not found")
    return r
def rfq_out(r):
    d = dict(r); d.pop("snapshot", None); d["has_quote"] = bool(r["snapshot"])
    d["parts"] = [pj(p) for p in q("select * from parts where rfq_id=?", (r["id"],))]
    return d
def pj(p):
    d = dict(p); d["routing"] = json.loads(d["routing"]); d["sources"] = json.loads(d["sources"]); return d
@app.get("/api/rfqs")
def rfqs(status: Optional[str] = None, u=Depends(user)):
    rows = q("select r.id,rfq_no,customer,due_date,status,annual_value,u.name owner,(select count(*) from parts where rfq_id=r.id) parts from rfqs r left join users u on u.id=r.owner_id order by due_date")
    return [dict(r) for r in rows if not status or r["status"] == status]
@app.post("/api/rfqs")
def new_rfq(b: NewRfq, u=Depends(user)):
    n = (q("select count(*) c from rfqs", one=True)["c"]) + 1
    i = ex("insert into rfqs(rfq_no,customer,due_date,owner_id,created_at) values(?,?,?,?,?)", (f"RFQ-{date.today():%y%m}-{n:03d}", b.customer, b.due_date, u["id"], now()))
    return {"id": i}
@app.get("/api/rfqs/{i}")
def get_rfq(i: int, u=Depends(user)): return rfq_out(rfq_or_404(i))

# ---------- AI intake (extraction only; maths never uses AI) ----------
def parse_lines(text):
    """Deterministic fallback: rows like 'Name | Material | GrossWt | AnnualVol | CycleMin'."""
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        cols = [c.strip() for c in re.split(r"[|\t]|\s{2,}", line) if c.strip()]
        nums = [c for c in cols if re.fullmatch(r"[\d,]*\.?\d+", c)]
        if len(cols) >= 4 and len(nums) >= 2:
            txt = [c for c in cols if c not in nums]
            out.append({"name": txt[0], "material": txt[1] if len(txt) > 1 else "", "gross_wt": float(nums[0].replace(",", "")),
                        "annual_volume": int(float(nums[1].replace(",", ""))), "source": f"Line {n} of pasted text"})
    return out
def parse_llm(text):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key: return None
    prompt = ("Extract parts from this RFQ email/part list. Reply ONLY with a JSON array of objects with keys name, material, gross_wt (kg, number), "
              "annual_volume (integer), source (short quote of where you saw it). Omit anything you are unsure of.\n\n" + text[:8000])
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", method="POST",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        data=json.dumps({"model": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5"), "max_tokens": 1500, "messages": [{"role": "user", "content": prompt}]}).encode())
    try:
        with urllib.request.urlopen(req, timeout=30) as r: txt = json.load(r)["content"][0]["text"]
        rows = json.loads(re.search(r"\[.*\]", txt, re.S).group(0))
        return [{"name": str(x["name"]), "material": str(x.get("material", "")), "gross_wt": float(x["gross_wt"]),
                 "annual_volume": int(x["annual_volume"]), "source": "AI: " + str(x.get("source", "email"))[:80]} for x in rows]
    except Exception:
        return None  # fall back to the deterministic parser
class Intake(BaseModel): text: str = Field(min_length=5, max_length=20000)
@app.post("/api/rfqs/{i}/intake")
def intake(i: int, b: Intake, u=Depends(user)):
    r = rfq_or_404(i)
    if r["status"] != "estimating": raise HTTPException(409, "RFQ is locked")
    rows = parse_llm(b.text) or parse_lines(b.text)
    if not rows: raise HTTPException(422, "Could not find any parts. Use: Name | Material | Gross wt kg | Annual volume")
    for x in rows:
        src = {"name": x["source"], "material": x["source"], "gross_wt": x["source"], "annual_volume": x["source"]}
        ex("insert into parts(rfq_id,name,material,gross_wt,annual_volume,sources) values(?,?,?,?,?,?)",
           (i, x["name"][:80], x["material"][:60], x["gross_wt"], x["annual_volume"], json.dumps(src)))
    return {"added": len(rows)}

class Op(BaseModel): machine: str = Field(max_length=60); cycle_min: float = Field(gt=0, le=600)
class PartIn(BaseModel):
    name: str = Field(min_length=1, max_length=80); material: str = Field(min_length=1, max_length=60)
    gross_wt: float = Field(gt=0, le=5000); annual_volume: int = Field(gt=0, le=100_000_000); routing: List[Op] = []
def part_or_404(i):
    p = q("select * from parts where id=?", (i,), one=True)
    if not p: raise HTTPException(404, "Part not found")
    return p
@app.put("/api/parts/{i}")
def edit_part(i: int, b: PartIn, u=Depends(user)):
    p = part_or_404(i)
    if rfq_or_404(p["rfq_id"])["status"] != "estimating": raise HTTPException(409, "RFQ is locked")
    # any manual edit marks the field as estimator-entered and requires acceptance again
    ex("update parts set name=?,material=?,gross_wt=?,annual_volume=?,routing=?,accepted=0 where id=?",
       (b.name, b.material, b.gross_wt, b.annual_volume, json.dumps([o.model_dump() for o in b.routing]), i))
    return {"ok": True}
@app.post("/api/parts/{i}/accept")
def accept(i: int, u=Depends(user)):
    p = part_or_404(i)
    if not json.loads(p["routing"]): raise HTTPException(422, "Add at least one routing step first")
    ex("update parts set accepted=1 where id=?", (i,)); return {"ok": True}
@app.delete("/api/parts/{i}")
def del_part(i: int, u=Depends(user)):
    p = part_or_404(i)
    if rfq_or_404(p["rfq_id"])["status"] != "estimating": raise HTTPException(409, "RFQ is locked")
    ex("delete from parts where id=?", (i,)); return {"ok": True}

def cost_for(p, as_of):
    d = {"material": p["material"], "gross_wt": p["gross_wt"], "annual_volume": p["annual_volume"], "routing": json.loads(p["routing"])}
    return cost_part(d, rates_on(as_of))
@app.get("/api/parts/{i}/cost")
def part_cost(i: int, u=Depends(user)):
    try: return cost_for(part_or_404(i), date.today().isoformat())
    except ValueError as e: raise HTTPException(422, str(e))
@app.get("/api/parts/{i}/suggest")
def suggest(i: int, u=Depends(user)):
    p = part_or_404(i); words = set(re.findall(r"\w+", p["name"].lower())); best = None
    for r in q("select * from past_parts"):
        s = len(words & set(re.findall(r"\w+", r["name"].lower()))) * 2 + (r["material"] == p["material"])
        if s > 0 and (not best or s > best[0]): best = (s, r)
    if not best: return None
    r = best[1]; return {"routing": json.loads(r["routing"]), "from": f"{r['name']} ({r['material']}), {r['quote_no']}, {r['customer']}, quoted {r['quoted_on']}"}

# ---------- workflow ----------
def snapshot(r):
    today = date.today().isoformat(); parts, used, total = [], {}, 0
    for p in q("select * from parts where rfq_id=?", (r["id"],)):
        if not p["accepted"]: raise HTTPException(422, f"Accept part '{p['name']}' first")
        try: c = cost_for(p, today)
        except ValueError as e: raise HTTPException(422, f"{p['name']}: {e}")
        used.update(c.pop("rates_used")); total += c["annual_value"]
        parts.append({**pj(p), **c})
    if not parts: raise HTTPException(422, "Add at least one part")
    return {"as_of": today, "parts": parts, "rates_used": used, "annual_value": round(total, 2)}
@app.post("/api/rfqs/{i}/submit")
def submit(i: int, u=Depends(user)):
    r = rfq_or_404(i)
    if r["status"] != "estimating": raise HTTPException(409, "Already submitted")
    s = snapshot(r); limit = rates_on(s["as_of"]).get("policy:approval_limit", {"value": 2500000})["value"]
    status = "pending_approval" if s["annual_value"] > limit else "approved"
    ex("update rfqs set snapshot=?,annual_value=?,status=? where id=?", (json.dumps(s), s["annual_value"], status, i))
    return {"status": status, "limit": limit}
class Reject(BaseModel): reason: str = Field(min_length=3, max_length=200)
@app.post("/api/rfqs/{i}/approve")
def approve(i: int, u=Depends(head)):
    if rfq_or_404(i)["status"] != "pending_approval": raise HTTPException(409, "Not awaiting approval")
    ex("update rfqs set status='approved',approved_by=? where id=?", (u["name"], i)); return {"ok": True}
@app.post("/api/rfqs/{i}/return")
def send_back(i: int, b: Reject, u=Depends(head)):
    if rfq_or_404(i)["status"] != "pending_approval": raise HTTPException(409, "Not awaiting approval")
    ex("update rfqs set status='estimating',snapshot=null,annual_value=null,lost_reason=? where id=?", ("Returned: " + b.reason, i)); return {"ok": True}
@app.post("/api/rfqs/{i}/send")
def send(i: int, u=Depends(user)):
    if rfq_or_404(i)["status"] != "approved": raise HTTPException(409, "Only approved quotes can be sent")  # server-side gate
    ex("update rfqs set status='sent' where id=?", (i,)); return {"ok": True}  # mock: no real email
class Outcome(BaseModel): outcome: str = Field(pattern="^(won|lost)$"); reason: str = Field("", max_length=200)
@app.post("/api/rfqs/{i}/outcome")
def outcome(i: int, b: Outcome, u=Depends(user)):
    if rfq_or_404(i)["status"] != "sent": raise HTTPException(409, "Quote not sent yet")
    if b.outcome == "lost" and not b.reason.strip(): raise HTTPException(422, "Give a reason for the loss")
    ex("update rfqs set status=?,lost_reason=? where id=?", (b.outcome, b.reason or None, i)); return {"ok": True}

@app.get("/api/search")
def search(term: str = "", u=Depends(user)):
    t = f"%{term.strip()}%"
    res = [dict(r) for r in q("select quote_no,customer,name,material,price,quoted_on from past_parts where name like ? or material like ? or customer like ? order by quoted_on desc limit 50", (t, t, t))]
    return res

@app.get("/api/rfqs/{i}/pdf")
def pdf(i: int, u=Depends(user)):
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib import colors
    r = rfq_or_404(i)
    if not r["snapshot"] or r["status"] in ("estimating", "pending_approval"): raise HTTPException(409, "Quote is not approved yet")
    s = json.loads(r["snapshot"]); st = getSampleStyleSheet(); buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36)
    el = [Paragraph(f"Quotation for {r['rfq_no']}", st["Title"]), Paragraph(f"Customer: {r['customer']} &nbsp; Rate date: {s['as_of']} &nbsp; All prices in INR per piece, GST shown separately", st["Normal"]), Spacer(1, 12)]
    rows = [("material", "Material"), ("conversion", "Machining"), ("setup", "Setup / piece"), ("overhead", "Overhead"), ("packing", "Packing"), ("freight", "Freight"), ("margin", "Margin"), ("price", "Price ex-GST"), ("gst", "GST"), ("price_incl_gst", "Price incl. GST")]
    for p in s["parts"]:
        el.append(Paragraph(f"{p['name']} ({p['material']}, {p['gross_wt']} kg)", st["Heading3"]))
        data = [["Per piece (Rs)"] + [f"{v['volume']:,} pcs/yr" for v in p["volumes"]]] + [[lab] + [f"{v[k]:,.2f}" for v in p["volumes"]] for k, lab in rows]
        t = Table(data, colWidths=[150, 110, 110, 110], hAlign="LEFT")
        t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 8), ("ALIGN", (1, 0), (-1, -1), "RIGHT"), ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.grey), ("FONTNAME", (0, 8), (-1, 8), "Helvetica-Bold")]))
        el += [t, Spacer(1, 8)]
    el.append(Paragraph(f"Annual value ex-GST: Rs {s['annual_value']:,.0f}. Approved by: {r['approved_by'] or 'not required (below limit)'}. Rates used: " + "; ".join(f"{k} {v['value']:g} (from {v['effective_from']})" for k, v in sorted(s["rates_used"].items())), st["Italic"]))
    doc.build(el)
    return Response(buf.getvalue(), media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{r["rfq_no"]}.pdf"'})

def init():
    c = db(); c.executescript(SCHEMA); c.commit(); c.close()
    if not q("select 1 from users limit 1", one=True):
        import seed; seed.run(ex, hp)
init()
@app.get("/")
def home(): return FileResponse("static/index.html")
app.mount("/static", StaticFiles(directory="static"), name="static")
