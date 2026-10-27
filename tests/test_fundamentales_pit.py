"""Producto Fundamentales PIT (Equipo Rojo): capas, regla PIT, contrato y herramienta."""
from __future__ import annotations

import json
import sys
import types
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

# src/agent/__init__ arrastra src.ml.news_sources, que importa fcntl (no existe en
# Windows). Sustituto vacío solo en Windows y solo si nadie lo cargó antes.
if sys.platform == "win32" and "fcntl" not in sys.modules:
    _f = types.ModuleType("fcntl")
    _f.LOCK_EX = _f.LOCK_UN = _f.LOCK_SH = _f.LOCK_NB = 0
    _f.flock = lambda *a, **k: None
    sys.modules["fcntl"] = _f

from productos.fundamentales_pit import capas  # noqa: E402
from productos.fundamentales_pit.calidad import revisar  # noqa: E402
from productos.fundamentales_pit.pit import dividendos_al, fundamentales_al  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SEMANTICA = ROOT / "productos" / "fundamentales_pit" / "capa_semantica.yaml"
LEGACY = ROOT / "data" / "fundamentals.parquet"
HOY = date(2026, 10, 26)


def _silver(filas: list[tuple]) -> pd.DataFrame:
    """filas: (ticker, quarter_end, ingresos, utilidad_neta)."""
    df = pd.DataFrame(filas, columns=["ticker", "quarter_end", "ingresos", "utilidad_neta"])
    df["quarter_end"] = pd.to_datetime(df["quarter_end"])
    df["utilidad_bruta"] = df["ingresos"] * 0.4
    df["utilidad_operacion"] = df["ingresos"] * 0.2
    df["upa"] = np.nan
    df["origen"] = "reporte_propio"
    df["reexpresado"] = False
    df["formato"] = "corporativo"
    df["primera_descarga"] = pd.Timestamp("2026-10-24")
    df["fuente"] = capas.FUENTE
    return df[capas.SILVER_COLS]


@pytest.fixture()
def golden_sintetico() -> pd.DataFrame:
    s = _silver([
        ("AAA", "2024-03-31", 100.0, 10.0), ("AAA", "2024-06-30", 110.0, 11.0),
        ("AAA", "2024-09-30", 120.0, 12.0), ("AAA", "2024-12-31", 130.0, 13.0),
        ("AAA", "2025-03-31", 150.0, 15.0),
        # hueco: faltan 2025T2, T3 y T4
        ("AAA", "2026-03-31", 180.0, 9.0),
        ("BBB", "2025-03-31", 50.0, -5.0), ("BBB", "2026-03-31", 55.0, 2.0),
    ])
    return capas.construir_golden(s, generado_en=pd.Timestamp("2026-10-26"))


@pytest.fixture()
def golden_real(tmp_path) -> Path:
    if not LEGACY.exists():
        pytest.skip("no está data/fundamentals.parquet")
    g = capas.construir_golden(capas.silver_desde_legacy(LEGACY), pd.Timestamp("2026-10-26"))
    p = tmp_path / "fundamentales_pit.parquet"
    g.to_parquet(p, index=False)
    return p


def _agente(golden_path: Path, tmp_path: Path, dividendos: pd.DataFrame | None = None):
    from src.agent.fundamentales import AgenteFundamentales
    div_path = tmp_path / "dividendos.parquet"
    (dividendos if dividendos is not None else capas.construir_dividendos(tmp_path / "no.csv")).to_parquet(div_path)
    return AgenteFundamentales(golden_path=golden_path, dividendos_path=div_path, hoy=HOY)


# ------------------------------------------------------------- correcciones

def test_yoy_compara_contra_el_mismo_trimestre_aunque_haya_hueco(golden_sintetico):
    g = golden_sintetico.set_index(["ticker", "quarter_end"])
    # 2026T1 contra 2025T1 (150 -> 180 = +20 %), NO contra la fila 4 posiciones atrás (2024T2).
    assert g.loc[("AAA", "2026-03-31"), "fund_revenue_yoy"] == pytest.approx(0.20)
    assert g.loc[("AAA", "2025-03-31"), "fund_revenue_yoy"] == pytest.approx(0.50)
    # sin el mismo trimestre del año anterior -> NaN, nunca otro trimestre
    assert np.isnan(g.loc[("AAA", "2024-06-30"), "fund_revenue_yoy"])


