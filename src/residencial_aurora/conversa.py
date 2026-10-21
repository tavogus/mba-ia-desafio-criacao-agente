"""Execução da conversa e leitura das confirmações pendentes."""

from __future__ import annotations

import uuid
from typing import Any

from google.adk.runners import Runner
from google.adk.sessions.sqlite_session_service import SqliteSessionService
from google.genai import types

from residencial_aurora.condominio import nome_de_visitante, normalizar_data, obter_condominio
from residencial_aurora.config import APP_NAME

NOME_CONFIRMACAO = "adk_request_confirmation"


def _texto(eventos: list[Any]) -> str:
    partes: list[str] = []
    for evento in eventos:
        if getattr(evento, "author", None) == "user" or getattr(evento, "partial", None):
            continue
        conteudo = getattr(evento, "content", None)
        if conteudo is None:
            continue
        for parte in conteudo.parts or []:
            if parte.text:
                partes.append(parte.text)
    return "\n".join(trecho.strip() for trecho in partes if trecho.strip()).strip()


def _detalhes(nome_ferramenta: str, args: dict[str, Any]) -> dict[str, Any]:
    condo = obter_condominio()
    if nome_ferramenta == "reservar_area":
        area = condo.resolver_area(str(args.get("area", "")))
        data = normalizar_data(str(args.get("data", ""))) or args.get("data")
        return {"area": area.id if area else args.get("area"), "data": data}
    if nome_ferramenta == "autorizar_visitante":
        data = normalizar_data(str(args.get("data", ""))) or args.get("data")
        return {"nome": nome_de_visitante(str(args.get("nome") or "")), "data": data}
    return {
        chave: valor
        for chave, valor in args.items()
        if chave not in {"tool_context", "apartamento"}
    }


def confirmacoes_pendentes(eventos: list[Any]) -> list[dict[str, Any]]:
    respondidas: set[str] = set()
    for evento in eventos:
        for resposta in evento.get_function_responses():
            if resposta.name == NOME_CONFIRMACAO and resposta.id:
                respondidas.add(resposta.id)

    pendentes: list[dict[str, Any]] = []
    for evento in eventos:
        for chamada in evento.get_function_calls():
            if chamada.name != NOME_CONFIRMACAO or not chamada.id or chamada.id in respondidas:
                continue
            args = dict(chamada.args or {})
            original = args.get("originalFunctionCall") or {}
            if not isinstance(original, dict):
                original = {}
            argumentos = original.get("args") or {}
            if not isinstance(argumentos, dict):
                argumentos = {}
            nome = str(original.get("name") or "confirmar")
            pendentes.append(
                {
                    "id": chamada.id,
                    "acao": nome,
                    "detalhes": _detalhes(nome, argumentos),
                }
            )
    return pendentes


def mensagem_texto(texto: str) -> types.Content:
    return types.Content(role="user", parts=[types.Part(text=texto)])


def mensagem_confirmacao(confirmacao_id: str, confirmado: bool) -> types.Content:
    return types.Content(
        role="user",
        parts=[
            types.Part(
                function_response=types.FunctionResponse(
                    id=confirmacao_id,
                    name=NOME_CONFIRMACAO,
                    response={"confirmed": confirmado},
                )
            )
        ],
    )


async def criar_sessao(
    sessions: SqliteSessionService,
    apartamento: str,
) -> str:
    session_id = str(uuid.uuid4())
    sessao = await sessions.create_session(
        app_name=APP_NAME,
        user_id=apartamento,
        session_id=session_id,
        state={"apartamento": apartamento},
    )
    obter_condominio().registrar_sessao(sessao.id, apartamento, apartamento)
    return sessao.id


async def executar(
    runner: Runner,
    sessions: SqliteSessionService,
    *,
    user_id: str,
    session_id: str,
    mensagem: types.Content,
) -> dict[str, Any]:
    novos: list[Any] = []
    async for evento in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=mensagem,
    ):
        novos.append(evento)
    sessao = await sessions.get_session(
        app_name=APP_NAME,
        user_id=user_id,
        session_id=session_id,
    )
    eventos = sessao.events if sessao is not None else []
    return {
        "resposta": _texto(novos),
        "confirmacoes_pendentes": confirmacoes_pendentes(eventos),
    }


def serializar_eventos(eventos: list[Any]) -> list[dict[str, Any]]:
    saida = []
    for evento in eventos:
        saida.append(evento.model_dump(mode="json"))
    return saida
