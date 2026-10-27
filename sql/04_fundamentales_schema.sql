-- ============================================================================
-- PRODUCTO DE DATOS: FUNDAMENTALES PIT (Equipo Rojo)
-- Base de datos: SQL Server (MercadoBursatil). Requiere los esquemas stage y
-- golden de 01_create_schema.sql. NO borra la base: solo las tablas de este
-- producto.
-- Contrato: productos/fundamentales_pit/contrato.yaml (misma estructura).
-- Estado: NO probado contra un SQL Server real (solo revisión de sintaxis).
-- Pendiente con el profe: confirmar si el producto vive aquí o en Postgres.
-- ============================================================================

USE MercadoBursatil;
GO

-- ----------------------------------------------------------------------------
-- Silver: stage.fundamentales_trimestre
-- Un trimestre por fila, con montos y de dónde salió la cifra.
-- ticker es la clave interna (WALMEX, BIMBOA...), no stage.cat_tickers.ticker_id:
-- las dos claves no están conciliadas todavía y una FK equivocada sería peor.
-- ----------------------------------------------------------------------------
IF OBJECT_ID('stage.fundamentales_trimestre', 'U') IS NOT NULL
    DROP TABLE stage.fundamentales_trimestre;
GO

CREATE TABLE stage.fundamentales_trimestre (
    ticker              VARCHAR(20)    NOT NULL,
    quarter_end         DATE           NOT NULL,
    ingresos            DECIMAL(20,2)  NULL,
    utilidad_bruta      DECIMAL(20,2)  NULL,
    utilidad_operacion  DECIMAL(20,2)  NULL,
    utilidad_neta       DECIMAL(20,2)  NULL,
    upa                 DECIMAL(18,6)  NULL,
    origen              VARCHAR(20)    NOT NULL,   -- reporte_propio | comparativo | legacy_parquet
    reexpresado         BIT            NOT NULL DEFAULT 0,
    formato             VARCHAR(30)    NOT NULL,   -- corporativo | sin_ingresos_corporativos
    primera_descarga    DATETIME2      NULL,
    fuente              VARCHAR(100)   NOT NULL,
    CONSTRAINT pk_fund_trimestre PRIMARY KEY (ticker, quarter_end),
    CONSTRAINT ck_fund_trimestre_origen CHECK (origen IN ('reporte_propio', 'comparativo', 'legacy_parquet'))
);
GO

-- ----------------------------------------------------------------------------
-- Golden: golden.fundamentales_pit
-- Lo que consumen dashboard, agente y modelos. Mismas columnas que el parquet.
-- ----------------------------------------------------------------------------
IF OBJECT_ID('golden.fundamentales_pit', 'U') IS NOT NULL
    DROP TABLE golden.fundamentales_pit;
GO

CREATE TABLE golden.fundamentales_pit (
    ticker              VARCHAR(20)    NOT NULL,
    quarter_end         DATE           NOT NULL,
    available_date      DATE           NOT NULL,   -- quarter_end + 70 días
    fund_revenue_abs    DECIMAL(20,2)  NULL,
    fund_gross_abs      DECIMAL(20,2)  NULL,
    fund_oper_abs       DECIMAL(20,2)  NULL,
    fund_net_abs        DECIMAL(20,2)  NULL,
    fund_eps            DECIMAL(18,6)  NULL,
    fund_gross_margin   FLOAT          NULL,
    fund_oper_margin    FLOAT          NULL,
    fund_net_margin     FLOAT          NULL,
    fund_revenue_yoy    FLOAT          NULL,
    fund_earnings_yoy   FLOAT          NULL,
    origen              VARCHAR(20)    NOT NULL,
    reexpresado         BIT            NOT NULL DEFAULT 0,
    formato             VARCHAR(30)    NOT NULL,
    fuente              VARCHAR(100)   NOT NULL,
    version_producto    VARCHAR(10)    NOT NULL,
    generado_en         DATE           NOT NULL,
    CONSTRAINT pk_fundamentales_pit PRIMARY KEY (ticker, quarter_end),
    CONSTRAINT ck_fund_pit_disponible CHECK (available_date > quarter_end),
    CONSTRAINT ck_fund_pit_margen_bruto CHECK (fund_gross_margin IS NULL OR fund_gross_margin <= 1.0),
    INDEX idx_fund_pit_disponible (available_date, ticker)
);
GO

-- ----------------------------------------------------------------------------
-- Golden: golden.dividendos_eventos
-- Un dividendo se conoce el día que se anuncia: available_date = fecha_anuncio.
-- ----------------------------------------------------------------------------
IF OBJECT_ID('golden.dividendos_eventos', 'U') IS NOT NULL
    DROP TABLE golden.dividendos_eventos;
GO

CREATE TABLE golden.dividendos_eventos (
    ticker              VARCHAR(20)    NOT NULL,
    fecha_anuncio       DATE           NOT NULL,
    fecha_corte         DATE           NOT NULL,   -- ex-derecho: quien tenga la acción cobra
    fecha_pago          DATE           NOT NULL,
    monto_por_accion    DECIMAL(18,6)  NOT NULL,
    moneda              CHAR(3)        NOT NULL DEFAULT 'MXN',
    fuente              VARCHAR(100)   NOT NULL,
    available_date      AS fecha_anuncio PERSISTED,
    CONSTRAINT pk_dividendos_eventos PRIMARY KEY (ticker, fecha_anuncio, fecha_pago),
    CONSTRAINT ck_dividendos_orden CHECK (fecha_anuncio <= fecha_corte AND fecha_corte <= fecha_pago),
    CONSTRAINT ck_dividendos_monto CHECK (monto_por_accion > 0)
);
GO

-- ----------------------------------------------------------------------------
-- Puerto PIT: golden.fn_fundamentales_al(@fecha)
-- Lo que se sabía al cierre de @fecha. Los consumidores deben leer por aquí.
-- ----------------------------------------------------------------------------
IF OBJECT_ID('golden.fn_fundamentales_al', 'IF') IS NOT NULL
    DROP FUNCTION golden.fn_fundamentales_al;
GO

CREATE FUNCTION golden.fn_fundamentales_al (@fecha DATE)
RETURNS TABLE
AS
RETURN
    SELECT *
    FROM golden.fundamentales_pit
    WHERE available_date <= @fecha;
GO

PRINT 'Producto fundamentales_pit creado: stage.fundamentales_trimestre, golden.fundamentales_pit, golden.dividendos_eventos, golden.fn_fundamentales_al';
GO
