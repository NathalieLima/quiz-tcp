"""Programa cliente do Quiz TCP.

Abre a conexão TCP com o servidor e fala o protocolo QUIZ/1.0. A tela do
jogador pode ser:
  * o navegador (padrão): este programa serve a página em 127.0.0.1 e repassa
    cada comando da página para o socket TCP (ver ponte_web.py);
  * o terminal (--terminal): comandos digitados, sem navegador.
Nos dois casos a conexão com o servidor é a mesma e usa o mesmo protocolo.

Uso: python cliente.py --host 127.0.0.1 --porta 5000
"""

from __future__ import annotations

import argparse
import queue
import socket
import sys
import threading
import webbrowser
from typing import Callable

import protocolo as p

TAMANHO_RECV = 4096
ESPERA_RESPOSTA = 5.0   # segundos que o modo terminal espera a resposta do LOGIN
ESPERA_ANTES_DE_SAIR = 1.5  # tempo para a página receber o aviso de conexão perdida

# Direções informadas aos ouvintes da conexão
ENVIADA = "enviada"
RECEBIDA = "recebida"
PERDIDA = "perdida"

# Um ouvinte recebe (direção, linha). Na direção PERDIDA a linha vem vazia.
Ouvinte = Callable[[str, str], None]


class Conexao:
    """Conexão TCP com o servidor: envia linhas, lê linhas e mantém o keepalive."""

    def __init__(self, host: str, porta: int, intervalo_ping: float = p.INTERVALO_PING) -> None:
        self.host = host
        self.porta = porta
        self.intervalo_ping = intervalo_ping
        self.ativa = False
        self._sock: socket.socket | None = None
        self._leitor = p.LeitorDeLinhas()
        self._ouvintes: list[Ouvinte] = []
        # O envio pode partir de várias threads (página, terminal, keepalive);
        # este lock impede que duas linhas se misturem no socket.
        self._trava_envio = threading.Lock()
        self._trava_estado = threading.Lock()
        self._parar_ping = threading.Event()

    def adicionar_ouvinte(self, ouvinte: Ouvinte) -> None:
        """Registra uma função avisada de cada linha enviada ou recebida."""
        self._ouvintes.append(ouvinte)

    def conectar(self) -> None:
        """Abre a conexão TCP com o servidor (levanta OSError se não conseguir)."""
        # AF_INET = IPv4; SOCK_STREAM = TCP. O connect faz o handshake de 3 vias.
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect((self.host, self.porta))
        except OSError:
            sock.close()
            raise
        self._sock = sock
        self.ativa = True

    def iniciar(self) -> None:
        """Inicia a thread que lê o socket e a thread do keepalive."""
        threading.Thread(target=self._ler_socket, name="leitura", daemon=True).start()
        threading.Thread(target=self._manter_keepalive, name="keepalive", daemon=True).start()

    def enviar(self, linha: str) -> bool:
        """Envia uma linha do protocolo. Devolve False se a conexão caiu."""
        if not self.ativa or self._sock is None:
            return False
        try:
            with self._trava_envio:
                self._sock.sendall(p.codificar_linha(linha))
        except OSError:
            self._marcar_queda()
            return False
        self._avisar(ENVIADA, linha)
        return True

    def fechar(self) -> None:
        """Fecha a conexão por decisão do próprio cliente (sem aviso de queda)."""
        with self._trava_estado:
            estava_ativa = self.ativa
            self.ativa = False
        self._parar_ping.set()
        if estava_ativa and self._sock is not None:
            try:
                # shutdown acorda a thread de leitura, que pode estar parada no recv.
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                # O servidor já tinha encerrado a conexão: basta fechar o socket.
                pass
            self._sock.close()

    # -- threads internas ---------------------------------------------------

    def _ler_socket(self) -> None:
        """Lê o socket até a conexão terminar, repassando cada linha completa."""
        assert self._sock is not None
        try:
            while True:
                dados = self._sock.recv(TAMANHO_RECV)
                if not dados:
                    break  # recv vazio: o servidor fechou a conexão
                self._leitor.alimentar(dados)
                while (bruta := self._leitor.proxima_linha()) is not None:
                    self._avisar(RECEBIDA, bruta.decode(p.CODIFICACAO, errors="replace"))
        except (OSError, p.LinhaMuitoLonga):
            # OSError: a conexão foi derrubada (servidor caiu, rede caiu) ou
            # fechada por fechar(). Linha longa demais: o servidor não está
            # seguindo o protocolo. Nos dois casos a conexão acabou.
            pass
        self._marcar_queda()

    def _manter_keepalive(self) -> None:
        """Envia PING de tempos em tempos para o servidor saber que estamos vivos."""
        while not self._parar_ping.wait(self.intervalo_ping):
            if not self.enviar(p.CMD_PING):
                break

    def _marcar_queda(self) -> None:
        """Registra a perda da conexão e avisa os ouvintes, uma única vez."""
        with self._trava_estado:
            if not self.ativa:
                return
            self.ativa = False
        self._parar_ping.set()
        if self._sock is not None:
            self._sock.close()
        self._avisar(PERDIDA, "")

    def _avisar(self, direcao: str, linha: str) -> None:
        """Chama todos os ouvintes registrados."""
        for ouvinte in self._ouvintes:
            ouvinte(direcao, linha)


