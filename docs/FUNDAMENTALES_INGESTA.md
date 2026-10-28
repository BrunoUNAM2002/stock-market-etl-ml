# Fundamentales PIT: ingesta, almacenamiento y hallazgos

Documento del equipo Rojo (Fundamentales PIT). Resume cómo correr la ingesta, dónde queda cada dato y qué encontramos en la corrida real del 26-oct-2026. Lo que no se probó está marcado como tal.

## 1. Qué es el producto

Estados financieros trimestrales por emisora de la BMV (montos, márgenes, crecimiento y UPA), guardados de forma **point-in-time (PIT)**: cada fila trae `available_date`, la fecha desde la cual el dato ya era público. Nadie debe usar una fila antes de esa fecha.

```
available_date = quarter_end + 70 días
```

El detalle del contrato y de la capa semántica está en `productos/fundamentales_pit/` (`contrato.yaml`, `capa_semantica.yaml`, README).

## 2. Cómo correr la ingesta

Requisitos: `pip install -r requirements.txt`, `pip install pyarrow pytest` y un `.env` (copiado de `.env.example`) con `DATABURSATIL_API_TOKEN`. **El `.env` no se sube al repo.**

```bash
# Producto completo (bronze -> silver -> golden + calidad). Pide todos los años a la API.
python -m productos.fundamentales_pit.capas todo --bronze

# Sin token: arma silver/golden desde data/fundamentals.parquet (solo ratios)
python -m productos.fundamentales_pit.capas todo

# Dashboard (lee golden)
python build_dashboard.py

# Tests del producto
python -m pytest tests/test_fundamentales_pit.py -q

# Ingesta original (alimenta data/fundamentals.parquet, que usa src/ml/features.py)
python scripts/run_all_ingests.py --fundamentals --force-fundamentals --skip-audit
```

## 3. Dónde se guarda cada cosa

| Qué | Dónde |
|---|---|
| Bronze: respuesta cruda de la API | `data/fundamentals/bronze/<ticker>/resultado_trimestre/<periodo>.json` |
| Silver: un trimestre por fila, con montos | `data/fundamentals/silver/fundamentales_trimestre.parquet` |
| Golden: lo que consumen dashboard, agente y modelos | `data/fundamentals/golden/fundamentales_pit.parquet` |
| Dividendos (hoy vacío) | `data/fundamentals/golden/dividendos_eventos.parquet` |
| Reporte de calidad | `data/fundamentals/golden/calidad.md` y `calidad.json` |
| Ingesta original (solo ratios) | `data/fundamentals.parquet` |
| Dashboard | `data/dashboard_fundamentales.html` |
| Precios, noticias, intradía | `data/market/`, `data/news/`, `data/intraday/` |

`data/fundamentals/` y el HTML del dashboard están en el `.gitignore`: se regeneran con los comandos de arriba.

**Tablas SQL.** El repo tiene dos bases: SQL Server `MercadoBursatil` (`sql/01` y `sql/02`, esquemas `control`, `stage`, `golden`, `ml`) y Postgres `mercado` (`sql/03`, esquema `lab`). `sql/04_fundamentales_schema.sql` define las tablas de este producto para SQL Server. **No se ha ejecutado en un servidor real**, y falta confirmar con el profe en qué motor vive golden.

## 4. Resultado de la corrida real (26-oct-2026)

| Dato | Valor |
|---|---|
| Llamadas a la API | 1,440 (36 emisoras x 40); 4 fallaron por red y se repitieron después con éxito |
| Filas en silver y golden | **1,338** (1,336 en la primera descarga y 2 más al reintentar los errores de red; la ingesta original dio 1,146 y el parquet del repo traía 946) |
| Emisoras con datos | **32 de 36** |
| Rango de trimestres | 2016-03-31 a 2026-09-30 (2016 llega como comparativo de 2017) |
| Origen de las filas | 1,208 de reporte propio, 128 de comparativo |
| Trimestres reexpresados después | 186 |
| Ratios recortados en +-5 | 64 (el valor real se perdió) |
| UPA (`fund_eps`) con dato | 100% de las filas |
| Tests | 24 de 24 en verde |

**Control de calidad:** 14 reglas en OK y 1 en FALLA (hallazgo 5). La de trimestres consecutivos pasó de FALLA a OK al reintentar bronze (hallazgo 6). Las reglas están en `contrato.yaml`.

**Ingesta original (`run_all_ingests.py --fundamentals`, 27-oct-2026, con `fundamentals.py` corregido):**

| Dato | Valor |
|---|---|
| Emisoras con datos | 32 de 36 (mismas 4 sin datos) |
| Filas | 1,233 (2017-03-31 a 2026-09-30) |
| Duración | 365 s |
| Trimestres por emisora | 38 (hasta 2026-06-30), 39 (hasta 2026-09-30) y 35 (ALFAA, hasta 2025-09-30): son todos los trimestres del rango, sin huecos |
| Emisoras por trimestre | 2025-09-30: 32 · 2025-12-31: 31 · 2026-06-30: 31 · 2026-09-30: 20 |
| Columnas | las 5 de ratios más `fund_revenue_abs` y `fund_net_abs` |

## 5. Hallazgos

