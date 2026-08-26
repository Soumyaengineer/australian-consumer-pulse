"""
02_probe_cpi_m.py — check whether the legacy Monthly CPI Indicator (CPI_M)
gives us a continuous monthly All-groups history back to 2019, so we can use
it to extend the (short) complete monthly CPI. Read-only probe.
"""
import json, io, requests, pandas as pd

HOSTS = ["https://api.data.abs.gov.au", "https://data.api.abs.gov.au/rest"]
def http_get(path, accept):
    last=None
    for h in HOSTS:
        u=h+path
        try:
            r=requests.get(u, headers={"Accept":accept,"User-Agent":"acp/0.1"}, timeout=90)
            if r.status_code==200 and r.text.strip(): return u,r.text
            last=f"{u} -> HTTP {r.status_code} {r.text[:100]!r}"
        except Exception as e: last=f"{u} -> {type(e).__name__}: {e}"
    raise RuntimeError("all hosts failed :: "+str(last))

def structure(flow):
    _,t=http_get(f"/dataflow/ABS/{flow}?references=all", "application/vnd.sdmx.structure+json")
    d=json.loads(t)["data"]
    dims=sorted(d["dataStructures"][0]["dataStructureComponents"]["dimensionList"]["dimensions"],
                key=lambda x:x.get("position",0))
    dim_cl={}
    for dm in dims:
        e=dm.get("localRepresentation",{}).get("enumeration"); cl=None
        if isinstance(e,str) and "Codelist=" in e: cl=e.split("Codelist=")[1].split("(")[0].split(":")[-1]
        elif isinstance(e,dict): cl=e.get("id")
        dim_cl[dm["id"]]=cl
    codes={cl["id"]:{c["id"]:(c.get("name") if isinstance(c.get("name"),str)
            else next(iter((c.get("name") or {}).values()),"")) for c in cl.get("codes",[])}
            for cl in d.get("codelists",[])}
    return d["dataflows"][0].get("version"),[x["id"] for x in dims],dim_cl,codes

ver,order,dim_cl,codes=structure("CPI_M")
print(f"CPI_M version={ver}")
print("dims:", ".".join(order))

# show INDEX codes that look like 'all groups'
idx_cl=dim_cl.get("INDEX"); meas_cl=dim_cl.get("MEASURE"); tsest_cl=dim_cl.get("TSEST")
print("\nINDEX codes containing 'group':")
allg=None
for cid,nm in codes.get(idx_cl,{}).items():
    if "group" in nm.lower():
        print(f"   {cid:8s} {nm}")
        if nm.strip().lower().startswith("all groups"): allg=cid
print("\nMEASURE codes:")
meas=None
for cid,nm in codes.get(meas_cl,{}).items():
    print(f"   {cid:6s} {nm}")
    if "index" in nm.lower() and meas is None: meas=cid
print("\nTSEST codes:", {c:n for c,n in codes.get(tsest_cl,{}).items()})

# build a key in CPI_M's own dim order
allg = "10001" if "10001" in codes.get(idx_cl,{}) else allg  # canonical headline All groups CPI
want={"MEASURE":meas or "1","INDEX":allg or "10001","TSEST":"10","FREQ":"M"}  # REGION blank=all
key=".".join(want.get(d,"") for d in order)
print(f"\nChosen -> MEASURE={want['MEASURE']} INDEX(all groups)={want['INDEX']} key={key}")

u,t=http_get(f"/data/CPI_M/{key}?startPeriod=2019-01","application/vnd.sdmx.data+csv")
df=pd.read_csv(io.StringIO(t))
d=pd.PeriodIndex(df["TIME_PERIOD"].astype(str),freq="M").to_timestamp()
print(f"\nCPI_M All-groups pull: rows={len(df)}  {d.min():%Y-%m} -> {d.max():%Y-%m}")
reg_col="REGION" if "REGION" in df.columns else [c for c in df.columns if c.upper()=="REGION"]
if "REGION" in df.columns:
    print("regions present:", sorted(df["REGION"].astype(str).unique()))
print("\n--> If this spans 2019-01 to ~now, CPI_M is our long monthly deflator.")
