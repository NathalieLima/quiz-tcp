# Roteiro da demonstração do Quiz TCP

Duração prevista: 8 a 10 minutos. Ensaie pelo menos uma vez com o cronômetro: os passos 4 a 7 acontecem dentro de uma partida em andamento e dependem de ritmo.

## 1. Preparação (antes de o professor chegar)

### Comandos

Servidor, com tempos curtos para a demonstração caber no prazo (4 perguntas de 20 s):

```bash
python servidor.py --tempo 20 --rodadas 4
```

Três clientes, cada um em um terminal:

```bash
python cliente.py --porta-web 8000
```

```bash
python cliente.py --porta-web 8001
```

```bash
python cliente.py --porta-web 8002
```

Um terminal extra para a conexão manual. Deixe o comando digitado, sem executar:

```bash
nc 127.0.0.1 5000
```

No Windows sem `nc`, use `telnet 127.0.0.1 5000` ou `ncat 127.0.0.1 5000` (do Nmap). Se nenhum estiver disponível, o plano B é `python cliente.py --terminal --debug`, enviando as linhas com o comando `bruto <linha>`. Teste antes qual deles funciona na máquina da apresentação.

### Disposição das janelas

| Posição na tela | Janela |
|---|---|
| Metade esquerda, em cima | Terminal do servidor (o log precisa estar legível: aumente a fonte) |
| Metade esquerda, embaixo | Terminal do `nc`/`telnet` |
| Metade direita | Duas janelas do navegador lado a lado: `http://127.0.0.1:8000` (ana) e `http://127.0.0.1:8001` (bia) |
| Minimizados | Os três terminais dos clientes e a terceira aba `http://127.0.0.1:8002` (caio) |

Em cada página, abra o painel "Protocolo" (barra escura no rodapé). Deixe o README aberto em outra aba, na seção 7, para mostrar os diagramas.

Antes de começar, faça o LOGIN nas três páginas (ana, bia, caio) para ganhar tempo, mas não crie a sala ainda.

## 2. Passo a passo

| Tempo | O que fazer | O que mostrar e dizer |
|---|---|---|
| 0:00 | Mostrar o diagrama da seção 3 do README | "São dois programas: `servidor.py` e `cliente.py`. Eles conversam por um socket TCP com um protocolo nosso, o QUIZ/1.0. O navegador é só a tela do cliente." (use o parágrafo da seção 4 deste roteiro) |
| 0:45 | Na página da ana: "Criar sala". Nas da bia e do caio (nessa ordem): digitar o código e "Entrar na sala" | No painel "Protocolo" da ana: `>> CREATE`, `<< OK CREATE XXXX`, `<< EVT JOINED bia`, `<< EVT JOINED caio`. No log do servidor, as mesmas linhas com horário e endereço. "Requisição, resposta e evento: os três tipos de mensagem do protocolo." |
| 1:30 | No terminal do `nc`, conectar e digitar, uma linha por vez: `FOO`, `LOGIN ana`, `LOGIN dani`, `JOIN ZZZZ`, `JOIN XXXX` (o código real), `ANSWER A` | Respostas: `ERR 01 comando_desconhecido`, `ERR 12 nome_em_uso`, `OK LOGIN dani`, `ERR 21 sala_inexistente`, `OK JOIN XXXX LOBBY ana ana,bia,caio,dani`, `ERR 03 estado_invalido`. "O protocolo é texto puro e independe do nosso cliente e do navegador. Os erros têm código por faixa: 0x gerais, 1x autenticação, 2x sala, 3x resposta." As páginas mostram "dani entrou na sala". |
| 3:00 | Na página da ana: "Iniciar partida" | `EVT START 4`, depois `EVT QUESTION` seguido de 4 linhas `EVT OPTION`, sempre nessa ordem. A barra de tempo corre nas páginas. |
| 3:20 | **Pergunta 1.** Responder na página da ana e da bia. No `nc`: `ANSWER B` e depois `ANSWER C`. Deixar caio sem responder | `EVT ANSWERED 1 4`, `2 4`, ... (só a contagem, sem revelar a resposta). No `nc`: `OK ANSWER B` e `ERR 32 ja_respondeu`. Quando o prazo acaba: `EVT RESULT` diferente para cada jogador, `EVT RANKING` e as linhas `EVT RANK`. caio aparece com `-` (sem resposta). |
| 4:15 | **Pergunta 2.** Todos respondem rápido (ana, bia, caio nas páginas e `ANSWER A` no `nc`) | A pergunta fecha na hora, sem esperar os 20 s: fechamento antecipado. Pontuação maior para quem respondeu mais rápido (500 a 1000). |
| 5:00 | **Pergunta 3.** Assim que ela aparecer, fechar à força o terminal do cliente da bia (fechar a janela ou Ctrl+C). Depois responder com ana, caio e dani | Log do servidor: `desconectou (disconnect)`. Nas outras páginas: `EVT LEFT bia disconnect` e o aviso "bia perdeu a conexão". A pergunta fecha quando os três restantes respondem; bia continua no ranking com a pontuação congelada. |
| 5:45 | Reabrir o cliente da bia (`python cliente.py --porta-web 8001`), recarregar a aba, fazer LOGIN como `bia` e entrar com o mesmo código | `<< OK JOIN XXXX JOGO ana ana,caio,dani,bia`: estado `JOGO`, ela volta direto à partida com os pontos preservados. Os demais recebem `EVT JOINED bia`. |
| 6:45 | **Pergunta 4.** Fechar à força o terminal do cliente da ana (a anfitriã). Responder com os demais | `EVT LEFT ana disconnect` seguido de `EVT HOST caio`: assume quem está há mais tempo na sala. A página do caio passa a mostrar a etiqueta "anfitrião" e o aviso "Agora você é o anfitrião da sala". |
| 7:30 | Esperar o fim | `EVT RANKING` final com todos, inclusive ana (congelada), e 5 s depois `EVT END <vencedor> <pontos>`. Tela final com o vencedor. A sala volta ao lobby e o novo anfitrião vê o botão "Iniciar partida". |
| 8:00 | No terminal do servidor: Ctrl+C | `EVT SHUTDOWN` em todos os clientes; páginas mostram "Servidor desligado"; nenhum traceback. |
| 8:15 | Rodar os testes: `python -m unittest discover -s tests` | 100 testes em cerca de 5 s, incluindo cada código de erro e as quedas. |

