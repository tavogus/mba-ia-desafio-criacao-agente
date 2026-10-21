"""Consulta o regulamento devolvendo um único artigo.

O arquivo `dados/regulamento.md` é lido aqui e não entra nas instruções
dos agentes. A resposta da ferramenta leva só o artigo escolhido.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

from residencial_aurora.config import caminho_dados

_PARADA = {
    "ate",
    "aos",
    "com",
    "como",
    "das",
    "dos",
    "ela",
    "ele",
    "em",
    "essa",
    "esse",
    "esta",
    "este",
    "horas",
    "hora",
    "mais",
    "mas",
    "nao",
    "nas",
    "nos",
    "para",
    "pela",
    "pelo",
    "por",
    "qual",
    "quais",
    "que",
    "sao",
    "sem",
    "ser",
    "sua",
    "seu",
    "uma",
    "uns",
}


@dataclass(frozen=True)
class Artigo:
    capitulo: str
    numero: str
    texto: str


def _sem_acento(texto: str) -> str:
    base = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in base if not unicodedata.combining(c)).lower()


def _tokens(texto: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-z0-9]+", _sem_acento(texto))
        if len(token) > 2 and token not in _PARADA
    ]


@lru_cache(maxsize=1)
def artigos() -> tuple[Artigo, ...]:
    texto = (caminho_dados() / "regulamento.md").read_text(encoding="utf-8")
    capitulo = ""
    numero = ""
    buffer: list[str] = []
    saida: list[Artigo] = []

    def fechar() -> None:
        if not numero:
            return
        corpo = "\n".join(buffer).strip()
        if corpo:
            saida.append(Artigo(capitulo=capitulo, numero=numero, texto=corpo))

    for linha in texto.splitlines():
        if linha.startswith("## "):
            fechar()
            numero = ""
            buffer = []
            capitulo = linha[3:].strip()
            continue
        if linha.startswith("**Art."):
            fechar()
            numero = linha.split("**")[1].strip().rstrip(".")
            buffer = [linha]
            continue
        if numero:
            buffer.append(linha)
    fechar()
    return tuple(saida)


def _pontuar(pergunta: str, artigo: Artigo) -> int:
    consulta = _tokens(pergunta)
    if not consulta:
        return 0
    corpo = set(_tokens(artigo.capitulo + " " + artigo.texto))
    pontos = 0
    for token in consulta:
        if token in corpo:
            pontos += 3
            continue
        if len(token) >= 6 and any(
            item.startswith(token[:6]) or token.startswith(item[:6])
            for item in corpo
            if len(item) >= 6
        ):
            pontos += 2
    return pontos


def consultar(pergunta: str) -> dict[str, str]:
    """Devolve o artigo mais relacionado. Nunca o regulamento inteiro."""
    ranqueados = sorted(artigos(), key=lambda item: _pontuar(pergunta, item), reverse=True)
    if not ranqueados or _pontuar(pergunta, ranqueados[0]) <= 0:
        return {"capitulo": "", "artigo": "", "trecho": ""}
    melhor = ranqueados[0]
    return {
        "capitulo": melhor.capitulo,
        "artigo": melhor.numero,
        "trecho": melhor.texto.strip(),
    }


def trecho_de_outro_assunto(texto: str, capitulo_permitido: str) -> bool:
    """True se `texto` copia um pedaço de capítulo que não é o consultado."""
    if not texto:
        return False
    for artigo in artigos():
        if capitulo_permitido and artigo.capitulo == capitulo_permitido:
            continue
        corpo = artigo.texto
        limite = max(len(corpo) - 40, 0)
        for inicio in range(0, limite + 1, 20):
            janela = corpo[inicio : inicio + 40]
            if len(janela.strip()) < 40:
                continue
            if janela in texto:
                return True
    return False
