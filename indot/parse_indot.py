import re, json, glob, os
from pypdf import PdfReader

CONTRACT_RE = re.compile(r'\b([A-Z]{1,2})\s*-(\d{5})-([A-Z])\b')
PLAN_NAME_RE = re.compile(r'\n([A-Z0-9 &\.\-\(\),\'/]+?)\s+(Yes|No)\(')
BID_NAME_RE = re.compile(r'\n([A-Z0-9 &\.\-\(\),\'/]+?)\s+\$[\d,]+\.\d{2}\s+[A-Z]')
BID_AMOUNT_RE = re.compile(r'\$([\d,]+\.\d{2})\s+[A-Z]')
FEIN_RE = re.compile(r'(\d{2}-\d{7})')
EE_RE = re.compile(r"Engineer's Estimate:\s*\$([\d,]+\.\d{2})")
DATE_RE = re.compile(r'FOR THE LETTING OF\s*\n?\s*([A-Za-z]+ \d{1,2}, \d{4})')
DISTRICT_RE = re.compile(r'\n([A-Z][a-zA-Z]+) District\b')
COUNTY_RE = re.compile(r'County:\s*([A-Z ]+)\n')

def clean(n):
    return re.sub(r'\s+', ' ', n).strip()

def full_text(path):
    r = PdfReader(path)
    return '\n'.join(p.extract_text() for p in r.pages)

def load_blocks(txt):
    matches = list(CONTRACT_RE.finditer(txt))
    blocks = {}
    for i, m in enumerate(matches):
        cid = m.group(0).replace(' ', '')
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(txt)
        blocks.setdefault(cid, []).append(txt[start:end])
    return blocks

def parse_letting(key, bph_path, official_path):
    bph_txt = full_text(bph_path)
    off_txt = full_text(official_path)
    bph_blocks = load_blocks(bph_txt)
    off_blocks = load_blocks(off_txt)

    letting_date_m = DATE_RE.search(off_txt) or DATE_RE.search(bph_txt)
    letting_date = letting_date_m.group(1) if letting_date_m else None

    records = []
    for cid, off_segs in off_blocks.items():
        otxt = off_segs[0]
        ee_m = EE_RE.search(otxt)
        if not ee_m:
            continue
        ee = float(ee_m.group(1).replace(',', ''))
        dist_m = DISTRICT_RE.search(otxt)
        district = dist_m.group(1) if dist_m else None
        county_m = COUNTY_RE.search(otxt)
        county = county_m.group(1).strip() if county_m else None

        # actual bidders: name + amount + following FEIN
        bidders = []
        for m in BID_NAME_RE.finditer(otxt):
            name = clean(m.group(1))
            amt_m = BID_AMOUNT_RE.search(otxt, m.start())
            if not amt_m:
                continue
            amt = float(amt_m.group(1).replace(',', ''))
            tail = otxt[m.end():m.end() + 120]
            fein_m = FEIN_RE.search(tail)
            fein = fein_m.group(1) if fein_m else None
            bidders.append({"name": name, "amount": amt, "fein": fein})
        if not bidders:
            continue
        bidders.sort(key=lambda b: b["amount"])
        winner_fein = bidders[0]["fein"] or bidders[0]["name"]

        # pre-bid candidate list (planholders) for same contract
        candidates = []
        if cid in bph_blocks:
            ptxt = bph_blocks[cid][0]
            for m in PLAN_NAME_RE.finditer(ptxt):
                name = clean(m.group(1))
                valid = m.group(2)
                tail = ptxt[m.end():m.end() + 120]
                fein_m = FEIN_RE.search(tail)
                fein = fein_m.group(1) if fein_m else None
                candidates.append({"name": name, "fein": fein, "valid_for_bid": valid})

        for b in bidders:
            key_id = b["fein"] or b["name"]
            records.append({
                "letting_key": key, "letting_date": letting_date, "contract_id": cid,
                "district": district, "county": county, "engineers_estimate": ee,
                "bidder_name": b["name"], "bidder_fein": b["fein"],
                "bidder_total": b["amount"],
                "is_winner": int(key_id == winner_fein),
                "n_actual_bidders": len(bidders),
                "n_candidates_any": len(candidates),
                "n_candidates_validyes": sum(1 for c in candidates if c["valid_for_bid"] == "Yes"),
                "candidates_json": json.dumps(candidates),
            })
    return records

if __name__ == "__main__":
    manifest = json.load(open("manifest.json"))
    all_records = []
    for m in manifest:
        try:
            recs = parse_letting(m["key"], m["bph"], m["official"])
            print(f"{m['key']}: {len(recs)} bidder-rows, {len(set(r['contract_id'] for r in recs))} contracts")
            all_records.extend(recs)
        except Exception as e:
            print(f"{m['key']}: PARSE FAILED -- {e}")
    json.dump(all_records, open("indot_parsed.json", "w"))
    print(f"\nTOTAL: {len(all_records)} bidder-rows across {len(manifest)} lettings")
