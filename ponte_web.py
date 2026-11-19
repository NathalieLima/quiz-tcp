"""Ponte local entre o navegador e a conexão TCP do cliente.

Por que isto existe: um navegador não consegue abrir um socket TCP puro (ele
só fala HTTP e WebSocket, que já são protocolos de aplicação prontos). Então o
navegador NÃO se conecta ao servidor do quiz. Ele conversa apenas com o
cliente.py da mesma máquina, por HTTP em 127.0.0.1, e serve só de tela:

    navegador  <-- HTTP + Server-Sent Events -->  cliente.py  <-- TCP, QUIZ/1.0 -->  servidor.py

Rotas (seção 10 do README):
    GET  /, /app.js, /estilo.css   arquivos da página
    GET  /eventos                  fluxo SSE com cada linha que passa pelo socket TCP
    POST /comando                  recebe {"linha": "..."} e envia a linha pelo socket TCP

Esta interface não faz parte do protocolo QUIZ/1.0.
"""

from __future__ import annotations

import json
import queue
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING

import protocolo as p

if TYPE_CHECKING:
    from cliente import Conexao

ENDERECO_LOCAL = "127.0.0.1"  # a ponte nunca atende outras máquinas
PASTA_WEB = Path(__file__).resolve().parent / "web"
TAMANHO_HISTORICO = 1000   # eventos guardados para reconstruir a tela de uma aba nova
LIMITE_CORPO = 4096        # tamanho máximo do corpo aceito em POST /comando
INTERVALO_SSE = 15.0       # segundos entre os comentários que mantêm o fluxo SSE vivo
TENTATIVAS_DE_PORTA = 20   # quantas portas seguidas tentar se a pedida estiver ocupada

# Únicos arquivos que a ponte serve: rota -> (arquivo em web/, tipo de conteúdo).
# Como a lista é fixa, não há como pedir um arquivo de fora da pasta web/.
ARQUIVOS: dict[str, tuple[str, str]] = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/estilo.css": ("estilo.css", "text/css; charset=utf-8"),
}

# Linhas do keepalive: aparecem ao vivo no painel, mas não entram no histórico.
SEM_HISTORICO = (p.CMD_PING, p.RESP_PONG)


def extrair_linha(corpo: bytes) -> str:
    """Valida o corpo de POST /comando e devolve a linha a enviar ao servidor.

    Levanta ValueError com a explicação se o corpo não for aceitável.
    """
    try:
        dados = json.loads(corpo)
    except ValueError as erro:  # JSON inválido ou bytes que não são UTF-8
        raise ValueError("o corpo deve ser um JSON válido") from erro
    linha = dados.get("linha") if isinstance(dados, dict) else None
    if not isinstance(linha, str) or not linha:
        raise ValueError('o JSON deve ter o campo "linha" com um texto não vazio')
    if "\n" in linha or "\r" in linha:
        raise ValueError("a linha não pode conter quebra de linha")
    if len(linha.encode(p.CODIFICACAO)) > p.TAMANHO_MAXIMO_LINHA:
        raise ValueError(f"a linha passa de {p.TAMANHO_MAXIMO_LINHA} bytes")
    return linha


class ServidorHTTPLocal(ThreadingHTTPServer):
    """Servidor HTTP da ponte, com uma thread por requisição."""

    # Sem reaproveitar endereço: se a porta já tem dono (outro cliente.py na
    # mesma máquina), a abertura deve falhar para tentarmos a próxima porta.
    allow_reuse_address = False
    ponte: "Ponte"

    def handle_error(self, request: object, client_address: object) -> None:
        """Não mostra traceback quando o navegador fecha a conexão no meio."""
        if isinstance(sys.exc_info()[1], ConnectionError):
            return
        super().handle_error(request, client_address)


