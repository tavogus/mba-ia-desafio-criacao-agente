"""API HTTP do assistente."""

from __future__ import annotations

import logging
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Request
from pydantic import BaseModel
from pydantic import field_validator

from residencial_aurora.agentes import criar_app_adk
from residencial_aurora.condominio import obter_condominio
from residencial_aurora.config import APP_NAME
from residencial_aurora.config import caminho_sessoes
from residencial_aurora.config import configurar_ambiente
from residencial_aurora.config import modelo_gemini
from residencial_aurora.conversa import confirmacoes_pendentes
from residencial_aurora.conversa import criar_sessao
from residencial_aurora.conversa import executar
from residencial_aurora.conversa import mensagem_confirmacao
from residencial_aurora.conversa import mensagem_texto
from residencial_aurora.conversa import serializar_eventos

logger = logging.getLogger("aurora")


class PedidoSessao(BaseModel):
    apartamento: str

    @field_validator("apartamento", mode="before")
    @classmethod
    def _texto(cls, valor: Any) -> str:
        return str(valor).strip()


class PedidoMensagem(BaseModel):
    texto: str


class PedidoConfirmacao(BaseModel):
    id: str
    confirmado: bool


def _preparar_sqlite(caminho: Path) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(caminho) as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 30000")


def criar_aplicacao(modelo: Any | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configurar_ambiente()
        obter_condominio().garantir_inicial()
        _preparar_sqlite(caminho_sessoes())
        from google.adk.runners import Runner
        from google.adk.sessions.sqlite_session_service import SqliteSessionService

        sessions = SqliteSessionService(str(caminho_sessoes()))
        runner = Runner(
            app=criar_app_adk(modelo or modelo_gemini()),
            session_service=sessions,
        )
        app.state.runner = runner
        app.state.sessions = sessions
        yield
        await sessions.close()

    app = FastAPI(title="Residencial Aurora", lifespan=lifespan)

    async def exigir_sessao(request: Request, session_id: str):
        meta = obter_condominio().meta_sessao(session_id)
        if meta is None:
            raise HTTPException(status_code=404, detail="sessao nao encontrada")
        user_id, apartamento = meta
        sessao = await request.app.state.sessions.get_session(
            app_name=APP_NAME,
            user_id=user_id,
            session_id=session_id,
        )
        if sessao is None:
            raise HTTPException(status_code=404, detail="sessao nao encontrada")
        return user_id, apartamento, sessao

    @app.post("/sessoes", status_code=201)
    async def post_sessao(pedido: PedidoSessao, request: Request) -> dict[str, str]:
        condo = obter_condominio()
        if pedido.apartamento not in condo.apartamentos():
            raise HTTPException(status_code=404, detail="apartamento nao encontrado")
        session_id = await criar_sessao(request.app.state.sessions, pedido.apartamento)
        return {"session_id": session_id}

    @app.post("/sessoes/{session_id}/mensagens")
    async def post_mensagem(
        session_id: str,
        pedido: PedidoMensagem,
        request: Request,
    ) -> dict[str, Any]:
        user_id, _, _ = await exigir_sessao(request, session_id)
        try:
            return await executar(
                request.app.state.runner,
                request.app.state.sessions,
                user_id=user_id,
                session_id=session_id,
                mensagem=mensagem_texto(pedido.texto),
            )
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("falha ao enviar mensagem")
            raise HTTPException(status_code=500, detail="erro ao processar a mensagem") from exc

    @app.post("/sessoes/{session_id}/confirmacoes")
    async def post_confirmacao(
        session_id: str,
        pedido: PedidoConfirmacao,
        request: Request,
    ) -> dict[str, Any]:
        user_id, _, sessao = await exigir_sessao(request, session_id)
        pendentes = {item["id"] for item in confirmacoes_pendentes(sessao.events)}
        if pedido.id not in pendentes:
            raise HTTPException(status_code=409, detail="confirmacao nao pendente")
        try:
            return await executar(
                request.app.state.runner,
                request.app.state.sessions,
                user_id=user_id,
                session_id=session_id,
                mensagem=mensagem_confirmacao(pedido.id, pedido.confirmado),
            )
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("falha ao responder confirmacao")
            raise HTTPException(
                status_code=500,
                detail="erro ao processar a confirmacao",
            ) from exc

    @app.get("/sessoes/{session_id}/eventos")
    async def get_eventos(session_id: str, request: Request) -> list[dict[str, Any]]:
        _, _, sessao = await exigir_sessao(request, session_id)
        return serializar_eventos(sessao.events)

    @app.get("/apartamentos/{apartamento}/reservas")
    def get_reservas(apartamento: str) -> list[dict[str, str]]:
        return obter_condominio().listar_reservas(apartamento)

    @app.get("/apartamentos/{apartamento}/visitantes")
    def get_visitantes(apartamento: str) -> list[dict[str, str]]:
        return obter_condominio().listar_visitantes(apartamento)

    return app


app = criar_aplicacao()


def main() -> None:
    import uvicorn

    configurar_ambiente()
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(
        "residencial_aurora.api:app",
        host="0.0.0.0",
        port=8000,
    )
