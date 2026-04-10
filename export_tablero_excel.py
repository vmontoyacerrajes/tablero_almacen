# export_tablero_excel.py
import sys
import json
import requests
import pandas as pd

def main(month: str, base_url: str = "http://localhost:8000"):
    # 1) bajar datos
    r_res = requests.get(f"{base_url}/api/resultados", params={"month": month}, timeout=60)
    r_res.raise_for_status()
    resultados = r_res.json()

    r_tab = requests.get(f"{base_url}/api/tablero", params={"month": month}, timeout=60)
    r_tab.raise_for_status()
    tablero = r_tab.json()

    # 2) normalizar a tablas
    df_tab = pd.json_normalize(tablero)
    df_res = pd.json_normalize(resultados)

    # orden sugerido (si existen columnas)
    prefer = [
        "dia","is_habil","dia_habil_num",
        "pedidos","pend_surtido_prev_neto","total_a_surtir_neto",
        "surtidos","verificados","embarcados",
        "pct_surtido","pct_verificacion","pct_embarque",
        "capacidad_diaria","capacidad_instalada_acum","capacidad_utilizada_acum","pct_capacidad_utilizada_acum",
        # si tu backend ya trae atrasos
        "atrasos.surtido_inicio","atrasos.verificacion_inicio","atrasos.embarque_inicio",
        "atrasos.surtido_resuelto_hoy","atrasos.verificacion_resuelto_hoy","atrasos.embarque_resuelto_hoy",
    ]
    cols = [c for c in prefer if c in df_tab.columns]
    rest = [c for c in df_tab.columns if c not in cols]
    df_tab = df_tab[cols + rest]

    # 3) escribir excel
    out = f"tablero_{month}.xlsx"
    with pd.ExcelWriter(out, engine="openpyxl") as w:
        df_res.to_excel(w, sheet_name="resultados", index=False)
        df_tab.to_excel(w, sheet_name="tablero_diario", index=False)

        # hoja extra: “tabla_para_ui” con denominadores y % como los debería usar UI
        df_ui = df_tab.copy()
        if "total_a_surtir_neto" in df_ui.columns:
            denom = df_ui["total_a_surtir_neto"].replace({0: pd.NA})
            for k in ("surtidos","verificados","embarcados"):
                if k in df_ui.columns:
                    df_ui[f"pct_{k}_sobre_total"] = (df_ui[k] / denom)
        df_ui.to_excel(w, sheet_name="ui_checks", index=False)

    print(f"OK -> {out}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python export_tablero_excel.py 2026-01 [base_url]")
        sys.exit(1)
    month = sys.argv[1]
    base_url = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:8000"
    main(month, base_url)