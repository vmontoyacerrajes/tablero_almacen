# app/services/tablero.py
import os
import json as pyjson
import datetime as dt
from typing import List, Dict, Any, Optional, Tuple

# Opcional: leer settings (capacidad, umbrales, feriados) desde DB
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
        preferred = ("rest", "rows", "data", "items", "result", "registros", "records", "tablero", "dash", "detalle", "payload", "list")
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
        if isinstance(o, dict): return {"type":"dict","keys":list(o.keys())[:20]}
        if isinstance(o, list): return {"type":"list","len":len(o)}
        return {"type":type(o).__name__}
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
        resp = requests.get(url, headers={"User-Agent":"tablero-api/1.0"}, timeout=20, verify=certifi.where())
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
    if not settings or not getattr(settings, "holidays", None):
        return set()
    try:
        return set(dt.date.fromisoformat(x) for x in settings.holidays)
    except Exception:
        return set()

def _business_days_in_month(year: int, month: int, holidays: set) -> int:
    d = dt.date(year, month, 1)
    res = 0
    while d.month == month:
        if d.weekday() < 5 and d not in holidays:
            res += 1
        d += dt.timedelta(days=1)
    return res

def _business_days_elapsed(today: dt.date, holidays: set) -> int:
    d = today.replace(day=1)
    res = 0
    while d <= today:
        if d.weekday() < 5 and d not in holidays:
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
# Settings desde DB (solo para capacidades/umbrales/feriados)
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
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12
}

def _parse_date_any(v: Any) -> Optional[dt.date]:
    if v is None:
        return None
    if isinstance(v, dt.date) and not isinstance(v, dt.datetime):
        return v
    if isinstance(v, dt.datetime):
        return v.date()
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y",
                "%Y-%m", "%Y/%m",
                "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            d = dt.datetime.strptime(s, fmt)
            if fmt in ("%Y-%m", "%Y/%m"):
                return dt.date(d.year, d.month, 1)
            return d.date()
        except Exception:
            pass
    parts = s.lower().replace(",", " ").split()
    try:
        if len(parts) == 3 and parts[1] in _MONTHS_ES:
            dd = int(parts[0]); mm = _MONTHS_ES[parts[1]]; yy = int(parts[2])
            return dt.date(yy, mm, dd)
        if len(parts) == 2 and parts[0] in _MONTHS_ES:
            mm = _MONTHS_ES[parts[0]]; yy = int(parts[1])
            return dt.date(yy, mm, 1)
    except Exception:
        pass
    return None

def _parse_time_any(v: Any) -> Optional[tuple]:
    if v is None: return None
    s = str(v).strip()
    if not s: return None
    if ":" in s:
        parts = s.split(":")
        try:
            h = int(parts[0]); m = int(parts[1]); s2 = int(parts[2]) if len(parts) > 2 else 0
            return (h, m, s2)
        except Exception:
            return None
    if s.isdigit():
        try:
            n = int(s)
            if n < 100: return (n, 0, 0)
            h, m = divmod(n, 100)
            return (h, m, 0)
        except Exception:
            return None
    return None

def _parse_datetime_any(v: Any) -> Optional[dt.datetime]:
    if v is None:
        return None
    if isinstance(v, dt.datetime):
        return v
    if isinstance(v, dt.date):
        return dt.datetime(v.year, v.month, v.day)
    s = str(v).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return dt.datetime.strptime(s, fmt)
        except Exception:
            pass
    d = _parse_date_any(s)
    if d:
        return dt.datetime(d.year, d.month, d.day)
    return None

def _merge_date_time(date_v: Any, time_v: Any) -> Optional[dt.datetime]:
    dtm = _parse_datetime_any(date_v)
    if dtm and (dtm.hour or dtm.minute or dtm.second):
        return dtm
    d = _parse_date_any(date_v)
    if not d:
        return None
    t = _parse_time_any(time_v)
    if not t:
        return dt.datetime(d.year, d.month, d.day)
    return dt.datetime(d.year, d.month, d.day, t[0], t[1], t[2])

def _has_value(x: Any) -> bool:
    if x is None:
        return False
    s = str(x).strip()
    if not s:
        return False
    if s in ("0000-00-00", "0000-00-00 00:00:00", "null", "None"):
        return False
    return True