Observações para o ensaio:

- Quem assume como anfitrião é quem está há mais tempo na sala. Como a bia saiu e voltou no passo 5:45, ela foi para o fim da fila (`ana,caio,dani,bia`), então o novo anfitrião é o caio. Por isso o caio precisa entrar na sala antes do `nc`: se a ordem for outra, o `EVT HOST` vai para o `dani`, que não tem página.
- O `nc` não envia `PING` sozinho. Se ele ficar 60 s sem enviar nada, cai com `ERR 90`. Durante a partida isso não acontece, porque cada `ANSWER` conta. Se quiser mostrar o `ERR 90` de propósito, abra um segundo `nc` no início e deixe-o parado: ele cai sozinho após 1 minuto.
- Se sobrar tempo, recarregue uma página no meio de uma pergunta: ela reconstrói a tela a partir do histórico que o `cliente.py` guarda.

## 3. Pontos a destacar por critério de nota

### Aplicação (3,5)

- Servidor concorrente: uma thread por cliente, mais uma thread que vigia os prazos das salas.
- Estado compartilhado (salas, nomes, pontuações) protegido por um lock; as respostas de uma sala são processadas uma por vez.
- Eventos em tempo real iniciados pelo servidor: entrada e saída de jogadores, perguntas, contagem de respostas, resultado e ranking.
- Várias salas simultâneas, de 2 a 10 jogadores cada.
- Interface web completa e também modo terminal, ambos sobre a mesma conexão TCP.
- Regras do jogo isoladas em `quiz.py`, sem código de rede, testadas com relógio falso.

### Protocolo de aplicação (3,0)

- Máquina de estados do cliente (CONECTADO, AUTENTICADO, NA_SALA, EM_JOGO) com a tabela de comandos permitidos por estado; fora dela, `ERR 03`.
- Máquina de estados da sala (LOBBY, PERGUNTA, RESULTADO).
- Três tipos de mensagem: requisição, resposta e evento. Eventos sempre começam com `EVT`, então nunca se confundem com respostas.
- Delimitação de mensagens por `\n` sobre o fluxo de bytes do TCP, com limite de 1024 bytes por linha.
- Tabela de 15 códigos de erro organizados por faixa, cada um com o que acontece depois.
- Regras de tempo: prazo, fechamento antecipado, pausa, keepalive e inatividade.
- Tratamento de queda: detecção por três caminhos, `EVT LEFT`, troca de anfitrião, pontuação congelada e retorno à partida.

### Documentação (2,5)

- README único com propósito, funcionamento, justificativa do TCP, requisitos mínimos e guia de execução.
- Diagramas Mermaid: arquitetura, estados do cliente, estados da sala e sequência de uma rodada.
- Tabelas de requisições, eventos, erros, regras de tempo e falhas.
- Explicação da arquitetura com o frontend e da interface local (seção 10), deixando claro o que é e o que não é o protocolo do trabalho.
- Tabela de problemas comuns e descrição dos testes.

### Originalidade (1,0)

