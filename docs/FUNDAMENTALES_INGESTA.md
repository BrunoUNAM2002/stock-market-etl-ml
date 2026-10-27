# Fundamentales PIT: ingesta, almacenamiento y hallazgos

Documento del equipo de **Fundamentales PIT**. Resume cómo correr la ingesta, dónde queda guardado cada dato y qué encontramos en la primera corrida.

## 1. Qué es el producto

Datos de estados financieros trimestrales por emisora de la BMV (márgenes y crecimiento), guardados de forma **point-in-time (PIT)**: cada fila trae `available_date`, la fecha desde la cual el dato ya era público. El modelo nunca debe usar una fila antes de esa fecha.

```
available_date = quarter_end + 70 días   (PUBLICATION_LAG_DAYS en src/api/fundamentals.py)
```

## 2. Cómo correr la ingesta

Requisitos: Python con `pip install -r requirements.txt` y un archivo `.env` (copiado de `.env.example`) con `DATABURSATIL_API_TOKEN`. **El `.env` no se sube al repo.**

```bash
# Solo fundamentales (forzando refresco)
python scripts/run_all_ingests.py --fundamentals --force-fundamentals --skip-audit

# Toda la ingesta sin tocar SQL Server
python scripts/run_all_ingests.py --all --skip-sql --force-fundamentals
```

Sin `--force-fundamentals`, el orquestador se salta la descarga si el archivo tiene menos de 7 días.

Para ver el parquet se necesita `pyarrow` (`pip install pyarrow`).

## 3. Dónde se guarda cada cosa

| Qué | Dónde | Formato |
|---|---|---|
| **Fundamentales** | `data/fundamentals.parquet` | Parquet local |
| Precios (OHLCV) | `data/market/` (un archivo por ticker) | Parquet |
| Noticias | `data/news/` | Archivos locales |
| Intradía 5 min | `data/intraday/` | Archivos locales |
| Reporte de la corrida | `data/ingest/last_run.json` | JSON |

**Tablas SQL.** El repo tiene dos bases distintas:

- **SQL Server**, base `MercadoBursatil` (`sql/01_create_schema.sql`, `sql/02_create_ml_schema.sql`): esquemas `control`, `stage`, `golden` y `ml`. La conexión se configura en `.env` (`SQL_SERVER`, `SQL_DATABASE`, etc.).
- **Postgres 16**, base `mercado` (`sql/03_lab_schema.sql`): esquema `lab` (`price_daily`, `news_daily`, `decision`, `entry_plan`, `outcome`, entre otras).

> **Fundamentales no tiene tabla SQL todavía.** Hoy viven solo en el `.parquet`.

## 4. Estructura del archivo `fundamentals.parquet`

8 columnas, una fila por `ticker` y `quarter_end`:

| Columna | Significado |
|---|---|
| `ticker` | Emisora |
| `quarter_end` | Cierre del trimestre |
| `fund_gross_margin` | Utilidad bruta / ingresos |
| `fund_oper_margin` | Utilidad operativa / ingresos |
| `fund_net_margin` | Utilidad neta / ingresos |
| `fund_revenue_yoy` | Crecimiento de ingresos vs. mismo trimestre del año anterior |
| `fund_earnings_yoy` | Crecimiento de utilidad neta vs. mismo trimestre del año anterior |
| `available_date` | Fecha desde la cual el dato es público (PIT) |

Los YoY salen vacíos (`NaN`) en los primeros 4 trimestres de cada emisora porque no hay año previo. Esto es normal.

**Solo se guardan ratios**, no los montos originales (ingresos, utilidades). Si se quieren los montos hay que modificar `src/api/fundamentals.py`.

## 5. Resultado de la corrida del 2026-10-24

| Dato | Valor |
|---|---|
| Emisoras con datos | 32 de 36 |
| Filas totales | 1,146 |
| Duración | 200.6 s |
| Estado | `success: true` |
| Corrida anterior (parquet que venía en el repo) | 946 filas |

**Emisoras sin datos ("sin datos" en la API o el mapeo):** GFNORTEO, GENTERA, Q, GFINBURO.

## 6. Hallazgos

1. **4 emisoras fallaron.** No sabemos si la API no las tiene o si el mapeo en `src/data/bmv_catalog.py` es incorrecto. Pendiente de revisar.
2. **Cobertura desigual.** La mayoría tiene 36 trimestres (2017-03-31 a 2026-06-30). VISTA tiene 32 y empieza en 2018. **ALFAA se corta en 2024-12-31.**
3. **GAPB trae un trimestre más (2026-09-30)**, con `available_date` esperada el 2026-12-09, posterior a la fecha de la corrida. El `merge_asof` no debería usarlo hasta entonces, pero hay que confirmar por qué la API lo entrega.
4. **Faltan UPA y dividendos**, que la diapositiva del profe incluye en el producto.
5. **`dividend_yield`, `pe_ratio` y `market_cap` vienen de un snapshot de yfinance**: un valor actual repetido en toda la historia. Eso es look-ahead y rompe el principio PIT.
6. **El rezago de 70 días es fijo** para todas las emisoras; no es la fecha real de publicación de cada reporte.

## 7. Dashboard de analítica

`build_dashboard.py` (en la raíz del repo) lee `data/fundamentals.parquet` y genera `data/dashboard_fundamentales.html`:

```bash
pip install pyarrow
python build_dashboard.py
```

El HTML es una foto de los datos del momento en que se genera. Hay que regenerarlo después de cada ingesta.

<!-- Agregar aquí capturas: ![Dashboard](img/dashboard.png) -->

## 8. Pendientes

- [ ] Esquema SQL propio para fundamentales (y definir en qué base: SQL Server o Postgres).
- [ ] Contrato de datos (columnas, tipos, frecuencia, nulos, dueño).
- [ ] Agregar UPA.
- [ ] Agregar dividendos con fecha de disponibilidad real.
- [ ] Reemplazar o quitar el snapshot de yfinance en el producto PIT.
- [ ] Investigar los 4 tickers sin datos y el corte de ALFAA.
- [ ] Guardar los montos originales además de los ratios.

## 9. Producto de datos (capas, contrato, capa semántica, agente)

Las capas bronze/silver/golden, el contrato de datos, la capa semántica y la
herramienta del agente (`ver_fundamentales`) viven en
[`productos/fundamentales_pit/`](../productos/fundamentales_pit/README.md).
El dashboard ahora lee la capa golden:

```bash
python -m productos.fundamentales_pit.capas todo
python build_dashboard.py
```