# ---------------------------------------------------------------------------
# Textos em português para o modo terminal
# ---------------------------------------------------------------------------

MENSAGENS_ERRO: dict[str, str] = {
    p.ERRO_COMANDO_DESCONHECIDO: "O servidor não conhece esse comando.",
    p.ERRO_ARGUMENTOS_INVALIDOS: "Comando com argumentos faltando, sobrando ou inválidos.",
    p.ERRO_ESTADO_INVALIDO: "Esse comando não pode ser usado agora.",
    p.ERRO_LINHA_MUITO_LONGA: "Linha longa demais. O servidor encerrou a conexão.",
    p.ERRO_NOME_INVALIDO: "Nome inválido. Use de 3 a 16 letras, dígitos ou _ (sem espaços nem acentos).",
    p.ERRO_NOME_EM_USO: "Esse nome já está em uso. Escolha outro.",
    p.ERRO_SALA_INEXISTENTE: "Sala não encontrada. Confira o código.",
    p.ERRO_JOGO_EM_ANDAMENTO: "Essa sala está com uma partida em andamento. Tente de novo quando terminar.",
    p.ERRO_SALA_CHEIA: "A sala está cheia (máximo de 10 jogadores).",
    p.ERRO_NAO_E_HOST: "Só o anfitrião pode iniciar a partida.",
    p.ERRO_JOGADORES_INSUFICIENTES: "São necessários pelo menos 2 jogadores para iniciar.",
    p.ERRO_SEM_PERGUNTA_ABERTA: "Não há pergunta aberta para você responder agora.",
    p.ERRO_JA_RESPONDEU: "Você já respondeu esta pergunta.",
    p.ERRO_OPCAO_INVALIDA: "Opção inválida. Responda com A, B, C ou D.",
    p.ERRO_INATIVIDADE: "Conexão encerrada pelo servidor por inatividade.",
}

MOTIVOS_SAIDA: dict[str, str] = {
    p.MOTIVO_LEAVE: "saiu da sala",
    p.MOTIVO_QUIT: "encerrou a sessão",
    p.MOTIVO_QUEDA: "perdeu a conexão",
    p.MOTIVO_TIMEOUT: "foi desconectado por inatividade",
}

AJUDA = """Comandos:
  criar              cria uma sala (você vira o anfitrião)
  entrar <codigo>    entra em uma sala
  iniciar            inicia a partida (só o anfitrião)
  responder <letra>  responde a pergunta (ou digite só a letra: A, B, C ou D)
  sair               sai da sala
  encerrar           encerra a sessão e fecha o programa
  bruto <linha>      envia a linha exatamente como digitada (para testar o protocolo)
  ajuda              mostra esta lista"""