def test_yoy_legacy_se_anula_si_pct_change_comparo_mal(tmp_path):
    filas = [("AAA", q) for q in ["2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31",
                                  "2025-03-31", "2026-03-31"]]
    legacy = pd.DataFrame(filas, columns=["ticker", "quarter_end"])
    legacy["quarter_end"] = pd.to_datetime(legacy["quarter_end"])
    for c in ("fund_gross_margin", "fund_oper_margin", "fund_net_margin"):
        legacy[c] = 0.1
    legacy["fund_revenue_yoy"] = [np.nan] * 4 + [0.5, 0.99]   # 0.99 = calculado contra 2024T2
    legacy["fund_earnings_yoy"] = legacy["fund_revenue_yoy"]
    legacy["available_date"] = legacy["quarter_end"] + pd.Timedelta(days=70)
    p = tmp_path / "legacy.parquet"
    legacy.to_parquet(p)
    g = capas.construir_golden(capas.silver_desde_legacy(p)).set_index("quarter_end")
    assert g.loc["2025-03-31", "fund_revenue_yoy"] == pytest.approx(0.5)   # válido: se conserva
    assert np.isnan(g.loc["2026-03-31", "fund_revenue_yoy"])               # inválido: se anula


def test_en_el_parquet_del_repo_se_anulan_los_yoy_mal_calculados(golden_real):
    g = pd.read_parquet(golden_real)
    # Medido el 2026-10-26: 35 YoY de ingresos comparados contra el trimestre equivocado,
    # 28 de ellos en 2026T1 (todas las emisoras).
    assert g.loc[g["quarter_end"] == "2026-03-31", "fund_revenue_yoy"].isna().all()
    legacy = pd.read_parquet(LEGACY)
    assert legacy["fund_revenue_yoy"].notna().sum() - g["fund_revenue_yoy"].notna().sum() == 35


def _registro(periodo: str, datos: dict, fetched="2026-10-24T12:00:00+00:00") -> dict:
    return {"ticker": "AAA", "emisora": "AAA", "periodo": periodo, "financieros": "resultado_trimestre",
            "fetched_at": fetched, "status_code": 200, "payload": {"resultado_trimestre": datos}}


def test_la_cifra_original_gana_al_comparativo_reexpresado(tmp_path):
    reg_2023 = _registro("1T_2023", {"2023-01-01_2023-03-31": {
        "revenue": ["Ingresos", 88.0], "profitloss": ["Utilidad", 8.0]}})
    reg_2024 = _registro("1T_2024", {
        "2024-01-01_2024-03-31": {"revenue": ["Ingresos", 100.0], "profitloss": ["Utilidad", 10.0]},
        "2023-01-01_2023-03-31": {"revenue": ["Ingresos", 90.0], "profitloss": ["Utilidad", 8.0]},
    })
    for reg in (reg_2023, reg_2024):
        p = tmp_path / "AAA" / "resultado_trimestre" / f"{reg['periodo']}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(reg), encoding="utf-8")
    s = capas.silver_desde_bronze(tmp_path).set_index("quarter_end")
    q = s.loc[pd.Timestamp("2023-03-31")]
    assert q["ingresos"] == 88.0 and q["origen"] == "reporte_propio"
    assert bool(q["reexpresado"]) is True          # el comparativo dijo 90
    assert s.loc[pd.Timestamp("2024-03-31"), "ingresos"] == 100.0


def test_banco_sin_revenue_se_marca_en_vez_de_desaparecer():
    reg = _registro("1T_2024", {"2024-01-01_2024-03-31": {
        "interestincome": ["Ingresos por intereses", 500.0], "profitloss": ["Utilidad", 70.0]}})
    filas = capas.filas_de_bronze(reg)
    assert len(filas) == 1
    assert filas[0]["formato"] == "sin_ingresos_corporativos"
    assert filas[0]["utilidad_neta"] == 70.0


