"""Bronze -> silver -> golden de Fundamentales PIT.

    python -m productos.fundamentales_pit.capas bronze      # necesita DATABURSATIL_API_TOKEN
    python -m productos.fundamentales_pit.capas silver
    python -m productos.fundamentales_pit.capas golden
    python -m productos.fundamentales_pit.capas todo        # silver + golden + calidad
    python -m productos.fundamentales_pit.capas todo --bronze  # además baja bronze primero

BRONZE  data/fundamentals/bronze/<ticker>/<financieros>/<periodo>.json
        La respuesta de la API tal cual, con la fecha de descarga. Nunca el token.
        Sirve para rehacer silver sin volver a llamar a la API.
SILVER  data/fundamentals/silver/fundamentales_trimestre.parquet
        Un trimestre por fila, con MONTOS (no solo ratios) y de dónde salió.
GOLDEN  data/fundamentals/golden/fundamentales_pit.parquet
        Lo que consumen dashboard, agente y modelos. Columnas fund_* compatibles
        con src/ml/features.py y build_dashboard.py.

Dos correcciones respecto a src/api/fundamentals.py (que no se modifica):

1. YoY por FECHA, no por posición. pct_change(4) compara contra "4 filas
   atrás"; si falta un trimestre, compara contra el trimestre equivocado.
   En el parquet del repo eso pasa en los 28 registros de 2026T1.
2. Cifra original antes que comparativo. La ingesta original pide solo años
   pares y saca los impares del comparativo del reporte del año siguiente,
   que se publica un año después y puede venir reexpresado. Aquí, si existe
   el reporte propio del trimestre, gana; el comparativo solo rellena huecos y
   queda marcado (origen = "comparativo").
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from productos.fundamentales_pit.pit import BASE, DIVIDENDOS_GOLDEN, GOLDEN, ROOT

logger = logging.getLogger(__name__)

VERSION_PRODUCTO = "1.0.0"
FUENTE = "databursatil:/v2/financieros"
BRONZE = BASE / "bronze"
SILVER = BASE / "silver" / "fundamentales_trimestre.parquet"
LEGACY = ROOT / "data" / "fundamentals.parquet"
DIVIDENDOS_MANUAL = BASE / "dividendos_manual.csv"
API = "https://api.databursatil.com/v2/financieros"

# Campos de resultado_trimestre (nombres tal como los usa src/api/fundamentals.py).
CAMPOS = {
    "ingresos": ("revenue",),
    "utilidad_bruta": ("grossprofit",),
    "utilidad_operacion": ("profitlossfromoperatingactivities",),
    "utilidad_neta": ("profitloss",),
    # UPA: NO verificado que la API lo traiga. Si viene, se toma; si no, queda NaN.
    "upa": ("basicearningslosspershare", "basicearningspershare",
            "dilutedearningslosspershare", "earningspershare"),
}

SILVER_COLS = ["ticker", "quarter_end", "ingresos", "utilidad_bruta", "utilidad_operacion",
               "utilidad_neta", "upa", "origen", "reexpresado", "formato",
               "primera_descarga", "fuente"]

GOLDEN_COLS = [
    "ticker", "quarter_end", "available_date",
    "fund_revenue_abs", "fund_gross_abs", "fund_oper_abs", "fund_net_abs", "fund_eps",
    "fund_gross_margin", "fund_oper_margin", "fund_net_margin",
    "fund_revenue_yoy", "fund_earnings_yoy",
    "origen", "reexpresado", "formato", "fuente", "version_producto", "generado_en",
]

DIVIDENDOS_COLS = ["ticker", "fecha_anuncio", "fecha_corte", "fecha_pago",
                   "monto_por_accion", "moneda", "fuente", "available_date"]


def _lag_dias() -> int:
    from src.api.fundamentals import PUBLICATION_LAG_DAYS
    return int(PUBLICATION_LAG_DAYS)


def _mapa_emisoras() -> dict[str, str]:
    from src.data.bmv_catalog import BMV_EMISORA_FUNDAMENTALS
    return dict(BMV_EMISORA_FUNDAMENTALS)


# ------------------------------------------------------------------ BRONZE

def periodos(year_start: int, year_end: int, estricto: bool = True) -> list[str]:
    """estricto=True: los 4 trimestres de TODOS los años (el doble de llamadas que
    la ingesta original, pero cada trimestre trae su cifra original)."""
    anios = range(year_start, year_end + 1) if estricto else range(year_start + 1, year_end + 1, 2)
    return [f"{q}T_{y}" for y in anios for q in (1, 2, 3, 4)]


def _es_error_de_red(archivo: Path) -> bool:
    """True si el JSON guardado no es una respuesta de la API (status None: conexión
    caída, timeout) o no se puede leer. Esos archivos se vuelven a pedir."""
    try:
        return json.loads(archivo.read_text(encoding="utf-8")).get("status_code") is None
    except Exception:
        return True


# Una respuesta que no es 200 de un trimestre reciente puede ser "todavía no se
# publica": la API contesta 400 ("periodo desconocido") hasta que sale el reporte.
VENTANA_REINTENTO_DIAS = 365


def _fin_de_trimestre(periodo: str) -> pd.Timestamp:
    """'3T_2026' -> 2026-09-30."""
    q, anio = periodo.split("T_")
    return pd.Timestamp(year=int(anio), month=int(q) * 3, day=1) + pd.offsets.MonthEnd(0)


def _reintentar(archivo: Path, periodo: str, hoy: pd.Timestamp | None = None) -> bool:
    """True si hay que volver a pedir un archivo de bronze que ya existe:
    - guardó un error de red (status None) o no se puede leer;
    - guardó una respuesta que no es 200 de un trimestre que cerró hace menos de
      VENTANA_REINTENTO_DIAS (o que todavía no cierra): puede que ya se haya publicado.
    Un 400 de un trimestre viejo (por ejemplo, los bancos que la API no ofrece) se
    queda como está, para no repetir esas llamadas en cada corrida."""
    if _es_error_de_red(archivo):
        return True
    status = json.loads(archivo.read_text(encoding="utf-8")).get("status_code")
    if status == 200:
        return False
    hoy = pd.Timestamp(hoy) if hoy is not None else pd.Timestamp.now().normalize()
    return _fin_de_trimestre(periodo) >= hoy - pd.Timedelta(days=VENTANA_REINTENTO_DIAS)


def bajar_bronze(token: str, tickers: list[str] | None = None, year_start: int = 2017,
                 year_end: int | None = None, estricto: bool = True,
                 financieros: tuple[str, ...] = ("resultado_trimestre",),
                 forzar: bool = False, pausa: float = 0.2, http=None, dest: Path = BRONZE) -> dict:
    """Guarda cada respuesta de la API tal cual. Idempotente: si el archivo ya
    existe no vuelve a llamar (salvo forzar=True), excepto si guardó un error de red
    o un "periodo desconocido" de un trimestre reciente (ver _reintentar)."""
    import requests
    http = http or requests
    year_end = year_end or datetime.now().year
    mapa = _mapa_emisoras()
    tickers = tickers or sorted(mapa)
    resumen = {"llamadas": 0, "omitidas_ya_existian": 0, "errores": 0, "por_ticker": {}}
    for ticker in tickers:
        emisora = mapa.get(ticker)
        if not emisora:
            resumen["por_ticker"][ticker] = "sin mapeo en bmv_catalog"
            continue
        ok = 0
        for fin in financieros:
            for per in periodos(year_start, year_end, estricto):
                archivo = dest / ticker / fin / f"{per}.json"
                if archivo.exists() and not forzar and not _reintentar(archivo, per):
                    resumen["omitidas_ya_existian"] += 1
                    continue
                try:
                    r = http.get(API, params={"token": token, "emisora": emisora,
                                              "periodo": per, "financieros": fin}, timeout=30)
                    status = r.status_code
                    try:
                        payload = r.json()
                    except ValueError:
                        payload = {"_texto_no_json": r.text[:2000]}
                except Exception as exc:  # red caída, timeout
                    status, payload = None, {"_error": str(exc)}
                    resumen["errores"] += 1
                resumen["llamadas"] += 1
                archivo.parent.mkdir(parents=True, exist_ok=True)
                archivo.write_text(json.dumps({
                    "ticker": ticker, "emisora": emisora, "periodo": per, "financieros": fin,
                    "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "status_code": status, "payload": payload,
                }, ensure_ascii=False), encoding="utf-8")
                ok += status == 200
                if pausa:
                    time.sleep(pausa)
        resumen["por_ticker"][ticker] = f"{ok} respuestas 200"
    return resumen


# ------------------------------------------------------------------ SILVER

def _monto(item) -> float:
    """Line item ['etiqueta', monto] -> monto; cualquier otra cosa -> NaN."""
    if isinstance(item, list) and len(item) >= 2 and isinstance(item[1], (int, float)):
        return float(item[1])
    if isinstance(item, (int, float)) and not isinstance(item, bool):
        return float(item)
    return np.nan


def _fin_de_periodo(pkey: str) -> pd.Timestamp | None:
    """'2024-01-01_2024-03-31' -> 2024-03-31."""
    try:
        return pd.Timestamp(pkey.split("_")[1])
    except Exception:
        return None


def _periodo_de(quarter_end: pd.Timestamp) -> str:
    return f"{quarter_end.quarter}T_{quarter_end.year}"


def filas_de_bronze(registro: dict) -> list[dict]:
    """Un archivo bronze -> filas candidatas de silver (una por periodo que traiga)."""
    filas = []
    fin = registro.get("financieros", "resultado_trimestre")
    data = (registro.get("payload") or {}).get(fin)
    if registro.get("status_code") != 200 or not isinstance(data, dict):
        return filas
    for pkey, campos in data.items():
        qend = _fin_de_periodo(pkey)
        if qend is None or not isinstance(campos, dict):
            continue
        fila = {"ticker": registro["ticker"], "quarter_end": qend,
                "origen": "reporte_propio" if _periodo_de(qend) == registro["periodo"] else "comparativo",
                "descargado": pd.Timestamp(registro["fetched_at"]).tz_convert(None)}
        for col, claves in CAMPOS.items():
            fila[col] = next((v for k in claves if not np.isnan(v := _monto(campos.get(k)))), np.nan)
        # Bancos y aseguradoras no reportan revenue/grossprofit: se marca en vez de
        # descartar la emisora entera (hipótesis de por qué fallan GFNORTEO,
        # GENTERA, Q y GFINBURO; confirmar con el JSON crudo).
        sin_estructura = np.isnan(fila["ingresos"]) and np.isnan(fila["utilidad_bruta"])
        fila["formato"] = "sin_ingresos_corporativos" if sin_estructura and campos else "corporativo"
        filas.append(fila)
    return filas


def _elegir(grupo: pd.DataFrame) -> pd.Series:
    """Una fila por (ticker, trimestre): gana la cifra ORIGINAL (reporte propio,
    la primera descarga); el comparativo solo si no hay otra cosa."""
    g = grupo.assign(_rank=(grupo["origen"] != "reporte_propio").astype(int))
    g = g.sort_values(["_rank", "descargado"])
    elegida = g.iloc[0].copy()
    otras = g.iloc[1:]
    reexp = False
    for c in ("ingresos", "utilidad_neta"):
        a = elegida[c]
        for b in otras[c]:
            if np.isfinite(a) and np.isfinite(b) and abs(a) > 0 and abs(b - a) / abs(a) > 0.005:
                reexp = True
    elegida["reexpresado"] = reexp
    elegida["primera_descarga"] = g["descargado"].min()
    return elegida.drop(labels=["_rank", "descargado"])


def silver_desde_bronze(origen: Path = BRONZE) -> pd.DataFrame:
    filas = []
    for archivo in sorted(origen.glob("*/*/*.json")):
        filas.extend(filas_de_bronze(json.loads(archivo.read_text(encoding="utf-8"))))
    if not filas:
        return pd.DataFrame(columns=SILVER_COLS)
    df = pd.DataFrame(filas)
    df = (df.groupby(["ticker", "quarter_end"], group_keys=False)
            .apply(_elegir, include_groups=False).reset_index())
    df["fuente"] = FUENTE
    return df[SILVER_COLS].sort_values(["ticker", "quarter_end"]).reset_index(drop=True)


def silver_desde_legacy(path: Path = LEGACY) -> pd.DataFrame:
    """Respaldo sin token: data/fundamentals.parquet trae RATIOS, no montos (salvo
    fund_revenue_abs / fund_net_abs si se generó con la versión nueva). Se
    conservan los ratios en columnas _legacy para que golden los use."""
    d = pd.read_parquet(path)
    d["quarter_end"] = pd.to_datetime(d["quarter_end"])
    out = pd.DataFrame({
        "ticker": d["ticker"].astype(str),
        "quarter_end": d["quarter_end"],
        "ingresos": d.get("fund_revenue_abs", np.nan),
        "utilidad_bruta": np.nan,
        "utilidad_operacion": np.nan,
        "utilidad_neta": d.get("fund_net_abs", np.nan),
        "upa": np.nan,
        "origen": "legacy_parquet",
        "reexpresado": False,
        "formato": "corporativo",
        "primera_descarga": pd.NaT,
        "fuente": f"{FUENTE} (via data/fundamentals.parquet)",
    })
    for c in ("fund_gross_margin", "fund_oper_margin", "fund_net_margin",
              "fund_revenue_yoy", "fund_earnings_yoy"):
        out[f"{c}_legacy"] = d[c].values
    return out.sort_values(["ticker", "quarter_end"]).reset_index(drop=True)


# ------------------------------------------------------------------ GOLDEN

def _yoy_por_fecha(df: pd.DataFrame, col: str) -> pd.Series:
    """Crecimiento contra el MISMO trimestre del año anterior, buscado por fecha.
    Si ese trimestre no existe, NaN (nunca otro trimestre)."""
    clave = df["quarter_end"].dt.year * 4 + df["quarter_end"].dt.quarter
    previo = pd.Series(df[col].values, index=pd.MultiIndex.from_arrays([df["ticker"], clave]))
    previo = previo[~previo.index.duplicated()]
    idx = pd.MultiIndex.from_arrays([df["ticker"], clave - 4])
    antes = previo.reindex(idx).to_numpy()
    actual = df[col].to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        yoy = np.where(np.isfinite(antes) & (antes != 0), actual / antes - 1, np.nan)
    return pd.Series(yoy, index=df.index)


def _yoy_legacy_valido(df: pd.DataFrame) -> pd.Series:
    """El YoY legacy se calculó con pct_change(4). Solo es correcto si las 4 filas
    previas son los 4 trimestres inmediatos anteriores (sin huecos)."""
    clave = df["quarter_end"].dt.year * 4 + df["quarter_end"].dt.quarter
    ok = pd.Series(True, index=df.index)
    for k in range(1, 5):
        ok &= (clave - df.groupby("ticker")["quarter_end"].transform(
            lambda s, k=k: (s.dt.year * 4 + s.dt.quarter).shift(k))) == k
    return ok


def construir_golden(silver: pd.DataFrame, generado_en: pd.Timestamp | None = None) -> pd.DataFrame:
    s = silver.sort_values(["ticker", "quarter_end"]).reset_index(drop=True).copy()
    s["quarter_end"] = pd.to_datetime(s["quarter_end"])
    g = pd.DataFrame({"ticker": s["ticker"], "quarter_end": s["quarter_end"]})
    g["available_date"] = s["quarter_end"] + pd.Timedelta(days=_lag_dias())
    g["fund_revenue_abs"] = s["ingresos"].astype(float)
    g["fund_gross_abs"] = s["utilidad_bruta"].astype(float)
    g["fund_oper_abs"] = s["utilidad_operacion"].astype(float)
    g["fund_net_abs"] = s["utilidad_neta"].astype(float)
    g["fund_eps"] = s["upa"].astype(float)

    ingresos = g["fund_revenue_abs"].where(g["fund_revenue_abs"] != 0)
    for col, monto in (("fund_gross_margin", "fund_gross_abs"),
                       ("fund_oper_margin", "fund_oper_abs"),
                       ("fund_net_margin", "fund_net_abs")):
        calc = g[monto] / ingresos
        legacy = s.get(f"{col}_legacy")
        g[col] = calc if legacy is None else calc.where(calc.notna(), legacy)

    yoy_rev = _yoy_por_fecha(g, "fund_revenue_abs")
    yoy_net = _yoy_por_fecha(g, "fund_net_abs")
    if "fund_revenue_yoy_legacy" in s:
        valido = _yoy_legacy_valido(g)
        yoy_rev = yoy_rev.where(yoy_rev.notna(), s["fund_revenue_yoy_legacy"].where(valido))
        yoy_net = yoy_net.where(yoy_net.notna(), s["fund_earnings_yoy_legacy"].where(valido))
    g["fund_revenue_yoy"] = yoy_rev
    g["fund_earnings_yoy"] = yoy_net

    # Mismo recorte que la ingesta original para ratios (no para montos).
    for c in ("fund_gross_margin", "fund_oper_margin", "fund_net_margin",
              "fund_revenue_yoy", "fund_earnings_yoy"):
        g[c] = g[c].replace([np.inf, -np.inf], np.nan).clip(-5, 5)

    g["origen"] = s["origen"]
    g["reexpresado"] = s["reexpresado"].fillna(False).astype(bool)
    g["formato"] = s["formato"]
    g["fuente"] = s["fuente"]
    g["version_producto"] = VERSION_PRODUCTO
    g["generado_en"] = generado_en or pd.Timestamp.now().normalize()
    return g[GOLDEN_COLS]


def construir_dividendos(path: Path = DIVIDENDOS_MANUAL) -> pd.DataFrame:
    """Dividendos con sus tres fechas. Fuente pendiente (ver README): mientras
    tanto se lee un CSV capturado a mano con las columnas de la plantilla."""
    if not path.exists():
        return pd.DataFrame({c: pd.Series(dtype="object") for c in DIVIDENDOS_COLS})
    d = pd.read_csv(path)
    for c in ("fecha_anuncio", "fecha_corte", "fecha_pago"):
        d[c] = pd.to_datetime(d[c])
    mal = d[(d["fecha_anuncio"] > d["fecha_corte"]) | (d["fecha_corte"] > d["fecha_pago"])]
    if not mal.empty:
        raise ValueError(f"Fechas fuera de orden (anuncio <= corte <= pago): {mal.to_dict('records')}")
    d["ticker"] = d["ticker"].str.upper()
    d["available_date"] = d["fecha_anuncio"]   # se sabe el día que se anuncia
    return d[DIVIDENDOS_COLS]


def _escribir(df: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    return path


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("paso", choices=["bronze", "silver", "golden", "calidad", "todo"])
    ap.add_argument("--bronze", action="store_true", help="con 'todo': bajar bronze primero")
    ap.add_argument("--tickers", nargs="*")
    ap.add_argument("--desde", type=int, default=2017)
    ap.add_argument("--no-estricto", action="store_true",
                    help="pedir solo años pares como la ingesta original (no PIT-estricto)")
    ap.add_argument("--forzar", action="store_true", help="volver a bajar aunque exista el JSON")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if a.paso == "bronze" or (a.paso == "todo" and a.bronze):
        from dotenv import load_dotenv
        load_dotenv()
        token = os.environ.get("DATABURSATIL_API_TOKEN")
        if not token:
            raise SystemExit("Falta DATABURSATIL_API_TOKEN en .env")
        r = bajar_bronze(token, a.tickers, a.desde, estricto=not a.no_estricto, forzar=a.forzar)
        print(f"[bronze] llamadas={r['llamadas']} ya_existian={r['omitidas_ya_existian']} "
              f"errores={r['errores']}")
        if a.paso == "bronze":
            return

    if a.paso in ("silver", "todo"):
        hay_bronze = any(BRONZE.glob("*/*/*.json"))
        silver = silver_desde_bronze() if hay_bronze else silver_desde_legacy()
        if not hay_bronze:
            print("[silver] sin bronze: se usa data/fundamentals.parquet (solo ratios)")
        print(f"[silver] {len(silver)} filas -> {_escribir(silver, SILVER)}")
    if a.paso in ("golden", "todo"):
        silver = pd.read_parquet(SILVER)
        golden = construir_golden(silver)
        print(f"[golden] {len(golden)} filas -> {_escribir(golden, GOLDEN)}")
        div = construir_dividendos()
        print(f"[golden] dividendos {len(div)} filas -> {_escribir(div, DIVIDENDOS_GOLDEN)}")
    if a.paso in ("calidad", "todo"):
        from productos.fundamentales_pit.calidad import escribir_reporte
        print(f"[calidad] {escribir_reporte()}")


if __name__ == "__main__":
    main()