def descrever_resposta(mensagem: p.Mensagem) -> str | None:
    """Texto em português para uma resposta do servidor (OK, ERR, BYE)."""
    campos = mensagem.campos
    if mensagem.tipo == p.RESP_ERR:
        texto = MENSAGENS_ERRO.get(mensagem.nome, "Erro desconhecido.")
        return f"Erro {mensagem.nome}: {texto}"
    if mensagem.tipo == p.RESP_BYE:
        return "Sessão encerrada. Até a próxima!"
    if mensagem.tipo != p.RESP_OK:
        return None  # PONG não é mostrado
    if mensagem.nome == p.CMD_LOGIN:
        return f"Olá, {campos[0]}! Digite 'criar' para abrir uma sala ou 'entrar <codigo>'."
    if mensagem.nome == p.CMD_CREATE:
        return f"Sala criada. Código: {campos[0]}. Passe o código aos outros jogadores e digite 'iniciar'."
    if mensagem.nome == p.CMD_JOIN:
        jogadores = campos[3].replace(",", ", ")
        if campos[1] == p.JOIN_JOGO:
            return (
                f"Você voltou à partida da sala {campos[0]} com a pontuação preservada. "
                "Aguarde a próxima pergunta."
            )
        return f"Você entrou na sala {campos[0]}. Anfitrião: {campos[2]}. Jogadores: {jogadores}."
    if mensagem.nome == p.CMD_START:
        return "Partida iniciada."
    if mensagem.nome == p.CMD_ANSWER:
        return f"Resposta {campos[0]} registrada. Aguarde o resultado."
    if mensagem.nome == p.CMD_LEAVE:
        return "Você saiu da sala."
    return None


def descrever_evento(mensagem: p.Mensagem) -> str | None:
    """Texto em português para um evento do servidor (EVT ...)."""
    nome, campos = mensagem.nome, mensagem.campos
    if nome == p.EVT_JOINED:
        return f"* {campos[0]} entrou na sala."
    if nome == p.EVT_LEFT:
        return f"* {campos[0]} {MOTIVOS_SAIDA.get(campos[1], 'saiu')}."
    if nome == p.EVT_HOST:
        return f"* {campos[0]} agora é o anfitrião."
    if nome == p.EVT_START:
        return f"\nA partida começou! Serão {campos[0]} perguntas."
    if nome == p.EVT_QUESTION:
        return f"\nPergunta {campos[0]} de {campos[1]} ({campos[2]} s para responder):\n  {campos[3]}"
    if nome == p.EVT_OPTION:
        return f"    {campos[0]}) {campos[1]}"
    if nome == p.EVT_ANSWERED:
        return f"  [{campos[0]} de {campos[1]} responderam]"
    if nome == p.EVT_RESULT:
        return descrever_resultado(campos)
    if nome == p.EVT_RANKING:
        return "Ranking:"
    if nome == p.EVT_RANK:
        return f"  {campos[0]}º {campos[1]}: {campos[2]} pontos"
    if nome == p.EVT_END:
        return f"\nFim da partida! Vencedor: {campos[0]}, com {campos[1]} pontos.\nA sala voltou ao lobby."
    if nome == p.EVT_SHUTDOWN:
        return "\nO servidor está sendo desligado."
    return None


def descrever_resultado(campos: list[str]) -> str:
    """Texto do EVT RESULT: <n> <correta> <sua_resposta> <pontos> <total>."""
    _, correta, minha, pontos, total = campos
    if minha == p.SEM_RESPOSTA:
        situacao = "Você não respondeu."
    elif minha == correta:
        situacao = f"Você respondeu {minha}: CERTO!"
    else:
        situacao = f"Você respondeu {minha}: ERRADO."
    return f"\nResposta correta: {correta}. {situacao} +{pontos} pontos (total: {total})."