- Frontend web sobre socket TCP puro, sem WebSocket: o `cliente.py` faz a ponte.
- Painel "Protocolo" ao vivo, mostrando as linhas brutas que passam pelo socket.
- Retorno à partida com a pontuação preservada.
- Pontuação por velocidade medida no servidor.
- Perguntas sobre redes de computadores, o próprio conteúdo da disciplina.

## 4. Como explicar o frontend

Parágrafo pronto:

> O enunciado pede um cliente e um servidor que se comunicam por socket TCP ou UDP, com um protocolo de aplicação nosso. Essa comunicação é a que acontece entre o `cliente.py` e o `servidor.py`: um socket TCP puro, por onde passam apenas as linhas de texto do protocolo QUIZ/1.0. O servidor não fala HTTP nem WebSocket. O navegador não participa dessa comunicação, e nem poderia, porque um navegador não consegue abrir um socket TCP puro. Ele é só a tela do programa cliente, na mesma máquina: conversa com o `cliente.py` por HTTP em `127.0.0.1`, como uma interface gráfica conversaria com o resto do programa. Cada botão da página vira uma linha do protocolo que o `cliente.py` envia pelo socket, e cada linha que chega do servidor é repassada à página. Nenhuma regra do jogo está no JavaScript. A prova disso é que o jogo funciona igual pelo modo terminal e até pelo `nc`, sem navegador nenhum, e que o painel "Protocolo" mostra as mesmas linhas que aparecem no log do servidor.

## 5. Trechos de código para abrir se o professor pedir

| Assunto | Arquivo | Onde |
|---|---|---|
| Criação do socket, `bind` e `listen` | `servidor.py` | `Servidor.iniciar` |
| `accept` e uma thread por cliente | `servidor.py` | `Servidor.servir` |
| Leitor por linhas com buffer | `protocolo.py` | classe `LeitorDeLinhas` (`alimentar` e `proxima_linha`) |
| Laço de leitura do socket no servidor | `servidor.py` | `Servidor._receber_ate_sair` |
| Validação de comando, argumentos e estado | `servidor.py` e `protocolo.py` | `Servidor._processar_linha`; tabelas `ARGUMENTOS` e `ESTADOS_PERMITIDOS` |
| Processamento de resposta com lock | `servidor.py` | `with self.trava` em `_receber_ate_sair`, e `Servidor._tratar_answer` |
| Registro da resposta e medição do tempo | `quiz.py` | `Quiz.responder` |
| Cálculo da pontuação | `quiz.py` | `calcular_pontos` |
| Ranking e desempate | `quiz.py` | `Sala.ranking` |
| Detecção de queda | `servidor.py` | `_receber_ate_sair` (leitura vazia e inatividade), `_atender_cliente` (exceção de conexão), `_enviar` (falha no envio) |
| O que acontece depois da queda | `servidor.py` e `quiz.py` | `Servidor._desconectar`, `Servidor._tirar_da_sala`, `Quiz.sair` |
| Retorno à partida | `quiz.py` e `servidor.py` | `Quiz.entrar`, `Servidor._tratar_join` |
| Thread de tempo das salas | `servidor.py` | `Servidor._vigiar_salas`, `_fechar_pergunta`, `_avancar` |
| Envio de eventos e ordem das linhas | `servidor.py` | `Servidor._enviar`, `_enviar_a_sala`, `_enviar_pergunta` |
| Desligamento com `EVT SHUTDOWN` | `servidor.py` | `Servidor.parar` e `main` |
| Conexão TCP do cliente e keepalive | `cliente.py` | classe `Conexao` (`conectar`, `_ler_socket`, `_manter_keepalive`) |
| Repasse entre a ponte e o socket | `ponte_web.py` | `Ponte.ao_trafegar` (socket para a página), `TratadorHTTP.do_POST` (página para o socket), `TratadorHTTP._transmitir_eventos` (fluxo SSE) |
| Tratamento dos eventos na página | `web/app.js` | `tratarEvento`, `tratarEventoDoServidor`, `tratarResposta` |

## 6. Perguntas prováveis e respostas curtas

**Por que TCP e não UDP?**
Porque nenhuma resposta pode se perder e as mensagens precisam chegar em ordem (pergunta antes das opções, resultado antes do ranking). O TCP já garante as duas coisas e ainda avisa quando a conexão termina, o que usamos para saber quem saiu da sala. O volume de dados é mínimo, então a sobrecarga do TCP não pesa.

**O que mudaria se fosse UDP?**
Teríamos que reimplementar no protocolo de aplicação o que o TCP dá pronto: número de sequência e confirmação em cada mensagem, retransmissão com temporizador, descarte de duplicatas, reordenação e um controle de sessão com heartbeat para saber quem está conectado. Em compensação, não precisaríamos do buffer de linhas, porque cada datagrama já é uma mensagem inteira.

