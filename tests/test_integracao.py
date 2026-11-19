"""Testes de integração: servidor real em uma porta livre e clientes com sockets reais.

Os tempos são curtos (pausa de 0,3 s) para a suíte terminar em poucos segundos.
"""

from __future__ import annotations

import os
import socket
import struct
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import protocolo as p
from quiz import Pergunta
from servidor import Servidor

ESPERA = 3.0  # segundos que um cliente de teste espera por uma linha

# Em todas as perguntas de teste a opção correta é a A.
PERGUNTAS = [Pergunta(f"Pergunta {n}?", ("certa", "errada 1", "errada 2", "errada 3"), "A") for n in range(1, 7)]


class ClienteDeTeste:
    """Cliente mínimo do protocolo, feito direto sobre um socket TCP."""

    def __init__(self, porta: int) -> None:
        self.sock = socket.create_connection(("127.0.0.1", porta), timeout=ESPERA)
        self.leitor = p.LeitorDeLinhas()

    def enviar(self, linha: str) -> None:
        self.sock.sendall(p.codificar_linha(linha))

    def ler(self) -> str | None:
        """Lê a próxima linha. Devolve None se o servidor fechou a conexão."""
        while True:
            bruta = self.leitor.proxima_linha()
            if bruta is not None:
                return bruta.decode("utf-8")
            try:
                dados = self.sock.recv(4096)
            except ConnectionError:
                return None
            if not dados:
                return None
            self.leitor.alimentar(dados)

    def esperar(self, prefixo: str) -> str:
        """Lê linhas até encontrar uma que comece com ``prefixo``."""
        while True:
            linha = self.ler()
            if linha is None:
                raise AssertionError(f"conexão fechada enquanto esperava {prefixo!r}")
            if linha.startswith(prefixo):
                return linha

    def pedir(self, linha: str) -> str:
        """Envia uma requisição e devolve a resposta (pulando os eventos)."""
        self.enviar(linha)
        while True:
            resposta = self.ler()
            if resposta is None:
                raise AssertionError(f"conexão fechada sem resposta a {linha!r}")
            if not resposta.startswith("EVT "):
                return resposta

    def derrubar(self) -> None:
        """Queda abrupta: fecha com RST, como um processo morto ou cabo solto."""
        formato = "HH" if os.name == "nt" else "ii"
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack(formato, 1, 0))
        self.sock.close()

    def fechar(self) -> None:
        self.sock.close()


class BaseIntegracao(unittest.TestCase):
    """Sobe um servidor por teste, em uma porta livre escolhida pelo sistema."""

    TEMPO = 5.0
    PAUSA = 0.3
    RODADAS = 2
    INATIVIDADE = 60.0

    def setUp(self) -> None:
        self.servidor = Servidor(
            "127.0.0.1", 0, PERGUNTAS,
            tempo=self.TEMPO, rodadas=self.RODADAS, pausa=self.PAUSA,
            inatividade=self.INATIVIDADE, registrar=lambda texto: None,
        )
        self.servidor.iniciar()
        threading.Thread(target=self.servidor.servir, daemon=True).start()
        self.clientes: list[ClienteDeTeste] = []

    def tearDown(self) -> None:
        for cliente in self.clientes:
            cliente.fechar()
        self.servidor.parar()

    def conectar(self, nome: str | None = None) -> ClienteDeTeste:
        """Abre uma conexão e, se um nome for dado, faz o LOGIN."""
        cliente = ClienteDeTeste(self.servidor.porta)
        self.clientes.append(cliente)
        if nome is not None:
            self.assertEqual(cliente.pedir(f"LOGIN {nome}"), f"OK LOGIN {nome}")
        return cliente

    def abrir_sala(self, *nomes: str) -> tuple[str, list[ClienteDeTeste]]:
        """O primeiro nome cria a sala e os demais entram. Devolve código e clientes."""
        anfitriao = self.conectar(nomes[0])
        codigo = anfitriao.pedir("CREATE").split(" ")[2]
        clientes = [anfitriao]
        for nome in nomes[1:]:
            cliente = self.conectar(nome)
            self.assertTrue(cliente.pedir(f"JOIN {codigo}").startswith(f"OK JOIN {codigo} LOBBY"))
            clientes.append(cliente)
        return codigo, clientes

    def iniciar_partida(self, *nomes: str) -> tuple[str, list[ClienteDeTeste]]:
        """Abre uma sala, inicia a partida e espera a primeira pergunta em todos."""
        codigo, clientes = self.abrir_sala(*nomes)
        self.assertEqual(clientes[0].pedir("START"), "OK START")
        for cliente in clientes:
            cliente.esperar("EVT QUESTION 1 ")
        return codigo, clientes