def traduzir_comando(texto: str) -> str | None:
    """Traduz um comando amigável do terminal para uma linha do protocolo.

    Devolve None se o comando não for reconhecido.
    """
    palavras = texto.split()
    comando = palavras[0].lower()
    argumentos = palavras[1:]
    if comando == "bruto":
        return texto.split(" ", 1)[1] if " " in texto else None
    if comando == "criar" and not argumentos:
        return p.CMD_CREATE
    if comando == "entrar" and len(argumentos) == 1:
        return p.montar_requisicao(p.CMD_JOIN, argumentos[0].upper())
    if comando == "iniciar" and not argumentos:
        return p.CMD_START
    if comando == "responder" and len(argumentos) == 1:
        return p.montar_requisicao(p.CMD_ANSWER, argumentos[0].upper())
    if len(palavras) == 1 and comando.upper() in p.LETRAS:
        return p.montar_requisicao(p.CMD_ANSWER, comando.upper())
    if comando == "sair" and not argumentos:
        return p.CMD_LEAVE
    if comando == "encerrar" and not argumentos:
        return p.CMD_QUIT
    return None


# ---------------------------------------------------------------------------
# Modo terminal
# ---------------------------------------------------------------------------


class Terminal:
    """Joga pelo terminal, traduzindo comandos amigáveis para o protocolo."""

    def __init__(self, conexao: Conexao, debug: bool) -> None:
        self.conexao = conexao
        self.debug = debug
        self.autenticado = False
        self.encerrado = threading.Event()
        self._respostas_login: queue.Queue[p.Mensagem] = queue.Queue()
        conexao.adicionar_ouvinte(self.ao_trafegar)

    def ao_trafegar(self, direcao: str, linha: str) -> None:
        """Chamado pela conexão a cada linha enviada ou recebida."""
        if direcao == PERDIDA:
            if not self.encerrado.is_set():
                print("\nA conexão com o servidor foi perdida. Pressione Enter para sair.")
            self.encerrado.set()
            return
        if self.debug:
            print(f"{'>>' if direcao == ENVIADA else '<<'} {linha}")
        if direcao == RECEBIDA:
            self._mostrar(p.interpretar_linha(linha))

    def _mostrar(self, mensagem: p.Mensagem) -> None:
        """Mostra ao jogador, em português, uma mensagem vinda do servidor."""
        if mensagem.tipo == p.PREFIXO_EVENTO:
            texto = descrever_evento(mensagem)
        else:
            texto = descrever_resposta(mensagem)
            if not self.autenticado and mensagem.tipo in (p.RESP_OK, p.RESP_ERR):
                self._respostas_login.put(mensagem)
        if texto:
            print(texto)
        if mensagem.tipo == p.RESP_BYE:
            self.encerrado.set()

    def executar(self) -> None:
        """Pede o nome, faz o LOGIN e depois lê comandos até o jogador encerrar."""
        try:
            self._fazer_login()
            if not self.encerrado.is_set():
                print(AJUDA)
            while not self.encerrado.is_set():
                texto = input().strip()
                if self.encerrado.is_set():
                    break
                if texto:
                    self._executar_comando(texto)
        except (KeyboardInterrupt, EOFError):
            print("\nSaindo.")

    def _fazer_login(self) -> None:
        """Repete o pedido de nome até o servidor aceitar o LOGIN."""
        while not self.autenticado and not self.encerrado.is_set():
            nome = input("Seu nome (3 a 16 letras, dígitos ou _): ").strip()
            if not nome or self.encerrado.is_set():
                continue
            self.conexao.enviar(p.montar_requisicao(p.CMD_LOGIN, nome))
            try:
                resposta = self._respostas_login.get(timeout=ESPERA_RESPOSTA)
            except queue.Empty:
                print("O servidor não respondeu ao LOGIN.")
                continue
            self.autenticado = resposta.tipo == p.RESP_OK

    def _executar_comando(self, texto: str) -> None:
        """Traduz e envia um comando digitado pelo jogador."""
        if texto.lower() == "ajuda":
            print(AJUDA)
            return
        linha = traduzir_comando(texto)
        if linha is None:
            print("Comando não reconhecido. Digite 'ajuda' para ver a lista.")
        else:
            self.conexao.enviar(linha)
            if linha == p.CMD_QUIT:
                # Espera o BYE do servidor para sair sem pedir mais um Enter.
                self.encerrado.wait(ESPERA_RESPOSTA)


