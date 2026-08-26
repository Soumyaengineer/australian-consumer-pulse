"""
01_pull_abs.py  —  Australian Consumer Pulse: reproducible ABS Data API pull

Pulls three ABS series from the free SDMX Data API (no key), tidies them into
long format, and stages them as CSVs in ../raw/ (git-ignored). Also writes a
provenance JSON so the exact IDs/versions/keys/timestamp are recorded.

Series & why (see README for the full reasoning):
  HSI_M  Monthly Household Spending Indicator — nominal $ spending, all
         categories, all states. We deflate this by CPI ourselves.
  CPI    Consumer Price Index (v2), monthly (FREQ=M), All groups, by region.
         NOTE: CPI has no state breakdown, only capital cities + Australia, so
         each state is deflated by its capital-city CPI (a documented proxy).
  LF     Labour Force — unemployment & participation rate, monthly, by state
         (contextual: does spending track the labour market?).

Run from anywhere:  python3 ingest/01_pull_abs.py
Requires: requests, pandas  (present in a standard Anaconda base env).
"""
from __future__ import annotations
import io, os, sys, json, datetime as dt
import requests
import pandas as pd

# ----------------------------------------------------------------- config
HOSTS = ["https://api.data.abs.gov.au", "https://data.api.abs.gov.au/rest"]
START = "2019-01"                      # MHSI monthly effectively starts here
STRUCT_ACCEPT = "application/vnd.sdmx.structure+json"
DATA_ACCEPT   = "application/vnd.sdmx.data+csv"
HERE = os.path.dirname(os.path.abspath(__file__))
RAW  = os.path.normpath(os.path.join(HERE, "..", "raw"))
os.makedirs(RAW, exist_ok=True)

# Capital-city CPI region  ->  state (code, name). The defensible NSW=Sydney proxy.
CITY_TO_STATE = {
    "50": ("AUS", "Australia"),      "1": ("1", "New South Wales"),
    "2":  ("2",   "Victoria"),       "3": ("3", "Queensland"),
    "4":  ("4",   "South Australia"), "5": ("5", "Western Australia"),
    "6":  ("6",   "Tasmania"),       "7": ("7", "Northern Territory"),
    "8":  ("8",   "Australian Capital Territory"),
}

# dataKey per flow (dot-separated, blank = wildcard/all), in the dimension order
# HSI_M : MEASURE.CATEGORY.PRICE_ADJUSTMENT.TSEST.STATE.FREQ
# CPI   : MEASURE.INDEX.TSEST.REGION.FREQ
# LF    : MEASURE.SEX.AGE.TSEST.REGION.FREQ
PULLS = {
    "HSI_M_nominal": dict(flow="HSI_M", key="7..CUR.10..M"),   # $ spend, all cats/states, Original
    "HSI_M_cvm_chk": dict(flow="HSI_M", key="7.TOT.CVM.10..M"),# ABS real (Total) — cross-check only
    "CPI":           dict(flow="CPI",   key="1.10001.10..M"),  # All-groups index, all regions
    "LF":            dict(flow="LF",     key="M12+M13.3.1599.20..M"),  # partic.+unemp rate, SA
}

# --------------------------------------------------------------- http helpers
def http_get(path: str, accept: str) -> tuple[str, str]:
    """Try each host; return (url, text) from the first that works."""
    last = None
    for h in HOSTS:
        url = h + path
        try:
            r = requests.get(url, headers={"Accept": accept,
                                           "User-Agent": "acp-pull/0.1"}, timeout=90)
            if r.status_code == 200 and r.text.strip():
                return url, r.text
            last = f"{url} -> HTTP {r.status_code} {r.text[:120]!r}"
        except Exception as e:                                    # noqa
            last = f"{url} -> {type(e).__name__}: {e}"
    raise RuntimeError("all hosts failed :: " + str(last))

def get_structure(flow: str) -> dict:
    """Return {version, dim_order, dim_codelist{dim:cl}, codes{cl:{code:name}}}."""
    _, txt = http_get(f"/dataflow/ABS/{flow}?references=all", STRUCT_ACCEPT)
    data = json.loads(txt)["data"]
    ver = data["dataflows"][0].get("version")
    dsd = data["dataStructures"][0]["dataStructureComponents"]["dimensionList"]
    dims = sorted(dsd.get("dimensions", []), key=lambda d: d.get("position", 0))
    dim_cl = {}
    for d in dims:
        enum = d.get("localRepresentation", {}).get("enumeration")
        cl = None
        if isinstance(enum, str) and "Codelist=" in enum:
            cl = enum.split("Codelist=")[1].split("(")[0].split(":")[-1]
        elif isinstance(enum, dict):
            cl = enum.get("id")
        dim_cl[d["id"]] = cl
    codes = {}
    for cl in data.get("codelists", []):
        codes[cl["id"]] = {c["id"]: (c.get("name") if isinstance(c.get("name"), str)
                                     else next(iter((c.get("name") or {}).values()), ""))
                           for c in cl.get("codes", [])}
    return dict(version=ver, dim_order=[d["id"] for d in dims],
                dim_codelist=dim_cl, codes=codes)