class TestePartidaCompleta(BaseIntegracao):
    """Uma partida inteira com dois clientes, conferindo cada mensagem."""

    def test_partida_com_dois_clientes(self) -> None:
        codigo, (ana, bia) = self.abrir_sala("ana", "bia")
        self.assertTrue(p.codigo_valido(codigo))
        self.assertEqual(ana.esperar("EVT JOINED"), "EVT JOINED bia")
        inicio = time.monotonic()
        self.assertEqual(ana.pedir("START"), "OK START")
        self.assertEqual(ana.esperar("EVT START"), "EVT START 2")
        self.assertEqual(bia.esperar("EVT START"), "EVT START 2")
        total_ana = 0
        for numero in (1, 2):
            for cliente in (ana, bia):
                pergunta = cliente.esperar("EVT QUESTION")
                self.assertTrue(pergunta.startswith(f"EVT QUESTION {numero} 2 5 Pergunta "), pergunta)
                # As 4 opções vêm logo depois da pergunta, em ordem, sem nada no meio.
                opcoes = [cliente.ler() for _ in range(4)]
                self.assertEqual(
                    opcoes,
                    ["EVT OPTION A certa", "EVT OPTION B errada 1", "EVT OPTION C errada 2", "EVT OPTION D errada 3"],
                )
            self.assertEqual(ana.pedir("ANSWER A"), "OK ANSWER A")
            self.assertEqual(bia.esperar("EVT ANSWERED"), "EVT ANSWERED 1 2")
            self.assertEqual(bia.pedir("ANSWER B"), "OK ANSWER B")
            # EVT RESULT é individual: cada um recebe os próprios dados.
            _, _, n, correta, minha, pontos, total = ana.esperar("EVT RESULT").split(" ")
            self.assertEqual((n, correta, minha), (str(numero), "A", "A"))
            self.assertTrue(500 <= int(pontos) <= 1000, pontos)
            total_ana += int(pontos)
            self.assertEqual(int(total), total_ana)
            self.assertEqual(bia.esperar("EVT RESULT"), f"EVT RESULT {numero} A B 0 0")
            # O ranking vem logo depois do resultado, com as linhas em ordem.
            for cliente in (ana, bia):
                self.assertEqual(cliente.ler(), "EVT RANKING 2")
                self.assertEqual(cliente.ler(), f"EVT RANK 1 ana {total_ana}")
                self.assertEqual(cliente.ler(), "EVT RANK 2 bia 0")
        self.assertEqual(ana.ler(), f"EVT END ana {total_ana}")
        self.assertEqual(bia.ler(), f"EVT END ana {total_ana}")
        # Fechamento antecipado: a partida inteira durou menos que o prazo de uma pergunta.
        self.assertLess(time.monotonic() - inicio, self.TEMPO)
        # A sala voltou ao lobby: ANSWER não vale mais e uma nova partida pode começar.
        self.assertEqual(ana.pedir("ANSWER A"), "ERR 03 estado_invalido")
        self.assertEqual(bia.pedir("START"), "ERR 24 nao_e_host")
        self.assertEqual(ana.pedir("START"), "OK START")
        self.assertEqual(bia.esperar("EVT QUESTION").split(" ")[2], "1")

    def test_mensagens_partidas_e_agrupadas_no_socket(self) -> None:
        cliente = self.conectar()
        cliente.sock.sendall(b"LOG")
        time.sleep(0.05)
        cliente.sock.sendall(b"IN ana\nPI")
        time.sleep(0.05)
        cliente.sock.sendall(b"NG\nPING\r\n")
        self.assertEqual(cliente.ler(), "OK LOGIN ana")
        self.assertEqual(cliente.ler(), "PONG")
        self.assertEqual(cliente.ler(), "PONG")

    def test_leave_e_quit(self) -> None:
        codigo, (ana, bia, caio) = self.abrir_sala("ana", "bia", "caio")
        self.assertEqual(caio.pedir("LEAVE"), "OK LEAVE")
        self.assertEqual(ana.esperar("EVT LEFT"), "EVT LEFT caio leave")
        self.assertTrue(caio.pedir("CREATE").startswith("OK CREATE "))  # voltou a AUTENTICADO
        self.assertEqual(bia.pedir("QUIT"), "BYE")
        self.assertIsNone(bia.ler())  # o servidor fechou a conexão depois do BYE
        self.assertEqual(ana.esperar("EVT LEFT"), "EVT LEFT bia quit")

    def test_sala_vazia_e_apagada(self) -> None:
        codigo, (ana,) = self.abrir_sala("ana")
        self.assertEqual(ana.pedir("LEAVE"), "OK LEAVE")
        self.assertEqual(self.conectar("bia").pedir(f"JOIN {codigo}"), "ERR 21 sala_inexistente")

    def test_desligamento_do_servidor_avisa_os_clientes(self) -> None:
        ana = self.conectar("ana")
        self.servidor.parar()
        self.assertEqual(ana.ler(), "EVT SHUTDOWN")
        self.assertIsNone(ana.ler())


