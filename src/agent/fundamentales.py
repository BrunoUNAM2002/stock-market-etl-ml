"""Herramienta del agente para el dominio Fundamentales PIT (Equipo Rojo).

`ver_fundamentales` responde con lo que se sabía en `fecha_consulta`, no con
lo que se sabe hoy. La regla no depende de que el modelo la respete:

* Modo resumen (ticker): devuelve los últimos trimestres ya publicados.
* Modo SQL: arma una base SQLite EN MEMORIA que solo contiene las filas con
  available_date <= fecha_consulta y ejecuta ahí un SELECT. Las filas futuras
  no existen en esa base, así que ninguna consulta puede verlas.

Solo lectura: un autorizador de SQLite rechaza todo lo que no sea leer
(INSERT, UPDATE, DELETE, CREATE, DROP, ATTACH, PRAGMA…).

Este módulo no importa src.agent.tools a propósito: así se puede probar en
cualquier sistema (tools.py arrastra src.ml.news_sources, que usa fcntl y no
existe en Windows).
"""
from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from datetime import date
from pathlib import Path

import pandas as pd

from productos.fundamentales_pit.pit import (
    DIVIDENDOS_GOLDEN,
    GOLDEN,
    a_fecha,
    cargar_golden,
    dividendos_al,
    fundamentales_al,
)
from src.agent.base import ObservationAgent

SEMANTICA = Path(__file__).resolve().parents[2] / "productos" / "fundamentales_pit" / "capa_semantica.yaml"

# Códigos del autorizador de SQLite (https://sqlite.org/c3ref/c_alter_table.html)
_PERMITIDOS = {
    getattr(sqlite3, "SQLITE_READ", 20),
    getattr(sqlite3, "SQLITE_SELECT", 21),
    getattr(sqlite3, "SQLITE_FUNCTION", 31),
    getattr(sqlite3, "SQLITE_RECURSIVE", 33),
}

_COLS_RESUMEN = ["quarter_end", "available_date", "fund_revenue_abs", "fund_net_abs", "fund_eps",
                 "fund_gross_margin", "fund_oper_margin", "fund_net_margin",
                 "fund_revenue_yoy", "fund_earnings_yoy", "origen"]


def _solo_lectura(accion, *_):
    return sqlite3.SQLITE_OK if accion in _PERMITIDOS else sqlite3.SQLITE_DENY


def _texto_fechas(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[c]):
            out[c] = out[c].dt.strftime("%Y-%m-%d")
    return out


def _registros(df: pd.DataFrame) -> list[dict]:
    limpio = _texto_fechas(df).astype(object).where(pd.notna(df), None)
    return limpio.to_dict(orient="records")


def _mapa_tickers() -> pd.DataFrame:
    from src.data.bmv_catalog import BMV_YFINANCE
    return pd.DataFrame(sorted(BMV_YFINANCE.items()), columns=["ticker", "ticker_yahoo"])


