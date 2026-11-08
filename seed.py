"""Demo data. Rates, overhead %, scrap %, margin %, packing and freight are ASSUMPTIONS (see TDD)."""
import json, secrets
DEMO_USERS = [("estimator@demo.test", "Asha Estimator", "estimator", "Estimate#2026"),
              ("saleshead@demo.test", "Vikram Sales Head", "sales_head", "Approve#2026")]
RATES = [  # kind, key, value, effective_from
    ("material", "EN8", 68, "2026-04-01"), ("material", "EN8", 74, "2026-10-01"),  # price rise: shows versioning
    ("material", "40Cr4 forging", 82, "2026-04-01"), ("material", "20MnCr5", 88, "2026-04-01"),
    ("machine", "CNC Turning", 850, "2026-04-01"), ("machine", "Forging Press", 1450, "2026-04-01"),
    ("machine", "Gear Hobbing", 950, "2026-04-01"), ("machine", "Heat Treatment", 600, "2026-04-01"), ("machine", "Cylindrical Grinding", 700, "2026-04-01"),
    ("overhead", "overhead_pct", 18, "2026-04-01"), ("overhead", "scrap_pct", 3, "2026-04-01"),
    ("packing", "per_pc", 6, "2026-04-01"), ("freight", "per_kg", 4.5, "2026-04-01"),
    ("setup", "per_lot", 3000, "2026-04-01"), ("margin", "margin_pct", 12, "2026-04-01"),
    ("tax", "gst_pct", 18, "2026-04-01"), ("policy", "approval_limit", 2500000, "2026-04-01")]
PAST = [  # quote_no, customer, name, material, routing, price, date
    ("Q-2510-031", "Company B (two-wheelers)", "Front wheel spindle", "EN8", [("CNC Turning", 3.0), ("Cylindrical Grinding", 1.2)], 118.4, "2025-10-12"),
    ("Q-2509-007", "Company C (tractors)", "Hydraulic lift arm", "40Cr4 forging", [("Forging Press", 10.8), ("Heat Treatment", 2.0)], 612.0, "2025-09-03"),
    ("Q-2508-019", "Company D (drivetrains)", "Gear blank", "20MnCr5", [("CNC Turning", 2.5), ("Gear Hobbing", 4.2)], 241.5, "2025-08-21"),
    ("Q-2507-044", "Company D (drivetrains)", "Pinion shaft", "20MnCr5", [("CNC Turning", 3.4), ("Gear Hobbing", 5.0), ("Heat Treatment", 1.5)], 305.0, "2025-07-15"),
    ("Q-2506-012", "Company B (two-wheelers)", "Gear shift spindle", "EN8", [("CNC Turning", 2.6)], 96.2, "2025-06-30")]
RFQS = [  # no, customer, due, part(name, material, wt, vol), routing
    ("RFQ-2611-014", "Company B (two-wheelers)", "2026-11-10", ("Rear wheel spindle", "EN8", 0.6, 180000), [("CNC Turning", 3.2)]),
    ("RFQ-2611-019", "Company C (tractors)", "2026-11-12", ("Hydraulic lift arm", "40Cr4 forging", 4.6, 24000), [("Forging Press", 11.5)]),
    ("RFQ-2611-023", "Company D (drivetrains)", "2026-11-06", ("Gear blank", "20MnCr5", 1.4, 60000), [("Gear Hobbing", 4.8)])]

def run(ex, hp):
    for email, name, role, pw in DEMO_USERS:
        salt = secrets.token_hex(8); ex("insert into users(email,name,role,salt,pw) values(?,?,?,?,?)", (email, name, role, salt, hp(pw, salt)))
    for k, key, v, e in RATES: ex("insert into rates(kind,key,value,effective_from,note,created_by) values(?,?,?,?,?,?)", (k, key, v, e, "seed", "system"))
    for qn, c, n, m, ro, p, d in PAST:
        ex("insert into past_parts(quote_no,customer,name,material,routing,price,quoted_on) values(?,?,?,?,?,?,?)", (qn, c, n, m, json.dumps([{"machine": a, "cycle_min": b} for a, b in ro]), p, d))
    for no, cust, due, (n, m, w, v), ro in RFQS:
        rid = ex("insert into rfqs(rfq_no,customer,due_date,owner_id,created_at) values(?,?,?,1,'2026-11-01')", (no, cust, due))
        src = {k: "RFQ email (seed)" for k in ("name", "material", "gross_wt", "annual_volume")}
        ex("insert into parts(rfq_id,name,material,gross_wt,annual_volume,routing,sources) values(?,?,?,?,?,?,?)",
           (rid, n, m, w, v, json.dumps([{"machine": a, "cycle_min": b} for a, b in ro]), json.dumps(src)))
