# Assistente do Residencial Aurora

O morador conversa com um agente principal. Quem reserva, cancela, autoriza visitante e consulta o regulamento são especialistas, e cada efeito passa por uma ferramenta. Cobrança, acesso, apartamento e exclusividade da data não dependem do que a mensagem pede: o código é que grava, recusa ou pede confirmação.

## Arquitetura

O aplicativo ADK se chama `residencial_aurora` e fica em `src/residencial_aurora/agentes.py`. A retomada de confirmação está ligada (`ResumabilityConfig(is_resumable=True)`) e a sessão fica no `SqliteSessionService`. Com essa combinação, a resposta da confirmação volta para o agente que pediu a ferramenta.

O morador fala só com o agente principal, `aurora`. Ele não tem ferramenta de reserva, visitante ou regulamento. A única ação dele é `transfer_to_agent`. A transferência existe para o principal não carregar o regulamento nem gravar dados: cada assunto fica com quem tem a ferramenta correspondente.

- `reservas` lê e grava áreas comuns. `listar_minhas_reservas`, `consultar_disponibilidade`, `reservar_area` e `cancelar_reserva` estão em `src/residencial_aurora/ferramentas.py`. Salão e churrasqueira têm taxa e pedem confirmação; a quadra não tem. Cancelar não pede confirmação.
- `visitantes` lista e autoriza entrada com `listar_meus_visitantes` e `autorizar_visitante`. Autorizar entrada sempre pede confirmação, porque libera alguém no prédio.
- `regulamento` responde dúvidas chamando `consultar_regulamento`. A ferramenta lê `dados/regulamento.md` e devolve um único artigo.

Os três especialistas estão em `sub_agents` do principal e não transferem de volta (`disallow_transfer_to_parent` e `disallow_transfer_to_peers`). A mensagem seguinte do morador volta ao principal, que escolhe de novo. A confirmação pendente é exceção: o Runner devolve a resposta ao especialista que emitiu `adk_request_confirmation`.

Reservas, visitantes e o vínculo sessão-apartamento ficam em SQLite (`estado/condominio.db`). As conversas ficam em `estado/sessoes.db`. Os arquivos de `dados/` são só o estado inicial e não são alterados.

## Garantias

### 1. Cobrança ou acesso só com confirmação

Em `src/residencial_aurora/agentes.py`, a reserva com taxa e a autorização de visitante usam `require_confirmation`:

```python
FunctionTool(reservar_area, require_confirmation=reserva_exige_confirmacao),
```

```python
FunctionTool(
    autorizar_visitante,
    require_confirmation=visitante_exige_confirmacao,
),
```

`reserva_exige_confirmacao`, em `ferramentas.py`, devolve verdadeiro só quando a área tem taxa maior que zero e a data é válida. A quadra, o cancelamento e as consultas não passam por isso. Uma frase no chat não preenche `tool_context.tool_confirmation`: esse objeto só existe quando a rota de confirmações responde `adk_request_confirmation`. A própria ferramenta recusa a gravação sem essa confirmação:

```python
if area_info.taxa > 0:
    confirmacao = tool_context.tool_confirmation
    if confirmacao is None or not confirmacao.confirmed:
        return {
            "ok": False,
            "motivo": "confirmacao_obrigatoria",
            "gravado": False,
```

`autorizar_visitante` faz a mesma checagem. A rota `POST /sessoes/{session_id}/confirmacoes`, em `src/residencial_aurora/api.py`, só segue se o id ainda está pendente na sessão. Qualquer outro id, inclusive um já respondido, responde 409 e não chama o Runner:

```python
if pedido.id not in pendentes:
    raise HTTPException(status_code=409, detail="confirmacao nao pendente")
```

### 2. Cada sessão pertence a um apartamento

O apartamento é gravado uma vez, em `registrar_sessao`, na criação da sessão. As ferramentas não têm parâmetro `apartamento`. `_apartamento`, em `ferramentas.py`, lê o id da sessão ADK e consulta a tabela `sessoes`:

```python
def _apartamento(tool_context: ToolContext) -> str:
    session_id = tool_context.session.id
    apartamento = obter_condominio().apartamento_da_sessao(session_id)
```

Cancelar e listar filtram por esse apartamento. Consultar disponibilidade devolve só se a data está livre ou ocupada, sem código nem unidade. `depois_do_modelo`, em `src/residencial_aurora/filtros.py`, ainda apaga da saída do modelo código de reserva, nome de visitante e número de apartamento que não sejam da sessão. O texto do morador permanece; o que o modelo acrescenta, não.

### 3. Nada se perde no reinício

`src/residencial_aurora/api.py` abre `SqliteSessionService` em `estado/sessoes.db`. Reservas e visitantes ficam em `estado/condominio.db`, aberto a cada operação, sem cache em memória. Subir a API de novo lê os mesmos arquivos. `garantir_inicial` só semeia o banco quando o arquivo ainda não existe, então um reinício não volta os dados iniciais. Quem volta ao início é o comando de restauração.

### 4. O regulamento é consultado, não carregado

`INSTRUCAO_PRINCIPAL`, em `agentes.py`, não contém o regulamento. `consultar_regulamento`, em `ferramentas.py`, manda para `consultar` a pergunta do morador nesta invocação, não um resumo do modelo. `consultar`, em `src/residencial_aurora/regulamento_busca.py`, escolhe um artigo e devolve só o texto dele. Se a resposta final do agente `regulamento` trouxer trecho de outro capítulo, `depois_do_modelo` troca essa resposta pelo artigo consultado. O arquivo inteiro não entra em evento nem na instrução do principal.

### 5. Dois moradores, uma reserva

A exclusividade não é uma consulta feita antes da gravação. Em `garantir_schema`, o SQLite só aceita uma reserva ativa por área e data:

```sql
CREATE UNIQUE INDEX IF NOT EXISTS reservas_uma_por_data
    ON reservas (area, data) WHERE ativa = 1;
```

`criar_reserva` insere dentro de `BEGIN IMMEDIATE`. Se outra transação gravou a mesma área e data, o `INSERT` falha e a ferramenta devolve recusa, com HTTP 200. O código da reserva fica na tabela mesmo depois do cancelamento (`ativa = 0`), então não é reutilizado. A coluna `origem` guarda o id da chamada da ferramenta: uma retomada do ADK não grava a mesma ação duas vezes.

## Como rodar

Pré-requisitos: Python 3.12 ou superior, [uv](https://docs.astral.sh/uv/) e uma chave do Google AI Studio.

O `.env` não entra no Git. Copie o exemplo e preencha a chave. `GEMINI_MODEL` pode ficar vazio; nesse caso os agentes usam `gemini-3.5-flash`.

```bash
uv sync
cp .env.example .env
uv run restaurar
uv run aurora
```

A API responde em `http://localhost:8000`. Não há banco externo: o SQLite é criado em `estado/`.

`uv run restaurar` regrava reservas e visitantes a partir de `dados/reservas.json` e `dados/visitantes.json` e apaga as sessões. Pare a API antes de restaurar. Reiniciar a API, sem esse comando, mantém conversas e dados.