class Ponte:
    """Liga a conexão TCP do cliente às abas do navegador."""

    def __init__(self, conexao: "Conexao", porta_web: int = 8000) -> None:
        self.conexao = conexao
        self._trava = threading.Lock()
        self._historico: deque[str] = deque(maxlen=TAMANHO_HISTORICO)
        self._assinantes: list[queue.Queue[str | None]] = []
        self._servindo = False
        self._http = self._abrir_servidor_http(porta_web)
        self._http.ponte = self
        self.porta: int = self._http.server_address[1]
        conexao.adicionar_ouvinte(self.ao_trafegar)

    @staticmethod
    def _abrir_servidor_http(porta_web: int) -> ServidorHTTPLocal:
        """Abre o servidor HTTP na porta pedida ou na próxima que estiver livre."""
        ultimo_erro: OSError | None = None
        for porta in range(porta_web, porta_web + TENTATIVAS_DE_PORTA):
            try:
                return ServidorHTTPLocal((ENDERECO_LOCAL, porta), TratadorHTTP)
            except OSError as erro:
                ultimo_erro = erro  # porta ocupada: tenta a seguinte
        assert ultimo_erro is not None
        raise ultimo_erro

    # -- ciclo de vida ------------------------------------------------------

    def servir(self) -> None:
        """Atende requisições HTTP até parar() ser chamado."""
        self._servindo = True
        try:
            self._http.serve_forever(poll_interval=0.1)
        finally:
            self._servindo = False
            self._http.server_close()

    def iniciar_em_thread(self) -> threading.Thread:
        """Atende em uma thread separada (usado nos testes)."""
        self._servindo = True
        thread = threading.Thread(target=self.servir, name="ponte-web", daemon=True)
        thread.start()
        return thread

    def parar(self) -> None:
        """Encerra os fluxos de eventos abertos e para o servidor HTTP."""
        with self._trava:
            for fila in self._assinantes:
                fila.put(None)  # None avisa o fluxo SSE de que deve terminar
        if self._servindo:
            self._http.shutdown()

    # -- eventos ------------------------------------------------------------

    def ao_trafegar(self, direcao: str, linha: str) -> None:
        """Ouvinte da conexão TCP: publica cada linha enviada ou recebida."""
        if direcao == "perdida":
            self.publicar({"tipo": "CONEXAO", "nome": "PERDIDA"})
            return
        mensagem = p.interpretar_linha(linha)
        self.publicar(
            {
                "direcao": direcao,
                "bruta": linha,
                "tipo": mensagem.tipo,
                "nome": mensagem.nome,
                "campos": mensagem.campos,
            },
            guardar=linha not in SEM_HISTORICO,
        )

    def publicar(self, evento: dict[str, object], guardar: bool = True) -> None:
        """Entrega um evento a todas as abas abertas e, se pedido, ao histórico."""
        evento["hora"] = int(time.time() * 1000)  # milissegundos, como o Date.now() do JS
        texto = json.dumps(evento, ensure_ascii=False)
        with self._trava:
            if guardar:
                self._historico.append(texto)
            for fila in self._assinantes:
                fila.put(texto)

    def assinar(self) -> tuple[queue.Queue[str | None], list[str]]:
        """Registra uma aba. Devolve a fila dela e o histórico recente.

        As duas coisas acontecem com o lock tomado para que nenhum evento se
        perca nem se repita entre o histórico e a fila.
        """
        fila: queue.Queue[str | None] = queue.Queue()
        with self._trava:
            self._assinantes.append(fila)
            return fila, list(self._historico)

    def cancelar(self, fila: queue.Queue[str | None]) -> None:
        """Remove uma aba que fechou o fluxo de eventos."""
        with self._trava:
            if fila in self._assinantes:
                self._assinantes.remove(fila)


class TratadorHTTP(BaseHTTPRequestHandler):
    """Atende cada requisição HTTP do navegador."""

    server: ServidorHTTPLocal

    def log_message(self, format: str, *args: object) -> None:
        """Silencia o log padrão do http.server (o terminal é do jogo)."""

    def do_GET(self) -> None:
        """Arquivos da página e fluxo de eventos."""
        caminho = self.path.split("?", 1)[0]
        if caminho == "/eventos":
            self._transmitir_eventos()
        elif caminho in ARQUIVOS:
            self._enviar_arquivo(*ARQUIVOS[caminho])
        else:
            self._responder_json(404, {"erro": "rota não encontrada"})

    def do_POST(self) -> None:
        """POST /comando: repassa uma linha do protocolo ao socket TCP."""
        if self.path.split("?", 1)[0] != "/comando":
            self._responder_json(404, {"erro": "rota não encontrada"})
            return
        # Exigir JSON impede que outra página aberta no navegador dispare
        # comandos nesta ponte com um formulário comum.
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            self._responder_json(415, {"erro": "use Content-Type: application/json"})
            return
        try:
            tamanho = int(self.headers.get("Content-Length", ""))
            if not 0 < tamanho <= LIMITE_CORPO:
                raise ValueError("corpo ausente ou grande demais")
            linha = extrair_linha(self.rfile.read(tamanho))
        except ValueError as erro:
            self._responder_json(400, {"erro": str(erro)})
            return
        if self.server.ponte.conexao.enviar(linha):
            self._responder_json(200, {"ok": True})
        else:
            self._responder_json(503, {"erro": "sem conexão com o servidor do quiz"})

    # -- respostas ----------------------------------------------------------

    def _responder_json(self, status: int, dados: dict[str, object]) -> None:
        """Envia uma resposta JSON curta."""
        self._responder(status, "application/json; charset=utf-8", json.dumps(dados).encode("utf-8"))

    def _enviar_arquivo(self, nome: str, tipo: str) -> None:
        """Envia um dos arquivos fixos da pasta web/."""
        try:
            conteudo = (PASTA_WEB / nome).read_bytes()
        except OSError:
            self._responder_json(404, {"erro": f"arquivo web/{nome} não encontrado"})
            return
        self._responder(200, tipo, conteudo)

    def _responder(self, status: int, tipo: str, corpo: bytes) -> None:
        """Envia uma resposta HTTP completa."""
        self.send_response(status)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corpo)

    def _transmitir_eventos(self) -> None:
        """GET /eventos: mantém a resposta aberta e escreve um evento por vez.

        Server-Sent Events é HTTP comum: a resposta não termina, e cada
        evento é um bloco "data: <json>" seguido de uma linha em branco.
        """
        ponte = self.server.ponte
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        fila, historico = ponte.assinar()
        try:
            # Primeiro o histórico, para a aba reconstruir a tela atual.
            for texto in historico:
                self._escrever_evento(texto)
            while True:
                try:
                    texto = fila.get(timeout=INTERVALO_SSE)
                except queue.Empty:
                    # Comentário SSE: mantém o fluxo vivo e revela aba fechada.
                    self.wfile.write(b": ativo\n\n")
                    self.wfile.flush()
                    continue
                if texto is None:
                    break  # a ponte está parando
                self._escrever_evento(texto)
        except OSError:
            # A aba foi fechada ou recarregada: o fluxo dela simplesmente termina.
            return
        finally:
            ponte.cancelar(fila)

    def _escrever_evento(self, texto: str) -> None:
        """Escreve um evento no formato Server-Sent Events."""
        self.wfile.write(f"data: {texto}\n\n".encode("utf-8"))
        self.wfile.flush()