class TestePrazo(BaseIntegracao):
    """Pergunta que fecha pelo fim do prazo, sem todos responderem."""

    TEMPO = 0.4

    def test_quem_nao_responde_fica_sem_pontos(self) -> None:
        _, (ana, bia) = self.iniciar_partida("ana", "bia")
        self.assertEqual(ana.pedir("ANSWER A"), "OK ANSWER A")
        self.assertEqual(bia.esperar("EVT RESULT"), "EVT RESULT 1 A - 0 0")
        self.assertTrue(ana.esperar("EVT RESULT").startswith("EVT RESULT 1 A A "))
        ana.esperar("EVT QUESTION 2 ")  # depois da pausa, a partida segue


class TesteCodigosDeErro(BaseIntegracao):
    """Cada código de erro da seção 8 do README, provocado em um servidor real."""

    def test_01_comando_desconhecido(self) -> None:
        cliente = self.conectar()
        self.assertEqual(cliente.pedir("FOO"), "ERR 01 comando_desconhecido")
        self.assertEqual(cliente.pedir("login ana"), "ERR 01 comando_desconhecido")
        self.assertEqual(cliente.pedir(""), "ERR 01 comando_desconhecido")
        self.assertEqual(cliente.pedir("PING"), "PONG")  # a conexão é mantida

    def test_02_argumentos_invalidos(self) -> None:
        cliente = self.conectar()
        self.assertEqual(cliente.pedir("LOGIN"), "ERR 02 argumentos_invalidos")
        self.assertEqual(cliente.pedir("LOGIN ana maria"), "ERR 02 argumentos_invalidos")
        self.assertEqual(cliente.pedir("LOGIN  ana"), "ERR 02 argumentos_invalidos")
        self.assertEqual(cliente.pedir("PING agora"), "ERR 02 argumentos_invalidos")
        cliente.sock.sendall(b"LOGIN \xff\xfe\n")  # bytes que não são UTF-8 válido
        self.assertEqual(cliente.ler(), "ERR 02 argumentos_invalidos")
        self.assertEqual(cliente.pedir("LOGIN ana"), "OK LOGIN ana")  # a conexão é mantida

    def test_03_estado_invalido(self) -> None:
        cliente = self.conectar()
        for linha in ("CREATE", "JOIN ABCD", "START", "ANSWER A", "LEAVE"):
            self.assertEqual(cliente.pedir(linha), "ERR 03 estado_invalido", linha)  # CONECTADO
        cliente.pedir("LOGIN ana")
        for linha in ("LOGIN ana", "START", "ANSWER A", "LEAVE"):
            self.assertEqual(cliente.pedir(linha), "ERR 03 estado_invalido", linha)  # AUTENTICADO
        cliente.pedir("CREATE")
        for linha in ("LOGIN ana", "CREATE", "JOIN ABCD", "ANSWER A"):
            self.assertEqual(cliente.pedir(linha), "ERR 03 estado_invalido", linha)  # NA_SALA
        _, (bia, caio) = self.iniciar_partida("bia", "caio")
        for linha in ("LOGIN bia", "CREATE", "JOIN ABCD", "START"):
            self.assertEqual(bia.pedir(linha), "ERR 03 estado_invalido", linha)  # EM_JOGO

    def test_04_linha_muito_longa(self) -> None:
        cliente = self.conectar()
        cliente.sock.sendall(b"X" * (p.TAMANHO_MAXIMO_LINHA + 76))
        self.assertEqual(cliente.ler(), "ERR 04 linha_muito_longa")
        self.assertIsNone(cliente.ler())  # o servidor fecha a conexão

    def test_11_nome_invalido(self) -> None:
        cliente = self.conectar()
        for nome in ("ab", "a" * 17, "joão", "ana-1"):
            self.assertEqual(cliente.pedir(f"LOGIN {nome}"), "ERR 11 nome_invalido", nome)
        self.assertEqual(cliente.pedir("LOGIN ana_1"), "OK LOGIN ana_1")  # continua em CONECTADO

    def test_12_nome_em_uso(self) -> None:
        self.conectar("ana")
        outro = self.conectar()
        self.assertEqual(outro.pedir("LOGIN ana"), "ERR 12 nome_em_uso")
        self.assertEqual(outro.pedir("LOGIN bia"), "OK LOGIN bia")

    def test_21_sala_inexistente(self) -> None:
        cliente = self.conectar("ana")
        self.assertEqual(cliente.pedir("JOIN ZZZZ"), "ERR 21 sala_inexistente")
        self.assertEqual(cliente.pedir("JOIN abc"), "ERR 21 sala_inexistente")
        self.assertTrue(cliente.pedir("CREATE").startswith("OK CREATE "))  # continua em AUTENTICADO

    def test_22_jogo_em_andamento(self) -> None:
        codigo, _ = self.iniciar_partida("ana", "bia")
        self.assertEqual(self.conectar("caio").pedir(f"JOIN {codigo}"), "ERR 22 jogo_em_andamento")

    def test_23_sala_cheia(self) -> None:
        nomes = [f"jogador{n}" for n in range(1, p.MAX_JOGADORES + 1)]
        codigo, _ = self.abrir_sala(*nomes)
        self.assertEqual(self.conectar("atrasado").pedir(f"JOIN {codigo}"), "ERR 23 sala_cheia")

    def test_24_nao_e_host(self) -> None:
        _, (ana, bia) = self.abrir_sala("ana", "bia")
        self.assertEqual(bia.pedir("START"), "ERR 24 nao_e_host")

    def test_25_jogadores_insuficientes(self) -> None:
        _, (ana,) = self.abrir_sala("ana")
        self.assertEqual(ana.pedir("START"), "ERR 25 jogadores_insuficientes")

    def test_31_sem_pergunta_aberta(self) -> None:
        _, (ana, bia) = self.iniciar_partida("ana", "bia")
        ana.pedir("ANSWER A")
        bia.pedir("ANSWER A")
        ana.esperar("EVT RESULT")
        # A sala está em RESULTADO (pausa antes da próxima pergunta).
        self.assertEqual(ana.pedir("ANSWER A"), "ERR 31 sem_pergunta_aberta")

    def test_32_ja_respondeu(self) -> None:
        _, (ana, bia) = self.iniciar_partida("ana", "bia")
        self.assertEqual(ana.pedir("ANSWER B"), "OK ANSWER B")
        self.assertEqual(ana.pedir("ANSWER A"), "ERR 32 ja_respondeu")

    def test_33_opcao_invalida(self) -> None:
        _, (ana, bia) = self.iniciar_partida("ana", "bia")
        for letra in ("E", "a", "AB", "1"):
            self.assertEqual(ana.pedir(f"ANSWER {letra}"), "ERR 33 opcao_invalida", letra)
        self.assertEqual(ana.pedir("ANSWER A"), "OK ANSWER A")  # continua em EM_JOGO


