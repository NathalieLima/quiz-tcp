"""Testes das regras do jogo (quiz.py), sem rede e com relógio falso."""

from __future__ import annotations

import random
import sys
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import protocolo as p
from quiz import ErroDeJogo, Pergunta, Quiz, Sala, calcular_pontos, carregar_perguntas, validar_perguntas

# Em todas as perguntas de teste a opção correta é a A.
PERGUNTAS = [Pergunta(f"Pergunta {n}?", ("certa", "errada 1", "errada 2", "errada 3"), "A") for n in range(1, 7)]


class RelogioFalso:
    """Relógio controlado pelo teste: o tempo só anda quando o teste manda."""

    def __init__(self) -> None:
        self.agora = 100.0

    def __call__(self) -> float:
        return self.agora

    def avancar(self, segundos: float) -> None:
        self.agora += segundos


class BaseQuiz(unittest.TestCase):
    """Cria um Quiz com prazo de 20 s, pausa de 5 s e 3 rodadas."""

    def setUp(self) -> None:
        self.relogio = RelogioFalso()
        self.quiz = Quiz(
            PERGUNTAS, tempo_pergunta=20, pausa=5, rodadas=3,
            relogio=self.relogio, sorteio=random.Random(1),
        )

    def sala_em_jogo(self, *nomes: str) -> Sala:
        """Cria uma sala com os jogadores dados e inicia a partida."""
        sala = self.quiz.criar_sala(nomes[0])
        for nome in nomes[1:]:
            self.quiz.entrar(sala.codigo, nome)
        self.quiz.iniciar(sala, nomes[0])
        return sala

    def assertErro(self, codigo: str, funcao: object, *argumentos: object) -> None:
        """Confere que a chamada levanta ErroDeJogo com o código esperado."""
        with self.assertRaises(ErroDeJogo) as contexto:
            funcao(*argumentos)  # type: ignore[operator]
        self.assertEqual(contexto.exception.codigo, codigo)


class TestePontuacao(BaseQuiz):
    """Fórmula: 500 + (500 x tempo_restante_ms) // tempo_total_ms."""

    def test_formula(self) -> None:
        self.assertEqual(calcular_pontos(20000, 20000), 1000)
        self.assertEqual(calcular_pontos(10000, 20000), 750)
        self.assertEqual(calcular_pontos(1, 20000), 500)
        self.assertEqual(calcular_pontos(0, 20000), 500)
        self.assertEqual(calcular_pontos(14800, 20000), 870)

    def test_resposta_imediata_vale_1000(self) -> None:
        sala = self.sala_em_jogo("ana", "bia")
        self.assertEqual(self.quiz.responder(sala, "ana", "A").pontos, 1000)

    def test_resposta_no_meio_do_prazo_vale_750(self) -> None:
        sala = self.sala_em_jogo("ana", "bia")
        self.relogio.avancar(10)
        self.assertEqual(self.quiz.responder(sala, "ana", "A").pontos, 750)

    def test_resposta_no_ultimo_instante_vale_500(self) -> None:
        sala = self.sala_em_jogo("ana", "bia")
        self.relogio.avancar(20)
        self.assertEqual(self.quiz.responder(sala, "ana", "A").pontos, 500)

    def test_resposta_depois_do_prazo_e_recusada(self) -> None:
        sala = self.sala_em_jogo("ana", "bia")
        self.relogio.avancar(20.01)
        self.assertErro(p.ERRO_SEM_PERGUNTA_ABERTA, self.quiz.responder, sala, "ana", "A")

    def test_resposta_errada_vale_zero(self) -> None:
        sala = self.sala_em_jogo("ana", "bia")
        self.assertEqual(self.quiz.responder(sala, "ana", "C").pontos, 0)

    def test_sem_resposta_vale_zero(self) -> None:
        sala = self.sala_em_jogo("ana", "bia")
        self.quiz.responder(sala, "ana", "A")
        resultados = self.quiz.fechar_pergunta(sala)
        self.assertEqual((resultados["bia"].letra, resultados["bia"].pontos, resultados["bia"].total), ("-", 0, 0))
        self.assertEqual((resultados["ana"].letra, resultados["ana"].pontos, resultados["ana"].total), ("A", 1000, 1000))

    def test_pontos_acumulam_entre_perguntas(self) -> None:
        sala = self.sala_em_jogo("ana", "bia")
        self.quiz.responder(sala, "ana", "A")
        self.quiz.fechar_pergunta(sala)
        self.quiz.avancar(sala)
        self.relogio.avancar(10)
        self.quiz.responder(sala, "ana", "A")
        resultados = self.quiz.fechar_pergunta(sala)
        self.assertEqual((resultados["ana"].pontos, resultados["ana"].total), (750, 1750))

    def test_segunda_resposta_e_recusada(self) -> None:
        sala = self.sala_em_jogo("ana", "bia")
        self.quiz.responder(sala, "ana", "B")
        self.assertErro(p.ERRO_JA_RESPONDEU, self.quiz.responder, sala, "ana", "A")
        self.assertEqual(sala.respostas["ana"].letra, "B")

    def test_resposta_com_pergunta_fechada_e_recusada(self) -> None:
        sala = self.sala_em_jogo("ana", "bia")
        self.quiz.fechar_pergunta(sala)
        self.assertEqual(sala.estado, p.RESULTADO)
        self.assertErro(p.ERRO_SEM_PERGUNTA_ABERTA, self.quiz.responder, sala, "ana", "A")


