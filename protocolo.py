"""Protocolo de aplicação QUIZ/1.0.

Este módulo é a única definição do protocolo no código: constantes, códigos de
erro, montagem e interpretação das mensagens e o leitor de linhas. Ele é usado
pelo servidor e pelo cliente, para que os dois lados nunca divirjam.

Resumo do formato (seção 7 do README):
  * mensagens em texto UTF-8, uma por linha, terminadas em "\\n";
  * campos separados por um espaço;
  * somente o último campo pode conter espaços (texto de pergunta ou de opção).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Constantes gerais
# ---------------------------------------------------------------------------

VERSAO = "QUIZ/1.0"
PORTA_PADRAO = 5000
CODIFICACAO = "utf-8"
TAMANHO_MAXIMO_LINHA = 1024  # bytes, sem contar o "\n" final

# Tempos, em segundos
TEMPO_PERGUNTA = 20      # prazo para responder cada pergunta
PAUSA_RESULTADO = 5      # pausa entre o resultado e a próxima pergunta
INTERVALO_PING = 20      # o cliente envia PING a cada 20 s (keepalive)
TEMPO_INATIVIDADE = 60   # sem nenhuma linha nesse tempo, o servidor fecha

# Limites do jogo
RODADAS_PADRAO = 5
MIN_JOGADORES = 2
MAX_JOGADORES = 10
LETRAS = ("A", "B", "C", "D")

# ---------------------------------------------------------------------------
# Requisições (cliente -> servidor)
# ---------------------------------------------------------------------------

CMD_LOGIN = "LOGIN"
CMD_CREATE = "CREATE"
CMD_JOIN = "JOIN"
CMD_START = "START"
CMD_ANSWER = "ANSWER"
CMD_LEAVE = "LEAVE"
CMD_PING = "PING"
CMD_QUIT = "QUIT"

# Quantos argumentos cada comando exige (qualquer outra quantidade é ERR 02).
ARGUMENTOS: dict[str, int] = {
    CMD_LOGIN: 1,
    CMD_CREATE: 0,
    CMD_JOIN: 1,
    CMD_START: 0,
    CMD_ANSWER: 1,
    CMD_LEAVE: 0,
    CMD_PING: 0,
    CMD_QUIT: 0,
}

# ---------------------------------------------------------------------------
# Respostas e eventos (servidor -> cliente)
# ---------------------------------------------------------------------------

RESP_OK = "OK"
RESP_ERR = "ERR"
RESP_PONG = "PONG"
RESP_BYE = "BYE"
PREFIXO_EVENTO = "EVT"
TIPO_REQUISICAO = "REQ"  # usado só por interpretar_linha, não trafega no socket

EVT_JOINED = "JOINED"
EVT_LEFT = "LEFT"
EVT_HOST = "HOST"
EVT_START = "START"
EVT_QUESTION = "QUESTION"
EVT_OPTION = "OPTION"
EVT_ANSWERED = "ANSWERED"
EVT_RESULT = "RESULT"
EVT_RANKING = "RANKING"
EVT_RANK = "RANK"
EVT_END = "END"
EVT_SHUTDOWN = "SHUTDOWN"

# Eventos cujo último campo é texto livre: nome -> quantidade de campos fixos
# que vêm antes do texto. É a regra "só o último campo pode conter espaços".
CAMPOS_ANTES_DO_TEXTO: dict[str, int] = {
    EVT_QUESTION: 3,  # <n> <total> <tempo> <texto>
    EVT_OPTION: 1,    # <letra> <texto>
}

# Motivos do EVT LEFT
MOTIVO_LEAVE = "leave"
MOTIVO_QUIT = "quit"
MOTIVO_QUEDA = "disconnect"
MOTIVO_TIMEOUT = "timeout"

SEM_RESPOSTA = "-"  # campo <sua_resposta> do EVT RESULT de quem não respondeu

# ---------------------------------------------------------------------------
# Estados
# ---------------------------------------------------------------------------

# Estados do cliente (seção 7.2)
CONECTADO = "CONECTADO"
AUTENTICADO = "AUTENTICADO"
NA_SALA = "NA_SALA"
EM_JOGO = "EM_JOGO"

# Estados da sala (seção 7.3)
LOBBY = "LOBBY"
PERGUNTA = "PERGUNTA"
RESULTADO = "RESULTADO"

# Campo <estado> da resposta ao JOIN
JOIN_LOBBY = "LOBBY"
JOIN_JOGO = "JOGO"

# Em quais estados do cliente cada comando é aceito (fora deles: ERR 03).
ESTADOS_PERMITIDOS: dict[str, tuple[str, ...]] = {
    CMD_LOGIN: (CONECTADO,),
    CMD_CREATE: (AUTENTICADO,),
    CMD_JOIN: (AUTENTICADO,),
    CMD_START: (NA_SALA,),
    CMD_ANSWER: (EM_JOGO,),
    CMD_LEAVE: (NA_SALA, EM_JOGO),
    CMD_PING: (CONECTADO, AUTENTICADO, NA_SALA, EM_JOGO),
    CMD_QUIT: (CONECTADO, AUTENTICADO, NA_SALA, EM_JOGO),
}

# ---------------------------------------------------------------------------
# Códigos de erro (seção 8)
# Faixas: 0x gerais, 1x autenticação, 2x sala, 3x resposta, 9x conexão.
# ---------------------------------------------------------------------------

ERRO_COMANDO_DESCONHECIDO = "01"
ERRO_ARGUMENTOS_INVALIDOS = "02"
ERRO_ESTADO_INVALIDO = "03"
ERRO_LINHA_MUITO_LONGA = "04"
ERRO_NOME_INVALIDO = "11"
ERRO_NOME_EM_USO = "12"
ERRO_SALA_INEXISTENTE = "21"
ERRO_JOGO_EM_ANDAMENTO = "22"
ERRO_SALA_CHEIA = "23"
ERRO_NAO_E_HOST = "24"
ERRO_JOGADORES_INSUFICIENTES = "25"
ERRO_SEM_PERGUNTA_ABERTA = "31"
ERRO_JA_RESPONDEU = "32"
ERRO_OPCAO_INVALIDA = "33"
ERRO_INATIVIDADE = "90"

ERROS: dict[str, str] = {
    ERRO_COMANDO_DESCONHECIDO: "comando_desconhecido",
    ERRO_ARGUMENTOS_INVALIDOS: "argumentos_invalidos",
    ERRO_ESTADO_INVALIDO: "estado_invalido",
    ERRO_LINHA_MUITO_LONGA: "linha_muito_longa",
    ERRO_NOME_INVALIDO: "nome_invalido",
    ERRO_NOME_EM_USO: "nome_em_uso",
    ERRO_SALA_INEXISTENTE: "sala_inexistente",
    ERRO_JOGO_EM_ANDAMENTO: "jogo_em_andamento",
    ERRO_SALA_CHEIA: "sala_cheia",
    ERRO_NAO_E_HOST: "nao_e_host",
    ERRO_JOGADORES_INSUFICIENTES: "jogadores_insuficientes",
    ERRO_SEM_PERGUNTA_ABERTA: "sem_pergunta_aberta",
    ERRO_JA_RESPONDEU: "ja_respondeu",
    ERRO_OPCAO_INVALIDA: "opcao_invalida",
    ERRO_INATIVIDADE: "inatividade",
}

# ---------------------------------------------------------------------------
# Validações
# ---------------------------------------------------------------------------

_FORMATO_NOME = re.compile(r"[A-Za-z0-9_]{3,16}")
_FORMATO_CODIGO = re.compile(r"[A-Z0-9]{4}")


def nome_valido(nome: str) -> bool:
    """Nome de usuário: 3 a 16 caracteres, apenas letras sem acento, dígitos e "_"."""
    return _FORMATO_NOME.fullmatch(nome) is not None


def codigo_valido(codigo: str) -> bool:
    """Código de sala: exatamente 4 caracteres, letras maiúsculas e dígitos."""
    return _FORMATO_CODIGO.fullmatch(codigo) is not None


def letra_valida(letra: str) -> bool:
    """Letra de resposta: A, B, C ou D, em maiúscula."""
    return letra in LETRAS


# ---------------------------------------------------------------------------
# Montagem de mensagens
# ---------------------------------------------------------------------------


def _juntar(*campos: object) -> str:
    """Junta os campos com um espaço, que é o separador do protocolo."""
    return " ".join(str(campo) for campo in campos)


def montar_requisicao(comando: str, *argumentos: object) -> str:
    """Monta uma requisição, por exemplo ``ANSWER B``."""
    return _juntar(comando, *argumentos)


def montar_ok(comando: str, *campos: object) -> str:
    """Monta uma resposta de sucesso, por exemplo ``OK CREATE K7QD``."""
    return _juntar(RESP_OK, comando, *campos)


def montar_erro(codigo: str) -> str:
    """Monta ``ERR <código> <motivo>`` a partir da tabela de erros."""
    return _juntar(RESP_ERR, codigo, ERROS[codigo])


def montar_evento(nome: str, *campos: object) -> str:
    """Monta um evento, por exemplo ``EVT RANK 1 bia 870``."""
    return _juntar(PREFIXO_EVENTO, nome, *campos)


# ---------------------------------------------------------------------------
# Interpretação de mensagens
# ---------------------------------------------------------------------------


@dataclass
class Mensagem:
    """Uma linha do protocolo já separada em partes.

    ``tipo``   é ``REQ``, ``OK``, ``ERR``, ``EVT``, ``PONG`` ou ``BYE``.
    ``nome``   é o comando (REQ e OK), o código (ERR) ou o evento (EVT).
    ``campos`` são os demais campos, na ordem em que aparecem na linha.
    """

    tipo: str
    nome: str = ""
    campos: list[str] = field(default_factory=list)


def separar_requisicao(linha: str) -> tuple[str, list[str]]:
    """Separa uma requisição em comando e argumentos.

    A separação é feita em cada espaço. Dois espaços seguidos geram um
    argumento vazio, que o servidor recusa com ERR 02 pela contagem.
    """
    partes = linha.split(" ")
    return partes[0], partes[1:]


def interpretar_linha(linha: str) -> Mensagem:
    """Interpreta qualquer linha do protocolo (requisição, resposta ou evento)."""
    primeiro, _, resto = linha.partition(" ")

    if primeiro == PREFIXO_EVENTO:
        nome, _, argumentos = resto.partition(" ")
        if not argumentos:
            return Mensagem(PREFIXO_EVENTO, nome)
        if nome in CAMPOS_ANTES_DO_TEXTO:
            # O último campo é texto livre: limita a quantidade de divisões
            # para que os espaços do texto não virem campos separados.
            campos = argumentos.split(" ", CAMPOS_ANTES_DO_TEXTO[nome])
        else:
            campos = argumentos.split(" ")
        return Mensagem(PREFIXO_EVENTO, nome, campos)

    if primeiro == RESP_OK:
        nome, _, argumentos = resto.partition(" ")
        return Mensagem(RESP_OK, nome, argumentos.split(" ") if argumentos else [])

    if primeiro == RESP_ERR:
        codigo, _, motivo = resto.partition(" ")
        return Mensagem(RESP_ERR, codigo, [motivo] if motivo else [])

    if primeiro in (RESP_PONG, RESP_BYE):
        return Mensagem(primeiro)

    comando, argumentos_req = separar_requisicao(linha)
    return Mensagem(TIPO_REQUISICAO, comando, argumentos_req)


# ---------------------------------------------------------------------------
# Leitura por linhas
# ---------------------------------------------------------------------------


class LinhaMuitoLonga(Exception):
    """Chegaram mais de 1024 bytes sem um "\\n" (ERR 04)."""


class LeitorDeLinhas:
    """Transforma o fluxo de bytes do TCP em linhas completas.

    Por que isto é necessário: o TCP entrega um FLUXO de bytes, não mensagens.
    Um único ``recv`` pode devolver meia mensagem, uma mensagem inteira ou
    várias mensagens coladas, dependendo de como os dados foram divididos em
    segmentos no caminho. Por isso cada lado guarda o que recebeu em um buffer
    e só considera que existe uma mensagem quando encontra o delimitador "\\n".

    Uso::

        leitor.alimentar(sock.recv(4096))
        while (linha := leitor.proxima_linha()) is not None:
            ...
    """

    def __init__(self, limite: int = TAMANHO_MAXIMO_LINHA) -> None:
        self._buffer = bytearray()
        self._limite = limite

    def alimentar(self, dados: bytes) -> None:
        """Acrescenta ao buffer os bytes que acabaram de chegar pelo socket."""
        self._buffer.extend(dados)

    def proxima_linha(self) -> bytes | None:
        """Devolve a próxima linha completa (sem o "\\n") ou None se não há.

        Levanta LinhaMuitoLonga se a linha passar do limite, esteja ela
        completa ou ainda sem o "\\n".
        """
        posicao = self._buffer.find(b"\n")
        if posicao == -1:
            if len(self._buffer) > self._limite:
                raise LinhaMuitoLonga()
            return None  # mensagem ainda incompleta: espera o próximo recv
        if posicao > self._limite:
            raise LinhaMuitoLonga()
        linha = bytes(self._buffer[:posicao])
        del self._buffer[: posicao + 1]
        # telnet e alguns terminais terminam a linha com "\r\n": o "\r" é
        # descartado para que esses clientes também funcionem.
        if linha.endswith(b"\r"):
            linha = linha[:-1]
        return linha


def codificar_linha(linha: str) -> bytes:
    """Converte uma linha em bytes UTF-8 prontos para o ``sendall``."""
    return (linha + "\n").encode(CODIFICACAO)