1. **El YoY original estaba mal calculado.** `src/api/fundamentals.py` usaba `pct_change(4)`, que compara contra la fila 4 posiciones atrás; si falta un trimestre, compara contra el equivocado (en 2026T1 y T2 comparaba contra 18 meses antes). Se corrigió de dos formas: golden calcula el YoY por fecha, y `fundamentals.py` ahora rellena los trimestres faltantes antes de calcularlo. La corrección de `fundamentals.py` se probó con datos sintéticos y con la ingesta real del 27-oct: los conteos por emisora cubren todos los trimestres del rango. Además se recalculó el YoY de ingresos por fecha de forma independiente sobre `data/fundamentals.parquet`: de 1,100 filas con YoY de ingresos, 0 difieren y 0 se calcularon sin trimestre previo. Lo mismo con el YoY de utilidad neta: 1,105 filas, 0 difieren.
2. **Faltaban los trimestres de 2025.** La ingesta original pedía solo años pares y sacaba los impares del comparativo del año siguiente; 2025T3 y T4 dependían de reportes de 2026 que aún no existen. Al pedir 2025 directo: 2025-09-30 pasó de 1 a 32 emisoras y 2025-12-31 de 0 a 31, tanto en el flujo por capas como en la ingesta original.
3. **ALFAA** antes se cortaba en 2024-12-31 y ahora llega a 2025-09-30 (35 trimestres completos). No tiene 2025-12-31 ni 2026; no sabemos por qué.
4. **Cuatro emisoras sin estados financieros: GFNORTEO, GENTERA, GFINBURO y Q.** La API responde 400 ("emisora desconocida") y en `/v2/emisoras` su campo `financieros` viene vacío, aunque las emisoras sí existen. No es un error de nombre: la API no los ofrece. Las cuatro son del sector financiero; que esa sea la causa no está comprobado.
5. **PINFRA 2025-09-30 tiene margen operativo de 284%** (falla la regla de calidad). Viene así de la API: utilidad de operación de 14,195 mdp contra ingresos de 4,993 mdp (en 2024T3 fue 2,821 mdp). No encontramos la causa; el valor puede ser legítimo (por ejemplo, una ganancia extraordinaria), por eso no se corrigió.
6. **AC aparecía sin 2016-06-30 ni 2017-06-30 en golden** (falla la regla de trimestres consecutivos). Causa verificada en bronze: 4 llamadas de AC (`2T_2017`, `2T_2018`, `4T_2018` y `1T_2019`) terminaron en `ConnectionResetError` (el servidor cerró la conexión); son los únicos 4 archivos con error de red de todo bronze y coinciden con `errores=4`. `bajar_bronze` guardó cada error como si fuera la respuesta y no lo vuelve a pedir porque el archivo ya existe. La ingesta original sí trae esos trimestres. Efecto: 2017-06-30 falta, y 2018-06-30, 2018-12-31 y 2019-03-31 solo llegan como comparativo del año siguiente (posiblemente reexpresado) en lugar de su cifra original. Los bancos no son errores: dieron 400, una respuesta real de la API. **Resuelto:** `bajar_bronze` ahora vuelve a pedir los archivos que guardaron un error de red (`_es_error_de_red` en `capas.py`). Al reintentar: `llamadas=4`, `errores=0`, golden pasó de 1,336 a 1,338 filas, la regla de trimestres consecutivos quedó en OK y los 4 trimestres de AC salen con `origen = reporte_propio`.
7. **El trimestre 2026-09-30 ya lo reportaron varias emisoras**: el 26-oct solo estaba GAPB y el 27-oct ya hay 20. El rezago fijo de 70 días las deja disponibles hasta el **2026-12-09**, así que esas 20 filas tienen `available_date` futura y el modelo no debe usarlas todavía. El rezago fijo es conservador: no filtra futuro, pero retrasa información que ya era pública.
8. **La UPA ya viene en la API** (`basicearningslosspershare`) y entra a golden como `fund_eps`. En WALMEX, utilidad neta entre acciones en circulación coincide con `fund_eps` en tres de los cuatro últimos trimestres y difiere 0.01 en el último; solo se revisó esa emisora.
9. **Dividendos: la API los trae pero sin fecha de anuncio.** En `/v2/emisoras` cada emisora tiene un historial con fecha (parece la de pago), monto y tipo. La fecha ex-derecho y la moneda solo vienen en el dividendo más reciente, que además se repite en el histórico. La fecha de anuncio, que el contrato usa como `available_date`, no existe. Hay que decidir con qué fecha aproximarla; usar la de pago nunca filtra futuro, pero llega tarde. Incluye a las 4 emisoras que no tienen estados financieros.
10. **yfinance no es PIT.** `dividend_yield`, `pe_ratio` y `market_cap` en `src/ml/features.py` son un valor de hoy repetido en toda la historia. No están en este producto.

## 6. Pendientes

- [ ] Cargar dividendos desde la API y decidir la fecha de disponibilidad (ver hallazgo 9).
- [x] Reintentar los archivos de bronze con error de red (hecho en `capas.py`; AC recuperado).
- [ ] Investigar a PINFRA 2025-09-30, a AC y a ALFAA con la fuente original.
- [ ] Ejecutar `sql/04_fundamentales_schema.sql` en un servidor real y confirmar con el profe dónde vive golden.
- [ ] Confirmar con el profe qué significa "webfinals".
- [ ] Revisar con el equipo dueño de `src/agent/tools.py` el registro de la herramienta `ver_fundamentales`.