class TesteRanking(BaseQuiz):
    """Ordem do ranking e desempate alfabético."""

    def test_ordem_por_pontos(self) -> None:
        sala = self.sala_em_jogo("ana", "bia", "caio")
        self.quiz.responder(sala, "caio", "A")
        self.relogio.avancar(10)
        self.quiz.responder(sala, "ana", "A")
        self.quiz.responder(sala, "bia", "D")
        self.quiz.fechar_pergunta(sala)
        self.assertEqual(sala.ranking(), [(1, "caio", 1000), (2, "ana", 750), (3, "bia", 0)])

    def test_empate_vale_a_ordem_alfabetica(self) -> None:
        sala = self.sala_em_jogo("zeca", "Bia", "ana")
        for nome in ("zeca", "Bia", "ana"):
            self.quiz.responder(sala, nome, "A")
        self.quiz.fechar_pergunta(sala)
        self.assertEqual(sala.ranking(), [(1, "ana", 1000), (2, "Bia", 1000), (3, "zeca", 1000)])


class TesteAndamentoDaPartida(BaseQuiz):
    """Estados da sala, fechamento antecipado e fim da partida."""

    def test_iniciar_abre_a_primeira_pergunta(self) -> None:
        sala = self.sala_em_jogo("ana", "bia")
        self.assertEqual(sala.estado, p.PERGUNTA)
        self.assertEqual(sala.numero_pergunta, 1)
        self.assertEqual(len(sala.perguntas), 3)
        self.assertEqual(len(set(sala.perguntas)), 3)  # sorteio sem repetição
        self.assertEqual(sala.pontos, {"ana": 0, "bia": 0})

    def test_iniciar_exige_anfitriao(self) -> None:
        sala = self.quiz.criar_sala("ana")
        self.quiz.entrar(sala.codigo, "bia")
        self.assertErro(p.ERRO_NAO_E_HOST, self.quiz.iniciar, sala, "bia")

    def test_iniciar_exige_dois_jogadores(self) -> None:
        sala = self.quiz.criar_sala("ana")
        self.assertErro(p.ERRO_JOGADORES_INSUFICIENTES, self.quiz.iniciar, sala, "ana")

    def test_fechamento_antecipado_quando_todos_respondem(self) -> None:
        sala = self.sala_em_jogo("ana", "bia")
        self.assertFalse(sala.todos_responderam())
        self.quiz.responder(sala, "ana", "A")
        self.assertEqual(sala.contagem_respostas(), (1, 2))
        self.assertFalse(sala.todos_responderam())
        self.quiz.responder(sala, "bia", "B")
        self.assertEqual(sala.contagem_respostas(), (2, 2))
        self.assertTrue(sala.todos_responderam())

    def test_fechamento_antecipado_ignora_quem_caiu(self) -> None:
        sala = self.sala_em_jogo("ana", "bia", "caio")
        self.quiz.responder(sala, "ana", "A")
        self.quiz.responder(sala, "bia", "A")
        self.assertFalse(sala.todos_responderam())
        self.quiz.sair(sala, "caio")
        self.assertEqual(sala.contagem_respostas(), (2, 2))
        self.assertTrue(sala.todos_responderam())

    def test_prazos_da_pergunta_e_da_pausa(self) -> None:
        sala = self.sala_em_jogo("ana", "bia")
        self.relogio.avancar(19.9)
        self.assertEqual(self.quiz.salas_com_prazo_vencido(), [])
        self.relogio.avancar(0.1)
        self.assertEqual(self.quiz.salas_com_prazo_vencido(), [sala])
        self.quiz.fechar_pergunta(sala)
        self.assertEqual(self.quiz.salas_com_prazo_vencido(), [])
        self.relogio.avancar(5)
        self.assertEqual(self.quiz.salas_com_prazo_vencido(), [sala])

    def test_partida_termina_depois_da_ultima_pergunta(self) -> None:
        sala = self.sala_em_jogo("ana", "bia")
        for numero in (1, 2, 3):
            self.assertEqual(sala.numero_pergunta, numero)
            self.quiz.responder(sala, "ana", "A")
            self.quiz.fechar_pergunta(sala)
            proxima = self.quiz.avancar(sala)
            self.assertEqual(proxima is None, numero == 3)
        self.assertEqual(sala.estado, p.LOBBY)
        self.assertEqual(sala.ranking()[0], (1, "ana", 3000))
        self.assertEqual(self.quiz.salas_com_prazo_vencido(), [])

    def test_nova_partida_zera_a_pontuacao(self) -> None:
        sala = self.sala_em_jogo("ana", "bia")
        for _ in range(3):
            self.quiz.responder(sala, "ana", "A")
            self.quiz.fechar_pergunta(sala)
            self.quiz.avancar(sala)
        self.quiz.iniciar(sala, "ana")
        self.assertEqual(sala.pontos, {"ana": 0, "bia": 0})