# ---------------------------------------------------------------------------
# Modo web
# ---------------------------------------------------------------------------


def executar_modo_web(conexao: Conexao, porta_web: int, debug: bool) -> int:
    """Inicia a ponte HTTP local, abre o navegador e fica servindo a página."""
    from ponte_web import Ponte  # importado aqui porque o modo terminal não precisa dele

    try:
        ponte = Ponte(conexao, porta_web)
    except OSError as erro:
        print(f"Não foi possível abrir a interface web: {erro}")
        return 1

    def ao_trafegar(direcao: str, linha: str) -> None:
        if direcao == PERDIDA:
            print("\nA conexão com o servidor foi perdida. Encerrando o cliente.")
            # Dá um instante para a página receber o aviso antes de a ponte parar.
            threading.Timer(ESPERA_ANTES_DE_SAIR, ponte.parar).start()
        elif debug:
            print(f"{'>>' if direcao == ENVIADA else '<<'} {linha}")

    conexao.adicionar_ouvinte(ao_trafegar)
    conexao.iniciar()
    endereco = f"http://127.0.0.1:{ponte.porta}"
    if ponte.porta != porta_web:
        print(f"A porta {porta_web} está ocupada; usando a porta {ponte.porta}.")
    print(f"Interface do jogo: {endereco}")
    print("Se o navegador não abrir sozinho, copie o endereço acima. Ctrl+C fecha o cliente.")
    webbrowser.open(endereco)
    try:
        ponte.servir()
    except KeyboardInterrupt:
        print("\nSaindo.")
    finally:
        conexao.fechar()
        ponte.parar()
    return 0


# ---------------------------------------------------------------------------
# Linha de comando
# ---------------------------------------------------------------------------


def ler_argumentos() -> argparse.Namespace:
    """Lê as opções de linha de comando do cliente."""
    analisador = argparse.ArgumentParser(description="Cliente do Quiz TCP (protocolo QUIZ/1.0)")
    analisador.add_argument("--host", default="127.0.0.1", help="endereço do servidor")
    analisador.add_argument("--porta", type=int, default=p.PORTA_PADRAO, help="porta TCP do servidor")
    analisador.add_argument("--porta-web", type=int, default=8000, help="porta local da interface web")
    analisador.add_argument("--terminal", action="store_true", help="joga pelo terminal, sem navegador")
    analisador.add_argument("--debug", action="store_true", help="mostra as linhas brutas do protocolo")
    return analisador.parse_args()


def main() -> int:
    """Ponto de entrada do cliente."""
    # Evita erro ao imprimir acentos em terminais com codificação limitada.
    sys.stdout.reconfigure(errors="replace")
    argumentos = ler_argumentos()
    conexao = Conexao(argumentos.host, argumentos.porta)
    try:
        conexao.conectar()
    except OSError as erro:
        print(f"Não foi possível conectar a {argumentos.host}:{argumentos.porta}: {erro}")
        print("Confira se o servidor está rodando e se o endereço e a porta estão corretos.")
        return 1
    print(f"Conectado ao servidor {argumentos.host}:{argumentos.porta} por TCP.")
    if argumentos.terminal:
        terminal = Terminal(conexao, argumentos.debug)
        conexao.iniciar()
        terminal.executar()
        conexao.fechar()
        return 0
    return executar_modo_web(conexao, argumentos.porta_web, argumentos.debug)


if __name__ == "__main__":
    sys.exit(main())
