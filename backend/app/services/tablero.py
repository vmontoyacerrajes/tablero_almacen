# app/services/tablero.py
import os
import json as pyjson
import datetime as dt
import re
from typing import List, Dict, Any, Optional, Tuple

from sqlalchemy.orm import Session

import certifi
import requests


# =========================
# Configuración / Orígenes
# =========================

def _json_url_for_month(month: dt.date) -> str:
    tmpl = os.getenv("DASH_JSON_URL_TEMPLATE", "https://mov.cerraco.mx/temp/dash/{yyyy}-{mm}.json")
    yyyy = f"{month.year:04d}"
    mm = f"{month.month:02d}"
    return tmpl.replace("{yyyy}", yyyy).replace("{mm}", mm)

def _allowed_domain(url: str) -> bool:
    from urllib.parse import urlparse
    allow = os.getenv("ALLOWED_JSON_DOMAINS", "").strip()
    if not allow:
        return True
    host = (urlparse(url).hostname or "").lower()
    return any(host == d.strip().lower() for d in allow.split(",") if d.strip())

def _cache_paths(url: str) -> Tuple[str, int]:
    import hashlib
    d = os.getenv("JSON_CACHE_DIR", "/app/.cache/dashjson")
    os.makedirs(d, exist_ok=True)
    ttl = int(os.getenv("JSON_CACHE_TTL", "600"))  # 10 min
    h = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return os.path.join(d, f"{h}.json"), ttl


# ---------- util: buscar lista de dicts en un JSON arbitrario ----------
def _find_first_list_of_dicts(obj: Any) -> List[Dict] | None:
    if isinstance(obj, list):
        if not obj or all(isinstance(x, dict) for x in obj):
            return obj
        for x in obj:
            r = _find_first_list_of_dicts(x)
            if r is not None:
                return r
        return None
    if isinstance(obj, dict):
        preferred = ("rest", "rows", "data", "items", "result", "registros", "records",
                     "tablero", "dash", "detalle", "payload", "list")
        for k in preferred:
            if k in obj:
                v = obj[k]
                if isinstance(v, str):
                    try:
                        v = pyjson.loads(v)
                    except Exception:
                        pass
                if isinstance(v, list) and (not v or isinstance(v[0], dict)):
                    return v
                r = _find_first_list_of_dicts(v)
                if r is not None:
                    return r
        for v in obj.values():
            if isinstance(v, str):
                try:
                    v = pyjson.loads(v)
                except Exception:
                    pass
            r = _find_first_list_of_dicts(v)
            if r is not None:
                return r
    return None

def _coerce_json_rows(raw: Any) -> List[Dict[str, Any]]:
    key = os.getenv("JSON_LIST_KEY", "").strip()
    if key:
        cur = raw
        try:
            for part in key.split("."):
                if isinstance(cur, str):
                    cur = pyjson.loads(cur)
                if isinstance(cur, dict) and part in cur:
                    cur = cur[part]
                else:
                    raise KeyError(part)
            if isinstance(cur, str):
                cur = pyjson.loads(cur)
            if isinstance(cur, list) and (not cur or isinstance(cur[0], dict)):
                return cur
        except Exception:
            pass

    if isinstance(raw, list) and (not raw or isinstance(raw[0], dict)):
        return raw

    found = _find_first_list_of_dicts(raw)
    if found is not None:
        return found

    def _desc(o):
        if isinstance(o, dict):
            return {"type": "dict", "keys": list(o.keys())[:20]}
        if isinstance(o, list):
            return {"type": "list", "len": len(o)}
        return {"type": type(o).__name__}
    raise RuntimeError(f"El JSON no contiene una lista de registros reconocible. Raíz: {_desc(raw)}")

def _fetch_json_month(month: dt.date) -> List[Dict[str, Any]]:
    import time
    url = _json_url_for_month(month)
    if not _allowed_domain(url):
        raise RuntimeError(f"Dominio no permitido: {url}. Configura ALLOWED_JSON_DOMAINS.")
    path, ttl = _cache_paths(url)

    # cache
    try:
        if os.path.exists(path) and (time.time() - os.path.getmtime(path)) < ttl:
            with open(path, "r", encoding="utf-8") as f:
                raw = pyjson.load(f)
            return _coerce_json_rows(raw)
    except Exception:
        pass

    # descarga
    try:
        resp = requests.get(url, headers={"User-Agent": "tablero-api/1.0"}, timeout=20, verify=certifi.where())
        resp.raise_for_status()
        raw = resp.json()
    except requests.exceptions.RequestException as e:
        # fallback cache
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw = pyjson.load(f)
                return _coerce_json_rows(raw)
            except Exception:
                pass
        raise RuntimeError(f"No pude leer {url}. Detalle: {e}")

    # guarda cache
    try:
        with open(path, "w", encoding="utf-8") as f:
            pyjson.dump(raw, f, ensure_ascii=False)
    except Exception:
        pass

    return _coerce_json_rows(raw)


# =========================
# Utilidades de calendario
# =========================

def _holidays(settings) -> set:
    """Fallback legacy: settings.holidays (lista ISO)."""
    if not settings or not getattr(settings, "holidays", None):
        return set()
    try:
        return set(dt.date.fromisoformat(x) for x in settings.holidays)
    except Exception:
        return set()

