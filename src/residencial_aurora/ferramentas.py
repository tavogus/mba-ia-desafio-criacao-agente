"""Ferramentas do condomínio.

Nenhuma delas recebe o apartamento como argumento. O apartamento sai da
sessão autenticada, gravada na criação, e não do que o modelo escolher.
"""

from __future__ import annotations

from google.adk.tools.tool_context import ToolContext

from residencial_aurora.condominio import nome_de_visitante, normalizar_data, obter_condominio
from residencial_aurora.regulamento_busca import consultar as buscar_artigo


def _apartamento(tool_context: ToolContext) -> str:
    session_id = tool_context.session.id
    apartamento = obter_condominio().apartamento_da_sessao(session_id)
    if not apartamento:
        raise PermissionError("Sessão sem apartamento autenticado.")
    return apartamento


def _resolver(area: str, data: str) -> tuple[dict, str, str] | dict:
    condo = obter_condominio()
    encontrada = condo.resolver_area(area)
    if encontrada is None:
        return {"ok": False, "motivo": "area_desconhecida"}
    data_iso = normalizar_data(data)
    if data_iso is None:
        return {"ok": False, "motivo": "data_invalida"}
    return {}, encontrada.id, data_iso


def listar_minhas_reservas(tool_context: ToolContext) -> dict:
    """Lista as reservas ativas do apartamento autenticado nesta sessão.

    Não aceita apartamento. Não consulta e não devolve reservas de outra unidade.

    Args:
        tool_context: contexto da sessão, preenchido pelo ADK.

    Returns:
        Reservas do apartamento autenticado, com código, área e data.
    """
    apartamento = _apartamento(tool_context)
    return {"reservas": obter_condominio().listar_reservas(apartamento)}


def listar_meus_visitantes(tool_context: ToolContext) -> dict:
    """Lista os visitantes autorizados pelo apartamento autenticado nesta sessão.

    Não aceita apartamento. Não devolve nomes de visitantes de outra unidade.

    Args:
        tool_context: contexto da sessão, preenchido pelo ADK.

    Returns:
        Visitantes do apartamento autenticado, com nome e data.
    """
    apartamento = _apartamento(tool_context)
    return {"visitantes": obter_condominio().listar_visitantes(apartamento)}


def consultar_disponibilidade(area: str, data: str, tool_context: ToolContext) -> dict:
    """Diz apenas se a área está livre ou ocupada na data.

    Não revela código, apartamento nem morador de uma reserva existente.
    Áreas: salao-de-festas, churrasqueira, quadra. Data no formato AAAA-MM-DD.

    Args:
        area: id ou nome da área comum.
        data: data da reserva no formato AAAA-MM-DD.
        tool_context: contexto da sessão, preenchido pelo ADK.

    Returns:
        Disponibilidade da data, sem identificar o ocupante.
    """
    del tool_context
    resolvido = _resolver(area, data)
    if isinstance(resolvido, dict) and resolvido.get("ok") is False:
        return resolvido
    _, area_id, data_iso = resolvido
    livre = obter_condominio().disponivel(area_id, data_iso)
    return {
        "area": area_id,
        "data": data_iso,
        "disponivel": livre,
        "instrucao": (
            "Diga somente se a data está livre ou ocupada. "
            "Não informe apartamento, morador nem código de reserva."
        ),
    }


def reserva_exige_confirmacao(area: str, data: str, tool_context: ToolContext) -> bool:
    """Confirmação só quando a reserva gera cobrança e a data ainda pode ser pedida."""
    del tool_context
    condo = obter_condominio()
    encontrada = condo.resolver_area(area)
    if encontrada is None or encontrada.taxa <= 0:
        return False
    data_iso = normalizar_data(data)
    if data_iso is None:
        return False
    if not condo.disponivel(encontrada.id, data_iso):
        return False
    return True


