"""
00_explore_dataflows.py  —  ABS Data API discovery (Step 2, read-only)

Purpose: confirm the real dataflow IDs and, for each, print the dimension
order (which drives the dataKey) plus the code lists for the key dimensions
(state/region, spending category, frequency, measure, seasonal adjustment).

No third-party packages: uses only the Python standard library, so it runs
before we build the venv in Step 4. Nothing is saved — this only prints.
"""
import json, urllib.request, urllib.error

# Two valid ABS hosts for the SDMX API; we try the primary, then the alias.
HOSTS = ["https://api.data.abs.gov.au", "https://data.api.abs.gov.au/rest"]
ACCEPT = "application/vnd.sdmx.structure+json"
CANDIDATES = ["HSI_M", "CPI", "LF"]          # our hypotheses
KEYWORDS = ["household spending", "consumer price", "labour force"]

def fetch(path):
    last = None
    for h in HOSTS:
        url = h + path
        req = urllib.request.Request(url, headers={"Accept": ACCEPT,
                                                    "User-Agent": "acp-explore/0.1"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return url, json.loads(r.read().decode("utf-8"))
        except Exception as e:                # noqa
            last = f"{url} -> {type(e).__name__}: {e}"
    raise RuntimeError("all hosts failed :: " + str(last))

def name_of(x):
    """SDMX names come as a plain string or a {locale: text} dict."""
    if isinstance(x, dict):
        return x.get("en") or next(iter(x.values()), "")
    return x or ""

def codelist_codes(data, cl_id):
    for cl in data.get("codelists", []):
        if cl.get("id") == cl_id:
            return [(c.get("id"), name_of(c.get("name"))) for c in cl.get("codes", [])]
    return []

# ---------------------------------------------------------------- 1. ID sweep
print("#" * 72)
print("# STEP A — every ABS dataflow whose name matches our three topics")
print("#" * 72)
try:
    url, js = fetch("/dataflow/ABS?detail=allstubs")
    flows = js["data"]["dataflows"]
    print(f"(total dataflows published: {len(flows)}, via {url.split('?')[0]})\n")
    for df in flows:
        nm = name_of(df.get("name"))
        if any(k in nm.lower() for k in KEYWORDS):
            print(f"  ID = {df.get('id'):<14} v{df.get('version'):<8} {nm}")
except Exception as e:
    print("  ID sweep failed:", e)

# ---------------------------------------------------------- 2. dimension dump
def describe(flow):
    print("\n" + "=" * 72)
    print(f"DATAFLOW: {flow}")
    print("=" * 72)
    try:
        url, js = fetch(f"/dataflow/ABS/{flow}?references=all")
    except Exception as e:
        print(f"  NOT AVAILABLE as 'ABS/{flow}': {e}")
        return
    data = js["data"]
    df = data["dataflows"][0]
    print(f"  name    : {name_of(df.get('name'))}")
    print(f"  version : {df.get('version')}")
    dsd = (data.get("dataStructures") or [None])[0]
    if not dsd:
        print("  (no data structure returned)"); return
    dl = dsd["dataStructureComponents"]["dimensionList"]
    dims = sorted(dl.get("dimensions", []), key=lambda d: d.get("position", 0))
    print(f"  dataKey order: {'.'.join(d['id'] for d in dims)}   (+ TIME_PERIOD)")
    for d in dims:
        enum = d.get("localRepresentation", {}).get("enumeration")
        cl_id = None
        if isinstance(enum, str) and "Codelist=" in enum:
            cl_id = enum.split("Codelist=")[1].split("(")[0].split(":")[-1]
        elif isinstance(enum, dict):
            cl_id = enum.get("id")
        codes = codelist_codes(data, cl_id) if cl_id else []
        print(f"\n   • dim {d.get('position')}: {d['id']}  "
              f"(codelist {cl_id}, {len(codes)} codes)")
        shown = codes if len(codes) <= 40 else codes[:15]
        for cid, cname in shown:
            print(f"        {cid:<12} {cname}")
        if len(codes) > 40:
            print(f"        ... (+{len(codes)-15} more)")

for f in CANDIDATES:
    describe(f)

print("\nDONE. Copy this whole output back to your mentor.")
