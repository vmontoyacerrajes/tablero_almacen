# backend/tools/export_origen_excel.py
import sys
import os
import json
import datetime as dt
from typing import Any, Dict, List, Optional

import certifi
import requests
import pandas as pd


# -------------------------
# URL / JSON coerce
# -------------------------
def json_url_for_month(month_yyyy_mm: str) -> str:
    tmpl = os.getenv("DASH_JSON_URL_TEMPLATE", "https://mov.cerraco.mx/temp/dash/{yyyy}-{mm}.json")
    yyyy, mm = month_yyyy_mm.split("-")
    return tmpl.replace("{yyyy}", yyyy).replace("{mm}", mm)

def find_first_list_of_dicts(obj: Any) -> Optional[List[Dict[str, Any]]]:
    if isinstance(obj, list):
        if not obj or all(isinstance(x, dict) for x in obj):
            return obj
        for x in obj:
            r = find_first_list_of_dicts(x)
            if r is not None:
                return r
        return None
    if isinstance(obj, dict):
        preferred = ("rest","rows","data","items","result","registros","records","tablero","dash","detalle","payload","list")
        for k in preferred:
            if k in obj:
                v = obj[k]
                if isinstance(v, str):
                    try:
                        v = json.loads(v)
                    except Exception:
                        pass
                r = find_first_list_of_dicts(v)
                if r is not None:
                    return r
        for v in obj.values():
            if isinstance(v, str):
                try:
                    v = json.loads(v)
                except Exception:
                    pass
            r = find_first_list_of_dicts(v)
            if r is not None:
                return r
    return None

def coerce_rows(raw: Any) -> List[Dict[str, Any]]:
    # respeta JSON_LIST_KEY si existe
    key = (os.getenv("JSON_LIST_KEY") or "").strip()
    if key:
        cur = raw
        try:
            for part in key.split("."):
                if isinstance(cur, str):
                    cur = json.loads(cur)
                cur = cur[part]
            if isinstance(cur, str):
                cur = json.loads(cur)
            if isinstance(cur, list) and (not cur or isinstance(cur[0], dict)):
                return cur
        except Exception:
            pass

    if isinstance(raw, list) and (not raw or isinstance(raw[0], dict)):
        return raw
    found = find_first_list_of_dicts(raw)
    if found is None:
        raise RuntimeError("No se pudo detectar lista de registros dentro del JSON.")
    return found

def fetch_month_rows(month_yyyy_mm: str) -> List[Dict[str, Any]]:
    url = json_url_for_month(month_yyyy_mm)
    r = requests.get(url, timeout=30, verify=certifi.where(), headers={"User-Agent":"tablero-export/1.0"})
    r.raise_for_status()
    raw = r.json()
    return coerce_rows(raw)


# -------------------------
# Parse helpers (mínimos para auditoría)
# -------------------------
MONTHS_ES = {
    "ene":1,"enero":1,
    "feb":2,"febrero":2,
    "mar":3,"marzo":3,
    "abr":4,"abril":4,
    "may":5,"mayo":5,
    "jun":6,"junio":6,
    "jul":7,"julio":7,
    "ago":8,"agosto":8,
    "sep":9,"sept":9,"septiembre":9,"setiembre":9,
    "oct":10,"octubre":10,
    "nov":11,"noviembre":11,
    "dic":12,"diciembre":12,
}

NULL_STRINGS = {"0000-00-00","0000-00-00 00:00:00","null","none","nan",""}

def norm_str(v: Any) -> str:
    return " ".join(str(v).strip().split())

def has_value(v: Any) -> bool:
    if v is None:
        return False
    s = norm_str(v).lower()
    return s not in NULL_STRINGS

def parse_date_any(v: Any, default_year: Optional[int]=None) -> Optional[dt.date]:
    if v is None:
        return None
    if isinstance(v, dt.date) and not isinstance(v, dt.datetime):
        return v
    if isinstance(v, dt.datetime):
        return v.date()
    s = norm_str(v)
    if not s or s.lower() in NULL_STRINGS:
        return None

    fmts = ("%Y-%m-%d","%Y/%m/%d","%d-%m-%Y","%d/%m/%Y","%Y-%m-%d %H:%M:%S","%Y/%m/%d %H:%M:%S")
    for fmt in fmts:
        try:
            return dt.datetime.strptime(s, fmt).date()
        except Exception:
            pass

    # formatos tipo "06 ene", "7 ENE", "04 FEB"
    parts = s.replace(",", " ").split()
    if len(parts) >= 2:
        try:
            dd = int(parts[0])
            mon = "".join([c for c in parts[1].lower() if c.isalpha()])
            mm = MONTHS_ES.get(mon)
            if mm and default_year:
                return dt.date(int(default_year), int(mm), int(dd))
        except Exception:
            pass

    return None