class TesteSalas(BaseQuiz):
    """Criação, entrada, saída e troca de anfitrião."""

    def test_codigo_tem_quatro_caracteres_e_e_unico(self) -> None:
        codigos = {self.quiz.criar_sala(f"jogador{n}").codigo for n in range(50)}
        self.assertEqual(len(codigos), 50)
        self.assertTrue(all(p.codigo_valido(codigo) for codigo in codigos))

    def test_entrar_em_sala_inexistente(self) -> None:
        self.assertErro(p.ERRO_SALA_INEXISTENTE, self.quiz.entrar, "ZZZZ", "bia")

    def test_sala_cheia(self) -> None:
        sala = self.quiz.criar_sala("jogador1")
        for numero in range(2, p.MAX_JOGADORES + 1):
            self.quiz.entrar(sala.codigo, f"jogador{numero}")
        self.assertEqual(len(sala.conectados), p.MAX_JOGADORES)
        self.assertErro(p.ERRO_SALA_CHEIA, self.quiz.entrar, sala.codigo, "atrasado")

    def test_estranho_nao_entra_em_partida_em_andamento(self) -> None:
        sala = self.sala_em_jogo("ana", "bia")
        self.assertErro(p.ERRO_JOGO_EM_ANDAMENTO, self.quiz.entrar, sala.codigo, "caio")

    def test_troca_de_anfitriao(self) -> None:
        sala = self.quiz.criar_sala("ana")
        self.quiz.entrar(sala.codigo, "bia")
        self.quiz.entrar(sala.codigo, "caio")
        saida = self.quiz.sair(sala, "ana")
        self.assertEqual((saida.sala_apagada, saida.novo_host), (False, "bia"))
        self.assertEqual(sala.host, "bia")  # bia está há mais tempo na sala que caio

    def test_saida_de_quem_nao_e_anfitriao_nao_troca_o_host(self) -> None:
        sala = self.quiz.criar_sala("ana")
        self.quiz.entrar(sala.codigo, "bia")
        saida = self.quiz.sair(sala, "bia")
        self.assertEqual((saida.sala_apagada, saida.novo_host), (False, None))
        self.assertEqual(sala.host, "ana")

    def test_sala_vazia_e_apagada(self) -> None:
        sala = self.quiz.criar_sala("ana")
        saida = self.quiz.sair(sala, "ana")
        self.assertTrue(saida.sala_apagada)
        self.assertNotIn(sala.codigo, self.quiz.salas)