def pull_data(flow: str, key: str) -> pd.DataFrame:
    url, txt = http_get(f"/data/{flow}/{key}?startPeriod={START}", DATA_ACCEPT)
    try:
        df = pd.read_csv(io.StringIO(txt))
    except Exception as e:
        raise RuntimeError(f"could not parse CSV from {url}: {e}\nfirst 300 chars:\n{txt[:300]}")
    df.attrs["url"] = url
    return df

def to_month(s: pd.Series) -> pd.Series:
    return pd.PeriodIndex(s.astype(str), freq="M").to_timestamp()

# --------------------------------------------------------------------- run
prov = {"pulled_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "api_base_tried": HOSTS, "start_period": START, "series": {}}
def note(tag, flow, ver, url, df, extra=""):
    prov["series"][tag] = dict(dataflow=flow, version=ver, rows=int(len(df)),
                               url=url, extra=extra)

print("=" * 68, "\nABS pull starting\n", "=" * 68, sep="")

# --- structures (labels) ---
struct = {}
for flow in {"HSI_M", "CPI", "CPI_M", "LF"}:
    struct[flow] = get_structure(flow)
    print(f"structure {flow}: v{struct[flow]['version']}  dims={'.'.join(struct[flow]['dim_order'])}")

def label(flow, dim, code):
    cl = struct[flow]["dim_codelist"].get(dim)
    return struct[flow]["codes"].get(cl, {}).get(str(code), "")

# ---------- HSI_M (nominal, all categories/states) ----------
raw = pull_data(**{k: PULLS["HSI_M_nominal"][k] for k in ("flow", "key")})
hsi = pd.DataFrame({
    "series": "HSI_M",
    "date": to_month(raw["TIME_PERIOD"]),
    "state_code": raw["STATE"].astype(str),
    "state_name": [label("HSI_M", "STATE", c) for c in raw["STATE"]],
    "category_code": raw["CATEGORY"].astype(str),
    "category_name": [label("HSI_M", "CATEGORY", c) for c in raw["CATEGORY"]],
    "price_adjustment": raw["PRICE_ADJUSTMENT"].astype(str),
    "measure_code": raw["MEASURE"].astype(str),
    "value": pd.to_numeric(raw["OBS_VALUE"], errors="coerce"),
})
hsi.to_csv(os.path.join(RAW, "hsi_m.csv"), index=False)
note("HSI_M_nominal", "HSI_M", struct["HSI_M"]["version"], raw.attrs["url"], hsi,
     extra="MEASURE=7 (household spending $), PRICE_ADJUSTMENT=CUR, TSEST=10 (Original), FREQ=M")

# ---------- HSI_M CVM cross-check (Total only; OPTIONAL, non-fatal) ----------
cvm = None
for cvm_key in ("7.TOT.CVM.10..M", "10.TOT.CVM.10..M", "7.TOT.CVM.20..M", "10.TOT.CVM.20..M"):
    try:
        rawc = pull_data("HSI_M", cvm_key)
    except RuntimeError:
        continue
    cvm = pd.DataFrame({
        "series": "HSI_M", "date": to_month(rawc["TIME_PERIOD"]),
        "state_code": rawc["STATE"].astype(str),
        "state_name": [label("HSI_M", "STATE", c) for c in rawc["STATE"]],
        "category_code": "TOT", "price_adjustment": "CVM",
        "value": pd.to_numeric(rawc["OBS_VALUE"], errors="coerce"),
    })
    cvm.to_csv(os.path.join(RAW, "hsi_m_cvm_check.csv"), index=False)
    note("HSI_M_cvm_chk", "HSI_M", struct["HSI_M"]["version"], rawc.attrs["url"], cvm,
         extra=f"ABS chain volume (real) Total via key {cvm_key}, cross-check")
    print(f"  CVM cross-check OK via key {cvm_key}")
    break
if cvm is None:
    print("  NOTE: no CVM (ABS real) cross-check series available - proceeding without it (non-essential).")

# ---------- CPI (All groups, monthly, by region -> mapped to state) ----------
rawp = pull_data(**{k: PULLS["CPI"][k] for k in ("flow", "key")})
reg = rawp["REGION"].astype(str)
cpi = pd.DataFrame({
    "series": "CPI", "date": to_month(rawp["TIME_PERIOD"]),
    "city_code": reg,
    "city_name": [label("CPI", "REGION", c) for c in reg],
    "state_code": [CITY_TO_STATE.get(c, ("?", "?"))[0] for c in reg],
    "state_name": [CITY_TO_STATE.get(c, ("?", "?"))[1] for c in reg],
    "index_code": rawp["INDEX"].astype(str),
    "index_name": [label("CPI", "INDEX", c) for c in rawp["INDEX"]],
    "measure_code": rawp["MEASURE"].astype(str),
    "value": pd.to_numeric(rawp["OBS_VALUE"], errors="coerce"),
})
if "BASE_PERIOD" in rawp.columns:
    cpi["base_period"] = rawp["BASE_PERIOD"].astype(str).values
cpi.to_csv(os.path.join(RAW, "cpi.csv"), index=False)
note("CPI", "CPI", struct["CPI"]["version"], rawp.attrs["url"], cpi,
     extra="MEASURE=1 (index), INDEX=10001 (All groups), TSEST=10, FREQ=M")

# ---------- CPI_M legacy monthly indicator (Australia only; extends history to 2019) ----------
rawm = pull_data("CPI_M", "1.10001.10..M")
cpim = pd.DataFrame({
    "series": "CPI_M", "date": to_month(rawm["TIME_PERIOD"]),
    "city_code": rawm["REGION"].astype(str),
    "state_code": [CITY_TO_STATE.get(str(c), ("?", "?"))[0] for c in rawm["REGION"]],
    "state_name": [CITY_TO_STATE.get(str(c), ("?", "?"))[1] for c in rawm["REGION"]],
    "index_code": rawm["INDEX"].astype(str),
    "index_name": [label("CPI_M", "INDEX", c) for c in rawm["INDEX"]],
    "measure_code": rawm["MEASURE"].astype(str),
    "value": pd.to_numeric(rawm["OBS_VALUE"], errors="coerce"),
})
cpim.to_csv(os.path.join(RAW, "cpi_m.csv"), index=False)
note("CPI_M", "CPI_M", struct["CPI_M"]["version"], rawm.attrs["url"], cpim,
     extra="Legacy Monthly CPI Indicator, All groups (10001), Australia, Original, monthly; extends CPI monthly back to 2019")

# ---------- LF (unemployment + participation rate, by state) ----------
rawl = pull_data(**{k: PULLS["LF"][k] for k in ("flow", "key")})
lf = pd.DataFrame({
    "series": "LF", "date": to_month(rawl["TIME_PERIOD"]),
    "state_code": rawl["REGION"].astype(str),
    "state_name": [label("LF", "REGION", c) for c in rawl["REGION"]],
    "measure_code": rawl["MEASURE"].astype(str),
    "measure_name": [label("LF", "MEASURE", c) for c in rawl["MEASURE"]],
    "value": pd.to_numeric(rawl["OBS_VALUE"], errors="coerce"),
})
lf.to_csv(os.path.join(RAW, "lf.csv"), index=False)
note("LF", "LF", struct["LF"]["version"], rawl.attrs["url"], lf,
     extra="MEASURE=M12 (participation) + M13 (unemployment rate), SA, FREQ=M")

# --------------------------------------------------------- provenance + report
with open(os.path.join(HERE, "_provenance.json"), "w") as f:
    json.dump(prov, f, indent=2)

def cov(name, df, by=None):
    d = df["date"]
    line = f"  {name:16s} rows={len(df):6d}  {d.min():%Y-%m} -> {d.max():%Y-%m}"
    if by:
        line += f"  {by}={df[by].nunique()}"
    print(line)

print("\n" + "=" * 68 + "\nCOVERAGE REPORT (paste this back to your mentor)\n" + "=" * 68)
cov("HSI_M nominal", hsi, "state_code"); print(f"      categories = {hsi['category_code'].nunique()}")
cov("HSI_M CVM chk", cvm, "state_code") if cvm is not None else print("  HSI_M CVM chk    (not available - skipped)")
cov("CPI",           cpi, "city_code")
print("      CPI regions present:",
      ", ".join(f"{c}:{n}" for c, n in cpi[['city_code','city_name']].drop_duplicates().values))
cov("CPI_M (legacy)", cpim, "state_code")
cov("LF",            lf,  "state_code")
print("      LF measures:", ", ".join(sorted(lf['measure_code'].unique())))
print("\nHSI category sample (code : name):")
for c, n in hsi[['category_code','category_name']].drop_duplicates().head(20).values:
    print(f"      {c:6s} {n}")
print(f"\nStaged CSVs in: {RAW}")
print("Provenance:", os.path.join(HERE, "_provenance.json"))
print("=" * 68)
