"""Regras do jogo Quiz TCP, sem nenhum código de rede.

Aqui ficam as salas, as partidas, o registro das respostas, a pontuação e o
ranking. O servidor chama estas funções e cuida apenas de enviar as mensagens.
Como não há sockets neste módulo, todas as regras podem ser testadas sem rede
e com um relógio falso (ver tests/test_quiz.py).
"""

from __future__ import annotations

import json
import random
import string
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import protocolo as p

TAMANHO_CODIGO = 4
ALFABETO_CODIGO = string.ascii_uppercase + string.digits
LIMITE_TEXTO_BYTES = 900  # garante que EVT QUESTION e EVT OPTION cabem em 1024 bytes


class ErroDeJogo(Exception):
    """Uma regra do jogo foi violada. ``codigo`` é o código de erro do protocolo."""

    def __init__(self, codigo: str) -> None:
        super().__init__(p.ERROS[codigo])
        self.codigo = codigo


@dataclass(frozen=True)
class Pergunta:
    """Uma pergunta do banco: texto, 4 opções e a letra da opção correta."""

    texto: str
    opcoes: tuple[str, str, str, str]
    correta: str


@dataclass(frozen=True)
class Resposta:
    """Resposta aceita de um jogador na pergunta atual."""

    letra: str
    pontos: int


@dataclass(frozen=True)
class ResultadoJogador:
    """Dados de um jogador no fechamento de uma pergunta (viram o EVT RESULT)."""

    letra: str   # letra enviada ou "-" se não respondeu
    pontos: int  # pontos ganhos nesta pergunta
    total: int   # pontuação acumulada na partida


@dataclass(frozen=True)
class Saida:
    """O que mudou na sala quando um jogador saiu."""

    sala_apagada: bool
    novo_host: str | None


@dataclass
class Sala:
    """Uma sala de jogo e, quando há partida, o andamento dela."""

    codigo: str
    host: str
    # Jogadores conectados, na ordem em que entraram. O primeiro da lista é
    # quem está há mais tempo na sala, e assume se o anfitrião sair.
    conectados: list[str] = field(default_factory=list)
    estado: str = p.LOBBY
    perguntas: list[Pergunta] = field(default_factory=list)
    indice: int = -1  # índice da pergunta atual (0 é a primeira)
    # Pontuação de todos os participantes da partida, inclusive de quem saiu
    # (a pontuação de quem saiu fica congelada aqui).
    pontos: dict[str, int] = field(default_factory=dict)
    respostas: dict[str, Resposta] = field(default_factory=dict)
    # Quem retornou no meio da pergunta atual e por isso não pode respondê-la.
    sem_direito: set[str] = field(default_factory=set)
    prazo: float = 0.0  # instante em que termina a pergunta ou a pausa atual

    @property
    def em_partida(self) -> bool:
        """Indica se há uma partida em andamento (sala fora do lobby)."""
        return self.estado != p.LOBBY

    @property
    def pergunta_atual(self) -> Pergunta:
        """A pergunta que está aberta ou que acabou de ser fechada."""
        return self.perguntas[self.indice]

    @property
    def numero_pergunta(self) -> int:
        """Número da pergunta atual, começando em 1."""
        return self.indice + 1

    def contagem_respostas(self) -> tuple[int, int]:
        """Devolve (respondidas, jogadores) para o EVT ANSWERED.

        Só entram na conta os jogadores conectados que podem responder a
        pergunta atual: quem caiu ou retornou no meio dela fica de fora.
        """
        aptos = [nome for nome in self.conectados if nome not in self.sem_direito]
        respondidas = sum(1 for nome in aptos if nome in self.respostas)
        return respondidas, len(aptos)

    def todos_responderam(self) -> bool:
        """Fechamento antecipado: todos os conectados aptos já responderam?"""
        respondidas, aptos = self.contagem_respostas()
        return respondidas >= aptos

    def ranking(self) -> list[tuple[int, str, int]]:
        """Ranking da partida como lista de (posição, nome, pontos).

        Ordena por pontuação decrescente; em caso de empate vale a ordem
        alfabética do nome.
        """
        ordenado = sorted(
            self.pontos.items(),
            key=lambda item: (-item[1], item[0].lower(), item[0]),
        )
        return [(posicao, nome, pontos) for posicao, (nome, pontos) in enumerate(ordenado, 1)]


def calcular_pontos(restante_ms: int, total_ms: int) -> int:
    """Pontos de uma resposta correta: de 500 (último instante) a 1000 (imediata)."""
    restante_ms = max(0, min(restante_ms, total_ms))
    return 500 + (500 * restante_ms) // total_ms