**Por que o navegador não se conecta direto ao servidor?**
Porque a página web não tem acesso a sockets TCP puros. O navegador só abre conexões HTTP e WebSocket. Para ele falar com o nosso servidor, o servidor teria que falar um desses protocolos, e deixaríamos de ter um protocolo de aplicação próprio sobre TCP.

**Por que não usar WebSocket?**
Pelo mesmo motivo: WebSocket é um protocolo de aplicação pronto (com handshake HTTP e enquadramento próprio). O objetivo do trabalho é projetar o protocolo e usar o socket diretamente. Por isso o WebSocket não aparece em nenhum ponto, nem entre o navegador e o `cliente.py`, onde usamos HTTP simples e Server-Sent Events.

**Por que é preciso delimitar as mensagens com `\n` em TCP?**
Porque o TCP entrega um fluxo de bytes, sem fronteiras de mensagem. Um `recv` pode devolver meia linha, uma linha ou várias linhas coladas. O `\n` é o que marca o fim de cada mensagem: o `LeitorDeLinhas` guarda os bytes em um buffer e só entrega uma linha quando encontra o delimitador. Com `telnet` isso fica visível: cada tecla chega em um segmento separado e o servidor só responde depois do Enter.

**O que acontece se dois jogadores respondem ao mesmo tempo?**
Cada resposta chega na thread do seu cliente, mas as duas precisam do mesmo lock para serem processadas. Uma entra primeiro e a outra espera. Assim a contagem de respostas, o fechamento antecipado e a pontuação nunca são calculados pela metade, e a pergunta não é fechada duas vezes.

**Como o servidor percebe que um cliente caiu sem avisar?**
De três formas. Se o programa foi fechado, o sistema operacional do cliente encerra a conexão e o `recv` do servidor devolve vazio. Se o processo foi morto ou a conexão foi resetada, a leitura ou a escrita levanta uma exceção (`ConnectionResetError`, `BrokenPipeError`). Se o cabo foi puxado ou a máquina travou, nada chega: aí vale a inatividade. O cliente envia `PING` a cada 20 s; se o servidor ficar 60 s sem receber nenhuma linha, envia `ERR 90` e fecha.

**A latência afeta a pontuação?**
Um pouco. O tempo é medido no servidor, quando a resposta é processada, então o atraso da rede entra na conta. Em rede local são poucos milissegundos em uma janela de 20 s, ou seja, menos de 1 ponto. Medir no cliente seria pior, porque permitiria trapacear com o relógio. Como a medição é no servidor, o cliente não tem como mentir.

**Por que o prazo é decidido pelo servidor e não pela página?**
Porque o cliente não é confiável e os relógios não são sincronizados. A barra de tempo da página é só visual; quem fecha a pergunta é o servidor, e ele recusa com `ERR 31` qualquer resposta que chegue depois.

**Por que uma thread por cliente, e não `asyncio` ou `select`?**
Porque é o modelo mais direto para mostrar o uso de sockets: cada thread fica bloqueada no `recv` do seu cliente. Para dezenas de jogadores é suficiente. O custo é precisar do lock para o estado compartilhado.

**Por que o servidor envia os eventos com o lock tomado?**
Para garantir a ordem: enquanto uma thread envia `EVT QUESTION` e as 4 opções, nenhuma outra consegue enviar algo no meio. As mensagens são pequenas, então o envio só bloquearia se um cliente parasse de ler; nesse caso há um prazo de 5 s no envio, e o cliente é derrubado.

**E se alguém entrar com o nome de quem caiu?**
É uma limitação conhecida: não há senha, a identidade é só o nome. Enquanto o dono do nome estiver conectado, o servidor recusa com `ERR 12`. Depois da queda, quem fizer LOGIN com aquele nome assume a pontuação.

**Quais são as limitações e como seriam resolvidas?**

| Limitação | Como resolver |
|---|---|
| Sem senha nem autenticação | Devolver um token secreto no `OK LOGIN` e exigi-lo no retorno à partida |
| Estado só em memória | Gravar salas e pontuações em arquivo ou banco e recarregar ao iniciar |
| Sem criptografia | Envolver o socket com TLS (módulo `ssl`), sem mudar o protocolo |
| Precisa do `cliente.py` em cada máquina | Um gateway que traduzisse WebSocket para QUIZ/1.0 permitiria jogar pelo celular, mas fugiria do objetivo do trabalho |
| Latência influencia a pontuação | Medir o atraso de ida e volta com o `PING` e descontá-lo, aceitando o risco de manipulação |
| Um lock único e uma thread por cliente | Um lock por sala e E/S assíncrona, se fosse preciso atender milhares de conexões |
| Perguntas fixas no arquivo | Um comando do anfitrião para enviar as perguntas da sala |
