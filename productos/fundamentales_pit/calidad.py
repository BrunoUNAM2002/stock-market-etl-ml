"""Controles de calidad de golden contra el contrato (contrato.yaml).

    python -m productos.fundamentales_pit.calidad

Escribe data/fundamentals/golden/calidad.json y calidad.md. Cada control dice
si pasa, y si no, qué filas lo rompen: los problemas se reportan, no se borran.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from productos.fundamentales_pit.pit import GOLDEN, cargar_golden

AQUI = Path(__file__).resolve().parent
CONTRATO = AQUI / "contrato.yaml"


def cargar_contrato() -> dict:
    return yaml.safe_load(CONTRATO.read_text(encoding="utf-8"))


def _tipo_ok(serie: pd.Series, tipo: str) -> bool:
    if tipo == "date":
        return pd.api.types.is_datetime64_any_dtype(serie)
    if tipo == "float":
        return pd.api.types.is_float_dtype(serie)
    if tipo == "bool":
        return pd.api.types.is_bool_dtype(serie)
    if tipo == "string":
        return pd.api.types.is_string_dtype(serie) or pd.api.types.is_object_dtype(serie)
    return False


def revisar(golden: pd.DataFrame, contrato: dict | None = None) -> list[dict]:
    c = contrato or cargar_contrato()
    tabla = c["tablas"]["fundamentales_pit"]
    cols = tabla["columnas"]
    res: list[dict] = []

    def control(nombre: str, ok: bool, detalle: str = "", filas: int = 0) -> None:
        res.append({"control": nombre, "ok": bool(ok), "filas_afectadas": int(filas),
                    "detalle": detalle})

    faltan = [k for k in cols if k not in golden.columns]
    sobran = [k for k in golden.columns if k not in cols]
    control("esquema: columnas del contrato", not faltan and not sobran,
            f"faltan={faltan} sobran={sobran}")
    malos = [k for k, v in cols.items() if k in golden.columns and not _tipo_ok(golden[k], v["tipo"])]
    control("esquema: tipos", not malos, f"columnas con tipo distinto: {malos}")

    nulas = {k: int(golden[k].isna().sum()) for k, v in cols.items()
             if k in golden.columns and not v.get("nulos", False) and golden[k].isna().any()}
    control("nulos solo donde el contrato los permite", not nulas, str(nulas), sum(nulas.values()))

    dup = golden.duplicated(tabla["llave"]).sum()
    control(f"llave única {tabla['llave']}", dup == 0, "", dup)

    lag = pd.Timedelta(days=c["regla_pit"]["rezago_dias"])
    mal_lag = (golden["available_date"] != golden["quarter_end"] + lag).sum()
    control(f"available_date = quarter_end + {lag.days} días", mal_lag == 0, "", mal_lag)

    no_fin = (~golden["quarter_end"].dt.is_quarter_end).sum()
    control("quarter_end es fin de trimestre", no_fin == 0, "", no_fin)

    for col, (lo, hi) in c["slo"]["rangos_ratios"].items():
        fuera = ((golden[col] < lo) | (golden[col] > hi)).sum()
        control(f"{col} en [{lo}, {hi}]", fuera == 0, "", fuera)

    esperadas = c["slo"]["emisoras_esperadas"]
    minimo = c["slo"]["completitud_min_emisoras"]
    hay = golden["ticker"].nunique()
    control(f"completitud: >= {minimo} de {esperadas} emisoras", hay >= minimo,
            f"hay {hay} emisoras")

    clave = golden["quarter_end"].dt.year * 4 + golden["quarter_end"].dt.quarter
    salto = clave.groupby(golden["ticker"]).diff()
    huecos = golden.loc[salto > 1, ["ticker", "quarter_end"]]
    control("trimestres consecutivos (sin huecos dentro de cada emisora)", huecos.empty,
            "; ".join(f"{t} antes de {q.date()}" for t, q in huecos.itertuples(index=False))[:600],
            len(huecos))

    yoy_sin = golden["fund_revenue_yoy"].isna() & (salto.notna())
    control("informativo: YoY nulo por no tener el mismo trimestre del año anterior",
            True, "", int(yoy_sin.sum()))

    fin = golden.loc[golden["formato"] != "corporativo", "ticker"].unique().tolist()
    control("informativo: emisoras con formato no corporativo (bancos/seguros)", True, str(fin), len(fin))

    ratios = ["fund_gross_margin", "fund_oper_margin", "fund_net_margin",
              "fund_revenue_yoy", "fund_earnings_yoy"]
    recortados = int(golden[ratios].abs().eq(5.0).sum().sum())
    control("informativo: ratios recortados en ±5 (el valor real se perdió)", True,
            "la ingesta original aplica clip(-5, 5)", recortados)

    reexp = int(golden["reexpresado"].sum())
    control("informativo: trimestres con cifra reexpresada después", True, "", reexp)
    return res


def escribir_reporte(golden_path: Path = GOLDEN) -> Path:
    golden = cargar_golden(golden_path)
    res = revisar(golden)
    salida = Path(golden_path).with_name("calidad.json")
    salida.write_text(json.dumps({"filas": len(golden), "emisoras": int(golden["ticker"].nunique()),
                                  "controles": res}, ensure_ascii=False, indent=2), encoding="utf-8")
    md = ["# Calidad — fundamentales_pit", "",
          f"{len(golden)} filas · {golden['ticker'].nunique()} emisoras · "
          f"{golden['quarter_end'].min().date()} a {golden['quarter_end'].max().date()}", "",
          "| control | resultado | filas | detalle |", "|---|---|---|---|"]
    for r in res:
        md.append(f"| {r['control']} | {'OK' if r['ok'] else 'FALLA'} | {r['filas_afectadas']} | "
                  f"{r['detalle'].replace('|', '/')} |")
    salida.with_suffix(".md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return salida


if __name__ == "__main__":
    print(escribir_reporte())
