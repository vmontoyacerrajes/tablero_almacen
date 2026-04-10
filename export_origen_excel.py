#!/usr/bin/env python3
import sys
import json
import re
import datetime as dt
from typing import Any, Dict, List, Optional, Tuple

import requests
import pandas as pd

# ----------------------------
# Helpers: find list of dicts
# ----------------------------
PREFERRED_KEYS = (
    "rest","rows","data","items","result","registros","records",
    "tablero","dash","detalle","payload","list"
)

def _find_lists(obj: Any) -> List[List[Dict[str, Any]]]:
    found: List[List[Dict[str, Any]]] = []
    if isinstance(obj, list):
        if all(isinstance(x, dict) for x in obj):
            found.append(obj)
        for x in obj:
            found.extend(_find_lists(x))
    elif isinstance(obj, dict):
        # preferred first
        for k in PREFERRED_KEYS:
            if k in obj:
                v = obj[k]
                if isinstance(v, str):
                    try:
                        v = json.loads(v)
                    except Exception:
                        pass
                found.extend(_find_lists(v))
        # then all
        for v in obj.values():
            if isinstance(v, str):
                try:
                    v = json.loads(v)
                except Exception:
                    pass
            found.extend(_find_lists(v))
    return found

def coerce_rows(raw: Any) -> List[Dict[str, Any]]:
    # if raw is string json
    if isinstance(raw, str):
        raw = json.loads(raw)
    if isinstance(raw, list) and all(isinstance(x, dict) for x in raw):
        return raw
    lists = _find_lists(raw)
    if not lists:
        raise RuntimeError("No encontré ninguna lista de objetos en el JSON.")
    # pick biggest
    lists.sort(key=lambda a: len(a), reverse=True)
    return lists[0]

# ----------------------------
# Date parsing (same spirit as your backend)
# ----------------------------
_MONTHS_ES = {
    "enero":1,"febrero":2,"marzo":3,"abril":4,"mayo":5,"junio":6,
    "julio":7,"agosto":8,"septiembre":9,"setiembre":9,"octubre":10,
    "noviembre":11,"diciembre":12,
    "ene":1,"feb":2,"mar":3,"abr":4,"may":5,"jun":6,"jul":7,"ago":8,
    "sep":9,"sept":9,"oct":10,"nov":11,"dic":12,
}
_NULLS = {"", "null", "none", "nan", "0000-00-00", "0000-00-00 00:00:00"}

def norm_str(v: Any) -> str:
    return " ".join(str(v).strip().split())

def has_value(v: Any) -> bool:
    if v is None:
        return False
    s = norm_str(v).lower()
    return s not in _NULLS and s != ""

def clean_month_token(tok: str) -> str:
    tok = tok.lower()
    tok = re.sub(r"[^a-zñ]", "", tok)
    return tok

def parse_datetime_any(v: Any, default_year: Optional[int]=None, default_month: Optional[int]=None) -> Optional[dt.datetime]:
    if v is None:
        return None
    if isinstance(v, dt.datetime):
        return v
    if isinstance(v, dt.date):
        return dt.datetime(v.year, v.month, v.day)
    s = norm_str(v)
    if not s or s.lower() in _NULLS:
        return None

    for fmt in (
        "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%d/%m/%Y %H:%M:%S", "%d-%m-%Y %H:%M:%S",
        "%d/%m/%y %H:%M:%S", "%d-%m-%y %H:%M:%S",
        "%Y-%m-%d", "%Y/%m/%d",
        "%d/%m/%Y", "%d-%m-%Y",
        "%d/%m/%y", "%d-%m-%y",
    ):
        try:
            return dt.datetime.strptime(s, fmt)
        except Exception:
            pass

    # "05 ene 2026 15:46:28" or "05 ene 15:46:28" (sin año)
    m = re.match(r"^(\d{1,2})\s+([A-Za-zñÑ\.\- ]+)\s+(\d{2,4})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?$", s, re.I)
    if m:
        dd = int(m.group(1))
        mon = clean_month_token(m.group(2))
        yy = int(m.group(3))
        if yy < 100: yy = 2000 + yy
        hh = int(m.group(4) or 0); mm = int(m.group(5) or 0); ss = int(m.group(6) or 0)
        if mon in _MONTHS_ES:
            return dt.datetime(yy, _MONTHS_ES[mon], dd, hh, mm, ss)

    m2 = re.match(r"^(\d{1,2})\s+([A-Za-zñÑ\.\- ]+)(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?$", s, re.I)
    if m2 and default_year is not None:
        dd = int(m2.group(1))
        mon = clean_month_token(m2.group(2))
        hh = int(m2.group(3) or 0); mm = int(m2.group(4) or 0); ss = int(m2.group(5) or 0)
        if mon in _MONTHS_ES:
            return dt.datetime(int(default_year), _MONTHS_ES[mon], dd, hh, mm, ss)

    return None