def test_bronze_no_guarda_el_token_y_no_repite_llamadas(tmp_path, monkeypatch):
    monkeypatch.setattr(capas, "_mapa_emisoras", lambda: {"AAA": "AAA"})
    llamadas = []

    class Resp:
        status_code = 200
        text = ""

        def json(self):
            return {"resultado_trimestre": {}}

    class Http:
        def get(self, url, params, timeout):
            llamadas.append(params)
            return Resp()

    r = capas.bajar_bronze("TOKEN-SECRETO", ["AAA"], 2024, 2024, http=Http(), pausa=0, dest=tmp_path)
    assert r["llamadas"] == 4 and len(llamadas) == 4
    for archivo in tmp_path.rglob("*.json"):
        assert "TOKEN-SECRETO" not in archivo.read_text(encoding="utf-8")
    r2 = capas.bajar_bronze("TOKEN-SECRETO", ["AAA"], 2024, 2024, http=Http(), pausa=0, dest=tmp_path)
    assert r2["llamadas"] == 0 and r2["omitidas_ya_existian"] == 4


def test_modo_estricto_pide_todos_los_anios():
    assert capas.periodos(2023, 2024, estricto=True) == [
        "1T_2023", "2T_2023", "3T_2023", "4T_2023", "1T_2024", "2T_2024", "3T_2024", "4T_2024"]
    assert capas.periodos(2023, 2024, estricto=False) == ["1T_2024", "2T_2024", "3T_2024", "4T_2024"]


# ------------------------------------------------------------- regla PIT

def test_fundamentales_al_nunca_devuelve_el_futuro(golden_sintetico):
    for fecha in pd.date_range("2024-01-01", "2026-12-31", freq="17D"):
        vis = fundamentales_al(fecha, golden=golden_sintetico)
        assert (vis["available_date"] <= fecha).all()
    # 2026T1 se publica el 2026-06-09 (31-mar + 70 días)
    assert pd.Timestamp("2026-03-31") not in set(fundamentales_al("2026-06-08", "AAA", golden_sintetico)["quarter_end"])
    assert pd.Timestamp("2026-03-31") in set(fundamentales_al("2026-06-09", "AAA", golden_sintetico)["quarter_end"])


def test_dividendo_se_conoce_al_anunciarse_y_las_fechas_tienen_orden(tmp_path):
    bueno = tmp_path / "div.csv"
    bueno.write_text("ticker,fecha_anuncio,fecha_corte,fecha_pago,monto_por_accion,moneda,fuente\n"
                     "walmex,2026-03-10,2026-04-01,2026-04-15,1.0,MXN,aviso BMV\n", encoding="utf-8")
    d = capas.construir_dividendos(bueno)
    assert d.loc[0, "available_date"] == pd.Timestamp("2026-03-10")
    assert dividendos_al("2026-03-09", dividendos=d).empty
    assert len(dividendos_al("2026-03-10", "WALMEX", dividendos=d)) == 1
    malo = tmp_path / "malo.csv"
    malo.write_text("ticker,fecha_anuncio,fecha_corte,fecha_pago,monto_por_accion,moneda,fuente\n"
                    "WALMEX,2026-04-20,2026-04-01,2026-04-15,1.0,MXN,x\n", encoding="utf-8")
    with pytest.raises(ValueError):
        capas.construir_dividendos(malo)


# ------------------------------------------------------------- contrato

def test_golden_cumple_el_contrato(golden_sintetico):
    res = {r["control"]: r for r in revisar(golden_sintetico)}
    for nombre in ("esquema: columnas del contrato", "esquema: tipos",
                   "nulos solo donde el contrato los permite",
                   "llave única ['ticker', 'quarter_end']", "available_date = quarter_end + 70 días",
                   "quarter_end es fin de trimestre"):
        assert res[nombre]["ok"], res[nombre]
    assert not res["trimestres consecutivos (sin huecos dentro de cada emisora)"]["ok"]  # el hueco se reporta


# ------------------------------------------------------------- herramienta