class AgenteFundamentales(ObservationAgent):
    name = "ver_fundamentales"
    description = (
        "Fundamentales trimestrales de emisoras BMV (márgenes, crecimiento contra el año "
        "anterior, UPA, dividendos) TAL COMO SE CONOCÍAN en fecha_consulta (default: hoy). "
        "Pasa `ticker` para un resumen, o `sql` (un SELECT) para preguntas libres."
    )

    def __init__(self, golden_path: Path | None = None, dividendos_path: Path | None = None,
                 semantica_path: Path | None = None, timeout_s: float = 5.0,
                 max_filas: int = 50, hoy: date | None = None):
        self.golden_path = Path(golden_path or GOLDEN)
        self.dividendos_path = Path(dividendos_path or DIVIDENDOS_GOLDEN)
        self.semantica_path = Path(semantica_path or SEMANTICA)
        self.timeout_s = timeout_s
        self.max_filas = max_filas
        self.hoy = hoy  # inyectable para pruebas

    # ---------------------------------------------------------------- datos
    def _corte(self, fecha_consulta) -> tuple[pd.Timestamp, str | None]:
        hoy = a_fecha(self.hoy)
        corte = a_fecha(fecha_consulta) if fecha_consulta else hoy
        if corte > hoy:
            return hoy, f"fecha_consulta {corte.date()} es futura; se usa hoy ({hoy.date()})"
        return corte, None

    def _visibles(self, corte: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame]:
        golden = cargar_golden(self.golden_path)
        fund = fundamentales_al(corte, golden=golden)
        div = (dividendos_al(corte, dividendos=pd.read_parquet(self.dividendos_path))
               if self.dividendos_path.exists() else pd.DataFrame())
        return fund, div

    # --------------------------------------------------------------- modos
    def _resumen(self, ticker: str, corte: pd.Timestamp, n: int, fund: pd.DataFrame,
                 div: pd.DataFrame) -> dict:
        ticker = ticker.upper()
        todas = cargar_golden(self.golden_path)
        if ticker not in set(todas["ticker"]):
            return {"ok": False, "error_tipo": "ticker_desconocido",
                    "error": f"{ticker} no está en el producto. Emisoras: {sorted(todas['ticker'].unique())}"}
        filas = fund[fund["ticker"] == ticker].tail(max(1, int(n)))
        out = {"ok": True, "ticker": ticker, "fecha_consulta": str(corte.date()),
               "trimestres": _registros(filas[_COLS_RESUMEN].iloc[::-1])}
        if filas.empty:
            primero = todas.loc[todas["ticker"] == ticker, "available_date"].min()
            out["nota"] = (f"Ningún trimestre de {ticker} era público al {corte.date()}; "
                           f"el primero se publica el {primero.date()}.")
        if not div.empty:
            out["dividendos"] = _registros(div[div["ticker"] == ticker])
        return out

    def _sql(self, sql: str, corte: pd.Timestamp, fund: pd.DataFrame, div: pd.DataFrame) -> dict:
        consulta = sql.strip().rstrip(";")
        if not consulta:
            return {"ok": False, "error_tipo": "sql_no_permitido", "error": "SQL vacío."}
        inicio = time.monotonic()
        with closing(sqlite3.connect(":memory:")) as con:
            _texto_fechas(fund).to_sql("fundamentales_pit", con, index=False)
            div_tabla = div if not div.empty else pd.DataFrame(
                columns=["ticker", "fecha_anuncio", "fecha_corte", "fecha_pago", "monto_por_accion",
                         "moneda", "fuente", "available_date"])
            _texto_fechas(div_tabla).to_sql("dividendos_eventos", con, index=False)
            _mapa_tickers().to_sql("mapa_tickers", con, index=False)
            con.set_authorizer(_solo_lectura)
            con.set_progress_handler(
                lambda: 1 if time.monotonic() - inicio > self.timeout_s else 0, 10_000)
            try:
                cur = con.execute(consulta)
            except (sqlite3.ProgrammingError, sqlite3.Warning) as exc:
                # ProgrammingError hereda de DatabaseError: va primero.
                return {"ok": False, "error_tipo": "sql_no_permitido",
                        "error": f"Una sola sentencia SELECT por llamada ({exc})."}
            except sqlite3.DatabaseError as exc:
                msg = str(exc)
                if "not authorized" in msg or "prohibited" in msg:
                    return {"ok": False, "error_tipo": "sql_no_permitido",
                            "error": "Solo se permite leer (SELECT)."}
                if "interrupted" in msg:
                    return {"ok": False, "error_tipo": "timeout",
                            "error": f"La consulta pasó de {self.timeout_s} s."}
                return {"ok": False, "error_tipo": "sql_invalido", "error": msg}
            if cur.description is None:
                return {"ok": False, "error_tipo": "sql_no_permitido", "error": "La sentencia no devuelve filas."}
            cols = [d[0] for d in cur.description]
            filas = cur.fetchmany(self.max_filas + 1)
        return {"ok": True, "fecha_consulta": str(corte.date()), "columnas": cols,
                "filas": [dict(zip(cols, f)) for f in filas[: self.max_filas]],
                "truncado": len(filas) > self.max_filas}

    # -------------------------------------------------------------- interfaz
    def observe(self, ticker: str | None = None, fecha_consulta: str | None = None,
                n_trimestres: int = 4, sql: str | None = None) -> dict:
        if not ticker and not sql:
            return {"ok": False, "error_tipo": "parametros", "error": "Pasa `ticker` o `sql`."}
        try:
            corte, aviso = self._corte(fecha_consulta)
        except (ValueError, TypeError) as exc:
            return {"ok": False, "error_tipo": "fecha_invalida", "error": f"fecha_consulta: {exc}"}
        try:
            fund, div = self._visibles(corte)
        except FileNotFoundError as exc:
            return {"ok": False, "error_tipo": "sin_datos", "error": str(exc)}
        out = (self._sql(sql, corte, fund, div) if sql
               else self._resumen(ticker, corte, n_trimestres, fund, div))
        if aviso:
            out["aviso"] = aviso
        return out

    def tool_schema(self) -> dict:
        semantica = (self.semantica_path.read_text(encoding="utf-8")
                     if self.semantica_path.exists() else "")
        return {
            "name": self.name,
            "description": f"{self.description}\n\nCapa semántica:\n{semantica}",
            "input_schema": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Clave interna, p. ej. WALMEX"},
                    "fecha_consulta": {"type": "string",
                                       "description": "YYYY-MM-DD. Solo se ve lo publicado hasta ese día."},
                    "n_trimestres": {"type": "integer", "description": "Trimestres del resumen (default 4)"},
                    "sql": {"type": "string",
                            "description": "Un solo SELECT sobre fundamentales_pit, dividendos_eventos o mapa_tickers"},
                },
            },
        }