class TesteQuedaERetorno(BaseQuiz):
    """Pontuação congelada de quem saiu e retorno à partida."""

    def test_pontuacao_de_quem_saiu_fica_congelada_no_ranking(self) -> None:
        sala = self.sala_em_jogo("ana", "bia", "caio")
        self.quiz.responder(sala, "bia", "A")
        self.quiz.sair(sala, "bia")
        self.quiz.responder(sala, "ana", "B")
        self.quiz.responder(sala, "caio", "B")
        resultados = self.quiz.fechar_pergunta(sala)
        self.assertEqual(resultados["bia"].total, 1000)  # resposta aceita antes da queda vale
        self.assertEqual(sala.ranking()[0], (1, "bia", 1000))
        self.quiz.avancar(sala)
        self.quiz.responder(sala, "ana", "B")
        self.quiz.responder(sala, "caio", "B")
        resultados = self.quiz.fechar_pergunta(sala)
        self.assertEqual((resultados["bia"].letra, resultados["bia"].total), ("-", 1000))
        self.assertEqual(sala.ranking()[0], (1, "bia", 1000))

    def test_retorno_preserva_a_pontuacao(self) -> None:
        sala = self.sala_em_jogo("ana", "bia")
        self.quiz.responder(sala, "bia", "A")
        self.quiz.responder(sala, "ana", "A")
        self.quiz.fechar_pergunta(sala)
        self.quiz.avancar(sala)
        self.quiz.sair(sala, "bia")
        self.assertIs(self.quiz.entrar(sala.codigo, "bia"), sala)
        self.assertTrue(sala.em_partida)
        self.assertEqual(sala.pontos["bia"], 1000)
        self.assertEqual(sala.conectados, ["ana", "bia"])

    def test_quem_retorna_perde_a_pergunta_em_andamento(self) -> None:
        sala = self.sala_em_jogo("ana", "bia", "caio")
        self.quiz.sair(sala, "bia")
        self.quiz.entrar(sala.codigo, "bia")
        self.assertErro(p.ERRO_SEM_PERGUNTA_ABERTA, self.quiz.responder, sala, "bia", "A")
        # bia não é esperada no fechamento antecipado desta pergunta.
        self.quiz.responder(sala, "ana", "A")
        self.quiz.responder(sala, "caio", "A")
        self.assertEqual(sala.contagem_respostas(), (2, 2))
        self.assertTrue(sala.todos_responderam())
        self.quiz.fechar_pergunta(sala)
        # A partir da próxima pergunta ela participa normalmente.
        self.quiz.avancar(sala)
        self.assertEqual(self.quiz.responder(sala, "bia", "A").pontos, 1000)

    def test_quem_ja_tinha_respondido_mantem_a_resposta_ao_retornar(self) -> None:
        sala = self.sala_em_jogo("ana", "bia")
        self.quiz.responder(sala, "bia", "A")
        self.quiz.sair(sala, "bia")
        self.quiz.entrar(sala.codigo, "bia")
        self.assertErro(p.ERRO_JA_RESPONDEU, self.quiz.responder, sala, "bia", "B")
        self.assertEqual(sala.contagem_respostas(), (1, 2))

    def test_anfitriao_que_retorna_nao_recupera_o_posto(self) -> None:
        sala = self.sala_em_jogo("ana", "bia")
        self.quiz.sair(sala, "ana")
        self.quiz.entrar(sala.codigo, "ana")
        self.assertEqual(sala.host, "bia")


class TesteBancoDePerguntas(unittest.TestCase):
    """Carregamento e validação do arquivo de perguntas."""

    def test_arquivo_do_projeto_e_valido(self) -> None:
        perguntas = carregar_perguntas(RAIZ / "perguntas.json")
        self.assertGreaterEqual(len(perguntas), 20)
        for pergunta in perguntas:
            self.assertEqual(len(pergunta.opcoes), 4)
            self.assertEqual(len(set(pergunta.opcoes)), 4, pergunta.texto)
            self.assertIn(pergunta.correta, p.LETRAS)
            linha = p.montar_evento(p.EVT_QUESTION, 10, 10, 20, pergunta.texto)
            self.assertLessEqual(len(linha.encode("utf-8")), p.TAMANHO_MAXIMO_LINHA)
        self.assertEqual(len({pergunta.texto for pergunta in perguntas}), len(perguntas))

    def test_correta_pode_ser_letra_ou_indice(self) -> None:
        base = {"texto": "Pergunta?", "opcoes": ["a", "b", "c", "d"]}
        self.assertEqual(validar_perguntas([{**base, "correta": "c"}])[0].correta, "C")
        self.assertEqual(validar_perguntas([{**base, "correta": 0}])[0].correta, "A")
        self.assertEqual(validar_perguntas([{**base, "correta": 3}])[0].correta, "D")

    def test_perguntas_invalidas_sao_recusadas(self) -> None:
        base = {"texto": "Pergunta?", "opcoes": ["a", "b", "c", "d"], "correta": "A"}
        invalidas: list[object] = [
            [],
            {"texto": "não é lista"},
            ["não é objeto"],
            [{**base, "texto": ""}],
            [{**base, "texto": "duas\nlinhas"}],
            [{**base, "opcoes": ["a", "b", "c"]}],
            [{**base, "opcoes": ["a", "b", "c", ""]}],
            [{**base, "correta": "E"}],
            [{**base, "correta": 4}],
            [{"texto": "Pergunta?", "opcoes": ["a", "b", "c", "d"]}],
        ]
        for dados in invalidas:
            with self.assertRaises(ValueError, msg=str(dados)):
                validar_perguntas(dados)

    def test_rodadas_nao_pode_passar_do_tamanho_do_banco(self) -> None:
        with self.assertRaises(ValueError):
            Quiz(PERGUNTAS, rodadas=len(PERGUNTAS) + 1)


if __name__ == "__main__":
    unittest.main()