def test_herramienta_sql_no_puede_ver_el_futuro(golden_real, tmp_path):
    a = _agente(golden_real, tmp_path)
    r = a.observe(sql="SELECT COUNT(*) AS n FROM fundamentales_pit WHERE available_date > '2020-01-01'",
                  fecha_consulta="2020-01-01")
    assert r["ok"] and r["filas"][0]["n"] == 0
    r = a.observe(sql="SELECT MAX(available_date) AS m FROM fundamentales_pit", fecha_consulta="2020-01-01")
    assert r["filas"][0]["m"] <= "2020-01-01"


@pytest.mark.parametrize("sql", [
    "DROP TABLE fundamentales_pit",
    "DELETE FROM fundamentales_pit",
    "INSERT INTO mapa_tickers VALUES ('X', 'Y')",
    "UPDATE fundamentales_pit SET fund_net_margin = 1",
    "CREATE TABLE t (x)",
    "ATTACH DATABASE 'otra.db' AS otra",
    "PRAGMA table_info(fundamentales_pit)",
    "SELECT 1; DELETE FROM fundamentales_pit",
])
def test_herramienta_es_solo_lectura(golden_real, tmp_path, sql):
    r = _agente(golden_real, tmp_path).observe(sql=sql)
    assert r["ok"] is False and r["error_tipo"] == "sql_no_permitido"


def test_herramienta_corta_consultas_infinitas(golden_real, tmp_path):
    a = _agente(golden_real, tmp_path)
    a.timeout_s = 0.5
    r = a.observe(sql="WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT COUNT(*) FROM c")
    assert r["ok"] is False and r["error_tipo"] == "timeout"


def test_herramienta_resumen_y_fechas(golden_real, tmp_path):
    a = _agente(golden_real, tmp_path)
    r = a.observe(ticker="walmex", n_trimestres=3)
    assert r["ok"] and len(r["trimestres"]) == 3
    assert all(t["available_date"] <= "2026-10-26" for t in r["trimestres"])
    antes = a.observe(ticker="WALMEX", fecha_consulta="2017-05-01")
    assert antes["ok"] and antes["trimestres"] == [] and "2017-06-09" in antes["nota"]
    assert "futura" in a.observe(ticker="WALMEX", fecha_consulta="2030-01-01")["aviso"]
    assert a.observe(ticker="NOEXISTE")["error_tipo"] == "ticker_desconocido"
    assert a.observe()["error_tipo"] == "parametros"


def test_cada_consulta_de_la_capa_semantica_corre(golden_real, tmp_path):
    a = _agente(golden_real, tmp_path)
    ejemplos = yaml.safe_load(SEMANTICA.read_text(encoding="utf-8"))["consultas_de_ejemplo"]
    assert len(ejemplos) >= 5
    for e in ejemplos:
        r = a.observe(sql=e["sql"])
        assert r["ok"], (e["pregunta"], r)
        if "dividendos" not in e["sql"]:
            assert r["filas"], e["pregunta"]


def test_mapa_tickers_no_es_concatenar_mx(golden_real, tmp_path):
    r = _agente(golden_real, tmp_path).observe(
        sql="SELECT ticker, ticker_yahoo FROM mapa_tickers WHERE ticker_yahoo <> ticker || '.MX' ORDER BY ticker")
    assert {f["ticker"] for f in r["filas"]} == {"AMXL", "POSADAS", "TLEVISA", "VISTA"}


def test_el_agente_inversor_ofrece_la_herramienta():
    try:
        from src.agent.book import Book
        from src.agent.tools import build_tools
    except ImportError as exc:  # el agente del profe necesita dependencias pesadas
        pytest.skip(f"no se pudo importar src.agent.tools: {exc}")
    tools = build_tools(Book(payload={"cash_usd": 50, "budget_usd": 50, "positions": {}, "fills": []}))
    assert "ver_fundamentales" in tools
    schema = tools["ver_fundamentales"].tool_schema()
    assert schema["name"] == "ver_fundamentales" and "fecha_consulta" in schema["input_schema"]["properties"]
