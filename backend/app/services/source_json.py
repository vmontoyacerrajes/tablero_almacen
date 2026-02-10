# app/services/source_json.py
from __future__ import annotations
import os, re, json, time, datetime as dt
from typing import Any, Dict, Iterable, List, Optional
import requests

# ---------- Config ----------
URL_TMPL = os.getenv("MOVING_DASH_URL_TMPL", "https://mov.cerraco.mx/temp/dash/{yyyy}-{mm}.json")
REQ_TIMEOUT = float(os.getenv("MOVING_DASH_TIMEOUT", "8"))
USE_JSON_SOURCE = os.getenv("USE_JSON_SOURCE", "false").lower() == "true"

# Cache simple en memoria por (mes) con TTL (600 s ~ 10 min)
_cache: Dict[str, Dict[str, Any]] = {}  # key -> {"ts": epoch, "data": list}

# ---------- Fechas: normalizador robusto ----------
# Acepta ISO, dd-mm-yyyy, dd/mm/yyyy, "septiembre 2025", "10 septiembre 2025", timestamps
MONTHS_ES = {
    "enero":1,"febrero":2,"marzo":3,"abril":4,"mayo":5,"junio":6,
    "julio":7,"agosto":8,"septiembre":9,"setiembre":9,"octubre":10,"noviembre":11,"diciembre":12
}
def _parse_date_any(v: Any) -> Optional[dt.date]:
    if v in (None, "", "null"):
        return None
    # timestamp (segundos o ms)
    try:
        if isinstance(v, (int, float)) or (isinstance(v, str) and re.fullmatch(r"\d{10,13}", v)):
            x = int(v)
            if x > 10_000_000_000:  # ms
                x = x // 1000
            return dt.datetime.utcfromtimestamp(x).date()
    except:  # noqa
        pass
    s = str(v).strip()

    # ISO-like
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%m-%y", "%d/%m/%y", "%Y-%m"):
        try:
            d = dt.datetime.strptime(s, fmt)
            if fmt == "%Y-%m":
                d = d.replace(day=1)
            return d.date()
        except:  # noqa
            pass

    # “10 septiembre 2025” / “septiembre 2025”
    m = re.match(r"(?i)^\s*(\d{1,2})\s+([a-záéíóú]+)\s+(\d{4})\s*$", s)
    if m:
        day, mon_text, year = int(m.group(1)), m.group(2).lower(), int(m.group(3))
        mon = MONTHS_ES.get(mon_text)
        if mon:
            try:
                return dt.date(year, mon, day)
            except:  # noqa
                return None
    m2 = re.match(r"(?i)^\s*([a-záéíóú]+)\s+(\d{4})\s*$", s)
    if m2:
        mon_text, year = m2.group(1).lower(), int(m2.group(2))
        mon = MONTHS_ES.get(mon_text)
        if mon:
            try:
                return dt.date(year, mon, 1)
            except:  # noqa
                return None
    return None

def _cutoff_embarque(fecha_alta: Optional[dt.datetime | dt.date]) -> Optional[dt.date]:
    """
    Si no viene fecha_embarque, calculamos:
    - corte 13:05 (HHMM>=1305 -> +1 día)
    - si cae sábado -> +2; domingo -> +1
    """
    if not fecha_alta:
        return None
    if isinstance(fecha_alta, dt.date) and not isinstance(fecha_alta, dt.datetime):
        # sin hora -> tratamos como 00:00
        base = fecha_alta
        hhmm = 0
    else:
        fa: dt.datetime = fecha_alta if isinstance(fecha_alta, dt.datetime) else dt.datetime.combine(fecha_alta, dt.time())
        hhmm = fa.hour*100 + fa.minute
        base = fa.date()
    if hhmm >= 1305:
        base = base + dt.timedelta(days=1)
    # 0=lun .. 6=dom (weekday)
    wd = base.weekday()
    if wd == 5:      # sábado
        base = base + dt.timedelta(days=2)
    elif wd == 6:    # domingo
        base = base + dt.timedelta(days=1)
    return base

