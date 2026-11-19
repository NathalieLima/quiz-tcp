"""Programa servidor do Quiz TCP.

Aceita conexões TCP, cria uma thread por cliente e fala somente o protocolo
QUIZ/1.0 (nada de HTTP ou WebSocket). As regras do jogo ficam em quiz.py;
aqui ficam os sockets, as threads, a máquina de estados de cada cliente e o
envio dos eventos.

Uso: python servidor.py --host 0.0.0.0 --porta 5000
"""

from __future__ import annotations

import argparse
import os
import select
import socket
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

import protocolo as p
from quiz import ErroDeJogo, Pergunta, Quiz, Sala, carregar_perguntas

TAMANHO_RECV = 4096       # máximo de bytes lidos em cada recv
FILA_DE_CONEXOES = 16     # conexões pendentes que o sistema guarda (listen)
INTERVALO_ACCEPT = 0.5    # de quanto em quanto tempo o accept verifica se deve parar
INTERVALO_VIGIA = 0.05    # de quanto em quanto tempo os prazos das salas são conferidos
LIMITE_ENVIO = 5.0        # um envio que trave por mais que isso derruba o cliente


def registrar_no_terminal(texto: str) -> None:
    """Log padrão do servidor: horário e texto, no terminal."""
    print(f"[{datetime.now():%H:%M:%S}] {texto}", flush=True)


class Cliente:
    """Tudo o que o servidor guarda sobre uma conexão."""

    def __init__(self, sock: socket.socket, endereco: tuple[str, int]) -> None:
        self.sock = sock
        self.endereco = f"{endereco[0]}:{endereco[1]}"
        self.estado = p.CONECTADO
        self.nome: str | None = None
        self.sala: Sala | None = None
        self.leitor = p.LeitorDeLinhas()
        self.ultima_linha = time.monotonic()  # usado para detectar inatividade
        self.pediu_quit = False

    def identificacao(self) -> str:
        """Endereço do cliente e, se já fez LOGIN, o nome."""
        return f"{self.endereco} ({self.nome})" if self.nome else self.endereco