class Quiz:
    """Todas as salas do servidor e as regras que valem para elas.

    ``relogio`` é a função que informa o instante atual em segundos. Em
    produção é ``time.monotonic``; nos testes é um relógio falso, para testar
    prazos e pontuação sem esperar de verdade.
    """

    def __init__(
        self,
        perguntas: list[Pergunta],
        tempo_pergunta: float = p.TEMPO_PERGUNTA,
        pausa: float = p.PAUSA_RESULTADO,
        rodadas: int = p.RODADAS_PADRAO,
        relogio: Callable[[], float] = time.monotonic,
        sorteio: random.Random | None = None,
    ) -> None:
        if rodadas < 1 or rodadas > len(perguntas):
            raise ValueError(
                f"rodadas deve ficar entre 1 e {len(perguntas)} (quantidade de perguntas do banco)"
            )
        self.banco = perguntas
        self.tempo_pergunta = tempo_pergunta
        self.pausa = pausa
        self.rodadas = rodadas
        self.relogio = relogio
        self.sorteio = sorteio or random.Random()
        self.salas: dict[str, Sala] = {}

    # -- salas --------------------------------------------------------------

    def gerar_codigo(self) -> str:
        """Sorteia um código de 4 caracteres que nenhuma sala esteja usando."""
        while True:
            codigo = "".join(self.sorteio.choices(ALFABETO_CODIGO, k=TAMANHO_CODIGO))
            if codigo not in self.salas:
                return codigo

    def criar_sala(self, nome: str) -> Sala:
        """Cria uma sala em LOBBY com ``nome`` como anfitrião e único jogador."""
        sala = Sala(codigo=self.gerar_codigo(), host=nome, conectados=[nome])
        self.salas[sala.codigo] = sala
        return sala

    def entrar(self, codigo: str, nome: str) -> Sala:
        """Coloca ``nome`` na sala. Durante uma partida, só quem já participava.

        Erros: 21 (sala inexistente), 22 (jogo em andamento), 23 (sala cheia).
        """
        sala = self.salas.get(codigo)
        if sala is None:
            raise ErroDeJogo(p.ERRO_SALA_INEXISTENTE)
        if sala.em_partida:
            if nome not in sala.pontos:
                raise ErroDeJogo(p.ERRO_JOGO_EM_ANDAMENTO)
            # Retorno à partida: a pergunta em andamento é perdida, a menos
            # que o jogador já a tivesse respondido antes de cair.
            if sala.estado == p.PERGUNTA and nome not in sala.respostas:
                sala.sem_direito.add(nome)
        elif len(sala.conectados) >= p.MAX_JOGADORES:
            raise ErroDeJogo(p.ERRO_SALA_CHEIA)
        sala.conectados.append(nome)
        return sala

    def sair(self, sala: Sala, nome: str) -> Saida:
        """Tira ``nome`` da sala (saída voluntária ou queda).

        A pontuação continua em ``sala.pontos`` (congelada). Se a sala ficar
        vazia ela é apagada; se quem saiu era o anfitrião, assume o jogador
        que está há mais tempo na sala.
        """
        sala.conectados.remove(nome)
        if not sala.conectados:
            del self.salas[sala.codigo]
            return Saida(sala_apagada=True, novo_host=None)
        if sala.host == nome:
            sala.host = sala.conectados[0]
            return Saida(sala_apagada=False, novo_host=sala.host)
        return Saida(sala_apagada=False, novo_host=None)

    # -- partida ------------------------------------------------------------

    def iniciar(self, sala: Sala, nome: str) -> None:
        """Começa uma partida e abre a primeira pergunta.

        Erros: 24 (não é o anfitrião), 25 (menos de 2 jogadores).
        """
        if sala.em_partida:
            raise ErroDeJogo(p.ERRO_ESTADO_INVALIDO)
        if nome != sala.host:
            raise ErroDeJogo(p.ERRO_NAO_E_HOST)
        if len(sala.conectados) < p.MIN_JOGADORES:
            raise ErroDeJogo(p.ERRO_JOGADORES_INSUFICIENTES)
        sala.perguntas = self.sorteio.sample(self.banco, self.rodadas)
        sala.pontos = {jogador: 0 for jogador in sala.conectados}
        sala.indice = -1
        self._abrir_proxima_pergunta(sala)

    def _abrir_proxima_pergunta(self, sala: Sala) -> None:
        """Passa para a pergunta seguinte e inicia a contagem do prazo."""
        sala.indice += 1
        sala.respostas = {}
        sala.sem_direito = set()
        sala.estado = p.PERGUNTA
        sala.prazo = self.relogio() + self.tempo_pergunta

    def responder(self, sala: Sala, nome: str, letra: str) -> Resposta:
        """Registra a resposta de ``nome`` e calcula os pontos dela.

        O tempo é medido aqui, no servidor, no instante em que a resposta é
        processada. Erros: 31 (sem pergunta aberta), 32 (já respondeu).
        """
        agora = self.relogio()
        if sala.estado != p.PERGUNTA or nome in sala.sem_direito or agora > sala.prazo:
            raise ErroDeJogo(p.ERRO_SEM_PERGUNTA_ABERTA)
        if nome in sala.respostas:
            raise ErroDeJogo(p.ERRO_JA_RESPONDEU)
        pontos = 0
        if letra == sala.pergunta_atual.correta:
            restante_ms = round((sala.prazo - agora) * 1000)
            total_ms = round(self.tempo_pergunta * 1000)
            pontos = calcular_pontos(restante_ms, total_ms)
        resposta = Resposta(letra, pontos)
        sala.respostas[nome] = resposta
        return resposta

    def fechar_pergunta(self, sala: Sala) -> dict[str, ResultadoJogador]:
        """Fecha a pergunta, soma os pontos e inicia a pausa do resultado.

        Devolve o resultado de cada participante da partida. Quem não
        respondeu (inclusive quem caiu) fica com "-" e 0 pontos.
        """
        resultados: dict[str, ResultadoJogador] = {}
        for nome in sala.pontos:
            resposta = sala.respostas.get(nome)
            ganhos = resposta.pontos if resposta else 0
            sala.pontos[nome] += ganhos
            resultados[nome] = ResultadoJogador(
                letra=resposta.letra if resposta else p.SEM_RESPOSTA,
                pontos=ganhos,
                total=sala.pontos[nome],
            )
        sala.estado = p.RESULTADO
        sala.prazo = self.relogio() + self.pausa
        return resultados

    def avancar(self, sala: Sala) -> Pergunta | None:
        """Depois da pausa: abre a próxima pergunta ou encerra a partida.

        Devolve a nova pergunta, ou None se a partida terminou e a sala voltou
        ao LOBBY. O ranking final continua disponível em ``sala.ranking()``.
        """
        if sala.indice + 1 < len(sala.perguntas):
            self._abrir_proxima_pergunta(sala)
            return sala.pergunta_atual
        sala.estado = p.LOBBY
        return None

    def salas_com_prazo_vencido(self) -> list[Sala]:
        """Salas em partida cujo prazo (da pergunta ou da pausa) já passou."""
        agora = self.relogio()
        return [sala for sala in self.salas.values() if sala.em_partida and agora >= sala.prazo]


