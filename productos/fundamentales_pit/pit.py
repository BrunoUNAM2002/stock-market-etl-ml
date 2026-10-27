"""Acceso point-in-time a la capa golden.

Point-in-time (PIT) = "lo que se sabía en ese momento". Un trimestre que
cierra el 31-mar no se conoce el 31-mar: se conoce cuando la empresa lo
publica. Por eso cada fila trae available_date, y nadie que consulte "al día
X" puede ver una fila con available_date > X.

Todo consumidor (dashboard, agente, modelos) debería leer por aquí y no el
parquet directo: así la regla vive en un solo lugar.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "data" / "fundamentals"
GOLDEN = BASE / "golden" / "fundamentales_pit.parquet"
DIVIDENDOS_GOLDEN = BASE / "golden" / "dividendos_eventos.parquet"

FechaLike = str | date | datetime | pd.Timestamp


def a_fecha(valor: FechaLike | None) -> pd.Timestamp:
    """Normaliza a Timestamp sin hora. None = hoy."""
    if valor is None:
        return pd.Timestamp(date.today())
    ts = pd.Timestamp(valor)
    if ts.tzinfo is not None:
        ts = ts.tz_convert(None)
    return ts.normalize()


def cargar_golden(path: Path | None = None) -> pd.DataFrame:
    path = Path(path or GOLDEN)
    if not path.exists():
        raise FileNotFoundError(
            f"No existe {path}. Genera la capa con: "
            "python -m productos.fundamentales_pit.capas todo"
        )
    df = pd.read_parquet(path)
    for c in ("quarter_end", "available_date"):
        df[c] = pd.to_datetime(df[c])
    return df


def fundamentales_al(
    fecha: FechaLike | None = None,
    ticker: str | None = None,
    golden: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Lo que se sabía al cierre de `fecha` (default hoy).

    Filtra available_date <= fecha. Si se pasa `ticker`, solo esa emisora.
    Devuelve las filas ordenadas por (ticker, quarter_end).
    """
    df = cargar_golden() if golden is None else golden
    corte = a_fecha(fecha)
    out = df[pd.to_datetime(df["available_date"]) <= corte]
    if ticker is not None:
        out = out[out["ticker"] == ticker.upper()]
    return out.sort_values(["ticker", "quarter_end"]).reset_index(drop=True)


def ultimo_conocido(
    fecha: FechaLike | None = None, golden: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Una fila por emisora: el trimestre más reciente ya publicado en `fecha`."""
    vis = fundamentales_al(fecha, golden=golden)
    return vis.groupby("ticker", as_index=False).tail(1).reset_index(drop=True)


def dividendos_al(
    fecha: FechaLike | None = None,
    ticker: str | None = None,
    dividendos: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Dividendos anunciados hasta `fecha`. Un dividendo se conoce el día que se
    anuncia (fecha_anuncio), no el día de corte ni el de pago."""
    if dividendos is None:
        if not DIVIDENDOS_GOLDEN.exists():
            return pd.DataFrame()
        dividendos = pd.read_parquet(DIVIDENDOS_GOLDEN)
    if dividendos.empty:
        return dividendos
    corte = a_fecha(fecha)
    out = dividendos[pd.to_datetime(dividendos["available_date"]) <= corte]
    if ticker is not None:
        out = out[out["ticker"] == ticker.upper()]
    return out.sort_values(["ticker", "fecha_anuncio"]).reset_index(drop=True)