def get_inhabiles(db: Optional[Session], start: dt.date, end: dt.date) -> set[dt.date]:
    """
    Devuelve set de fechas inhábiles activas en rango [start, end).
    end es exclusivo.
    Siempre normaliza a datetime.date.
    """
    if db is None:
        return set()

    from sqlalchemy import text

    def _to_date(x) -> Optional[dt.date]:
        if x is None:
            return None
        if isinstance(x, dt.datetime):
            return x.date()
        if isinstance(x, dt.date):
            return x
        try:
            return dt.date.fromisoformat(str(x)[:10])
        except Exception:
            return None

    # Validación rápida de conexión / base
    try:
        dbname = db.execute(text("SELECT DATABASE()")).scalar()
    except Exception as e:
        raise RuntimeError(f"No pude ejecutar SELECT DATABASE(): {e}")

    # Conteo (SQL crudo) para validar que hay datos
    try:
        cnt = db.execute(
            text("SELECT COUNT(*) FROM calendario_inhabil WHERE activo=1 AND fecha >= :s AND fecha < :e"),
            {"s": start.isoformat(), "e": end.isoformat()}
        ).scalar()
    except Exception as e:
        raise RuntimeError(f"Error consultando calendario_inhabil en DB={dbname}: {e}")

    # Intento ORM (si existe el modelo)
    try:
        from app.models import CalendarioInhabil
        rows = (
            db.query(CalendarioInhabil.fecha)
              .filter(CalendarioInhabil.activo == 1)
              .filter(CalendarioInhabil.fecha >= start)
              .filter(CalendarioInhabil.fecha < end)
              .all()
        )
        fechas = set()
        for (x,) in rows:
            dx = _to_date(x)
            if dx:
                fechas.add(dx)

        if cnt and not fechas:
            raise RuntimeError(
                f"SQL crudo encontró {cnt} inhábiles pero ORM no trajo filas. "
                f"Revisa modelo CalendarioInhabil (tabla/columnas/tipos). DB={dbname}"
            )
        return fechas

    except Exception:
        # Fallback SQL crudo (robusto)
        rows = db.execute(
            text("SELECT fecha FROM calendario_inhabil WHERE activo=1 AND fecha >= :s AND fecha < :e"),
            {"s": start.isoformat(), "e": end.isoformat()}
        ).fetchall()

        fechas = set()
        for (x,) in rows:
            dx = _to_date(x)
            if dx:
                fechas.add(dx)
        return fechas