# ---------- Mapeo de nombres de campos ----------
# Ajusta aquí si el JSON usa otras llaves:
CANDIDATES = {
    "pedido_cco": ["pedido_cco", "pedcco", "ped_cco", "pedCCO", "pedido"],
    "part_cco":   ["part_cco", "num_part", "partidas", "num_partidas"],
    "fecha_alta": ["fecha_alta", "fecha", "f_alta", "creado", "created_at"],
    "fecha_surtido": ["fecha_surtido", "f_surtido", "surtido", "fecha_sur"],
    "fecha_embarque": ["fecha_embarque", "dia_emb", "f_embarque", "embarque"],
    "verificador": ["verificador", "verif", "usuario_verif"],
    "estado": ["estado", "proceso_txt", "proceso", "estatus"],
}

def _pick(d: dict, keys: list[str]) -> Any:
    for k in keys:
        if k in d:
            return d[k]
    return None

def _to_datetime(v: Any) -> Optional[dt.datetime]:
    if v in (None, "", "null"):
        return None
    if isinstance(v, dt.datetime):
        return v
    if isinstance(v, dt.date):
        return dt.datetime.combine(v, dt.time(0, 0))
    # intenta ISO con hora
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%d-%m-%Y %H:%M:%S", "%d/%m/%Y %H:%M:%S"):
        try:
            return dt.datetime.strptime(str(v), fmt)
        except:
            pass
    # timestamp?
    try:
        if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()):
            x = int(v)
            if x > 10_000_000_000:
                x = x // 1000
            return dt.datetime.utcfromtimestamp(x)
    except:
        pass
    # si no, intenta solo fecha
    d = _parse_date_any(v)
    return dt.datetime.combine(d, dt.time()) if d else None

def _normalize_record(raw: dict) -> dict:
    pedido = _pick(raw, CANDIDATES["pedido_cco"])
    part   = _pick(raw, CANDIDATES["part_cco"])
    f_alta = _pick(raw, CANDIDATES["fecha_alta"])
    f_sur  = _pick(raw, CANDIDATES["fecha_surtido"])
    f_emb  = _pick(raw, CANDIDATES["fecha_embarque"])
    veri   = _pick(raw, CANDIDATES["verificador"])
    estado = _pick(raw, CANDIDATES["estado"])

    # normaliza tipos
    try:
        part = int(part) if part is not None else None
    except:
        part = None

    fecha_alta_dt = _to_datetime(f_alta)
    fecha_surtido = _parse_date_any(f_sur)
    fecha_embarque = _parse_date_any(f_emb)
    if not fecha_embarque:
        fecha_embarque = _cutoff_embarque(fecha_alta_dt)

    return {
        "pedido_cco": pedido,
        "part_cco": part or 0,
        "fecha_alta": fecha_alta_dt.date() if fecha_alta_dt else None,
        "fecha_surtido": fecha_surtido,
        "fecha_embarque": fecha_embarque,
        "verificador": veri,
        "estado": str(estado) if estado is not None else None,
    }

def fetch_month(year: int, month: int) -> List[dict]:
    key = f"{year:04d}-{month:02d}"
    now = time.time()
    if key in _cache and (now - _cache[key]["ts"] < 600):
        return _cache[key]["data"]

    url = URL_TMPL.format(yyyy=f"{year:04d}", mm=f"{month:02d}")
    resp = requests.get(url, timeout=REQ_TIMEOUT)
    resp.raise_for_status()
    payload = resp.json()

    # si el JSON trae lista directamente:
    items = payload if isinstance(payload, list) else payload.get("data") or payload.get("rows") or []
    norm = [_normalize_record(x) for x in items if isinstance(x, dict)]
    _cache[key] = {"ts": now, "data": norm}
    return norm