"""Limpa o que o modelo tenta colocar na conversa.

O texto do morador permanece. O que o modelo devolve é filtrado: código,
visitante e número de outro apartamento não seguem adiante, e a resposta
sobre o regulamento fica restrita ao artigo consultado.
"""

from __future__ import annotations

import re

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from residencial_aurora.condominio import obter_condominio
from residencial_aurora.regulamento_busca import trecho_de_outro_assunto


def _apartamento(callback_context: CallbackContext) -> str | None:
    return obter_condominio().apartamento_da_sessao(callback_context.session.id)


def _higienizar_texto(texto: str, apartamento: str) -> str:
    condo = obter_condominio()
    limpo = texto
    for codigo in condo.codigos_de_outros(apartamento):
        limpo = limpo.replace(codigo, "")
    for nome in condo.visitantes_de_outros(apartamento):
        limpo = limpo.replace(nome, "")
    for numero in condo.apartamentos():
        if numero == apartamento:
            continue
        limpo = re.sub(rf"(?<!\d){re.escape(numero)}(?!\d)", "", limpo)
    return limpo


def _limpar_args(args: dict, apartamento: str) -> dict:
    condo = obter_condominio()
    proibidos = condo.codigos_de_outros(apartamento) | condo.visitantes_de_outros(apartamento)
    limpos = {}
    for chave, valor in args.items():
        if chave == "apartamento":
            continue
        if isinstance(valor, str) and valor.strip() in proibidos:
            continue
        limpos[chave] = valor
    return limpos


def _ultimo_artigo(callback_context: CallbackContext) -> tuple[str, str] | None:
    for evento in reversed(callback_context.session.events):
        for resposta in evento.get_function_responses():
            if resposta.name != "consultar_regulamento" or not isinstance(resposta.response, dict):
                continue
            corpo = resposta.response
            if "trecho" not in corpo and isinstance(corpo.get("result"), dict):
                corpo = corpo["result"]
            trecho = str(corpo.get("trecho") or "")
            capitulo = str(corpo.get("capitulo") or "")
            if trecho:
                return capitulo, trecho
    return None


def depois_do_modelo(
    callback_context: CallbackContext,
    llm_response: LlmResponse,
) -> LlmResponse | None:
    """Aplica as travas de apartamento e de regulamento na saída do modelo."""
    if llm_response.content is None or not llm_response.content.parts:
        return None
    apartamento = _apartamento(callback_context)
    if not apartamento:
        return None

    tem_chamada = any(parte.function_call for parte in llm_response.content.parts)
    if (
        callback_context.agent_name == "regulamento"
        and not tem_chamada
    ):
        artigo = _ultimo_artigo(callback_context)
        if artigo is not None:
            capitulo, trecho = artigo
            texto_atual = "".join(parte.text or "" for parte in llm_response.content.parts)
            if trecho_de_outro_assunto(texto_atual, capitulo) or trecho not in texto_atual:
                return LlmResponse(
                    content=types.Content(
                        role="model",
                        parts=[types.Part(text=trecho)],
                    )
                )

    novos: list[types.Part] = []
    mudou = False
    for parte in llm_response.content.parts:
        if parte.function_call is not None:
            args = dict(parte.function_call.args or {})
            limpos = _limpar_args(args, apartamento)
            if limpos != args:
                mudou = True
                novos.append(
                    types.Part(
                        function_call=types.FunctionCall(
                            name=parte.function_call.name,
                            args=limpos,
                            id=parte.function_call.id,
                        )
                    )
                )
            else:
                novos.append(parte)
            continue
        if parte.text:
            limpo = _higienizar_texto(parte.text, apartamento)
            if limpo != parte.text:
                mudou = True
                if limpo.strip():
                    novos.append(types.Part(text=limpo))
                continue
        novos.append(parte)
    if not mudou:
        return None
    if not novos:
        novos = [types.Part(text="")]
    return LlmResponse(content=types.Content(role="model", parts=novos))
