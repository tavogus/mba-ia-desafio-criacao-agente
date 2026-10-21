"""Estado do condomínio.

Reservas e visitantes vivem neste SQLite, separado dos arquivos de
`dados/`, que são só o estado inicial. A exclusividade de uma área numa
data é um índice único parcial: o banco recusa a segunda gravação.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

from residencial_aurora.config import caminho_banco, caminho_dados, caminho_sessoes

_condo: Condominio | None = None


def _sem_acento(texto: str) -> str:
    base = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in base if not unicodedata.combining(c)).lower().strip()


@dataclass(frozen=True)
class Area:
    id: str
    nome: str
    taxa: float


class Condominio:
    def __init__(self, caminho: Path) -> None:
        self.path = caminho
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _conectar(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def _transacao(self) -> Iterator[sqlite3.Connection]:
        conn = self._conectar()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def garantir_schema(self) -> None:
        with self._conectar() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS reservas (
                    codigo TEXT PRIMARY KEY,
                    apartamento TEXT NOT NULL,
                    area TEXT NOT NULL,
                    data TEXT NOT NULL,
                    ativa INTEGER NOT NULL CHECK (ativa IN (0, 1)),
                    origem TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS reservas_origem
                    ON reservas (origem) WHERE origem IS NOT NULL;
                CREATE UNIQUE INDEX IF NOT EXISTS reservas_uma_por_data
                    ON reservas (area, data) WHERE ativa = 1;

                CREATE TABLE IF NOT EXISTS visitantes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    apartamento TEXT NOT NULL,
                    nome TEXT NOT NULL,
                    data TEXT NOT NULL,
                    origem TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS visitantes_origem
                    ON visitantes (origem) WHERE origem IS NOT NULL;

                CREATE TABLE IF NOT EXISTS sessoes (
                    session_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    apartamento TEXT NOT NULL
                );
                """
            )

    def garantir_inicial(self) -> None:
        novo = not self.path.exists()
        self.garantir_schema()
        if novo:
            self._semear()

    def restaurar(self) -> None:
        """Volta reservas e visitantes aos arquivos de dados/ e apaga sessões."""
        for sufixo in ("", "-wal", "-shm"):
            alvo = Path(str(self.path) + sufixo)
            alvo.unlink(missing_ok=True)
        self.garantir_schema()
        self._semear()
        sessoes = caminho_sessoes()
        for sufixo in ("", "-wal", "-shm"):
            Path(str(sessoes) + sufixo).unlink(missing_ok=True)

    def _semear(self) -> None:
        dados = caminho_dados()
        reservas = json.loads((dados / "reservas.json").read_text(encoding="utf-8"))
        visitantes = json.loads((dados / "visitantes.json").read_text(encoding="utf-8"))
        with self._transacao() as conn:
            conn.execute("DELETE FROM reservas")
            conn.execute("DELETE FROM visitantes")
            conn.execute("DELETE FROM sessoes")
            conn.executemany(
                """
                INSERT INTO reservas (codigo, apartamento, area, data, ativa, origem)
                VALUES (?, ?, ?, ?, 1, NULL)
                """,
                [
                    (item["codigo"], item["apartamento"], item["area"], item["data"])
                    for item in reservas
                ],
            )
            conn.executemany(
                """
                INSERT INTO visitantes (apartamento, nome, data, origem)
                VALUES (?, ?, ?, NULL)
                """,
                [(item["apartamento"], item["nome"], item["data"]) for item in visitantes],
            )

    def apartamentos(self) -> dict[str, str]:
        bruto = json.loads(
            (caminho_dados() / "apartamentos.json").read_text(encoding="utf-8")
        )
        return {str(item["numero"]): item["morador"] for item in bruto}

    def areas(self) -> list[Area]:
        bruto = json.loads((caminho_dados() / "areas.json").read_text(encoding="utf-8"))
        return [
            Area(id=item["id"], nome=item["nome"], taxa=float(item["taxa"]))
            for item in bruto
        ]

    def resolver_area(self, texto: str) -> Area | None:
        if not texto or not str(texto).strip():
            return None
        chave = _sem_acento(str(texto))
        areas = self.areas()
        for area in areas:
            candidatos = {
                _sem_acento(area.id),
                _sem_acento(area.id.replace("-", " ")),
                _sem_acento(area.nome),
            }
            if chave in candidatos:
                return area
        for area in areas:
            nome = _sem_acento(area.nome)
            if nome and (nome in chave or chave in nome):
                return area
        return None

    def registrar_sessao(self, session_id: str, user_id: str, apartamento: str) -> None:
        with self._transacao() as conn:
            conn.execute(
                """
                INSERT INTO sessoes (session_id, user_id, apartamento)
                VALUES (?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    user_id = excluded.user_id,
                    apartamento = excluded.apartamento
                """,
                (session_id, user_id, apartamento),
            )

    def meta_sessao(self, session_id: str) -> tuple[str, str] | None:
        with self._conectar() as conn:
            row = conn.execute(
                "SELECT user_id, apartamento FROM sessoes WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return row["user_id"], row["apartamento"]

    def apartamento_da_sessao(self, session_id: str) -> str | None:
        meta = self.meta_sessao(session_id)
        if meta is None:
            return None
        return meta[1]

    def listar_reservas(self, apartamento: str) -> list[dict[str, str]]:
        with self._conectar() as conn:
            rows = conn.execute(
                """
                SELECT codigo, area, data FROM reservas
                WHERE apartamento = ? AND ativa = 1
                ORDER BY data, codigo
                """,
                (apartamento,),
            ).fetchall()
        return [
            {"codigo": row["codigo"], "area": row["area"], "data": row["data"]}
            for row in rows
        ]

    def listar_visitantes(self, apartamento: str) -> list[dict[str, str]]:
        with self._conectar() as conn:
            rows = conn.execute(
                """
                SELECT nome, data FROM visitantes
                WHERE apartamento = ?
                ORDER BY data, nome, id
                """,
                (apartamento,),
            ).fetchall()
        return [{"nome": row["nome"], "data": row["data"]} for row in rows]

    def codigos_de_outros(self, apartamento: str) -> set[str]:
        with self._conectar() as conn:
            rows = conn.execute(
                "SELECT codigo FROM reservas WHERE apartamento != ?",
                (apartamento,),
            ).fetchall()
        return {row["codigo"] for row in rows}

    def visitantes_de_outros(self, apartamento: str) -> set[str]:
        with self._conectar() as conn:
            rows = conn.execute(
                "SELECT nome FROM visitantes WHERE apartamento != ?",
                (apartamento,),
            ).fetchall()
        return {row["nome"] for row in rows}

    def disponivel(self, area_id: str, data: str) -> bool:
        with self._conectar() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM reservas
                WHERE area = ? AND data = ? AND ativa = 1
                """,
                (area_id, data),
            ).fetchone()
        return row is None

    def criar_reserva(
        self,
        apartamento: str,
        area_id: str,
        data: str,
        origem: str | None,
    ) -> dict:
        """Grava a reserva no mesmo instante em que a exclusividade é decidida.

        O INSERT concorre com o índice único parcial `reservas_uma_por_data`.
        Quem chega depois recebe recusa, não uma segunda reserva.
        """
        for _ in range(6):
            codigo = self._novo_codigo()
            try:
                with self._transacao() as conn:
                    if origem:
                        ja = conn.execute(
                            """
                            SELECT codigo, area, data, ativa FROM reservas
                            WHERE origem = ?
                            """,
                            (origem,),
                        ).fetchone()
                        if ja is not None and ja["ativa"] == 1:
                            return {
                                "ok": True,
                                "codigo": ja["codigo"],
                                "area": ja["area"],
                                "data": ja["data"],
                                "repetida": True,
                            }
                    conn.execute(
                        """
                        INSERT INTO reservas
                            (codigo, apartamento, area, data, ativa, origem)
                        VALUES (?, ?, ?, ?, 1, ?)
                        """,
                        (codigo, apartamento, area_id, data, origem),
                    )
                return {
                    "ok": True,
                    "codigo": codigo,
                    "area": area_id,
                    "data": data,
                    "repetida": False,
                }
            except sqlite3.IntegrityError:
                if origem:
                    existente = self._reserva_por_origem(origem)
                    if existente is not None:
                        return {
                            "ok": True,
                            "codigo": existente["codigo"],
                            "area": existente["area"],
                            "data": existente["data"],
                            "repetida": True,
                        }
                if not self.disponivel(area_id, data):
                    return {"ok": False, "motivo": "ocupada"}
        return {"ok": False, "motivo": "erro_interno"}

    def _reserva_por_origem(self, origem: str) -> sqlite3.Row | None:
        with self._conectar() as conn:
            return conn.execute(
                """
                SELECT codigo, area, data FROM reservas
                WHERE origem = ? AND ativa = 1
                """,
                (origem,),
            ).fetchone()

    def _novo_codigo(self) -> str:
        with self._conectar() as conn:
            for _ in range(8):
                codigo = "RSV-" + secrets.token_hex(4).upper()
                row = conn.execute(
                    "SELECT 1 FROM reservas WHERE codigo = ?",
                    (codigo,),
                ).fetchone()
                if row is None:
                    return codigo
        raise RuntimeError("Não foi possível gerar um código de reserva.")

    def cancelar_reserva(self, apartamento: str, area_id: str, data: str) -> dict:
        with self._transacao() as conn:
            row = conn.execute(
                """
                SELECT codigo FROM reservas
                WHERE apartamento = ? AND area = ? AND data = ? AND ativa = 1
                """,
                (apartamento, area_id, data),
            ).fetchone()
            if row is None:
                return {"ok": False, "motivo": "reserva_nao_encontrada"}
            conn.execute(
                "UPDATE reservas SET ativa = 0 WHERE codigo = ?",
                (row["codigo"],),
            )
        return {"ok": True, "codigo": row["codigo"], "area": area_id, "data": data}

    def autorizar_visitante(
        self,
        apartamento: str,
        nome: str,
        data: str,
        origem: str | None,
    ) -> dict:
        try:
            with self._transacao() as conn:
                if origem:
                    ja = conn.execute(
                        """
                        SELECT nome, data FROM visitantes WHERE origem = ?
                        """,
                        (origem,),
                    ).fetchone()
                    if ja is not None:
                        return {
                            "ok": True,
                            "nome": ja["nome"],
                            "data": ja["data"],
                            "repetida": True,
                        }
                conn.execute(
                    """
                    INSERT INTO visitantes (apartamento, nome, data, origem)
                    VALUES (?, ?, ?, ?)
                    """,
                    (apartamento, nome, data, origem),
                )
        except sqlite3.IntegrityError:
            if origem:
                with self._conectar() as conn:
                    ja = conn.execute(
                        "SELECT nome, data FROM visitantes WHERE origem = ?",
                        (origem,),
                    ).fetchone()
                if ja is not None:
                    return {
                        "ok": True,
                        "nome": ja["nome"],
                        "data": ja["data"],
                        "repetida": True,
                    }
            return {"ok": False, "motivo": "erro_interno"}
        return {"ok": True, "nome": nome, "data": data, "repetida": False}


def nome_de_visitante(valor: str) -> str:
    partes = " ".join(str(valor).split()).split(" ")
    artigos = {"a", "o", "as", "os", "da", "do", "de", "das", "dos"}
    while partes and partes[0].lower() in artigos:
        partes.pop(0)
    return " ".join(partes).strip(" .,;")


def normalizar_data(valor: str) -> str | None:
    texto = str(valor).strip()
    for formato in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(texto, formato).date().isoformat()
        except ValueError:
            continue
    return None


def obter_condominio() -> Condominio:
    global _condo
    caminho = caminho_banco()
    if _condo is None or _condo.path != caminho:
        _condo = Condominio(caminho)
        _condo.garantir_inicial()
    return _condo


def usar_condominio(condo: Condominio | None) -> None:
    global _condo
    _condo = condo