# ---------------------------------------------------------------------------
# Banco de perguntas
# ---------------------------------------------------------------------------


def _ler_correta(valor: object, posicao: int) -> str:
    """Aceita a resposta correta como letra ("A" a "D") ou índice (0 a 3)."""
    if isinstance(valor, str) and valor.upper() in p.LETRAS:
        return valor.upper()
    if isinstance(valor, int) and not isinstance(valor, bool) and 0 <= valor < len(p.LETRAS):
        return p.LETRAS[valor]
    raise ValueError(f"pergunta {posicao}: 'correta' deve ser uma letra de A a D ou um índice de 0 a 3")


def _texto_valido(valor: object) -> bool:
    """Texto não vazio, de uma linha só e que cabe em uma mensagem do protocolo."""
    return (
        isinstance(valor, str)
        and valor.strip() != ""
        and "\n" not in valor
        and "\r" not in valor
        and len(valor.encode(p.CODIFICACAO)) <= LIMITE_TEXTO_BYTES
    )


def validar_perguntas(dados: object) -> list[Pergunta]:
    """Confere o conteúdo lido do JSON e devolve a lista de perguntas."""
    if not isinstance(dados, list) or not dados:
        raise ValueError("o arquivo de perguntas deve conter uma lista não vazia")
    perguntas: list[Pergunta] = []
    for posicao, item in enumerate(dados, 1):
        if not isinstance(item, dict):
            raise ValueError(f"pergunta {posicao}: deve ser um objeto com texto, opcoes e correta")
        texto = item.get("texto")
        opcoes = item.get("opcoes")
        if not _texto_valido(texto):
            raise ValueError(f"pergunta {posicao}: 'texto' ausente, vazio ou longo demais")
        if not isinstance(opcoes, list) or len(opcoes) != len(p.LETRAS):
            raise ValueError(f"pergunta {posicao}: 'opcoes' deve ter exatamente 4 itens")
        if not all(_texto_valido(opcao) for opcao in opcoes):
            raise ValueError(f"pergunta {posicao}: opção ausente, vazia ou longa demais")
        correta = _ler_correta(item.get("correta"), posicao)
        perguntas.append(Pergunta(texto.strip(), tuple(o.strip() for o in opcoes), correta))
    return perguntas


def carregar_perguntas(caminho: str | Path) -> list[Pergunta]:
    """Lê e valida o arquivo JSON de perguntas."""
    with open(caminho, encoding="utf-8") as arquivo:
        return validar_perguntas(json.load(arquivo))