# =========================
# Mapeo de campos JSON
# =========================

def _extract_fields(rec: Dict[str, Any]) -> Dict[str, Any]:
    def g(*names):
        for n in names:
            if n in rec and rec[n] not in (None, ""):
                return rec[n]
        return None

    return {
        # Identificador y métricas
        "pedido":    g("pedido", "pedido_cco", "ped_cco", "pedCCO"),
        "num_part":  g("num_part", "part_cco", "partCCO"),

        # Estado (para cancelar/omitir contablemente)
        "proceso":   g("proceso", "estado", "status", "est"),

        # Fecha/hora de alta → DÍA BASE
        "fecha_alta": g("fecha_alta", "fecha", "falta", "created_at"),

        # Surtido
        "ped_fec_cje": g("ped_fec_cje", "fecha_surtido", "fsurt", "fechaSurtido"),
        "ped_tim_cje": g("ped_tim_cje", "fsurt_hora", "hora_surtido"),

        # Verificación
        "ent_fec_cco":  g("ent_fec_cco", "fecha_verif", "fv", "verif_fec", "fecha_verificacion"),
        "ent_tim_cco":  g("ent_tim_cco", "hora_verif", "verif_tim"),

        # Embarque real
        "fecha_salida": g("fecha_salida", "f_salida", "sal_fec", "fsalida", "fechaSalida"),

        # Fallbacks
        "ultimo_mod": g("ultimo_mod", "updated_at", "last_update"),
    }

def _is_cancelled(rec: Dict[str, Any]) -> bool:
    v = rec.get("proceso")
    if v is None: return False
    s = str(v).strip().lower()
    if "can" in s: return True
    try:
        n = int(s.split()[0])
        if n == 6: return True
    except Exception:
        pass
    return False

def _is_closed_status(rec: Dict[str, Any]) -> bool:
    """Heurística ligera: si 'proceso' contiene '5' y 'cer' (cerrado), o textos 'cerr', 'final', 'complet'."""
    v = rec.get("proceso")
    if v is None: return False
    s = str(v).strip().lower()
    if ("5" in s and "cer" in s) or ("cerr" in s) or ("final" in s) or ("complet" in s):
        return True
    return False


# =========================
# FECHA DE EMBARQUE (BASE) Y EVENTOS
# =========================

def _embarque_from_record(rec: Dict[str, Any]) -> Optional[dt.date]:
    """
    DÍA BASE desde fecha_alta:
    - base = DATE(fecha_alta); si HHMM >= 13:05 ⇒ +1 día
    - si base cae sábado ⇒ +2; si domingo ⇒ +1
    """
    dtm = _parse_datetime_any(rec.get("fecha_alta"))
    if not dtm:
        dtm = _merge_date_time(rec.get("fecha_alta"), None)
    if not dtm:
        return None

    hhmm = dtm.hour * 100 + dtm.minute
    base = dtm.date()
    if hhmm >= 1305:
        base = base + dt.timedelta(days=1)

    if base.weekday() == 5:      # sábado
        base = base + dt.timedelta(days=2)
    elif base.weekday() == 6:    # domingo
        base = base + dt.timedelta(days=1)

    return base

def _event_date_from_record(rec: Dict[str, Any], which: str) -> Optional[dt.date]:
    """
    Devuelve la fecha de evento según 'which':
    - 'surt'  -> ped_fec_cje (+ hora si viene)
    - 'verif' -> ent_fec_cco (+ hora si viene)
    - 'emb'   -> fecha_salida  (por defecto)
    """
    if which == "surt":
        dt_sur = _merge_date_time(rec.get("ped_fec_cje"), rec.get("ped_tim_cje"))
        return dt_sur.date() if dt_sur else _parse_date_any(rec.get("ped_fec_cje"))

    if which == "verif":
        dt_ver = _merge_date_time(rec.get("ent_fec_cco"), rec.get("ent_tim_cco"))
        return dt_ver.date() if dt_ver else _parse_date_any(rec.get("ent_fec_cco"))

    # emb por defecto
    return _parse_date_any(rec.get("fecha_salida"))


# =========================
# Serie diaria con BACKLOG neto + EMBARQUES por fecha de salida
# =========================