def parse_time_any(v: Any) -> Optional[Tuple[int,int,int]]:
    if v is None:
        return None
    s = norm_str(v)
    if not s or s.lower() in _NULLS:
        return None
    if ":" in s:
        parts = s.split(":")
        try:
            h = int(parts[0]); m = int(parts[1]); ss = int(parts[2]) if len(parts) > 2 else 0
            return (h,m,ss)
        except Exception:
            return None
    if s.isdigit():
        try:
            n = int(s)
            if n < 100:
                return (n,0,0)
            h, m = divmod(n, 100)
            return (h,m,0)
        except Exception:
            return None
    return None

def merge_date_time(date_v: Any, time_v: Any, default_year: Optional[int]=None, default_month: Optional[int]=None) -> Optional[dt.datetime]:
    dtm = parse_datetime_any(date_v, default_year=default_year, default_month=default_month)
    if dtm and (dtm.hour or dtm.minute or dtm.second):
        return dtm
    d = parse_datetime_any(date_v, default_year=default_year, default_month=default_month)
    if not d:
        return None
    t = parse_time_any(time_v)
    if not t:
        return dt.datetime(d.year, d.month, d.day)
    return dt.datetime(d.year, d.month, d.day, t[0], t[1], t[2])

# ----------------------------
# Extraction (raw keys you care about)
# ----------------------------
def get(rec: Dict[str, Any], *names: str) -> Any:
    for n in names:
        if n in rec and has_value(rec[n]):
            return rec[n]
    return None

def event_day_operativo(dtm: dt.datetime) -> dt.date:
    # 13:05 cutoff (solo para surt/verif y base), NO aplica a embarque en este script (solo queremos ver el origen)
    d = dtm.date()
    hhmm = dtm.hour*100 + dtm.minute
    if hhmm >= 1305:
        d = d + dt.timedelta(days=1)
    return d