class Servidor:
    """Servidor concorrente do protocolo QUIZ/1.0."""

    def __init__(
        self,
        host: str,
        porta: int,
        perguntas: list[Pergunta],
        tempo: float = p.TEMPO_PERGUNTA,
        rodadas: int = p.RODADAS_PADRAO,
        pausa: float = p.PAUSA_RESULTADO,
        inatividade: float = p.TEMPO_INATIVIDADE,
        registrar: Callable[[str], None] = registrar_no_terminal,
    ) -> None:
        self.host = host
        self.porta = porta
        self.quiz = Quiz(perguntas, tempo_pergunta=tempo, pausa=pausa, rodadas=rodadas)
        self.inatividade = inatividade
        self.registrar = registrar
        self.escuta: socket.socket | None = None
        self.clientes: set[Cliente] = set()
        self.nomes: dict[str, Cliente] = {}  # nome de usuário -> cliente conectado
        self.parando = threading.Event()
        # Por que um lock: cada cliente é atendido por uma thread, e há ainda a
        # thread que vigia os prazos. Todas mexem nas mesmas salas, nomes e
        # pontuações. Sem o lock, duas respostas processadas ao mesmo tempo
        # poderiam fechar a mesma pergunta duas vezes ou embaralhar o ranking.
        # Com ele, cada linha recebida é processada por inteiro (inclusive o
        # envio dos eventos que ela gera) antes de a próxima começar. É isso
        # que também garante a ordem das linhas para cada cliente: nada se
        # intercala entre EVT QUESTION e as 4 linhas EVT OPTION.
        # É um RLock para que uma função que já tem o lock possa chamar outra
        # que também o pede.
        self.trava = threading.RLock()
        self.tratadores: dict[str, Callable[[Cliente, list[str]], None]] = {
            p.CMD_LOGIN: self._tratar_login,
            p.CMD_CREATE: self._tratar_create,
            p.CMD_JOIN: self._tratar_join,
            p.CMD_START: self._tratar_start,
            p.CMD_ANSWER: self._tratar_answer,
            p.CMD_LEAVE: self._tratar_leave,
            p.CMD_PING: self._tratar_ping,
            p.CMD_QUIT: self._tratar_quit,
        }

    # ------------------------------------------------------------------
    # Socket de escuta e ciclo de vida
    # ------------------------------------------------------------------

    def iniciar(self) -> None:
        """Cria o socket de escuta e inicia a thread que vigia os prazos."""
        # AF_INET = endereços IPv4; SOCK_STREAM = TCP (fluxo confiável e ordenado).
        escuta = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # SO_REUSEADDR permite reiniciar o servidor logo em seguida, sem esperar
        # o sistema liberar a porta (estado TIME_WAIT). No Windows a opção tem
        # outro significado (deixaria dois servidores usarem a mesma porta ao
        # mesmo tempo) e não é necessária, por isso só é ativada fora dele.
        if os.name != "nt":
            escuta.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # bind: associa o socket ao endereço e à porta em que o servidor atende.
        escuta.bind((self.host, self.porta))
        # listen: coloca o socket em modo de escuta; a partir daqui o sistema
        # operacional completa o handshake TCP dos clientes e os deixa em fila.
        escuta.listen(FILA_DE_CONEXOES)
        # O accept espera no máximo INTERVALO_ACCEPT por vez. Sem isso, no
        # Windows o Ctrl+C não interrompe um accept bloqueado.
        escuta.settimeout(INTERVALO_ACCEPT)
        self.escuta = escuta
        self.porta = escuta.getsockname()[1]  # porta real, caso tenha sido pedida a 0
        threading.Thread(target=self._vigiar_salas, name="vigia", daemon=True).start()
        self.registrar(f"Servidor {p.VERSAO} escutando em {self.host}:{self.porta}")

    def servir(self) -> None:
        """Laço principal: aceita conexões e cria uma thread para cada cliente."""
        assert self.escuta is not None, "chame iniciar() antes de servir()"
        try:
            while not self.parando.is_set():
                try:
                    # accept: retira da fila uma conexão já estabelecida e devolve
                    # um NOVO socket, exclusivo para conversar com aquele cliente.
                    # O socket de escuta continua livre para aceitar os próximos.
                    sock, endereco = self.escuta.accept()
                except TimeoutError:
                    continue  # ninguém conectou neste intervalo; confere se deve parar
                # O envio tem prazo para que um cliente travado não segure o lock.
                sock.settimeout(LIMITE_ENVIO)
                cliente = Cliente(sock, endereco)
                with self.trava:
                    self.clientes.add(cliente)
                self.registrar(f"{cliente.endereco} conectou")
                threading.Thread(
                    target=self._atender_cliente, args=(cliente,), daemon=True
                ).start()
        finally:
            self.escuta.close()

    def parar(self) -> None:
        """Desligamento limpo: avisa todos com EVT SHUTDOWN e encerra as conexões."""
        if self.parando.is_set():
            return
        self.parando.set()
        with self.trava:
            for cliente in list(self.clientes):
                self._enviar(cliente, p.montar_evento(p.EVT_SHUTDOWN))
                self._derrubar(cliente)
        self.registrar("Servidor desligado")

    # ------------------------------------------------------------------
    # Thread de cada cliente
    # ------------------------------------------------------------------

    def _atender_cliente(self, cliente: Cliente) -> None:
        """Lê as linhas de um cliente até ele sair, cair ou ficar inativo."""
        motivo = p.MOTIVO_QUEDA
        try:
            motivo = self._receber_ate_sair(cliente)
        except OSError as erro:
            # Queda abrupta: o processo do cliente foi morto ou a rede caiu.
            # Aparece como ConnectionResetError, BrokenPipeError ou outro OSError.
            self.registrar(f"{cliente.identificacao()} erro de conexão: {erro}")
        finally:
            with self.trava:
                self._desconectar(cliente, motivo)
            cliente.sock.close()

    def _receber_ate_sair(self, cliente: Cliente) -> str:
        """Laço de leitura do socket. Devolve o motivo do fim da conexão."""
        while True:
            # Detecção de queda silenciosa (cabo solto, máquina travada): nesse
            # caso nenhum byte chega e o recv esperaria para sempre. Por isso a
            # espera tem limite: 60 s sem nenhuma linha (nem PING) encerram.
            restante = cliente.ultima_linha + self.inatividade - time.monotonic()
            if restante <= 0 or not self._ha_dados(cliente, restante):
                with self.trava:
                    self._enviar(cliente, p.montar_erro(p.ERRO_INATIVIDADE))
                return p.MOTIVO_TIMEOUT
            dados = cliente.sock.recv(TAMANHO_RECV)
            if not dados:
                # recv devolveu vazio: o outro lado fechou a conexão (FIN).
                return p.MOTIVO_QUEDA
            cliente.leitor.alimentar(dados)
            try:
                # Um recv pode trazer várias linhas, ou só um pedaço de uma.
                while (bruta := cliente.leitor.proxima_linha()) is not None:
                    cliente.ultima_linha = time.monotonic()
                    with self.trava:
                        self._processar_linha(cliente, bruta)
                    if cliente.pediu_quit:
                        return p.MOTIVO_QUIT
            except p.LinhaMuitoLonga:
                with self.trava:
                    self._enviar(cliente, p.montar_erro(p.ERRO_LINHA_MUITO_LONGA))
                return p.MOTIVO_QUEDA

    @staticmethod
    def _ha_dados(cliente: Cliente, espera: float) -> bool:
        """Espera até ``espera`` segundos por dados (ou fim de conexão) no socket."""
        prontos, _, _ = select.select([cliente.sock], [], [], espera)
        return bool(prontos)

    def _desconectar(self, cliente: Cliente, motivo: str) -> None:
        """Limpeza quando a conexão termina (seção 9.2 do README)."""
        if cliente not in self.clientes:
            return
        self.clientes.discard(cliente)
        # Durante o desligamento do servidor não há mais a quem avisar.
        if cliente.sala is not None and not self.parando.is_set():
            self._tirar_da_sala(cliente, motivo)
        if cliente.nome is not None:
            self.nomes.pop(cliente.nome, None)  # o nome fica livre para novo LOGIN
        self.registrar(f"{cliente.identificacao()} desconectou ({motivo})")

    # ------------------------------------------------------------------
    # Máquina de estados: valida e encaminha cada requisição
    # ------------------------------------------------------------------

    def _processar_linha(self, cliente: Cliente, bruta: bytes) -> None:
        """Valida uma linha recebida e chama o tratador do comando."""
        try:
            linha = bruta.decode(p.CODIFICACAO)
        except UnicodeDecodeError:
            self.registrar(f"{cliente.identificacao()} << (linha que não é UTF-8 válido)")
            self._enviar(cliente, p.montar_erro(p.ERRO_ARGUMENTOS_INVALIDOS))
            return
        self.registrar(f"{cliente.identificacao()} << {linha}")
        comando, argumentos = p.separar_requisicao(linha)
        if comando not in p.ARGUMENTOS:
            self._enviar(cliente, p.montar_erro(p.ERRO_COMANDO_DESCONHECIDO))
        elif len(argumentos) != p.ARGUMENTOS[comando] or "" in argumentos:
            self._enviar(cliente, p.montar_erro(p.ERRO_ARGUMENTOS_INVALIDOS))
        elif cliente.estado not in p.ESTADOS_PERMITIDOS[comando]:
            # O comando existe, mas não vale no estado atual do cliente.
            self._enviar(cliente, p.montar_erro(p.ERRO_ESTADO_INVALIDO))
        else:
            try:
                self.tratadores[comando](cliente, argumentos)
            except ErroDeJogo as erro:
                self._enviar(cliente, p.montar_erro(erro.codigo))

    def _tratar_login(self, cliente: Cliente, argumentos: list[str]) -> None:
        """LOGIN <nome>: CONECTADO -> AUTENTICADO."""
        nome = argumentos[0]
        if not p.nome_valido(nome):
            raise ErroDeJogo(p.ERRO_NOME_INVALIDO)
        if nome in self.nomes:
            raise ErroDeJogo(p.ERRO_NOME_EM_USO)
        cliente.nome = nome
        cliente.estado = p.AUTENTICADO
        self.nomes[nome] = cliente
        self._enviar(cliente, p.montar_ok(p.CMD_LOGIN, nome))

    def _tratar_create(self, cliente: Cliente, argumentos: list[str]) -> None:
        """CREATE: cria uma sala e torna o cliente anfitrião dela."""
        assert cliente.nome is not None
        cliente.sala = self.quiz.criar_sala(cliente.nome)
        cliente.estado = p.NA_SALA
        self._enviar(cliente, p.montar_ok(p.CMD_CREATE, cliente.sala.codigo))

    def _tratar_join(self, cliente: Cliente, argumentos: list[str]) -> None:
        """JOIN <codigo>: entra em uma sala ou retorna a uma partida em andamento."""
        assert cliente.nome is not None
        sala = self.quiz.entrar(argumentos[0], cliente.nome)
        cliente.sala = sala
        cliente.estado = p.EM_JOGO if sala.em_partida else p.NA_SALA
        estado_sala = p.JOIN_JOGO if sala.em_partida else p.JOIN_LOBBY
        jogadores = ",".join(sala.conectados)
        self._enviar(cliente, p.montar_ok(p.CMD_JOIN, sala.codigo, estado_sala, sala.host, jogadores))
        self._enviar_a_sala(sala, p.montar_evento(p.EVT_JOINED, cliente.nome), exceto=cliente)

    def _tratar_start(self, cliente: Cliente, argumentos: list[str]) -> None:
        """START: o anfitrião inicia a partida; todos da sala vão para EM_JOGO."""
        assert cliente.nome is not None and cliente.sala is not None
        sala = cliente.sala
        self.quiz.iniciar(sala, cliente.nome)
        self._enviar(cliente, p.montar_ok(p.CMD_START))
        for nome in sala.conectados:
            self.nomes[nome].estado = p.EM_JOGO
        self._enviar_a_sala(sala, p.montar_evento(p.EVT_START, len(sala.perguntas)))
        self._enviar_pergunta(sala)

    def _tratar_answer(self, cliente: Cliente, argumentos: list[str]) -> None:
        """ANSWER <letra>: registra a resposta e fecha a pergunta se todos responderam.

        É chamado com o lock tomado, então as respostas de uma sala são
        processadas uma de cada vez, na ordem em que chegaram.
        """
        assert cliente.nome is not None and cliente.sala is not None
        sala = cliente.sala
        letra = argumentos[0]
        if not p.letra_valida(letra):
            raise ErroDeJogo(p.ERRO_OPCAO_INVALIDA)
        self.quiz.responder(sala, cliente.nome, letra)
        self._enviar(cliente, p.montar_ok(p.CMD_ANSWER, letra))
        respondidas, jogadores = sala.contagem_respostas()
        self._enviar_a_sala(sala, p.montar_evento(p.EVT_ANSWERED, respondidas, jogadores))
        if sala.todos_responderam():
            self._fechar_pergunta(sala)  # fechamento antecipado

    def _tratar_leave(self, cliente: Cliente, argumentos: list[str]) -> None:
        """LEAVE: sai da sala e volta para AUTENTICADO."""
        self._enviar(cliente, p.montar_ok(p.CMD_LEAVE))
        self._tirar_da_sala(cliente, p.MOTIVO_LEAVE)
        cliente.estado = p.AUTENTICADO

    def _tratar_ping(self, cliente: Cliente, argumentos: list[str]) -> None:
        """PING: keepalive. Só receber a linha já zera a contagem de inatividade."""
        self._enviar(cliente, p.RESP_PONG)

    def _tratar_quit(self, cliente: Cliente, argumentos: list[str]) -> None:
        """QUIT: responde BYE; a thread do cliente fecha a conexão em seguida."""
        self._enviar(cliente, p.RESP_BYE)
        cliente.pediu_quit = True

    # ------------------------------------------------------------------
    # Salas: saída de jogador, perguntas, resultados e prazos
    # ------------------------------------------------------------------

    def _tirar_da_sala(self, cliente: Cliente, motivo: str) -> None:
        """Tira o cliente da sala e avisa os demais (EVT LEFT e, se preciso, EVT HOST)."""
        assert cliente.nome is not None and cliente.sala is not None
        sala = cliente.sala
        cliente.sala = None
        saida = self.quiz.sair(sala, cliente.nome)
        if saida.sala_apagada:
            self.registrar(f"sala {sala.codigo} ficou vazia e foi apagada")
            return
        self._enviar_a_sala(sala, p.montar_evento(p.EVT_LEFT, cliente.nome, motivo))
        if saida.novo_host is not None:
            self._enviar_a_sala(sala, p.montar_evento(p.EVT_HOST, saida.novo_host))
        # Quem saiu deixa de ser esperado: se todos os que ficaram já
        # responderam, a pergunta fecha sem aguardar o prazo.
        if sala.estado == p.PERGUNTA and sala.todos_responderam():
            self._fechar_pergunta(sala)

    def _enviar_pergunta(self, sala: Sala) -> None:
        """Envia EVT QUESTION e as 4 linhas EVT OPTION da pergunta atual."""
        pergunta = sala.pergunta_atual
        tempo = f"{self.quiz.tempo_pergunta:g}"
        linhas = [
            p.montar_evento(
                p.EVT_QUESTION, sala.numero_pergunta, len(sala.perguntas), tempo, pergunta.texto
            )
        ]
        for letra, opcao in zip(p.LETRAS, pergunta.opcoes):
            linhas.append(p.montar_evento(p.EVT_OPTION, letra, opcao))
        self._enviar_a_sala(sala, *linhas)

    def _fechar_pergunta(self, sala: Sala) -> None:
        """Fecha a pergunta e envia o resultado individual e o ranking."""
        resultados = self.quiz.fechar_pergunta(sala)
        ranking = sala.ranking()
        linhas_ranking = [p.montar_evento(p.EVT_RANKING, len(ranking))]
        for posicao, nome, pontos in ranking:
            linhas_ranking.append(p.montar_evento(p.EVT_RANK, posicao, nome, pontos))
        correta = sala.pergunta_atual.correta
        for nome in list(sala.conectados):
            resultado = resultados[nome]
            # EVT RESULT é individual: cada jogador recebe apenas os próprios dados.
            linha_resultado = p.montar_evento(
                p.EVT_RESULT,
                sala.numero_pergunta,
                correta,
                resultado.letra,
                resultado.pontos,
                resultado.total,
            )
            self._enviar(self.nomes[nome], linha_resultado, *linhas_ranking)

    def _avancar(self, sala: Sala) -> None:
        """Depois da pausa: próxima pergunta ou fim da partida (EVT END)."""
        if self.quiz.avancar(sala) is not None:
            self._enviar_pergunta(sala)
            return
        _, vencedor, pontos = sala.ranking()[0]
        for nome in sala.conectados:
            self.nomes[nome].estado = p.NA_SALA  # a sala voltou ao lobby
        self._enviar_a_sala(sala, p.montar_evento(p.EVT_END, vencedor, pontos))

    def _vigiar_salas(self) -> None:
        """Thread do tempo: fecha perguntas vencidas e avança depois das pausas."""
        while not self.parando.wait(INTERVALO_VIGIA):
            with self.trava:
                for sala in self.quiz.salas_com_prazo_vencido():
                    if sala.estado == p.PERGUNTA:
                        self._fechar_pergunta(sala)
                    else:
                        self._avancar(sala)

    # ------------------------------------------------------------------
    # Envio
    # ------------------------------------------------------------------

    def _enviar(self, cliente: Cliente, *linhas: str) -> None:
        """Envia uma ou mais linhas a um cliente, em um único sendall.

        Se o envio falhar, o cliente é tratado como desconectado, e quem
        chamou segue normalmente: uma falha com um cliente não pode
        interromper o envio aos demais nem derrubar o servidor.
        """
        for linha in linhas:
            self.registrar(f"{cliente.identificacao()} >> {linha}")
        dados = b"".join(p.codificar_linha(linha) for linha in linhas)
        try:
            cliente.sock.sendall(dados)
        except OSError as erro:
            self.registrar(f"{cliente.identificacao()} falha no envio: {erro}")
            self._derrubar(cliente)

    def _enviar_a_sala(self, sala: Sala, *linhas: str, exceto: Cliente | None = None) -> None:
        """Envia as linhas a todos os jogadores conectados da sala."""
        for nome in list(sala.conectados):
            cliente = self.nomes[nome]
            if cliente is not exceto:
                self._enviar(cliente, *linhas)

    def _derrubar(self, cliente: Cliente) -> None:
        """Encerra a conexão de um cliente a partir de outra thread.

        O shutdown faz a leitura na thread desse cliente terminar; é ela que
        faz a limpeza (_desconectar) e fecha o socket.
        """
        try:
            cliente.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            # A conexão já tinha sido encerrada pelo outro lado: nada a fazer.
            return


