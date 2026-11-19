"""Testes do módulo protocolo.py: montagem, interpretação, validações e leitor de linhas."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import protocolo as p


class TesteMontagem(unittest.TestCase):
    """Cada tipo de mensagem é montado no formato exato do README."""

    def test_requisicoes(self) -> None:
        self.assertEqual(p.montar_requisicao(p.CMD_LOGIN, "ana"), "LOGIN ana")
        self.assertEqual(p.montar_requisicao(p.CMD_CREATE), "CREATE")
        self.assertEqual(p.montar_requisicao(p.CMD_JOIN, "K7QD"), "JOIN K7QD")
        self.assertEqual(p.montar_requisicao(p.CMD_ANSWER, "B"), "ANSWER B")

    def test_respostas_de_sucesso(self) -> None:
        self.assertEqual(p.montar_ok(p.CMD_LOGIN, "ana"), "OK LOGIN ana")
        self.assertEqual(p.montar_ok(p.CMD_START), "OK START")
        self.assertEqual(
            p.montar_ok(p.CMD_JOIN, "K7QD", p.JOIN_LOBBY, "ana", "ana,bia"),
            "OK JOIN K7QD LOBBY ana ana,bia",
        )

    def test_erros_usam_a_tabela(self) -> None:
        self.assertEqual(p.montar_erro("01"), "ERR 01 comando_desconhecido")
        self.assertEqual(p.montar_erro("21"), "ERR 21 sala_inexistente")
        self.assertEqual(p.montar_erro("90"), "ERR 90 inatividade")

    def test_tabela_de_erros_igual_a_do_readme(self) -> None:
        esperado = {
            "01": "comando_desconhecido",
            "02": "argumentos_invalidos",
            "03": "estado_invalido",
            "04": "linha_muito_longa",
            "11": "nome_invalido",
            "12": "nome_em_uso",
            "21": "sala_inexistente",
            "22": "jogo_em_andamento",
            "23": "sala_cheia",
            "24": "nao_e_host",
            "25": "jogadores_insuficientes",
            "31": "sem_pergunta_aberta",
            "32": "ja_respondeu",
            "33": "opcao_invalida",
            "90": "inatividade",
        }
        self.assertEqual(p.ERROS, esperado)

    def test_eventos(self) -> None:
        self.assertEqual(p.montar_evento(p.EVT_JOINED, "bia"), "EVT JOINED bia")
        self.assertEqual(p.montar_evento(p.EVT_LEFT, "bia", p.MOTIVO_QUEDA), "EVT LEFT bia disconnect")
        self.assertEqual(p.montar_evento(p.EVT_RESULT, 1, "B", "-", 0, 0), "EVT RESULT 1 B - 0 0")
        self.assertEqual(p.montar_evento(p.EVT_RANK, 1, "bia", 870), "EVT RANK 1 bia 870")
        self.assertEqual(p.montar_evento(p.EVT_SHUTDOWN), "EVT SHUTDOWN")
        self.assertEqual(
            p.montar_evento(p.EVT_QUESTION, 1, 5, 20, "Qual camada usa portas?"),
            "EVT QUESTION 1 5 20 Qual camada usa portas?",
        )

    def test_codificar_linha_acrescenta_o_delimitador(self) -> None:
        self.assertEqual(p.codificar_linha("PING"), b"PING\n")
        self.assertEqual(p.codificar_linha("EVT OPTION A Física"), "EVT OPTION A Física\n".encode("utf-8"))


class TesteInterpretacao(unittest.TestCase):
    """interpretar_linha separa tipo, nome e campos de qualquer linha."""

    def test_requisicao(self) -> None:
        mensagem = p.interpretar_linha("ANSWER B")
        self.assertEqual((mensagem.tipo, mensagem.nome, mensagem.campos), ("REQ", "ANSWER", ["B"]))
        self.assertEqual(p.interpretar_linha("CREATE").campos, [])

    def test_resposta_ok(self) -> None:
        mensagem = p.interpretar_linha("OK JOIN K7QD LOBBY ana ana,bia")
        self.assertEqual(mensagem.tipo, "OK")
        self.assertEqual(mensagem.nome, "JOIN")
        self.assertEqual(mensagem.campos, ["K7QD", "LOBBY", "ana", "ana,bia"])
        self.assertEqual(p.interpretar_linha("OK START").campos, [])

    def test_resposta_de_erro(self) -> None:
        mensagem = p.interpretar_linha("ERR 21 sala_inexistente")
        self.assertEqual((mensagem.tipo, mensagem.nome, mensagem.campos), ("ERR", "21", ["sala_inexistente"]))

    def test_pong_e_bye(self) -> None:
        self.assertEqual(p.interpretar_linha("PONG").tipo, "PONG")
        self.assertEqual(p.interpretar_linha("BYE").tipo, "BYE")

    def test_evento_simples(self) -> None:
        mensagem = p.interpretar_linha("EVT RANK 1 bia 870")
        self.assertEqual((mensagem.tipo, mensagem.nome, mensagem.campos), ("EVT", "RANK", ["1", "bia", "870"]))
        self.assertEqual(p.interpretar_linha("EVT SHUTDOWN").campos, [])

    def test_ultimo_campo_da_pergunta_pode_ter_espacos(self) -> None:
        mensagem = p.interpretar_linha("EVT QUESTION 1 5 20 Qual camada do modelo TCP/IP usa portas?")
        self.assertEqual(mensagem.campos, ["1", "5", "20", "Qual camada do modelo TCP/IP usa portas?"])

    def test_ultimo_campo_da_opcao_pode_ter_espacos(self) -> None:
        mensagem = p.interpretar_linha("EVT OPTION A Endereço IP e número de porta")
        self.assertEqual(mensagem.campos, ["A", "Endereço IP e número de porta"])

    def test_montar_e_interpretar_sao_inversos(self) -> None:
        linha = p.montar_evento(p.EVT_RESULT, 2, "C", "A", 0, 870)
        self.assertEqual(p.interpretar_linha(linha).campos, ["2", "C", "A", "0", "870"])

    def test_separar_requisicao(self) -> None:
        self.assertEqual(p.separar_requisicao("LOGIN ana"), ("LOGIN", ["ana"]))
        self.assertEqual(p.separar_requisicao("PING"), ("PING", []))
        # Dois espaços seguidos geram um argumento vazio (o servidor responde ERR 02).
        self.assertEqual(p.separar_requisicao("LOGIN  ana"), ("LOGIN", ["", "ana"]))


class TesteValidacoes(unittest.TestCase):
    """Formato de nome de usuário, código de sala e letra de resposta."""

    def test_nome_de_usuario(self) -> None:
        for nome in ("ana", "Bia_02", "a" * 16, "___"):
            self.assertTrue(p.nome_valido(nome), nome)
        for nome in ("", "ab", "a" * 17, "ana maria", "joão", "ana-1", "ana\n"):
            self.assertFalse(p.nome_valido(nome), nome)

    def test_codigo_de_sala(self) -> None:
        for codigo in ("K7QD", "AAAA", "1234"):
            self.assertTrue(p.codigo_valido(codigo), codigo)
        for codigo in ("", "k7qd", "K7Q", "K7QDX", "K7 D", "K7Q\n"):
            self.assertFalse(p.codigo_valido(codigo), codigo)

    def test_letra_de_resposta(self) -> None:
        for letra in ("A", "B", "C", "D"):
            self.assertTrue(p.letra_valida(letra), letra)
        for letra in ("", "a", "E", "AB", "1"):
            self.assertFalse(p.letra_valida(letra), letra)

    def test_tabelas_cobrem_todos_os_comandos(self) -> None:
        self.assertEqual(set(p.ARGUMENTOS), set(p.ESTADOS_PERMITIDOS))
        self.assertEqual(len(p.ARGUMENTOS), 8)


class TesteLeitorDeLinhas(unittest.TestCase):
    """O leitor só devolve mensagens completas, terminadas em "\\n"."""

    def ler_tudo(self, leitor: p.LeitorDeLinhas) -> list[bytes]:
        linhas = []
        while (linha := leitor.proxima_linha()) is not None:
            linhas.append(linha)
        return linhas

    def test_mensagem_partida_em_varios_recv(self) -> None:
        leitor = p.LeitorDeLinhas()
        leitor.alimentar(b"LOG")
        self.assertIsNone(leitor.proxima_linha())
        leitor.alimentar(b"IN a")
        self.assertIsNone(leitor.proxima_linha())
        leitor.alimentar(b"na\n")
        self.assertEqual(leitor.proxima_linha(), b"LOGIN ana")
        self.assertIsNone(leitor.proxima_linha())

    def test_varias_mensagens_em_um_unico_recv(self) -> None:
        leitor = p.LeitorDeLinhas()
        leitor.alimentar(b"LOGIN ana\nCREATE\nPING\n")
        self.assertEqual(self.ler_tudo(leitor), [b"LOGIN ana", b"CREATE", b"PING"])

    def test_mensagens_completas_e_um_pedaco(self) -> None:
        leitor = p.LeitorDeLinhas()
        leitor.alimentar(b"PING\nANSW")
        self.assertEqual(self.ler_tudo(leitor), [b"PING"])
        leitor.alimentar(b"ER B\n")
        self.assertEqual(self.ler_tudo(leitor), [b"ANSWER B"])

    def test_um_byte_por_vez(self) -> None:
        leitor = p.LeitorDeLinhas()
        linhas: list[bytes] = []
        for byte in b"JOIN K7QD\nSTART\n":
            leitor.alimentar(bytes([byte]))
            linhas.extend(self.ler_tudo(leitor))
        self.assertEqual(linhas, [b"JOIN K7QD", b"START"])

    def test_descarta_o_cr_do_telnet(self) -> None:
        leitor = p.LeitorDeLinhas()
        leitor.alimentar(b"PING\r\n")
        self.assertEqual(leitor.proxima_linha(), b"PING")

    def test_linha_vazia(self) -> None:
        leitor = p.LeitorDeLinhas()
        leitor.alimentar(b"\n")
        self.assertEqual(leitor.proxima_linha(), b"")

    def test_linha_no_limite_e_aceita(self) -> None:
        leitor = p.LeitorDeLinhas()
        leitor.alimentar(b"X" * p.TAMANHO_MAXIMO_LINHA + b"\n")
        self.assertEqual(len(leitor.proxima_linha() or b""), p.TAMANHO_MAXIMO_LINHA)

    def test_linha_longa_demais_sem_delimitador(self) -> None:
        leitor = p.LeitorDeLinhas()
        leitor.alimentar(b"X" * (p.TAMANHO_MAXIMO_LINHA + 1))
        with self.assertRaises(p.LinhaMuitoLonga):
            leitor.proxima_linha()

    def test_linha_longa_demais_com_delimitador(self) -> None:
        leitor = p.LeitorDeLinhas()
        leitor.alimentar(b"X" * (p.TAMANHO_MAXIMO_LINHA + 1) + b"\n")
        with self.assertRaises(p.LinhaMuitoLonga):
            leitor.proxima_linha()

    def test_linhas_anteriores_a_longa_sao_entregues(self) -> None:
        leitor = p.LeitorDeLinhas()
        leitor.alimentar(b"PING\n" + b"X" * 2000)
        self.assertEqual(leitor.proxima_linha(), b"PING")
        with self.assertRaises(p.LinhaMuitoLonga):
            leitor.proxima_linha()


if __name__ == "__main__":
    unittest.main()
