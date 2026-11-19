"""Testes da ponte web: servidor, conexão do cliente e ponte HTTP em portas livres."""

from __future__ import annotations

import json
import socket
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import protocolo as p
from cliente import Conexao
from ponte_web import Ponte, extrair_linha
from quiz import Pergunta
from servidor import Servidor

ESPERA = 3.0
PERGUNTAS = [Pergunta(f"Pergunta {n}?", ("certa", "errada 1", "errada 2", "errada 3"), "A") for n in range(1, 4)]


class TesteExtrairLinha(unittest.TestCase):
    """Validação do corpo de POST /comando, sem rede."""

    def test_linha_valida(self) -> None:
        self.assertEqual(extrair_linha(b'{"linha": "ANSWER B"}'), "ANSWER B")

    def test_corpos_invalidos(self) -> None:
        invalidos = [
            b"isto nao e json",
            b"\xff\xfe",
            b"[]",
            b"{}",
            b'{"linha": 5}',
            b'{"linha": ""}',
            b'{"linha": "LOGIN ana\\nCREATE"}',
            b'{"linha": "PING\\r"}',
            json.dumps({"linha": "X" * (p.TAMANHO_MAXIMO_LINHA + 1)}).encode(),
        ]
        for corpo in invalidos:
            with self.assertRaises(ValueError, msg=repr(corpo[:40])):
                extrair_linha(corpo)


