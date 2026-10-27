"""Producto de datos Fundamentales PIT (Equipo Rojo).

Capas: bronze (JSON crudo de la API) -> silver (un trimestre limpio por fila)
-> golden (lo que consumen dashboard, agente y modelos).

La regla que no se negocia: una fila solo existe para quien consulta en una
fecha >= su available_date. Ver pit.fundamentales_al().
"""
from productos.fundamentales_pit.pit import (  # noqa: F401
    DIVIDENDOS_GOLDEN,
    GOLDEN,
    cargar_golden,
    dividendos_al,
    fundamentales_al,
)
