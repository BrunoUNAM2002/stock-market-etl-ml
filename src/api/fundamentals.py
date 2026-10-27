"""
Fundamentales (estados financieros) vía DataBursátil — features NUEVAS,
ortogonales al precio, y BACKTESTEABLES (con lag de publicación).

A diferencia de tasas/índices (que solo dan snapshot actual), /v2/financieros
da estados financieros HISTÓRICOS por trimestre desde ~2017. Cada llamada
devuelve el trimestre pedido + el comparativo del año anterior, lo que permite
calcular crecimiento YoY directamente.

CRÍTICO — lag de publicación (anti look-ahead):
  Los resultados de un trimestre que cierra el 31-mar se publican ~6-8 semanas
  después. Por eso cada feature fundamental solo está "disponible" en
  available_date = fin_trimestre + PUBLICATION_LAG_DAYS. El merge con datos
  diarios usa esa fecha, nunca el cierre del trimestre.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.databursatil.com"
PUBLICATION_LAG_DAYS = 70   # días tras cierre de trimestre hasta disponibilidad pública

from src.data.bmv_catalog import BMV_EMISORA_FUNDAMENTALS as EMISORA_MAP

# Columnas de features fundamentales que produce este módulo
FUNDAMENTAL_COLS = [
    "fund_revenue_abs",    # INGRESOS ABSOLUTOS (NUEVO)
    "fund_net_abs",        # UTILIDAD NETA ABSOLUTA (NUEVO)
    "fund_gross_margin",   # utilidad bruta / ingresos
    "fund_oper_margin",    # utilidad operación / ingresos
    "fund_net_margin",     # utilidad neta / ingresos
    "fund_revenue_yoy",    # crecimiento de ingresos vs mismo trimestre año anterior
    "fund_earnings_yoy",   # crecimiento de utilidad neta YoY
]


def _quarter_end(period_key: str) -> pd.Timestamp | None:
    """'2024-01-01_2024-03-31' → Timestamp('2024-03-31')."""
    try:
        return pd.Timestamp(period_key.split("_")[1])
    except Exception:
        return None


def _val(item) -> float:
    """Extrae el monto numérico de un line item ['etiqueta', monto] o ['etiqueta']."""
    if isinstance(item, list) and len(item) >= 2 and isinstance(item[1], (int, float)):
        return float(item[1])
    return np.nan


def fetch_income_statement(emisora: str, periodos: list[str], token: str) -> dict:
    """
    Descarga resultado_trimestre para una lista de periodos.
    Devuelve {quarter_end (Timestamp): {revenue, grossprofit, oper, net}}.
    Aprovecha que cada llamada trae el trimestre + el comparativo del año anterior.
    """
    out: dict[pd.Timestamp, dict] = {}
    for per in periodos:
        try:
            r = requests.get(f"{BASE_URL}/v2/financieros", params={
                "token": token, "emisora": emisora,
                "periodo": per, "financieros": "resultado_trimestre",
            }, timeout=30)
            if r.status_code != 200:
                continue
            data = r.json().get("resultado_trimestre", {})
            if not isinstance(data, dict):
                continue
            for pkey, fields in data.items():
                qend = _quarter_end(pkey)
                if qend is None or not isinstance(fields, dict):
                    continue
                out[qend] = {
                    "revenue":   _val(fields.get("revenue")),
                    "gross":     _val(fields.get("grossprofit")),
                    "oper":      _val(fields.get("profitlossfromoperatingactivities")),
                    "net":       _val(fields.get("profitloss")),
                }
        except Exception as e:
            logger.warning(f"{emisora} {per}: {e}")
    return out


def compute_fundamental_features(quarters: dict[pd.Timestamp, dict]) -> pd.DataFrame:
    """
    Convierte los estados por trimestre en features (márgenes + crecimiento YoY).
    Devuelve DataFrame indexado por quarter_end con FUNDAMENTAL_COLS + available_date.
    """
    if not quarters:
        return pd.DataFrame()

    df = pd.DataFrame(quarters).T.sort_index()
    df.index = pd.to_datetime(df.index)

    # Rellenar trimestres faltantes con NaN para que pct_change(4) compare
    # contra el mismo trimestre del año anterior (por fecha, no por posición).
    # Las filas vacías se eliminan al final con dropna.
    df = df.reindex(pd.date_range(df.index.min(), df.index.max(), freq="QE"))

    # Guardar valores absolutos (dinero real)
    df["fund_revenue_abs"] = df["revenue"]
    df["fund_net_abs"] = df["net"]

    # Márgenes
    rev = df["revenue"].replace(0, np.nan)
    df["fund_gross_margin"] = df["gross"] / rev
    df["fund_oper_margin"]  = df["oper"]  / rev
    df["fund_net_margin"]   = df["net"]   / rev

    # Crecimiento YoY (mismo trimestre, 4 trimestres atrás)
    df["fund_revenue_yoy"]  = df["revenue"].pct_change(4, fill_method=None)
    df["fund_earnings_yoy"] = df["net"].pct_change(4, fill_method=None)

    # Disponibilidad pública (anti look-ahead)
    df["available_date"] = df.index + pd.Timedelta(days=PUBLICATION_LAG_DAYS)

    # Limpiar infinitos/outliers de ratios (excluyendo los montos absolutos)
    for c in FUNDAMENTAL_COLS:
        if c in ("fund_revenue_abs", "fund_net_abs"):
            df[c] = df[c].replace([np.inf, -np.inf], np.nan)
        else:
            df[c] = df[c].replace([np.inf, -np.inf], np.nan).clip(-5, 5)

    return df[FUNDAMENTAL_COLS + ["available_date"]].dropna(how="all", subset=FUNDAMENTAL_COLS)


def all_periods(year_start: int = 2017, year_end: int = 2026) -> list[str]:
    """Lista de periodos 'qT_YYYY'. Cada llamada trae también el año anterior."""
    periods = []
    for y in range(year_start, year_end + 1):
        for q in (1, 2, 3, 4):
            periods.append(f"{q}T_{y}")
    return periods


def fetch_ticker(ticker: str, token: str,
                 year_start: int = 2017, year_end: int = 2026) -> pd.DataFrame:
    """Pipeline completo para un ticker: fetch → features. DataFrame vacío si falla."""
    emisora = EMISORA_MAP.get(ticker)
    if not emisora:
        return pd.DataFrame()
    # Para minimizar llamadas, pedir 1 periodo por año (cada uno trae año-1 también),
    # pero pedimos los 4 trimestres de años pares para cubrir todo con la mitad de calls.
    # Años pares (cada llamada trae también el año anterior) + year_end-1 para que
    # 2025 llegue directo y no dependa de trimestres de 2026 aún no publicados.
    years = sorted(set(range(year_start + 1, year_end + 1, 2)) | {year_end - 1})
    periodos = [f"{q}T_{y}" for q in (1, 2, 3, 4) for y in years]
    quarters = fetch_income_statement(emisora, periodos, token)
    return compute_fundamental_features(quarters)