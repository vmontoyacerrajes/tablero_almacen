from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
import os, datetime as dt
import logging
import json as pyjson

from app.db import get_db
from app.services.tablero import (
    tablero_diario,
    tablero_resultados,
    get_settings,
    debug_json_probe,   # <- para /api/debug-json
)

app = FastAPI(title="Tablero Operaciones")

# Logger (visible con: docker compose logs -f api)
logger = logging.getLogger("uvicorn.error")

# CORS
cors = os.getenv("API_CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in cors],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def _parse_month_str(s: str) -> dt.date:
    """Convierte 'YYYY-MM' a date (día = 1). Lanza ValueError si es inválido."""
    try:
        y, m = map(int, s.split("-"))
        return dt.date(y, m, 1)
    except Exception:
        raise ValueError(f"Parámetro 'month' inválido, esperaba 'YYYY-MM', recibí: {s!r}")

def _settings_to_dict(s) -> dict:
    """Serializa DashboardSettings sin campos internos de SQLAlchemy."""
    if not s:
        return {}
    out = {}
    fields = [
        "month",
        "business_days",
        "installed_capacity_surtido",
        "installed_capacity_verificacion",
        "installed_capacity_embarques",
        "unit",
        "holidays",
        "thresholds",
    ]
    for k in fields:
        if not hasattr(s, k):
            continue
        v = getattr(s, k)
        if k == "month" and isinstance(v, (dt.date, dt.datetime)):
            v = v.date().isoformat() if isinstance(v, dt.datetime) else v.isoformat()
        if k == "thresholds" and isinstance(v, str):
            try:
                v = pyjson.loads(v)
            except Exception:
                pass
        out[k] = v
    return out

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/api/tablero")
def api_tablero(month: str, db: Session = Depends(get_db)):
    try:
        first = _parse_month_str(month)
        return tablero_diario(db, first)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Fallo en /api/tablero")
        raise HTTPException(status_code=502, detail=f"/api/tablero fallo: {e}")

@app.get("/api/resultados")
def api_resultados(month: str, db: Session = Depends(get_db)):
    try:
        first = _parse_month_str(month)
        return tablero_resultados(db, first)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Fallo en /api/resultados")
        raise HTTPException(status_code=502, detail=f"/api/resultados fallo: {e}")

@app.get("/api/settings")
def api_get_settings(month: str, db: Session = Depends(get_db)):
    try:
        first = _parse_month_str(month)
        s = get_settings(db, first)
        return _settings_to_dict(s)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Fallo en /api/settings (GET)")
        raise HTTPException(status_code=502, detail=f"/api/settings fallo: {e}")

@app.put("/api/settings")
def api_put_settings(month: str, body: dict, db: Session = Depends(get_db)):
    try:
        from app.models import DashboardSettings
        first = _parse_month_str(month)

        s = db.query(DashboardSettings).filter(DashboardSettings.month == first).first()
        if not s:
            s = DashboardSettings(month=first)
            db.add(s)

        s.business_days = body.get("business_days")
        s.installed_capacity_surtido = body.get("installed_capacity_surtido")
        s.installed_capacity_verificacion = body.get("installed_capacity_verificacion")
        s.installed_capacity_embarques = body.get("installed_capacity_embarques")
        s.unit = (body.get("unit") or "pedidos")
        s.holidays = body.get("holidays")
        s.thresholds = body.get("thresholds")
        db.commit()
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Fallo en /api/settings (PUT)")
        raise HTTPException(status_code=502, detail=f"/api/settings fallo: {e}")

# ---------- NUEVO: diagnóstico del JSON ----------
@app.get("/api/debug-json")
def api_debug_json(month: str):
    """
    Inspecciona el JSON remoto del mes dado, muestra ruta sugerida a la lista (auto_path),
    llaves de ejemplo y estatus HTTP.
    """
    try:
        first = _parse_month_str(month)
        return debug_json_probe(first)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Fallo en /api/debug-json")
        raise HTTPException(status_code=502, detail=f"/api/debug-json fallo: {e}")