def _json_rows_for_month(month: dt.date) -> List[Dict[str, Any]]:
    raw = _fetch_json_month(month)
    return [_extract_fields(r) for r in raw]

def _json_daily_aggregate(month: dt.date) -> List[Dict[str, Any]]:
    """
    - Construye 'orders' incluyendo:
        * pedidos con base < fin de mes (para backlog del mes)
        * y también pedidos cuya fecha de EMBARQUE/VERIF/SURT cae dentro del mes,
          aunque su base sea de meses previos (para no perder embarques del backlog).

    - Evento 'embarcado' se toma de:
        * por defecto: 'fecha_salida'
        * configurable por ENV EMB_EVENT_FIELD=[fecha_salida|ent_fec_cco|fecha_verificacion]
          (usaremos 'ent_fec_cco' si así lo pides)

    - Para cada día D:
        * pedidos = base_idx[D]  (bases del mes)
        * pend_prev_neto(D) = pedidos con base < D y sin emb_date < D y no cancelados/cerrados
        * total_a_surtir_neto(D) = pedidos + pend_prev_neto(D)
        * eventos del día = conteo de fechas == D (surt/verif/emb) dentro del mes
        * % = eventos_D / total_a_surtir_neto(D)
    """
    first = month.replace(day=1)
    nextm = (first.replace(day=28) + dt.timedelta(days=4)).replace(day=1)

    emb_field_env = (os.getenv("EMB_EVENT_FIELD") or "").strip().lower()
    emb_selector = "emb"  # 'emb' usa fecha_salida
    if emb_field_env in ("ent_fec_cco", "fecha_verificacion", "verif"):
        emb_selector = "verif"

    raw_rows = _json_rows_for_month(month)

    # 1) Consolidar por pedido
    from collections import defaultdict
    orders: Dict[Any, Dict[str, Any]] = {}

    for r in raw_rows:
        if _is_cancelled(r):
            # si está cancelado, igual podríamos ignorarlo por completo
            continue

        ped = r.get("pedido")
        if not ped:
            continue

        base = _embarque_from_record(r)
        emb_date = _event_date_from_record(r, "emb")  # fecha_salida
        ver_date = _event_date_from_record(r, "verif")
        sur_date = _event_date_from_record(r, "surt")

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
            # base más temprana
            if base and (o["base"] is None or base < o["base"]):
                o["base"] = base
            # earliest event dates
            if sur_date and (o["surt_date"] is None or sur_date < o["surt_date"]):
                o["surt_date"] = sur_date
            if ver_date and (o["verif_date"] is None or ver_date < o["verif_date"]):
                o["verif_date"] = ver_date
            if emb_date and (o["emb_date"] is None or emb_date < o["emb_date"]):
                o["emb_date"] = emb_date

        # Partidas: máximo Part CCO
        try:
            pv = int(r.get("num_part") or 0)
            if pv > o["parts_max"]:
                o["parts_max"] = pv
        except Exception:
            pass

        # Si detectamos un estado cerrado en algún renglón del mismo pedido
        if _is_closed_status(r):
            o["closed"] = True

    # 2) Índices por día dentro del mes
    base_idx: Dict[dt.date, list] = defaultdict(list)
    surt_idx: Dict[dt.date, int] = defaultdict(int)
    ver_idx:  Dict[dt.date, int] = defaultdict(int)
    emb_idx:  Dict[dt.date, int] = defaultdict(int)

    for ped, o in orders.items():
        # Pedidos del mes por base (para 'pedidos' del día y 'partidas')
        if o["base"] and (first <= o["base"] < nextm):
            base_idx[o["base"]].append(ped)

        # Eventos contados si caen dentro del mes (aunque la base sea anterior)
        if o["surt_date"] and (first <= o["surt_date"] < nextm):
            surt_idx[o["surt_date"]] += 1
        if o["verif_date"] and (first <= o["verif_date"] < nextm):
            ver_idx[o["verif_date"]] += 1
        if o["emb_date"] and (first <= o["emb_date"] < nextm):
            emb_idx[o["emb_date"]] += 1

    # 3) Serie diaria
    out: List[Dict[str, Any]] = []
    d = first
    while d < nextm:
        pedidos_del_dia = base_idx.get(d, [])
        pedidos = len(pedidos_del_dia)
        partidas = sum(orders[p]["parts_max"] for p in pedidos_del_dia)

        # Backlog neto al inicio del día d:
        # base < d y sin emb_date < d, y no cancelados/cerrados
        pend_prev_neto = 0
        for o in orders.values():
            if o["base"] and (o["base"] < d):
                if (o["emb_date"] is None) or (o["emb_date"] >= d):
                    if not o.get("closed", False):
                        pend_prev_neto += 1

        total_neto = pedidos + pend_prev_neto

        # Eventos del día D (dentro del mes)
        surt_hoy = surt_idx.get(d, 0)
        ver_hoy  = ver_idx.get(d, 0)
        emb_hoy  = emb_idx.get(d, 0)

        # % contra total_neto
        pct_surt_neto = (surt_hoy / total_neto) if total_neto else None
        pct_ver_neto  = (ver_hoy  / total_neto) if total_neto else None
        pct_emb_neto  = (emb_hoy  / total_neto) if total_neto else None

        # Debug para que puedas auditar rápidamente
        dbg = {
            "emb_event_source": ("fecha_salida" if emb_selector == "emb" else "ent_fec_cco/fecha_verificacion"),
            "base_lt_d": sum(1 for o in orders.values() if o["base"] and o["base"] < d),
            "abiertos_sin_salida": sum(1 for o in orders.values()
                                       if o["base"] and o["base"] < d
                                       and ((o["emb_date"] is None) or (o["emb_date"] >= d))
                                       and not o.get("closed", False)),
        }

        out.append({
            "dia": d.isoformat(),
            "schema_ver": "v8-emb-configurable-incluye-prebase",

            # ped/part por base del mes
            "pedidos": pedidos,
            "partidas": partidas,

            # backlog neto
            "pend_surtido_prev_neto": pend_prev_neto,
            "total_a_surtir_neto": total_neto,

            # eventos/día
            "surtidos": surt_hoy,
            "verificados": ver_hoy,
            "embarcados": emb_hoy,

            # % netos
            "pct_surtido": pct_surt_neto,
            "pct_verificacion": pct_ver_neto,
            "pct_embarque": pct_emb_neto,

            # debug
            "debug_backlog_neto": dbg
        })

        d += dt.timedelta(days=1)

    return out