class TestePonteWeb(unittest.TestCase):
    """Navegador (aqui, urllib) -> ponte HTTP -> socket TCP -> servidor."""

    def setUp(self) -> None:
        self.servidor = Servidor("127.0.0.1", 0, PERGUNTAS, rodadas=2, registrar=lambda texto: None)
        self.servidor.iniciar()
        threading.Thread(target=self.servidor.servir, daemon=True).start()
        self.conexao = Conexao("127.0.0.1", self.servidor.porta)
        self.conexao.conectar()
        self.ponte = Ponte(self.conexao, 0)  # porta 0: o sistema escolhe uma livre
        self.conexao.iniciar()
        self.ponte.iniciar_em_thread()
        self.base = f"http://127.0.0.1:{self.ponte.porta}"

    def tearDown(self) -> None:
        self.ponte.parar()
        self.conexao.fechar()
        self.servidor.parar()

    def obter(self, caminho: str) -> tuple[int, str]:
        """GET simples. Devolve (status, corpo)."""
        try:
            with urllib.request.urlopen(self.base + caminho, timeout=ESPERA) as resposta:
                return resposta.status, resposta.read().decode("utf-8")
        except urllib.error.HTTPError as erro:
            return erro.code, erro.read().decode("utf-8")

    def postar(self, corpo: bytes, tipo: str = "application/json") -> tuple[int, dict[str, object]]:
        """POST /comando. Devolve (status, JSON da resposta)."""
        requisicao = urllib.request.Request(
            self.base + "/comando", data=corpo, headers={"Content-Type": tipo}, method="POST"
        )
        try:
            with urllib.request.urlopen(requisicao, timeout=ESPERA) as resposta:
                return resposta.status, json.load(resposta)
        except urllib.error.HTTPError as erro:
            return erro.code, json.load(erro)

    def comandar(self, linha: str) -> None:
        """Envia uma linha do protocolo pela ponte e confere que foi aceita."""
        status, dados = self.postar(json.dumps({"linha": linha}).encode("utf-8"))
        self.assertEqual((status, dados), (200, {"ok": True}))

    def ler_eventos(self, parar_quando: Callable[[dict[str, object]], bool]) -> list[dict[str, object]]:
        """Lê o fluxo /eventos até um evento satisfazer a condição."""
        eventos: list[dict[str, object]] = []
        with urllib.request.urlopen(self.base + "/eventos", timeout=ESPERA) as fluxo:
            self.assertTrue(fluxo.headers["Content-Type"].startswith("text/event-stream"))
            for bruta in fluxo:
                texto = bruta.decode("utf-8").strip()
                if texto.startswith("data: "):
                    eventos.append(json.loads(texto[len("data: "):]))
                    if parar_quando(eventos[-1]):
                        return eventos
        raise AssertionError("o fluxo de eventos terminou antes do evento esperado")

    def esperar_linha(self, linha: str) -> dict[str, object]:
        """Espera o evento cuja linha bruta é ``linha`` e o devolve."""
        return self.ler_eventos(lambda evento: evento.get("bruta") == linha)[-1]

    def cliente_tcp(self) -> socket.socket:
        """Abre um segundo cliente, direto no socket TCP do servidor."""
        sock = socket.create_connection(("127.0.0.1", self.servidor.porta), timeout=ESPERA)
        self.addCleanup(sock.close)
        return sock

    # -- testes -------------------------------------------------------------

    def test_comando_chega_ao_servidor_pelo_socket_tcp(self) -> None:
        self.comandar("LOGIN ana")
        self.esperar_linha("OK LOGIN ana")
        # Prova de que o LOGIN chegou ao servidor: o nome agora está em uso lá.
        outro = self.cliente_tcp()
        outro.sendall(b"LOGIN ana\n")
        self.assertEqual(outro.recv(1024), b"ERR 12 nome_em_uso\n")

    def test_eventos_tem_o_formato_documentado(self) -> None:
        self.comandar("LOGIN ana")
        eventos = self.ler_eventos(lambda evento: evento.get("bruta") == "OK LOGIN ana")
        enviada, recebida = eventos[-2], eventos[-1]
        self.assertEqual(
            {chave: enviada[chave] for chave in ("direcao", "bruta", "tipo", "nome", "campos")},
            {"direcao": "enviada", "bruta": "LOGIN ana", "tipo": "REQ", "nome": "LOGIN", "campos": ["ana"]},
        )
        self.assertEqual(
            {chave: recebida[chave] for chave in ("direcao", "bruta", "tipo", "nome", "campos")},
            {"direcao": "recebida", "bruta": "OK LOGIN ana", "tipo": "OK", "nome": "LOGIN", "campos": ["ana"]},
        )
        self.assertIsInstance(recebida["hora"], int)

    def test_eventos_entregam_linhas_iniciadas_pelo_servidor(self) -> None:
        self.comandar("LOGIN ana")
        self.comandar("CREATE")
        criada = self.ler_eventos(lambda evento: evento.get("tipo") == "OK" and evento.get("nome") == "CREATE")[-1]
        codigo = criada["campos"][0]  # type: ignore[index]
        outro = self.cliente_tcp()
        outro.sendall(f"LOGIN bia\nJOIN {codigo}\n".encode("utf-8"))
        evento = self.esperar_linha("EVT JOINED bia")
        self.assertEqual((evento["tipo"], evento["nome"], evento["campos"]), ("EVT", "JOINED", ["bia"]))

    def test_aba_nova_recebe_o_historico(self) -> None:
        self.comandar("LOGIN ana")
        self.comandar("CREATE")
        self.ler_eventos(lambda evento: evento.get("nome") == "CREATE" and evento.get("tipo") == "OK")
        # Uma segunda "aba", aberta depois, recebe tudo desde o início.
        brutas = [evento.get("bruta") for evento in
                  self.ler_eventos(lambda evento: evento.get("nome") == "CREATE" and evento.get("tipo") == "OK")]
        self.assertEqual(brutas[:3], ["LOGIN ana", "OK LOGIN ana", "CREATE"])

    def test_comando_invalido_nao_chega_ao_socket(self) -> None:
        self.assertEqual(self.postar(b'{"linha": "LOGIN ana\\nCREATE"}')[0], 400)
        self.assertEqual(self.postar(b"{}")[0], 400)
        self.assertEqual(self.postar(b"nao e json")[0], 400)
        self.assertEqual(self.postar(json.dumps({"linha": "X" * 2000}).encode())[0], 400)
        self.assertEqual(self.postar(b'{"linha": "PING"}', tipo="text/plain")[0], 415)
        # Nada disso foi enviado: a primeira linha que passa pelo socket é o LOGIN abaixo.
        self.comandar("LOGIN ana")
        eventos = self.ler_eventos(lambda evento: evento.get("bruta") == "OK LOGIN ana")
        self.assertEqual([evento["bruta"] for evento in eventos], ["LOGIN ana", "OK LOGIN ana"])

    def test_arquivos_da_pagina(self) -> None:
        status, corpo = self.obter("/")
        self.assertEqual(status, 200)
        self.assertIn("<title>Quiz TCP</title>", corpo)
        self.assertEqual(self.obter("/app.js")[0], 200)
        self.assertEqual(self.obter("/estilo.css")[0], 200)

    def test_so_serve_arquivos_da_pasta_web(self) -> None:
        for caminho in ("/protocolo.py", "/../protocolo.py", "/web/index.html", "/..%2Fservidor.py", "/perguntas.json"):
            self.assertEqual(self.obter(caminho)[0], 404, caminho)

    def test_queda_da_conexao_tcp_vira_evento(self) -> None:
        self.comandar("LOGIN ana")
        self.esperar_linha("OK LOGIN ana")
        self.servidor.parar()
        eventos = self.ler_eventos(lambda evento: evento.get("tipo") == "CONEXAO")
        self.assertEqual((eventos[-1]["tipo"], eventos[-1]["nome"]), ("CONEXAO", "PERDIDA"))
        self.assertIn("EVT SHUTDOWN", [evento.get("bruta") for evento in eventos])
        # Sem conexão TCP, a ponte recusa novos comandos.
        self.assertEqual(self.postar(b'{"linha": "PING"}')[0], 503)


if __name__ == "__main__":
    unittest.main()