def main():
    if len(sys.argv) < 2:
        print("Uso: python export_origen_excel.py 2026-01 [URL_BASE]")
        print("Ej:  python export_origen_excel.py 2026-01 https://mov.cerraco.mx/temp/dash/2026-01.json")
        sys.exit(1)

    ym = sys.argv[1].strip()  # YYYY-MM
    if len(sys.argv) >= 3:
        url = sys.argv[2].strip()
    else:
        url = f"https://mov.cerraco.mx/temp/dash/{ym}.json"

    year = int(ym.split("-")[0])
    month = int(ym.split("-")[1])

    print("Descargando:", url)
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    raw = r.json()

    rows = coerce_rows(raw)
    print("rows:", len(rows))

    # Build a dataframe with the key fields
    out_rows = []
    for rec in rows:
        pedido = get(rec, "pedido","pedido_cco","ped_cco","pedCCO","id_pedido")
        fecha_alta = get(rec, "fecha","fecha_alta","falta","created_at")
        num_part = get(rec, "num_part","part_cco","partCCO","numPart","partidas")

        ent_fec_cco = get(rec, "ent_fec_cco")
        ent_tim_cco = get(rec, "ent_tim_cco")

        fecha_verificacion = get(rec, "fecha_verificacion")
        fecha_salida = get(rec, "fecha_salida","f_salida","sal_fec","fsalida","fechaSalida","salida_fecha")
        hora_salida  = get(rec, "hora_salida","sal_tim","fsalida_hora","horaSalida","salida_hora")

        # Parse datetimes (sin tocar inhábiles aquí, solo para ver si “caen” en 2026-01-05)
        alta_dtm = parse_datetime_any(fecha_alta, default_year=year, default_month=month)
        surt_dtm = merge_date_time(ent_fec_cco, ent_tim_cco, default_year=year, default_month=month)
        ver_dtm  = parse_datetime_any(fecha_verificacion, default_year=year, default_month=month)
        emb_dtm  = merge_date_time(fecha_salida, hora_salida, default_year=year, default_month=month)

        alta_day = alta_dtm.date().isoformat() if alta_dtm else None
        surt_day_op = event_day_operativo(surt_dtm).isoformat() if surt_dtm else None
        ver_day_op  = event_day_operativo(ver_dtm).isoformat()  if ver_dtm else None
        emb_day_cal = emb_dtm.date().isoformat() if emb_dtm else None

        out_rows.append({
            "pedido": pedido,
            "fecha_alta_raw": fecha_alta,
            "num_part_raw": num_part,

            "ent_fec_cco_raw": ent_fec_cco,
            "ent_tim_cco_raw": ent_tim_cco,
            "fecha_verificacion_raw": fecha_verificacion,
            "fecha_salida_raw": fecha_salida,
            "hora_salida_raw": hora_salida,

            "alta_day": alta_day,
            "surt_day_operativo_1305": surt_day_op,
            "verif_day_operativo_1305": ver_day_op,
            "emb_day_calendario": emb_day_cal,

            "alta_dtm": str(alta_dtm) if alta_dtm else None,
            "surt_dtm": str(surt_dtm) if surt_dtm else None,
            "verif_dtm": str(ver_dtm) if ver_dtm else None,
            "emb_dtm": str(emb_dtm) if emb_dtm else None,
        })

    df = pd.DataFrame(out_rows)

    # Sheet: rows that match Jan-05 in any dimension
    target_day = f"{year:04d}-{month:02d}-05"
    df_day = df[
        (df["alta_day"] == target_day) |
        (df["surt_day_operativo_1305"] == target_day) |
        (df["verif_day_operativo_1305"] == target_day) |
        (df["emb_day_calendario"] == target_day)
    ].copy()

    # counts by day
    def counts(col: str, label: str) -> pd.DataFrame:
        c = df[col].value_counts(dropna=True).rename(label)
        return c.to_frame()

    c_alta = counts("alta_day", "alta_rows")
    c_surt = counts("surt_day_operativo_1305", "surt_rows_operativo_1305")
    c_ver  = counts("verif_day_operativo_1305", "verif_rows_operativo_1305")
    c_emb  = counts("emb_day_calendario", "emb_rows_calendario")

    df_counts = c_alta.join(c_surt, how="outer").join(c_ver, how="outer").join(c_emb, how="outer").fillna(0).astype(int)
    df_counts = df_counts.sort_index()

    out_xlsx = f"origen_{ym}.xlsx"
    with pd.ExcelWriter(out_xlsx, engine="openpyxl") as w:
        # raw_rows (limit columns to the ones we care about)
        df.to_excel(w, index=False, sheet_name="raw_rows")
        df_day.to_excel(w, index=False, sheet_name=f"day_{target_day}")
        df_counts.to_excel(w, sheet_name="counts_by_day")

    print("OK ->", out_xlsx)

if __name__ == "__main__":
    main()