class TesteInatividade(BaseIntegracao):
    """ERR 90: o servidor fecha a conexão de quem fica sem enviar nenhuma linha."""

    INATIVIDADE = 0.5

    def test_90_inatividade(self) -> None:
        _, (ana, bia) = self.abrir_sala("ana", "bia")
        self.assertEqual(ana.ler(), "EVT JOINED bia")
        # ana mantém o keepalive; bia fica em silêncio.
        for _ in range(5):
            time.sleep(0.15)
            ana.enviar("PING")
        self.assertEqual(bia.ler(), "ERR 90 inatividade")
        self.assertIsNone(bia.ler())  # conexão fechada pelo servidor
        # ana continuou conectada (5 PONG) e foi avisada da saída de bia.
        recebidas = [ana.ler() for _ in range(6)]
        self.assertEqual(recebidas.count("PONG"), 5)
        self.assertIn("EVT LEFT bia timeout", recebidas)


class TesteQuedaDeCliente(BaseIntegracao):
    """Seção 9 do README: queda abrupta, troca de anfitrião e retorno à partida."""

    RODADAS = 3

    def test_queda_abrupta_no_meio_da_partida(self) -> None:
        _, (ana, bia, caio) = self.iniciar_partida("ana", "bia", "caio")
        self.assertEqual(bia.pedir("ANSWER A"), "OK ANSWER A")
        bia.derrubar()
        # Os demais são avisados e a pergunta continua aberta para eles.
        self.assertEqual(ana.esperar("EVT LEFT"), "EVT LEFT bia disconnect")
        self.assertEqual(caio.esperar("EVT LEFT"), "EVT LEFT bia disconnect")
        self.assertEqual(ana.pedir("ANSWER B"), "OK ANSWER B")
        self.assertEqual(caio.esperar("EVT ANSWERED"), "EVT ANSWERED 1 2")
        # Com caio, todos os conectados responderam: fecha sem esperar o prazo.
        self.assertEqual(caio.pedir("ANSWER B"), "OK ANSWER B")
        self.assertEqual(ana.esperar("EVT RESULT"), "EVT RESULT 1 A B 0 0")
        # A pontuação de quem caiu continua no ranking (resposta aceita antes da queda).
        self.assertEqual(ana.ler(), "EVT RANKING 3")
        _, _, posicao, nome, pontos = (ana.ler() or "").split(" ")
        self.assertEqual((posicao, nome), ("1", "bia"))
        self.assertGreaterEqual(int(pontos), 500)
        # A partida continua para os demais, com a pontuação de bia congelada.
        ana.esperar("EVT QUESTION 2 ")
        caio.esperar("EVT QUESTION 2 ")
        ana.pedir("ANSWER B")
        caio.pedir("ANSWER B")
        self.assertEqual(ana.esperar("EVT RANK 1 "), f"EVT RANK 1 bia {pontos}")

    def test_queda_do_anfitriao_no_lobby(self) -> None:
        _, (ana, bia, caio) = self.abrir_sala("ana", "bia", "caio")
        ana.derrubar()
        for cliente in (bia, caio):
            self.assertEqual(cliente.esperar("EVT LEFT"), "EVT LEFT ana disconnect")
            self.assertEqual(cliente.ler(), "EVT HOST bia")  # quem está há mais tempo na sala
        self.assertEqual(caio.pedir("START"), "ERR 24 nao_e_host")
        self.assertEqual(bia.pedir("START"), "OK START")

    def test_queda_do_anfitriao_durante_a_partida(self) -> None:
        _, (ana, bia, caio) = self.iniciar_partida("ana", "bia", "caio")
        ana.fechar()  # fechamento normal: o servidor lê vazio (fim de conexão)
        self.assertEqual(bia.esperar("EVT LEFT"), "EVT LEFT ana disconnect")
        self.assertEqual(bia.ler(), "EVT HOST bia")
        self.assertEqual(bia.pedir("ANSWER A"), "OK ANSWER A")
        self.assertEqual(caio.pedir("ANSWER A"), "OK ANSWER A")
        self.assertTrue(bia.esperar("EVT RESULT").startswith("EVT RESULT 1 A A "))

    def test_retorno_a_partida_com_o_mesmo_nome(self) -> None:
        codigo, (ana, bia, caio) = self.iniciar_partida("ana", "bia", "caio")
        for cliente in (ana, bia, caio):
            self.assertEqual(cliente.pedir("ANSWER A"), "OK ANSWER A")
        pontos_bia = bia.esperar("EVT RESULT").split(" ")[6]
        self.assertGreaterEqual(int(pontos_bia), 500)
        for cliente in (ana, bia, caio):
            cliente.esperar("EVT QUESTION 2 ")
        bia.derrubar()
        self.assertEqual(ana.esperar("EVT LEFT"), "EVT LEFT bia disconnect")
        # O nome foi liberado: bia conecta de novo e volta com JOIN.
        bia = self.conectar("bia")
        self.assertEqual(bia.pedir(f"JOIN {codigo}"), f"OK JOIN {codigo} JOGO ana ana,caio,bia")
        self.assertEqual(ana.esperar("EVT JOINED"), "EVT JOINED bia")
        # Um nome que não fazia parte da partida continua barrado.
        self.assertEqual(self.conectar("dani").pedir(f"JOIN {codigo}"), "ERR 22 jogo_em_andamento")
        # A pergunta em andamento é perdida para quem retornou.
        self.assertEqual(bia.pedir("ANSWER A"), "ERR 31 sem_pergunta_aberta")
        ana.pedir("ANSWER A")
        caio.pedir("ANSWER A")
        self.assertEqual(bia.esperar("EVT RESULT"), f"EVT RESULT 2 A - 0 {pontos_bia}")  # pontuação preservada
        # A partir da próxima pergunta ela joga normalmente.
        bia.esperar("EVT QUESTION 3 ")
        ana.esperar("EVT QUESTION 3 ")
        self.assertEqual(bia.pedir("ANSWER A"), "OK ANSWER A")
        self.assertEqual(ana.esperar("EVT ANSWERED"), "EVT ANSWERED 1 3")


if __name__ == "__main__":
    unittest.main()