# =========================
# KPIs de Resultados (encabezado)
# =========================

def _avg(v, d):
    return (v / d) if d else None

def _json_resultados(month: dt.date, settings) -> Dict[str, Any]:
    first = month.replace(day=1)
    month_end = (first.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
    today = dt.date.today()
    limit = min(today, month_end)

    holidays = _holidays(settings)
    th = _thresholds(settings)

    diario = _json_daily_aggregate(month)

    def _d(s):
        return _parse_date_any(s) if s else None

    mtd_rows = [r for r in diario if (r.get("dia") and _d(r["dia"]) and _d(r["dia"]) <= limit)]

    # Acumulados MTD
    ped_mtd = sum(int(r["pedidos"] or 0) for r in mtd_rows)
    part_mtd = sum(int(r["partidas"] or 0) for r in mtd_rows)
    sur_mtd  = sum(int(r["surtidos"] or 0) for r in mtd_rows)
    ver_mtd  = sum(int(r["verificados"] or 0) for r in mtd_rows)
    emb_mtd  = sum(int(r["embarcados"] or 0) for r in mtd_rows)

    # Resultado del día (día = limit)
    rdia = next((r for r in diario if r["dia"] == limit.isoformat()), None) or {
        "dia": limit.isoformat(), "pedidos": 0, "partidas": 0,
        "surtidos": 0, "verificados": 0, "embarcados": 0,
        "pend_surtido_prev_neto": 0, "total_a_surtir_neto": 0
    }

    # Días hábiles y transcurridos
    dias_hab_mes = settings.business_days if settings and getattr(settings, "business_days", None) \
        else _business_days_in_month(first.year, first.month, holidays)
    dias_trans = _business_days_elapsed(limit, holidays)
    dias_pend = max(dias_hab_mes - dias_trans, 0)

    # Capacidades
    cap_s = getattr(settings, "installed_capacity_surtido", None) if settings else None
    cap_v = getattr(settings, "installed_capacity_verificacion", None) if settings else None
    cap_e = getattr(settings, "installed_capacity_embarques", None) if settings else None

    # Porcentajes MTD (contra 'pedidos_mtd' como antes; si quieres contra total_a_surtir_neto MTD avísame)
    pct_surt_mtd = (sur_mtd / ped_mtd) if ped_mtd else None
    pct_ver_mtd  = (ver_mtd / ped_mtd) if ped_mtd else None
    pct_emb_mtd  = (emb_mtd / ped_mtd) if ped_mtd else None

    bono_puntos = _bono_points_from_pct(pct_emb_mtd)

    # Días verdes (>=90% verificación por día) — legado
    c90 = c80 = c79 = 0
    for r in mtd_rows:
        ped = int(r["pedidos"] or 0)
        ver = int(r["verificados"] or 0)
        pct = (ver / ped) if ped else None
        if pct is None:
            continue
        if pct >= 0.9: c90 += 1
        elif pct >= 0.8: c80 += 1
        else: c79 += 1

    dias_logrados = c90
    tasa_verde = (c90 / dias_trans) if dias_trans else 0
    proy = round(tasa_verde * dias_hab_mes) if dias_hab_mes else 0

    return {
        "periodo": first.strftime("%Y-%m"),
        "dias_habiles_mes": dias_hab_mes,
        "dias_transcurridos": dias_trans,
        "dias_pendientes": dias_pend,

        "pedidos_mtd": ped_mtd,
        "partidas_mtd": part_mtd,
        "surtidos_mtd": sur_mtd,
        "verificados_mtd": ver_mtd,
        "embarcados_mtd": emb_mtd,

        "pct_surtido": pct_surt_mtd,
        "pct_verificacion": pct_ver_mtd,
        "pct_embarque": pct_emb_mtd,

        "capacidad_diaria": {"surtido": cap_s, "verificacion": cap_v, "embarques": cap_e},
        "dias_equivalentes": {
            "surtido": (sur_mtd // cap_s) if cap_s else None,
            "verificacion": (ver_mtd // cap_v) if cap_v else None,
            "embarques": (emb_mtd // cap_e) if cap_e else None
        },

        "bono_header": {
            "puntos": bono_puntos,
            "porcentaje_embarque_mtd": pct_emb_mtd
        },

        "promedio_diario_mtd": {
            "pedidos": ped_mtd,
            "partidas": part_mtd,
            "surtidos": sur_mtd,
            "verificados": ver_mtd,
            "embarcados": emb_mtd
        },

        # Resultado del día con backlog neto
        "resultados_dia": {
            "dia": rdia["dia"],
            "pedidos": int(rdia.get("pedidos") or 0),
            "partidas": int(rdia.get("partidas") or 0),
            "surtidos": int(rdia.get("surtidos") or 0),
            "verificados": int(rdia.get("verificados") or 0),
            "embarcados": int(rdia.get("embarcados") or 0),
            "pend_surtido_prev": int(rdia.get("pend_surtido_prev_neto") or 0),
            "total_a_surtir": int(rdia.get("total_a_surtir_neto") or 0)
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
            "dias_transcurridos": dias_trans,
            "conteo": {"verde": c90, "ambar": c80, "rojo": c79},
            "dias_logrados_si_cierre_hoy": dias_logrados,
            "porcentaje_embarque_mtd": pct_emb_mtd,
            "puntos_mes": bono_puntos
        },
        "bono_al_cierre": {
            "hoy_si_cerrara": dias_logrados,
            "proyeccion_lineal": proy,
            "puntos_mes": bono_puntos
        }
    }


# =========================
# API públicas (JSON-only)
# =========================

def tablero_diario(db: Optional[Session], month: dt.date):
    return _json_daily_aggregate(month)

def tablero_resultados(db: Optional[Session], month: dt.date):
    s = get_settings(db, month)  # solo para capacidades/umbrales/feriados
    return _json_resultados(month, s)


# -------------------------
# Herramienta de diagnóstico
# -------------------------
def debug_json_probe(month: dt.date) -> Dict[str, Any]:
    url = _json_url_for_month(month)
    out = {"url": url}
    try:
        r = requests.get(url, headers={"User-Agent":"tablero-api/1.0"}, timeout=10, verify=certifi.where())
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