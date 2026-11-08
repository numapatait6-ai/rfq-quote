"""Pure, fixed-formula costing. No AI, no I/O. All rates come from the versioned rate master."""
VOLUME_FACTORS = (0.5, 1.0, 1.5)  # ASSUMPTION: quote at 50%, 100%, 150% of annual volume
LOTS_PER_YEAR = 12                # ASSUMPTION: one production lot per month


def resolve_rates(rows, as_of):
    """rows: (kind, key, value, effective_from ISO). Latest row with effective_from <= as_of wins."""
    best = {}
    for kind, key, value, eff in rows:
        k = f"{kind}:{key}"
        if eff <= as_of and (k not in best or eff > best[k]["effective_from"]):
            best[k] = {"value": value, "effective_from": eff}
    return best


def cost_part(part, rates):
    used = {}

    def rate(name):
        if name not in rates:
            raise ValueError(f"No rate for '{name}' on the quote date")
        used[name] = rates[name]
        return rates[name]["value"]

    gross = float(part["gross_wt"])
    material = gross * rate(f"material:{part['material']}") * (1 + rate("overhead:scrap_pct") / 100)
    conversion = sum(float(op["cycle_min"]) / 60 * rate(f"machine:{op['machine']}") for op in part["routing"])
    overhead = (material + conversion) * rate("overhead:overhead_pct") / 100
    packing = rate("packing:per_pc")
    freight = rate("freight:per_kg") * gross
    setup = rate("setup:per_lot")
    margin_pct = rate("margin:margin_pct")
    gst_pct = rate("tax:gst_pct")
    volumes = []
    for f in VOLUME_FACTORS:
        vol = max(1, round(part["annual_volume"] * f))
        setup_pc = setup / (vol / LOTS_PER_YEAR)
        cost = material + conversion + overhead + packing + freight + setup_pc
        price = cost * (1 + margin_pct / 100)
        volumes.append({
            "volume": vol, "material": round(material, 2), "conversion": round(conversion, 2),
            "setup": round(setup_pc, 2), "overhead": round(overhead, 2), "packing": round(packing, 2),
            "freight": round(freight, 2), "cost": round(cost, 2), "margin": round(price - cost, 2),
            "price": round(price, 2), "gst": round(price * gst_pct / 100, 2),
            "price_incl_gst": round(price * (1 + gst_pct / 100), 2),
        })
    annual_value = next(v for v, f in zip(volumes, VOLUME_FACTORS) if f == 1.0)["price"] * part["annual_volume"]
    return {"volumes": volumes, "annual_value": round(annual_value, 2), "rates_used": used}