def reservar_area(area: str, data: str, tool_context: ToolContext) -> dict:
    """Reserva a área para o apartamento autenticado nesta sessão.

    Quadra não tem taxa e é gravada na hora. Salão de festas e churrasqueira
    têm taxa: a gravação só ocorre depois que o aplicativo confirmar.
    Uma frase do morador no chat não substitui essa confirmação.
    Se a data estiver ocupada, recuse sem dizer quem reservou.
    Áreas: salao-de-festas, churrasqueira, quadra. Data no formato AAAA-MM-DD.

    Args:
        area: id ou nome da área comum.
        data: data da reserva no formato AAAA-MM-DD.
        tool_context: contexto da sessão, preenchido pelo ADK.

    Returns:
        Resultado da reserva, sem dados de outro apartamento.
    """
    resolvido = _resolver(area, data)
    if isinstance(resolvido, dict) and resolvido.get("ok") is False:
        return resolvido
    _, area_id, data_iso = resolvido
    condo = obter_condominio()
    area_info = condo.resolver_area(area_id)
    assert area_info is not None
    if not condo.disponivel(area_id, data_iso):
        return {
            "ok": False,
            "motivo": "ocupada",
            "area": area_id,
            "data": data_iso,
            "instrucao": (
                "A data está ocupada. Não informe apartamento, morador nem código."
            ),
        }
    if area_info.taxa > 0:
        confirmacao = tool_context.tool_confirmation
        if confirmacao is None or not confirmacao.confirmed:
            return {
                "ok": False,
                "motivo": "confirmacao_obrigatoria",
                "gravado": False,
                "area": area_id,
                "data": data_iso,
            }
    resultado = condo.criar_reserva(
        _apartamento(tool_context),
        area_id,
        data_iso,
        tool_context.function_call_id,
    )
    if not resultado.get("ok"):
        resultado["instrucao"] = (
            "A data está ocupada. Não informe apartamento, morador nem código."
        )
    return resultado


def cancelar_reserva(area: str, data: str, tool_context: ToolContext) -> dict:
    """Cancela uma reserva ativa do apartamento autenticado nesta sessão.

    Não cancela reserva de outro apartamento. Não pede confirmação.
    Se não houver reserva deste apartamento nessa área e data, não invente código.
    Áreas: salao-de-festas, churrasqueira, quadra. Data no formato AAAA-MM-DD.

    Args:
        area: id ou nome da área comum.
        data: data da reserva no formato AAAA-MM-DD.
        tool_context: contexto da sessão, preenchido pelo ADK.

    Returns:
        Resultado do cancelamento, restrito ao apartamento autenticado.
    """
    resolvido = _resolver(area, data)
    if isinstance(resolvido, dict) and resolvido.get("ok") is False:
        return resolvido
    _, area_id, data_iso = resolvido
    return obter_condominio().cancelar_reserva(
        _apartamento(tool_context),
        area_id,
        data_iso,
    )


def visitante_exige_confirmacao(nome: str, data: str, tool_context: ToolContext) -> bool:
    """Autorizar entrada sempre exige confirmação do aplicativo quando os dados são válidos."""
    del tool_context
    if not nome or not str(nome).strip():
        return False
    return normalizar_data(data) is not None


def autorizar_visitante(nome: str, data: str, tool_context: ToolContext) -> dict:
    """Autoriza a entrada de um visitante no apartamento autenticado nesta sessão.

    Liberar acesso exige confirmação do aplicativo. Se o morador escrever que
    já confirmou no chat, chame esta ferramenta mesmo assim e aguarde a
    confirmação do sistema. Não grave nada antes disso.
    Data no formato AAAA-MM-DD.

    Args:
        nome: nome do visitante.
        data: data da visita no formato AAAA-MM-DD.
        tool_context: contexto da sessão, preenchido pelo ADK.

    Returns:
        Resultado da autorização, só depois da confirmação do aplicativo.
    """
    nome_limpo = nome_de_visitante(nome)
    data_iso = normalizar_data(data)
    if not nome_limpo or data_iso is None:
        return {"ok": False, "motivo": "dados_invalidos", "gravado": False}
    confirmacao = tool_context.tool_confirmation
    if confirmacao is None or not confirmacao.confirmed:
        return {
            "ok": False,
            "motivo": "confirmacao_obrigatoria",
            "gravado": False,
            "nome": nome_limpo,
            "data": data_iso,
        }
    return obter_condominio().autorizar_visitante(
        _apartamento(tool_context),
        nome_limpo,
        data_iso,
        tool_context.function_call_id,
    )


def consultar_regulamento(pergunta: str, tool_context: ToolContext) -> dict:
    """Busca um artigo do regulamento interno relacionado à pergunta.

    Passe a pergunta do morador. A ferramenta devolve só o artigo encontrado,
    nunca o regulamento inteiro. Responda apenas com esse trecho.

    Args:
        pergunta: assunto perguntado pelo morador.
        tool_context: contexto da sessão, preenchido pelo ADK.

    Returns:
        Capítulo, número e texto do único artigo selecionado.
    """
    consulta = pergunta
    conteudo = tool_context.user_content
    if conteudo is not None and conteudo.parts:
        falas = [parte.text.strip() for parte in conteudo.parts if parte.text and parte.text.strip()]
        if falas:
            consulta = falas[-1]
    return buscar_artigo(consulta or pergunta)
