"""Restaura reservas, visitantes e sessões ao estado inicial."""

from __future__ import annotations

from residencial_aurora.condominio import obter_condominio
from residencial_aurora.config import configurar_ambiente


def restaurar() -> None:
    configurar_ambiente()
    obter_condominio().restaurar()


def main() -> None:
    restaurar()
    print("Reservas e visitantes restaurados. Sessões apagadas.")