def _inhabiles_set(db: Optional[Session], settings, month: dt.date) -> set:
    """Une tabla calendario_inhabil + settings.holidays (si existen)."""
    first = month.replace(day=1)
    nextm = (first.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    inh_db = get_inhabiles(db, first, nextm)
    inh_st = _holidays(settings)
    return set(inh_db) | set(inh_st)

def _is_business_day(d: dt.date, holidays: set) -> bool:
    return d.weekday() < 5 and d not in holidays

def _next_workday(d: dt.date, holidays: set) -> dt.date:
    """Avanza hasta el siguiente día hábil (excluye sáb/dom + inhábiles)."""
    while d.weekday() >= 5 or d in holidays:
        d += dt.timedelta(days=1)
    return d

def _business_days_in_month(year: int, month: int, holidays: set) -> int:
    d = dt.date(year, month, 1)
    res = 0
    while d.month == month:
        if _is_business_day(d, holidays):
            res += 1
        d += dt.timedelta(days=1)
    return res

def _business_days_elapsed(today: dt.date, holidays: set) -> int:
    d = today.replace(day=1)
    res = 0
    while d <= today:
        if _is_business_day(d, holidays):
            res += 1
        d += dt.timedelta(days=1)
    return res

def _thresholds(settings) -> Dict[str, float]:
    th = {"rojo": 0.8, "amarillo": 0.9}
    if settings and getattr(settings, "thresholds", None):
        try:
            if isinstance(settings.thresholds, dict):
                th.update(settings.thresholds)
            elif isinstance(settings.thresholds, str):
                th.update(pyjson.loads(settings.thresholds))
        except Exception:
            pass
    return th

def _classify(p: Optional[float], th: Dict[str, float]) -> str:
    if p is None:
        return "rojo"
    if p >= th.get("amarillo", 0.9):
        return "verde"
    if p >= th.get("rojo", 0.8):
        return "ambar"
    return "rojo"

def _bono_points_from_pct(p: Optional[float]) -> Optional[int]:
    if p is None:
        return None
    if p >= 1.0:
        return 6
    if p >= 0.8999:
        return 5
    if p >= 0.80:
        return 3
    return 0


# =========================
# Settings desde DB
# =========================

def get_settings(db: Optional[Session], month: dt.date):
    if db is None:
        return None
    try:
        from app.models import DashboardSettings
        return db.query(DashboardSettings).filter(DashboardSettings.month == month.replace(day=1)).first()
    except Exception:
        return None


# =========================
# Normalización de fechas
# =========================

_MONTHS_ES = {
    # completos
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
    # abreviados típicos (y variantes)
    "ene": 1,
    "feb": 2,
    "mar": 3,
    "abr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "ago": 8,
    "sep": 9, "sept": 9,
    "oct": 10,
    "nov": 11,
    "dic": 12,
}

_NULL_STRINGS = {"0000-00-00", "0000-00-00 00:00:00", "null", "none", "nan"}

def _norm_str(v: Any) -> str:
    return " ".join(str(v).strip().split())

def _clean_month_token(tok: str) -> str:
    tok = tok.lower()
    tok = re.sub(r"[^a-zñ]", "", tok)  # solo letras
    return tok

def _coerce_year(yy: int) -> int:
    if yy < 100:
        return 2000 + yy
    return yy

def _parse_date_any(v: Any, default_year: Optional[int] = None, default_month: Optional[int] = None) -> Optional[dt.date]:
    if v is None:
        return None
    if isinstance(v, dt.date) and not isinstance(v, dt.datetime):
        return v
    if isinstance(v, dt.datetime):
        return v.date()

    s = _norm_str(v)
    if not s:
        return None
    if s.lower() in _NULL_STRINGS:
        return None

    for fmt in (
        "%Y-%m-%d", "%Y/%m/%d",
        "%d/%m/%Y", "%d-%m-%Y",
        "%d/%m/%y", "%d-%m-%y",
        "%Y-%m", "%Y/%m",
        "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            d = dt.datetime.strptime(s, fmt)
            if fmt in ("%Y-%m", "%Y/%m"):
                return dt.date(d.year, d.month, 1)
            if "%y" in fmt:
                return dt.date(_coerce_year(d.year), d.month, d.day)
            return d.date()
        except Exception:
            pass

    parts_raw = s.lower().replace(",", " ").split()
    parts = [_clean_month_token(p) if i == 1 else p for i, p in enumerate(parts_raw)]

    try:
        # dd mon yyyy
        if len(parts) == 3:
            dd = int(parts[0])
            mon = parts[1]
            yy = int(parts[2])
            if mon in _MONTHS_ES:
                return dt.date(_coerce_year(yy), _MONTHS_ES[mon], dd)

        # dd mon (sin año) -> default_year requerido
        if len(parts) == 2:
            dd = int(parts[0])
            mon = parts[1]
            if mon in _MONTHS_ES and default_year is not None:
                return dt.date(int(default_year), _MONTHS_ES[mon], dd)

        # mon yyyy -> primer día
        if len(parts) == 2:
            mon = parts[0]
            yy = int(parts[1])
            if mon in _MONTHS_ES:
                return dt.date(_coerce_year(yy), _MONTHS_ES[mon], 1)

        if len(parts) == 1:
            mon = parts[0]
            if mon in _MONTHS_ES and default_year is not None:
                return dt.date(int(default_year), _MONTHS_ES[mon], 1)

    except Exception:
        pass

    return None

def _parse_time_any(v: Any) -> Optional[tuple]:
    if v is None:
        return None
    s = _norm_str(v)
    if not s:
        return None
    if s.lower() in _NULL_STRINGS:
        return None

    if ":" in s:
        parts = s.split(":")
        try:
            h = int(parts[0])
            m = int(parts[1])
            s2 = int(parts[2]) if len(parts) > 2 else 0
            return (h, m, s2)
        except Exception:
            return None

    if s.isdigit():
        try:
            n = int(s)
            if n < 100:
                return (n, 0, 0)
            h, m = divmod(n, 100)
            return (h, m, 0)
        except Exception:
            return None

    return None

def _parse_datetime_any(v: Any, default_year: Optional[int] = None, default_month: Optional[int] = None) -> Optional[dt.datetime]:
    if v is None:
        return None
    if isinstance(v, dt.datetime):
        return v
    if isinstance(v, dt.date):
        return dt.datetime(v.year, v.month, v.day)

    s = _norm_str(v)
    if not s:
        return None
    if s.lower() in _NULL_STRINGS:
        return None

    for fmt in (
        "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%d/%m/%Y %H:%M:%S", "%d-%m-%Y %H:%M:%S",
        "%d/%m/%y %H:%M:%S", "%d-%m-%y %H:%M:%S",
    ):
        try:
            x = dt.datetime.strptime(s, fmt)
            if "%y" in fmt:
                return dt.datetime(_coerce_year(x.year), x.month, x.day, x.hour, x.minute, x.second)
            return x
        except Exception:
            pass

    # "05 ene 2026 15:46:28"
    m = re.match(
        r"^(\d{1,2})\s+([A-Za-zñÑ\.\- ]+)\s+(\d{2,4})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?$",
        s, re.IGNORECASE
    )
    if m:
        try:
            dd = int(m.group(1))
            mon = _clean_month_token(m.group(2))
            yy = _coerce_year(int(m.group(3)))
            hh = int(m.group(4) or 0)
            mm = int(m.group(5) or 0)
            ss = int(m.group(6) or 0)
            if mon in _MONTHS_ES:
                return dt.datetime(yy, _MONTHS_ES[mon], dd, hh, mm, ss)
        except Exception:
            pass

    # "09 ene 15:46:28" (sin año) -> default_year
    m2 = re.match(
        r"^(\d{1,2})\s+([A-Za-zñÑ\.\- ]+)(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?$",
        s, re.IGNORECASE
    )
    if m2:
        try:
            dd = int(m2.group(1))
            mon = _clean_month_token(m2.group(2))
            hh = int(m2.group(3) or 0)
            mm = int(m2.group(4) or 0)
            ss = int(m2.group(5) or 0)
            if mon in _MONTHS_ES and default_year is not None:
                return dt.datetime(int(default_year), _MONTHS_ES[mon], dd, hh, mm, ss)
        except Exception:
            pass

    d = _parse_date_any(s, default_year=default_year, default_month=default_month)
    if d:
        return dt.datetime(d.year, d.month, d.day)

    return None

def _merge_date_time(date_v: Any, time_v: Any, default_year: Optional[int] = None, default_month: Optional[int] = None) -> Optional[dt.datetime]:
    dtm = _parse_datetime_any(date_v, default_year=default_year, default_month=default_month)
    if dtm and (dtm.hour or dtm.minute or dtm.second):
        return dtm

    d = _parse_date_any(date_v, default_year=default_year, default_month=default_month)
    if not d:
        return None

    t = _parse_time_any(time_v)
    if not t:
        return dt.datetime(d.year, d.month, d.day)

    return dt.datetime(d.year, d.month, d.day, t[0], t[1], t[2])

def _has_value(x: Any) -> bool:
    if x is None:
        return False
    s = _norm_str(x)
    if not s:
        return False
    if s.lower() in _NULL_STRINGS:
        return False
    return True


# =========================
# Mapeo de campos JSON
# =========================

def _extract_fields(rec: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normaliza un registro del JSON mensual a un esquema interno estable.

    Definición confirmada por operación:
      - Pedido del día / base:     fecha (alta)  -> fecha_alta
      - Partidas:                 num_part
      - Surtido:                  ent_fec_cco + ent_tim_cco
      - Verificación:             fecha_verificacion (datetime string: 'YYYY-MM-DD HH:MM:SS')
      - Embarque:                 fecha_salida (+ hora_salida si existiera)
    """
    def g(*names):
        for n in names:
            if n in rec and _has_value(rec[n]):
                return rec[n]
        return None

    fecha_alta = g("fecha", "fecha_alta", "falta", "created_at")
    num_part   = g("num_part", "part_cco", "partCCO", "numPart", "partidas")

    # ✅ surtido
    surt_fec = g("ent_fec_cco")
    surt_tim = g("ent_tim_cco")

    # ✅ verificación
    verif_dt = g("fecha_verificacion")

    # ✅ embarque
    fecha_salida = g("fecha_salida", "f_salida", "sal_fec", "fsalida", "fechaSalida", "salida_fecha")
    hora_salida  = g("hora_salida", "sal_tim", "fsalida_hora", "horaSalida", "salida_hora")

    pedido  = g("pedido", "pedido_cco", "ped_cco", "pedCCO", "id_pedido")
    proceso = g("proceso", "estado", "status", "est", "estatus")

    return {
        "pedido": pedido,
        "num_part": num_part,
        "proceso": proceso,
        "fecha_alta": fecha_alta,

        "surt_fec": surt_fec,
        "surt_tim": surt_tim,

        "verif_dt": verif_dt,

        "fecha_salida": fecha_salida,
        "hora_salida": hora_salida,

        "ultimo_mod": g("ultimo_mod", "updated_at", "last_update", "ultima_modificacion"),
    }

def _is_cancelled(rec: Dict[str, Any]) -> bool:
    v = rec.get("proceso")
    if v is None:
        return False
    s = str(v).strip().lower()
    if "can" in s:
        return True
    try:
        n = int(s.split()[0])
        if n == 6:
            return True
    except Exception:
        pass
    return False

def _is_closed_status(rec: Dict[str, Any]) -> bool:
    v = rec.get("proceso")
    if v is None:
        return False
    s = str(v).strip().lower()
    if ("5" in s and "cer" in s) or ("cerr" in s) or ("final" in s) or ("complet" in s):
        return True
    return False


# =========================
# FECHA BASE (13:05 + inhábiles) y EVENTOS
# =========================

def _operational_day_from_datetime(dtm: dt.datetime, holidays: set) -> dt.date:
    """
    Aplica regla 13:05:
      - si HHMM >= 13:05 => pasa al siguiente día
    Luego ajusta a siguiente día hábil (L-V, excluye inhábiles).
    """
    d = dtm.date()
    hhmm = dtm.hour * 100 + dtm.minute
    if hhmm >= 1305:
        d = d + dt.timedelta(days=1)
    return _next_workday(d, holidays)

def _calendar_day_from_datetime(dtm: dt.datetime) -> dt.date:
    """
    Día calendario del evento.
    NO aplica 13:05 ni mueve a día hábil.
    (Esta función existe por claridad; en producción el embarque sí se mueve a hábil,
     pero NO se empuja por 13:05.)
    """
    return dtm.date()

def _base_from_record(rec: Dict[str, Any], holidays: set) -> Optional[dt.date]:
    """
    DÍA BASE desde fecha_alta:
    - base = DATE(fecha_alta)
    - regla 13:05
    - ajuste a siguiente día hábil (incluye inhábiles)
    """
    dy = rec.get("_default_year")
    dm = rec.get("_default_month")
    dtm = (
        _parse_datetime_any(rec.get("fecha_alta"), default_year=dy, default_month=dm)
        or _merge_date_time(rec.get("fecha_alta"), None, default_year=dy, default_month=dm)
    )
    if not dtm:
        return None
    return _operational_day_from_datetime(dtm, holidays)

def _event_operational_day(
    date_v: Any,
    time_v: Any,
    holidays: set,
    default_year: Optional[int] = None,
    default_month: Optional[int] = None
) -> Optional[dt.date]:
    """
    Día operativo (Surtido / Verificación cuando viene como fecha+hora):
    - aplica 13:05 (si hay hora)
    - ajusta a siguiente día hábil si cae en sábado/domingo/inhábil
    """
    dtm = _merge_date_time(date_v, time_v, default_year=default_year, default_month=default_month)
    if not dtm:
        d = _parse_date_any(date_v, default_year=default_year, default_month=default_month)
        return _next_workday(d, holidays) if d else None
    return _operational_day_from_datetime(dtm, holidays)


def _event_calendar_to_workday(
    date_v: Any,
    time_v: Any,
    holidays: set,
    default_year: Optional[int] = None,
    default_month: Optional[int] = None,
) -> Optional[dt.date]:
    """
    Día del evento SIN regla 13:05.
    - Toma fecha+hora si existe
    - Usa dtm.date()
    - Si cae inhábil/sáb/dom -> siguiente hábil
    """
    dtm = _merge_date_time(date_v, time_v, default_year=default_year, default_month=default_month)
    if dtm:
        return _next_workday(dtm.date(), holidays)

    d = _parse_date_any(date_v, default_year=default_year, default_month=default_month)
    return _next_workday(d, holidays) if d else None

def _event_date_from_record(rec: Dict[str, Any], which: str, holidays: set) -> Optional[dt.date]:
    """
    Eventos (alineado a _extract_fields):

    - 'surt'  -> SURTIDO: surt_fec + surt_tim
                ✅ SIN 13:05, PERO sí se mueve a hábil si cae inhábil/sáb/dom.
    - 'verif' -> VERIFICACIÓN: verif_dt (datetime)
                ✅ SIN 13:05, PERO sí se mueve a hábil si cae inhábil/sáb/dom.
    - 'emb'   -> EMBARQUE: fecha_salida (+ hora_salida)
                ✅ SIN 13:05, PERO sí se mueve a hábil si cae inhábil/sáb/dom.
    """
    which = (which or "").strip().lower()
    dy = rec.get("_default_year")
    dm = rec.get("_default_month")

    if which == "surt":
        return _event_calendar_to_workday(
            rec.get("surt_fec"),
            rec.get("surt_tim"),
            holidays,
            default_year=dy,
            default_month=dm,
        )

    if which == "verif":
        dtm = _parse_datetime_any(rec.get("verif_dt"), default_year=dy, default_month=dm)
        if not dtm:
            return None
        return _next_workday(dtm.date(), holidays)

    if which == "emb":
        return _event_calendar_to_workday(
            rec.get("fecha_salida"),
            rec.get("hora_salida"),
            holidays,
            default_year=dy,
            default_month=dm,
        )

    return None

# =========================
# Serie diaria
# =========================

def _json_rows_for_month(month: dt.date) -> List[Dict[str, Any]]:
    raw = _fetch_json_month(month)
    out: List[Dict[str, Any]] = []
    for r in raw:
        x = _extract_fields(r)
        # defaults para parse de fechas tipo "09 ene" (sin año)
        x["_default_year"] = month.year
        x["_default_month"] = month.month
        out.append(x)
    return out

def _json_daily_aggregate(db: Optional[Session], month: dt.date, settings=None) -> List[Dict[str, Any]]:
    """
    Serie diaria del mes.
    - Usa inhábiles (tabla calendario_inhabil + settings.holidays)
    - Aplica regla 13:05 a base y eventos (surt/verif)
    - ✅ Embarque: NO aplica 13:05; si cae inhábil/sáb/dom -> siguiente hábil;
      y si queda antes de base -> se empuja a base.
    - Backlog neto (legacy) basado en embarque
    - ✅ Capacidad instalada/acumulada y capacidad utilizada/acumulada contra PARTIDAS SURTIDAS
    - ✅ Atrasos reales (surtido/verificación/embarque) por día
    """
    from sqlalchemy import text
    from collections import defaultdict

    def _resolve_installed_capacity(db: Optional[Session], month: dt.date, settings) -> int:
        default_cap = int(os.getenv("DEFAULT_INSTALLED_CAPACITY", "570") or "570")

        # 1) del mes actual
        try:
            if settings and getattr(settings, "installed_capacity_surtido", None) is not None:
                v = int(settings.installed_capacity_surtido or 0)
                if v > 0:
                    return v
        except Exception:
            pass

        # 2) fallback al último mes anterior con >0
        if db is not None:
            try:
                first_local = month.replace(day=1)
                v = db.execute(
                    text("""
                        SELECT installed_capacity_surtido
                        FROM dashboard_settings
                        WHERE month < :m
                          AND installed_capacity_surtido IS NOT NULL
                          AND installed_capacity_surtido > 0
                        ORDER BY month DESC
                        LIMIT 1
                    """),
                    {"m": first_local.isoformat()}
                ).scalar()
                if v is not None and int(v) > 0:
                    return int(v)
            except Exception:
                pass

        return default_cap

    # Rango del mes
    first = month.replace(day=1)
    nextm = (first.replace(day=28) + dt.timedelta(days=4)).replace(day=1)

    holidays = _inhabiles_set(db, settings, month)

    cap_diaria = _resolve_installed_capacity(db, month, settings)
    unit = "partidas"
    emb_event_source = "fecha_salida"

    raw_rows = _json_rows_for_month(month)

    # -------------------------
    # 1) Consolidar por pedido
    # -------------------------
    orders: Dict[Any, Dict[str, Any]] = {}

    for r in raw_rows:
        if _is_cancelled(r):
            continue

        ped = r.get("pedido")
        if not ped:
            continue

        base = _base_from_record(r, holidays)

        # Eventos correctos
        sur_date = _event_date_from_record(r, "surt", holidays)   # 13:05 + hábil
        ver_date = _event_date_from_record(r, "verif", holidays)  # 13:05 + hábil
        emb_date = _event_date_from_record(r, "emb", holidays)    # sin 13:05 + hábil

        # ✅ Alineación al día programado (base)
        if base:
            if sur_date and sur_date < base:
                sur_date = base
            if ver_date and ver_date < base:
                ver_date = base
            if emb_date and emb_date < base:
                emb_date = base

        o = orders.get(ped)
        if not o:
            o = {
                "base": base,
                "closed": _is_closed_status(r),
                "parts_max": 0,
                "surt_date": sur_date,
                "verif_date": ver_date,
                "emb_date": emb_date,
            }
            orders[ped] = o
        else:
            if base and (o["base"] is None or base < o["base"]):
                o["base"] = base
            if sur_date and (o["surt_date"] is None or sur_date < o["surt_date"]):
                o["surt_date"] = sur_date
            if ver_date and (o["verif_date"] is None or ver_date < o["verif_date"]):
                o["verif_date"] = ver_date
            if emb_date and (o["emb_date"] is None or emb_date < o["emb_date"]):
                o["emb_date"] = emb_date

        # Partidas por pedido: máximo num_part
        try:
            pv = int(r.get("num_part") or 0)
            if pv > o["parts_max"]:
                o["parts_max"] = pv
        except Exception:
            pass

        if _is_closed_status(r):
            o["closed"] = True

    # -------------------------
    # 2) Índices por día (SOLO eventos dentro del mes)
    # -------------------------
    base_idx: Dict[dt.date, list] = defaultdict(list)
    surt_idx: Dict[dt.date, int] = defaultdict(int)
    ver_idx:  Dict[dt.date, int] = defaultdict(int)
    emb_idx:  Dict[dt.date, int] = defaultdict(int)

    surt_parts_idx: Dict[dt.date, int] = defaultdict(int)

    for ped, o in orders.items():
        if o["base"] and (first <= o["base"] < nextm):
            base_idx[o["base"]].append(ped)

        if o["surt_date"] and (first <= o["surt_date"] < nextm):
            surt_idx[o["surt_date"]] += 1
            surt_parts_idx[o["surt_date"]] += int(o.get("parts_max") or 0)

        if o["verif_date"] and (first <= o["verif_date"] < nextm):
            ver_idx[o["verif_date"]] += 1

        if o["emb_date"] and (first <= o["emb_date"] < nextm):
            emb_idx[o["emb_date"]] += 1

    # -------------------------
    # 3) Serie diaria
    # -------------------------
    out: List[Dict[str, Any]] = []
    d = first

    hab_count = 0
    capacidad_instalada_acum = 0
    capacidad_utilizada_acum = 0  # partidas surtidas acum (solo en días hábiles)

    while d < nextm:
        is_habil = _is_business_day(d, holidays)
        if is_habil:
            hab_count += 1
            capacidad_instalada_acum = hab_count * int(cap_diaria)

        pedidos_del_dia = base_idx.get(d, [])
        pedidos = len(pedidos_del_dia)
        partidas = sum(int(orders[p].get("parts_max") or 0) for p in pedidos_del_dia)

        # Backlog neto al inicio del día d (arrastre): base < d y NO embarcado antes de d
        pend_prev_neto = 0
        for o in orders.values():
            if o.get("closed", False):
                continue
            if o["base"] and (o["base"] < d):
                if (o["emb_date"] is None) or (o["emb_date"] >= d):
                    pend_prev_neto += 1

        total_neto = pedidos + pend_prev_neto

        # -------------------------
        # Atrasos reales por etapa (inicio del día d)
        # -------------------------
        atraso_surt_inicio = 0
        atraso_verif_inicio = 0
        atraso_emb_inicio = 0

        atraso_surt_hoy = 0
        atraso_verif_hoy = 0
        atraso_emb_hoy = 0

        for o in orders.values():
            if o.get("closed", False):
                continue

            b = o.get("base")
            if not b or b >= d:
                continue

            sur = o.get("surt_date")
            ver = o.get("verif_date")
            emb = o.get("emb_date")

            if sur is None or sur >= d:
                atraso_surt_inicio += 1
                if sur == d:
                    atraso_surt_hoy += 1

            if sur is not None and sur < d:
                if ver is None or ver >= d:
                    atraso_verif_inicio += 1
                    if ver == d:
                        atraso_verif_hoy += 1

            if ver is not None and ver < d:
                if emb is None or emb >= d:
                    atraso_emb_inicio += 1
                    if emb == d:
                        atraso_emb_hoy += 1

        # Eventos del día (conteos)
        surt_hoy = int(surt_idx.get(d, 0))
        ver_hoy  = int(ver_idx.get(d, 0))
        emb_hoy  = int(emb_idx.get(d, 0))

        pct_surt_neto = (surt_hoy / total_neto) if total_neto else None
        pct_ver_neto  = (ver_hoy  / total_neto) if total_neto else None
        pct_emb_neto  = (emb_hoy  / total_neto) if total_neto else None

        # Partidas surtidas hoy y acumuladas
        partidas_surtidas_hoy = int(surt_parts_idx.get(d, 0))
        if is_habil:
            capacidad_utilizada_acum += partidas_surtidas_hoy

        pct_capacidad_utilizada_acum = (
            (capacidad_utilizada_acum / capacidad_instalada_acum)
            if capacidad_instalada_acum > 0
            else None
        )

        dbg = {
            "emb_event_source": emb_event_source,
            "base_lt_d": sum(1 for o in orders.values() if o["base"] and o["base"] < d),
            "abiertos_sin_salida": sum(
                1 for o in orders.values()
                if (not o.get("closed", False))
                and o["base"] and o["base"] < d
                and ((o["emb_date"] is None) or (o["emb_date"] >= d))
            ),
            "inhabiles_mes": len(holidays),
            "cap_diaria": int(cap_diaria),
            "hab_count": int(hab_count),
            "partidas_surtidas_hoy": int(partidas_surtidas_hoy),
            "atraso_surt_inicio": int(atraso_surt_inicio),
            "atraso_verif_inicio": int(atraso_verif_inicio),
            "atraso_emb_inicio": int(atraso_emb_inicio),
        }

        out.append({
            "dia": d.isoformat(),
            "schema_ver": "v11-atrasos-reales",

            "pedidos": int(pedidos),
            "partidas": int(partidas),

            "pend_surtido_prev_neto": int(pend_prev_neto),
            "total_a_surtir_neto": int(total_neto),

            "surtidos": int(surt_hoy),
            "verificados": int(ver_hoy),
            "embarcados": int(emb_hoy),

            "pct_surtido": pct_surt_neto,
            "pct_verificacion": pct_ver_neto,
            "pct_embarque": pct_emb_neto,

            "atrasos": {
                "surtido_inicio": int(atraso_surt_inicio),
                "verificacion_inicio": int(atraso_verif_inicio),
                "embarque_inicio": int(atraso_emb_inicio),

                "surtido_resuelto_hoy": int(atraso_surt_hoy),
                "verificacion_resuelto_hoy": int(atraso_verif_hoy),
                "embarque_resuelto_hoy": int(atraso_emb_hoy),
            },

            "is_habil": bool(is_habil),
            "dia_habil_num": int(hab_count) if is_habil else None,

            "capacidad_diaria": int(cap_diaria),
            "capacidad_instalada_acum": int(capacidad_instalada_acum),
            "capacidad_utilizada_acum": int(capacidad_utilizada_acum),
            "pct_capacidad_utilizada_acum": pct_capacidad_utilizada_acum,

            "unit": unit,
            "debug_backlog_neto": dbg,
        })

        d += dt.timedelta(days=1)

    return out


# =========================
# Resultados (encabezado)
# =========================

def _json_resultados(db: Optional[Session], month: dt.date, settings) -> Dict[str, Any]:
    """
    KPIs de encabezado:
    - Capacidad global (una sola) con fallback.
    - Capacidad utilizada: PARTIDAS SURTIDAS (capacidad_utilizada_acum MTD).
    - % embarque/surt/verif: legacy contra pedidos_mtd (como lo consume tu UI).
    """
    from sqlalchemy import text

    def _resolve_installed_capacity(db: Optional[Session], month: dt.date, settings) -> int:
        default_cap = int(os.getenv("DEFAULT_INSTALLED_CAPACITY", "570") or "570")

        try:
            if settings and getattr(settings, "installed_capacity_surtido", None) is not None:
                v = int(settings.installed_capacity_surtido or 0)
                if v > 0:
                    return v
        except Exception:
            pass

        if db is not None:
            try:
                first_local = month.replace(day=1)
                v = db.execute(
                    text("""
                        SELECT installed_capacity_surtido
                        FROM dashboard_settings
                        WHERE month < :m
                          AND installed_capacity_surtido IS NOT NULL
                          AND installed_capacity_surtido > 0
                        ORDER BY month DESC
                        LIMIT 1
                    """),
                    {"m": first_local.isoformat()}
                ).scalar()
                if v is not None and int(v) > 0:
                    return int(v)
            except Exception:
                pass

        return default_cap

    first = month.replace(day=1)
    month_end = (first.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
    today = dt.date.today()
    limit = min(today, month_end)

    holidays = _inhabiles_set(db, settings, month)

    diario = _json_daily_aggregate(db, month, settings=settings)

    def _d(s):
        return _parse_date_any(s) if s else None

    mtd_rows = [r for r in diario if (r.get("dia") and _d(r["dia"]) and _d(r["dia"]) <= limit)]

    ped_mtd = sum(int(r.get("pedidos") or 0) for r in mtd_rows)
    part_mtd = sum(int(r.get("partidas") or 0) for r in mtd_rows)
    sur_mtd  = sum(int(r.get("surtidos") or 0) for r in mtd_rows)
    ver_mtd  = sum(int(r.get("verificados") or 0) for r in mtd_rows)
    emb_mtd  = sum(int(r.get("embarcados") or 0) for r in mtd_rows)

    rdia = next((r for r in diario if r.get("dia") == limit.isoformat()), None) or {
        "dia": limit.isoformat(),
        "pedidos": 0, "partidas": 0,
        "surtidos": 0, "verificados": 0, "embarcados": 0,
        "pend_surtido_prev_neto": 0, "total_a_surtir_neto": 0,
        "capacidad_instalada_acum": 0,
        "capacidad_utilizada_acum": 0,
        "pct_capacidad_utilizada_acum": None,
        "unit": "partidas",
    }

    dias_hab_mes = (
        settings.business_days
        if settings and getattr(settings, "business_days", None)
        else _business_days_in_month(first.year, first.month, holidays)
    )
    dias_trans = _business_days_elapsed(limit, holidays)
    dias_pend = max(int(dias_hab_mes) - int(dias_trans), 0)

    cap_diaria = int(_resolve_installed_capacity(db, month, settings))
    capacidad_instalada = int(cap_diaria) * int(dias_hab_mes)
    capacidad_instalada_mtd = int(cap_diaria) * int(dias_trans or 0)

    # ✅ Capacidad utilizada MTD = PARTIDAS SURTIDAS (desde serie diaria)
    try:
        last_mtd = mtd_rows[-1] if mtd_rows else None
        capacidad_utilizada_mtd = int(last_mtd.get("capacidad_utilizada_acum") or 0) if last_mtd else 0
    except Exception:
        capacidad_utilizada_mtd = 0

    pct_capacidad_utilizada = (
        (capacidad_utilizada_mtd / capacidad_instalada_mtd)
        if capacidad_instalada_mtd > 0
        else None
    )

    pct_surt_mtd = (sur_mtd / ped_mtd) if ped_mtd else None
    pct_ver_mtd  = (ver_mtd / ped_mtd) if ped_mtd else None
    pct_emb_mtd  = (emb_mtd / ped_mtd) if ped_mtd else None

    bono_puntos = _bono_points_from_pct(pct_emb_mtd)

    # Días verdes (legacy): % verificación por día >= 90%
    c90 = c80 = c79 = 0
    for r in mtd_rows:
        ped = int(r.get("pedidos") or 0)
        ver = int(r.get("verificados") or 0)
        pct = (ver / ped) if ped else None
        if pct is None:
            continue
        if pct >= 0.9:
            c90 += 1
        elif pct >= 0.8:
            c80 += 1
        else:
            c79 += 1

    dias_logrados = c90
    tasa_verde = (c90 / dias_trans) if dias_trans else 0
    proy = round(tasa_verde * dias_hab_mes) if dias_hab_mes else 0

    return {
        "periodo": first.strftime("%Y-%m"),

        "dias_habiles_mes": int(dias_hab_mes),
        "dias_transcurridos": int(dias_trans),
        "dias_pendientes": int(dias_pend),

        "pedidos_mtd": int(ped_mtd),
        "partidas_mtd": int(part_mtd),
        "surtidos_mtd": int(sur_mtd),
        "verificados_mtd": int(ver_mtd),
        "embarcados_mtd": int(emb_mtd),

        "pct_surtido": pct_surt_mtd,
        "pct_verificacion": pct_ver_mtd,
        "pct_embarque": pct_emb_mtd,

        # ✅ compat con frontend (index.html usa res.capacidad_diaria.surtido)
        "capacidad_diaria": {"surtido": int(cap_diaria), "verificacion": int(cap_diaria), "embarques": int(cap_diaria)},
        "capacidad_diaria_detalle": {"surtido": int(cap_diaria), "verificacion": int(cap_diaria), "embarques": int(cap_diaria)},

        "capacidad_instalada": int(capacidad_instalada),
        "capacidad_utilizada": int(capacidad_utilizada_mtd),
        "pct_capacidad_utilizada": pct_capacidad_utilizada,
        "unit": "partidas",

        "bono_header": {
            "puntos": bono_puntos,
            "porcentaje_embarque_mtd": pct_emb_mtd
        },

        "promedio_diario_mtd": {
            "pedidos": (ped_mtd / dias_trans) if dias_trans else None,
            "partidas": (part_mtd / dias_trans) if dias_trans else None,
            "surtidos": (sur_mtd / dias_trans) if dias_trans else None,
            "verificados": (ver_mtd / dias_trans) if dias_trans else None,
            "embarcados": (emb_mtd / dias_trans) if dias_trans else None,
        },

        "resultados_dia": {
            "dia": rdia.get("dia"),
            "pedidos": int(rdia.get("pedidos") or 0),
            "partidas": int(rdia.get("partidas") or 0),
            "surtidos": int(rdia.get("surtidos") or 0),
            "verificados": int(rdia.get("verificados") or 0),
            "embarcados": int(rdia.get("embarcados") or 0),
            "pend_surtido_prev": int(rdia.get("pend_surtido_prev_neto") or 0),
            "total_a_surtir": int(rdia.get("total_a_surtir_neto") or 0),

            "capacidad_instalada_acum": int(rdia.get("capacidad_instalada_acum") or 0),
            "capacidad_utilizada_acum": int(rdia.get("capacidad_utilizada_acum") or 0),
            "pct_capacidad_utilizada_acum": rdia.get("pct_capacidad_utilizada_acum"),
            "unit": rdia.get("unit") or "partidas",
        },

        "bono_mensual": {
            "porcentaje_embarque_mtd": pct_emb_mtd,
            "puntos": bono_puntos,
            "regla": [
                {"gte": 1.0,    "puntos": 6},
                {"gte": 0.8999, "puntos": 5},
                {"gte": 0.80,   "puntos": 3},
                {"lt":  0.80,   "puntos": 0}
            ]
        },

        "bono_en_equipo": {
            "rangos": {"verde": ">= 90%", "ambar": "80% - 89.99%", "rojo": "< 80%"},
            "dias_transcurridos": int(dias_trans),
            "conteo": {"verde": int(c90), "ambar": int(c80), "rojo": int(c79)},
            "dias_logrados_si_cierre_hoy": int(dias_logrados),
            "porcentaje_embarque_mtd": pct_emb_mtd,
            "puntos_mes": bono_puntos
        },
        "bono_al_cierre": {
            "hoy_si_cerrara": int(dias_logrados),
            "proyeccion_lineal": int(proy),
            "puntos_mes": bono_puntos
        }
    }


# =========================
# API públicas (JSON-only)
# =========================

def tablero_diario(db: Optional[Session], month: dt.date):
    s = get_settings(db, month)
    return _json_daily_aggregate(db, month, settings=s)

def tablero_resultados(db: Optional[Session], month: dt.date):
    s = get_settings(db, month)
    return _json_resultados(db, month, s)


# -------------------------
# Herramientas de diagnóstico
# -------------------------

def debug_json_probe(month: dt.date) -> Dict[str, Any]:
    url = _json_url_for_month(month)
    out = {"url": url}
    try:
        r = requests.get(url, headers={"User-Agent": "tablero-api/1.0"}, timeout=10, verify=certifi.where())
        out["http_status"] = f"{r.status_code} {r.reason}"
        raw = r.json()
        out["root"] = {"type": type(raw).__name__}
        if isinstance(raw, dict):
            out["root"]["keys"] = list(raw.keys())[:20]
        rows = _coerce_json_rows(raw)
        out["json_list_key_env"] = os.getenv("JSON_LIST_KEY", "").strip() or "(auto)"
        out["json_list_key_len"] = len(rows)
        if rows:
            out["json_list_sample_keys"] = list(rows[0].keys())[:30]
        norm = [_extract_fields(rows[0])] if rows else []
        if norm:
            out["normalized_sample"] = norm[0]
    except Exception as e:
        out["error"] = str(e)
    return out

from sqlalchemy import text

def debug_calendar_probe(db: Optional[Session], month: dt.date) -> Dict[str, Any]:
    first = month.replace(day=1)
    nextm = (first.replace(day=28) + dt.timedelta(days=4)).replace(day=1)

    out = {
        "month": first.isoformat(),
        "range": {"start": first.isoformat(), "end_exclusive": nextm.isoformat()},
        "db_is_none": (db is None),
    }

    if db is None:
        out["error"] = "DB session es None. No se puede leer calendario_inhabil."
        return out

    out["database"] = db.execute(text("SELECT DATABASE()")).scalar()

    cnt = db.execute(
        text("SELECT COUNT(*) FROM calendario_inhabil WHERE activo=1 AND fecha >= :s AND fecha < :e"),
        {"s": first.isoformat(), "e": nextm.isoformat()}
    ).scalar()
    out["inhabiles_count_sql"] = int(cnt or 0)

    rows = db.execute(
        text("SELECT fecha, activo, descripcion FROM calendario_inhabil WHERE activo=1 AND fecha >= :s AND fecha < :e ORDER BY fecha LIMIT 10"),
        {"s": first.isoformat(), "e": nextm.isoformat()}
    ).fetchall()
    out["inhabiles_sample_sql"] = [
        {"fecha": str(r[0]), "activo": int(r[1]), "descripcion": r[2]} for r in rows
    ]

    try:
        inh = get_inhabiles(db, first, nextm)
        out["inhabiles_count_fn"] = len(inh)
        out["inhabiles_sample_fn"] = sorted([d.isoformat() for d in list(inh)])[:10]
    except Exception as e:
        out["inhabiles_count_fn"] = 0
        out["inhabiles_error_fn"] = str(e)

    return out

def _event_calendar_to_workday(date_v: Any, time_v: Any, holidays: set, default_year: Optional[int]=None, default_month: Optional[int]=None) -> Optional[dt.date]:
    """
    Día del evento SIN regla 13:05.
    - Toma fecha+hora si existe
    - Usa dtm.date()
    - Si cae en inhábil/sáb/dom -> siguiente hábil
    """
    dtm = _merge_date_time(date_v, time_v, default_year=default_year, default_month=default_month)
    if dtm:
        return _next_workday(dtm.date(), holidays)

    d = _parse_date_any(date_v, default_year=default_year, default_month=default_month)
    return _next_workday(d, holidays) if d else None

def debug_event_coverage(month: dt.date) -> Dict[str, Any]:
    """
    Diagnóstico de cobertura de eventos por mes, usando la MISMA lógica que el tablero:
      - Surtido: ent_fec_cco + ent_tim_cco (operativo: 13:05 + inhábiles)
      - Verificación: fecha_verificacion (operativo: 13:05 + inhábiles)
      - Embarque: fecha_salida (+ hora_salida) (sin 13:05; si cae inhábil/sáb/dom -> siguiente hábil)

    Cuenta:
      - by_row: cuántos registros del JSON tienen el campo presente (raw)
      - by_order: cuántos pedidos (consolidados) terminan con fecha en el mes para surt/verif/emb,
                 y cuántos pedidos no tienen ningún evento.
    """
    first = month.replace(day=1)
    nextm = (first.replace(day=28) + dt.timedelta(days=4)).replace(day=1)

    holidays: set[dt.date] = set()  # debug coverage sin DB

    raw_rows = _fetch_json_month(first)

    norm_rows = []
    for r in raw_rows:
        x = _extract_fields(r)
        x["_default_year"] = month.year
        x["_default_month"] = month.month
        norm_rows.append(x)

    def _in_month(d0: Optional[dt.date]) -> bool:
        return bool(d0 and first <= d0 < nextm)

    by_row = {"surtido_rows": 0, "verificacion_rows": 0, "embarque_rows": 0}
    for r in raw_rows:
        if _has_value(r.get("ent_fec_cco")):
            by_row["surtido_rows"] += 1
        if _has_value(r.get("fecha_verificacion")):
            by_row["verificacion_rows"] += 1
        if _has_value(r.get("fecha_salida")):
            by_row["embarque_rows"] += 1

    orders: Dict[Any, Dict[str, Any]] = {}
    for r in norm_rows:
        if _is_cancelled(r):
            continue
        ped = r.get("pedido")
        if not ped:
            continue

        base = _base_from_record(r, holidays)
        surt_date = _event_date_from_record(r, "surt", holidays)
        verif_date = _event_date_from_record(r, "verif", holidays)
        emb_date = _event_date_from_record(r, "emb", holidays)

        # clamp a base (misma idea que el tablero)
        if base:
            if surt_date and surt_date < base:
                surt_date = base
            if verif_date and verif_date < base:
                verif_date = base
            if emb_date and emb_date < base:
                emb_date = base

        o = orders.get(ped)
        if not o:
            orders[ped] = {"base": base, "surt": surt_date, "verif": verif_date, "emb": emb_date}
        else:
            if base and (o["base"] is None or base < o["base"]):
                o["base"] = base
            if surt_date and (o["surt"] is None or surt_date < o["surt"]):
                o["surt"] = surt_date
            if verif_date and (o["verif"] is None or verif_date < o["verif"]):
                o["verif"] = verif_date
            if emb_date and (o["emb"] is None or emb_date < o["emb"]):
                o["emb"] = emb_date

    orders_total = len(orders)
    orders_with_surt = sum(1 for o in orders.values() if _in_month(o.get("surt")))
    orders_with_veri = sum(1 for o in orders.values() if _in_month(o.get("verif")))
    orders_with_emb  = sum(1 for o in orders.values() if _in_month(o.get("emb")))
    orders_with_no_event = sum(
        1 for o in orders.values()
        if (o.get("surt") is None and o.get("verif") is None and o.get("emb") is None)
    )

    return {
        "month": first.strftime("%Y-%m"),
        "rows": len(raw_rows),
        "by_row": by_row,
        "by_order": {
            "orders_total": orders_total,
            "orders_with_surtido": orders_with_surt,
            "orders_with_verificacion": orders_with_veri,
            "orders_with_embarque": orders_with_emb,
            "orders_with_no_event": orders_with_no_event,
        },
        "notes": {
            "surtido_fields": ["ent_fec_cco", "ent_tim_cco"],
            "verificacion_field": ["fecha_verificacion"],
            "embarque_field": ["fecha_salida", "hora_salida (opcional)"],
            "embarque_rule": "sin 13:05; si cae inhábil/sáb/dom -> siguiente hábil; y si queda antes de base -> se empuja a base",
        }
    }