# ---------------------------------------------------------------------------
# Linha de comando
# ---------------------------------------------------------------------------


def ler_argumentos() -> argparse.Namespace:
    """Lê as opções de linha de comando do servidor."""
    analisador = argparse.ArgumentParser(description="Servidor do Quiz TCP (protocolo QUIZ/1.0)")
    analisador.add_argument("--host", default="0.0.0.0", help="endereço em que o servidor escuta")
    analisador.add_argument("--porta", type=int, default=p.PORTA_PADRAO, help="porta TCP do servidor")
    analisador.add_argument("--tempo", type=int, default=p.TEMPO_PERGUNTA, help="segundos por pergunta")
    analisador.add_argument("--rodadas", type=int, default=p.RODADAS_PADRAO, help="perguntas por partida")
    analisador.add_argument(
        "--perguntas",
        default=str(Path(__file__).resolve().parent / "perguntas.json"),
        help="arquivo de perguntas",
    )
    return analisador.parse_args()


def main() -> int:
    """Ponto de entrada do servidor."""
    # Evita erro ao imprimir acentos em terminais com codificação limitada.
    sys.stdout.reconfigure(errors="replace")
    argumentos = ler_argumentos()
    if argumentos.tempo < 1:
        print("Erro: --tempo deve ser de pelo menos 1 segundo.")
        return 1
    try:
        perguntas = carregar_perguntas(argumentos.perguntas)
        servidor = Servidor(
            argumentos.host, argumentos.porta, perguntas, argumentos.tempo, argumentos.rodadas
        )
    except (OSError, ValueError) as erro:
        print(f"Erro ao carregar as perguntas: {erro}")
        return 1
    try:
        servidor.iniciar()
    except OSError as erro:
        print(f"Erro ao abrir a porta {argumentos.porta}: {erro}")
        print("Confira se outro servidor já está usando essa porta ou escolha outra com --porta.")
        return 1
    print("Pressione Ctrl+C para desligar.")
    try:
        servidor.servir()
    except KeyboardInterrupt:
        print()
    finally:
        servidor.parar()
    return 0


if __name__ == "__main__":
    sys.exit(main())
