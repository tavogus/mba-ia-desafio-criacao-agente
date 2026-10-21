"""Caminhos, ambiente e modelo. Nada aqui lê o regulamento inteiro."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

APP_NAME = "residencial_aurora"
MODELO_PADRAO = "gemini-3.5-flash"


def raiz_projeto() -> Path:
    if valor := os.environ.get("AURORA_ROOT"):
        return Path(valor)
    aqui = Path(__file__).resolve()
    for pai in aqui.parents:
        if (pai / "dados" / "regulamento.md").is_file():
            return pai
    raise RuntimeError("Raiz do projeto não encontrada.")


def configurar_ambiente() -> None:
    load_dotenv(raiz_projeto() / ".env")
    if not os.environ.get("GOOGLE_API_KEY") and os.environ.get("GEMINI_API_KEY"):
        os.environ["GOOGLE_API_KEY"] = os.environ["GEMINI_API_KEY"]


def modelo_gemini() -> str:
    configurar_ambiente()
    return os.environ.get("GEMINI_MODEL", "").strip() or MODELO_PADRAO


def diretorio_estado() -> Path:
    if valor := os.environ.get("AURORA_ESTADO"):
        caminho = Path(valor)
    else:
        caminho = raiz_projeto() / "estado"
    caminho.mkdir(parents=True, exist_ok=True)
    return caminho


def caminho_banco() -> Path:
    return diretorio_estado() / "condominio.db"


def caminho_sessoes() -> Path:
    return diretorio_estado() / "sessoes.db"


def caminho_dados() -> Path:
    return raiz_projeto() / "dados"