def parse_time_hhmm(v: Any) -> Optional[dt.time]:
    if v is None:
        return None
    s = norm_str(v)
    if not s or s.lower() in NULL_STRINGS:
        return None
    # "1554" -> 15:54
    if s.isdigit():
        n = int(s)
        if n < 100:
            return dt.time(n,0,0)
        h, m = divmod(n, 100)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return dt.time(h,m,0)
    # "15:54:00"
    if ":" in s:
        try:
            parts = s.split(":")
            h = int(parts[0]); m = int(parts[1]); sec = int(parts[2]) if len(parts)>2 else 0
            return dt.time(h,m,sec)
        except Exception:
            return None
    return None

def parse_datetime_any(v: Any, default_year: Optional[int]=None) -> Optional[dt.datetime]:
    if v is None:
        return None
    if isinstance(v, dt.datetime):
        return v
    if isinstance(v, dt.date):
        return dt.datetime(v.year, v.month, v.day)
    s = norm_str(v)
    if not s or s.lower() in NULL_STRINGS:
        return None
    fmts = ("%Y-%m-%d %H:%M:%S","%Y/%m/%d %H:%M:%S","%Y-%m-%dT%H:%M:%S","%Y-%m-%d")
    for fmt in fmts:
        try:
            x = dt.datetime.strptime(s, fmt)
            if fmt == "%Y-%m-%d":
                return dt.datetime(x.year, x.month, x.day)
            return x
        except Exception:
            pass
    # "06 ene" -> 00:00
    d = parse_date_any(s, default_year=default_year)
    if d:
        return dt.datetime(d.year, d.month, d.day)
    return None

def merge_date_time(date_v: Any, time_v: Any, default_year: Optional[int]=None) -> Optional[dt.datetime]:
    # si date_v ya trae datetime, úsalo
    dtm = parse_datetime_any(date_v, default_year=default_year)
    if dtm and (dtm.hour or dtm.minute or dtm.second):
        return dtm
    d = parse_date_any(date_v, default_year=default_year)
    if not d:
        return None
    t = parse_time_hhmm(time_v)
    if not t:
        return dt.datetime(d.year, d.month, d.day)
    return dt.datetime(d.year, d.month, d.day, t.hour, t.minute, t.second)

def operational_day_1305(dtm: Optional[dt.datetime]) -> Optional[dt.date]:
    if not dtm:
        return None
    d = dtm.date()
    hhmm = dtm.hour*100 + dtm.minute
    if hhmm >= 1305:
        d = d + dt.timedelta(days=1)
    return d


# -------------------------
# Export
# -------------------------
def main():
    if len(sys.argv) < 2:
        print("Uso: python /app/tools/export_origen_excel.py YYYY-MM")
        sys.exit(2)

    month = sys.argv[1].strip()
    yyyy, mm = month.split("-")
    default_year = int(yyyy)

    rows = fetch_month_rows(month)

    out = []
    for r in rows:
        pedido = r.get("pedido")
        fecha_alta_raw = r.get("fecha")
        num_part_raw = r.get("num_part")

        ent_fec_cco_raw = r.get("ent_fec_cco")
        ent_tim_cco_raw = r.get("ent_tim_cco")
        fecha_verificacion_raw = r.get("fecha_verificacion")

        fecha_salida_raw = r.get("fecha_salida")
        hora_salida_raw = r.get("hora_salida")

        alta_dtm = parse_datetime_any(fecha_alta_raw, default_year=default_year)
        surt_dtm = merge_date_time(ent_fec_cco_raw, ent_tim_cco_raw, default_year=default_year)
        verif_dtm = parse_datetime_any(fecha_verificacion_raw, default_year=default_year)
        emb_dtm = merge_date_time(fecha_salida_raw, hora_salida_raw, default_year=default_year)

        out.append({
            "pedido": pedido,
            "fecha_alta_raw": fecha_alta_raw,
            "num_part_raw": num_part_raw,
            "ent_fec_cco_raw": ent_fec_cco_raw,
            "ent_tim_cco_raw": ent_tim_cco_raw,
            "fecha_verificacion_raw": fecha_verificacion_raw,
            "fecha_salida_raw": fecha_salida_raw,
            "hora_salida_raw": hora_salida_raw,

            "alta_day": operational_day_1305(alta_dtm),  # base por regla 13:05 (auditoría)
            "surt_day_operativo_1305": operational_day_1305(surt_dtm),  # auditoría
            "verif_day_operativo_1305": operational_day_1305(verif_dtm), # auditoría
            "emb_day_calendario": (emb_dtm.date() if emb_dtm else None),

            "alta_dtm": alta_dtm,
            "surt_dtm": surt_dtm,
            "verif_dtm": verif_dtm,
            "emb_dtm": emb_dtm,
        })

    df = pd.DataFrame(out)

    # salida
    os.makedirs("/app/exports", exist_ok=True)
    xlsx_path = f"/app/exports/origen_{month}.xlsx"
    csv_path  = f"/app/exports/origen_{month}.csv"

    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name="origen")
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    print("OK")
    print("XLSX:", xlsx_path)
    print("CSV :", csv_path)
    print("rows:", len(df))

if __name__ == "__main__":
    main()