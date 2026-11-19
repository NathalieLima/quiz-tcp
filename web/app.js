// Tela do Quiz TCP.
//
// Esta página é só a camada de apresentação do cliente.py. Ela não se conecta
// ao servidor do quiz: recebe do cliente.py local, por Server-Sent Events
// (/eventos), cada linha que passou pelo socket TCP, e envia os comandos do
// jogador por POST /comando. Nenhuma regra do jogo fica aqui: pontuação, prazo,
// validação e ranking são decididos pelo servidor. A página mostra o que
// recebe e envia o que o jogador escolhe.

'use strict';

// Texto mostrado ao jogador para cada código de erro do protocolo (ERR <código>).
const MENSAGENS_ERRO = {
  '01': 'O servidor não conhece esse comando.',
  '02': 'Comando com argumentos faltando, sobrando ou inválidos.',
  '03': 'Esse comando não pode ser usado agora.',
  '04': 'Linha longa demais. O servidor encerrou a conexão.',
  '11': 'Nome inválido. Use de 3 a 16 letras sem acento, dígitos ou _, sem espaços.',
  '12': 'Esse nome já está em uso. Escolha outro.',
  '21': 'Sala não encontrada. Confira o código.',
  '22': 'Essa sala está com uma partida em andamento. Tente de novo quando terminar.',
  '23': 'A sala está cheia (máximo de 10 jogadores).',
  '24': 'Só o anfitrião pode iniciar a partida.',
  '25': 'São necessários pelo menos 2 jogadores para iniciar.',
  '31': 'Não há pergunta aberta para você responder agora.',
  '32': 'Você já respondeu esta pergunta.',
  '33': 'Opção inválida. Responda com A, B, C ou D.',
  '90': 'O servidor encerrou a conexão por inatividade.',
};

const MOTIVOS_SAIDA = {
  leave: 'saiu da sala',
  quit: 'encerrou a sessão',
  disconnect: 'perdeu a conexão',
  timeout: 'foi desconectado por inatividade',
};

const LIMITE_PAINEL = 400;        // linhas mantidas no painel "Protocolo"
const IDADE_MAXIMA_AVISO = 4000;  // ms: eventos mais antigos (histórico) não geram aviso
const DURACAO_AVISO = 6000;       // ms que um aviso fica na tela

const $ = (id) => document.getElementById(id);

// O que a página sabe sobre a sessão. Tudo vem das mensagens do servidor.
const estado = {};

function estadoInicial() {
  return {
    tela: 'entrada',
    nome: null,       // nome aceito no LOGIN
    sala: null,       // código da sala
    host: null,       // nome do anfitrião
    jogadores: [],    // jogadores conectados na sala
    pergunta: null,   // { numero, total, tempo, texto, opcoes, inicio, aberta }
    ranking: [],      // linhas do último EVT RANK: { posicao, nome, pontos }
    encerrado: false, // a sessão acabou (BYE, desligamento, inatividade)
  };
}

// ---------------------------------------------------------------------------
// Telas e avisos
// ---------------------------------------------------------------------------

function mostrarTela(nome) {
  estado.tela = nome;
  for (const tela of document.querySelectorAll('.tela')) {
    tela.hidden = tela.id !== 'tela-' + nome;
  }
  for (const erro of document.querySelectorAll('.erro')) {
    erro.textContent = '';
  }
  atualizarIdentidade();
}

function atualizarIdentidade() {
  let texto = '';
  if (estado.nome) {
    texto = 'Jogador: ' + estado.nome;
    if (estado.sala) {
      texto += '  |  Sala: ' + estado.sala;
    }
  }
  $('identidade').textContent = texto;
}

// Mostra um erro na tela atual (ou como aviso, se a tela não tem área de erro).
function mostrarErro(texto) {
  const area = document.querySelector('#tela-' + estado.tela + ' .erro');
  if (area) {
    area.textContent = texto;
  } else {
    avisar(texto);
  }
}

// Aviso passageiro no canto da tela (entrada e saída de jogadores etc.).
function avisar(texto) {
  const aviso = document.createElement('div');
  aviso.className = 'aviso';
  aviso.textContent = texto;
  $('avisos').appendChild(aviso);
  setTimeout(() => aviso.remove(), DURACAO_AVISO);
}

function mostrarQueda(titulo, texto) {
  estado.encerrado = true;
  if (estado.pergunta) {
    estado.pergunta.aberta = false;
  }
  $('queda-titulo').textContent = titulo;
  $('queda-texto').textContent = texto;
  mostrarTela('queda');
}

function mostrarEspera(titulo, texto) {
  $('espera-titulo').textContent = titulo;
  $('espera-texto').textContent = texto;
  mostrarTela('espera');
}

// ---------------------------------------------------------------------------
// Envio de comandos ao cliente.py, que os repassa pelo socket TCP
// ---------------------------------------------------------------------------

async function enviar(linha) {
  try {
    const resposta = await fetch('/comando', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ linha }),
    });
    if (!resposta.ok) {
      const dados = await resposta.json();
      mostrarErro('O cliente local recusou o comando: ' + dados.erro);
    }
  } catch (erro) {
    mostrarErro('Não foi possível falar com o cliente.py local.');
  }
}

// ---------------------------------------------------------------------------
// Painel "Protocolo": linhas brutas enviadas e recebidas pelo socket TCP
// ---------------------------------------------------------------------------

function formatarHora(milissegundos) {
  const data = new Date(milissegundos);
  const dois = (numero) => String(numero).padStart(2, '0');
  return dois(data.getHours()) + ':' + dois(data.getMinutes()) + ':' + dois(data.getSeconds()) +
    '.' + String(data.getMilliseconds()).padStart(3, '0');
}

function registrarNoPainel(evento) {
  const painel = $('protocolo-linhas');
  const linha = document.createElement('div');
  let seta = '--';
  let texto = 'conexão TCP com o servidor perdida';
  linha.className = 'linha-protocolo';
  if (evento.direcao === 'enviada') {
    seta = '>>';
    texto = evento.bruta;
    linha.classList.add('enviada');
  } else if (evento.direcao === 'recebida') {
    seta = '<<';
    texto = evento.bruta;
    linha.classList.add('recebida');
  }
  linha.textContent = formatarHora(evento.hora) + '  ' + seta + '  ' + texto;
  painel.appendChild(linha);
  while (painel.childElementCount > LIMITE_PAINEL) {
    painel.firstElementChild.remove();
  }
  painel.scrollTop = painel.scrollHeight;
  $('protocolo-contagem').textContent = '(' + painel.childElementCount + ')';
}

// ---------------------------------------------------------------------------
// Sala (lobby)
// ---------------------------------------------------------------------------

function desenharSala() {
  const souHost = estado.host === estado.nome;
  $('sala-codigo').textContent = estado.sala || '';
  $('sala-contagem').textContent = '(' + estado.jogadores.length + ')';
  const lista = $('sala-jogadores');
  lista.replaceChildren();
  for (const jogador of estado.jogadores) {
    const item = document.createElement('li');
    const nome = document.createElement('span');
    nome.textContent = jogador;
    item.appendChild(nome);
    if (jogador === estado.host) {
      item.appendChild(criarEtiqueta('anfitrião', 'etiqueta-host'));
    }
    if (jogador === estado.nome) {
      item.appendChild(criarEtiqueta('você', 'etiqueta-voce'));
    }
    lista.appendChild(item);
  }
  $('botao-iniciar').hidden = !souHost;
  $('sala-situacao').textContent = souHost
    ? 'Passe o código aos outros jogadores e inicie a partida quando todos entrarem.'
    : 'Aguardando o anfitrião iniciar a partida.';
}

function criarEtiqueta(texto, classe) {
  const etiqueta = document.createElement('span');
  etiqueta.className = 'etiqueta ' + classe;
  etiqueta.textContent = texto;
  return etiqueta;
}

// ---------------------------------------------------------------------------
// Pergunta
// ---------------------------------------------------------------------------

function botoesDeOpcao() {
  return Array.from(document.querySelectorAll('.opcao'));
}

function habilitarOpcoes(habilitar) {
  for (const botao of botoesDeOpcao()) {
    botao.disabled = !habilitar;
  }
}

function abrirPergunta(evento) {
  const [numero, total, tempo, texto] = evento.campos;
  estado.pergunta = {
    numero,
    total,
    tempo: Number(tempo),
    texto,
    opcoes: {},
    inicio: evento.hora,  // instante em que o cliente.py recebeu a pergunta
    aberta: true,
  };
  $('pergunta-numero').textContent = 'Pergunta ' + numero + ' de ' + total;
  $('pergunta-texto').textContent = texto;
  $('pergunta-situacao').textContent = 'Escolha uma opção.';
  $('pergunta-contagem').textContent = '';
  for (const botao of botoesDeOpcao()) {
    botao.classList.remove('escolhida');
    botao.querySelector('.opcao-texto').textContent = '';
  }
  habilitarOpcoes(true);
  mostrarTela('pergunta');
  atualizarTempo();
}

function preencherOpcao(letra, texto) {
  if (!estado.pergunta) {
    return;
  }
  estado.pergunta.opcoes[letra] = texto;
  const botao = document.querySelector('.opcao[data-letra="' + letra + '"]');
  if (botao) {
    botao.querySelector('.opcao-texto').textContent = texto;
  }
}

function responder(letra) {
  // Desabilita na hora para evitar duplo clique. Quem aceita ou recusa a
  // resposta é o servidor (OK ANSWER ou ERR).
  habilitarOpcoes(false);
  $('pergunta-situacao').textContent = 'Enviando a resposta ' + letra + '...';
  enviar('ANSWER ' + letra);
}

function marcarResposta(letra) {
  habilitarOpcoes(false);
  for (const botao of botoesDeOpcao()) {
    botao.classList.toggle('escolhida', botao.dataset.letra === letra);
  }
  $('pergunta-situacao').textContent =
    'Sua resposta: ' + letra + '. Aguardando os outros jogadores ou o fim do prazo.';
}

// A barra de tempo é só visual. Quem decide o fim do prazo é o servidor,
// que avisa com EVT RESULT.
function atualizarTempo() {
  const pergunta = estado.pergunta;
  if (!pergunta || !pergunta.aberta) {
    return;
  }
  const total = pergunta.tempo * 1000;
  const restante = Math.max(0, total - (Date.now() - pergunta.inicio));
  $('barra-tempo-preenchida').style.width = (total > 0 ? (restante / total) * 100 : 0) + '%';
  $('pergunta-segundos').textContent = Math.ceil(restante / 1000) + ' s';
}

// ---------------------------------------------------------------------------
// Resultado e ranking
// ---------------------------------------------------------------------------

function descreverOpcao(letra) {
  const texto = estado.pergunta ? estado.pergunta.opcoes[letra] : undefined;
  return texto ? letra + ' - ' + texto : letra;
}

function mostrarResultado(campos) {
  const [numero, correta, minha, pontos, total] = campos;
  if (estado.pergunta) {
    estado.pergunta.aberta = false;
  }
  const titulo = $('resultado-titulo');
  titulo.classList.remove('certo', 'errado');
  if (minha === '-') {
    titulo.textContent = 'Sem resposta';
    titulo.classList.add('errado');
    $('resultado-minha').textContent = 'Você não respondeu esta pergunta.';
  } else if (minha === correta) {
    titulo.textContent = '✔ Resposta certa!';
    titulo.classList.add('certo');
    $('resultado-minha').textContent = 'Sua resposta: ' + descreverOpcao(minha);
  } else {
    titulo.textContent = '✘ Resposta errada';
    titulo.classList.add('errado');
    $('resultado-minha').textContent = 'Sua resposta: ' + descreverOpcao(minha);
  }
  const totalPerguntas = estado.pergunta ? estado.pergunta.total : null;
  $('resultado-numero').textContent = 'Resultado da pergunta ' + numero +
    (totalPerguntas ? ' de ' + totalPerguntas : '');
  $('resultado-correta').textContent = 'Resposta correta: ' + descreverOpcao(correta);
  $('resultado-pontos').textContent = '+' + pontos;
  $('resultado-total').textContent = total;
  $('resultado-proxima').textContent = totalPerguntas && numero === totalPerguntas
    ? 'Era a última pergunta. O resultado final vem em instantes.'
    : 'A próxima pergunta vem em instantes.';
  $('ranking-parcial').replaceChildren();
  mostrarTela('resultado');
}

function desenharRanking(corpoDaTabela) {
  corpoDaTabela.replaceChildren();
  for (const item of estado.ranking) {
    const linha = document.createElement('tr');
    if (item.nome === estado.nome) {
      linha.className = 'minha-linha';
    }
    const textos = [
      item.posicao + 'º',
      item.nome + (item.nome === estado.nome ? ' (você)' : ''),
      item.pontos,
    ];
    for (const texto of textos) {
      const celula = document.createElement('td');
      celula.textContent = texto;
      linha.appendChild(celula);
    }
    corpoDaTabela.appendChild(linha);
  }
}

function mostrarFinal(campos) {
  const [vencedor, pontos] = campos;
  $('final-vencedor').textContent = vencedor + (vencedor === estado.nome ? ' (você)' : '');
  $('final-pontos').textContent = pontos + ' pontos';
  desenharRanking($('ranking-final'));
  desenharSala();
  mostrarTela('final');
}

// ---------------------------------------------------------------------------
// Tratamento das mensagens do servidor
// ---------------------------------------------------------------------------

function tratarResposta(evento) {
  const campos = evento.campos;
  if (evento.tipo === 'ERR') {
    tratarErro(evento.nome);
  } else if (evento.tipo === 'BYE') {
    mostrarQueda('Sessão encerrada',
      'Você encerrou a sessão. Para jogar de novo, inicie o cliente.py outra vez.');
  } else if (evento.tipo === 'OK' && evento.nome === 'LOGIN') {
    estado.nome = campos[0];
    mostrarTela('saguao');
  } else if (evento.tipo === 'OK' && evento.nome === 'CREATE') {
    estado.sala = campos[0];
    estado.host = estado.nome;
    estado.jogadores = [estado.nome];
    desenharSala();
    mostrarTela('sala');
  } else if (evento.tipo === 'OK' && evento.nome === 'JOIN') {
    estado.sala = campos[0];
    estado.host = campos[2];
    estado.jogadores = campos[3].split(',');
    desenharSala();
    if (campos[1] === 'JOGO') {
      mostrarEspera('Você voltou à partida',
        'Sua pontuação foi preservada. Aguarde a próxima pergunta.');
    } else {
      mostrarTela('sala');
    }
  } else if (evento.tipo === 'OK' && evento.nome === 'ANSWER') {
    marcarResposta(campos[0]);
  } else if (evento.tipo === 'OK' && evento.nome === 'LEAVE') {
    estado.sala = null;
    estado.host = null;
    estado.jogadores = [];
    estado.pergunta = null;
    mostrarTela('saguao');
  }
  // OK START e PONG não mudam a tela.
}

function tratarErro(codigo) {
  const texto = (MENSAGENS_ERRO[codigo] || 'Erro desconhecido.') + ' (ERR ' + codigo + ')';
  if (codigo === '04' || codigo === '90') {
    mostrarQueda('Conexão encerrada pelo servidor', texto);
    return;
  }
  mostrarErro(texto);
  if (codigo === '31' || codigo === '32') {
    $('pergunta-situacao').textContent = '';
  }
}

function tratarEventoDoServidor(evento, recente) {
  const campos = evento.campos;
  switch (evento.nome) {
    case 'JOINED':
      if (!estado.jogadores.includes(campos[0])) {
        estado.jogadores.push(campos[0]);
      }
      desenharSala();
      document.querySelector('#tela-sala .erro').textContent = '';  // ex.: ERR 25 já não vale
      if (recente) avisar(campos[0] + ' entrou na sala.');
      break;
    case 'LEFT':
      estado.jogadores = estado.jogadores.filter((jogador) => jogador !== campos[0]);
      desenharSala();
      if (recente) avisar(campos[0] + ' ' + (MOTIVOS_SAIDA[campos[1]] || 'saiu') + '.');
      break;
    case 'HOST':
      estado.host = campos[0];
      desenharSala();
      if (recente) {
        avisar(campos[0] === estado.nome
          ? 'Agora você é o anfitrião da sala.'
          : campos[0] + ' agora é o anfitrião.');
      }
      break;
    case 'START':
      estado.ranking = [];
      mostrarEspera('A partida vai começar', 'Serão ' + campos[0] + ' perguntas. Prepare-se!');
      break;
    case 'QUESTION':
      abrirPergunta(evento);
      break;
    case 'OPTION':
      preencherOpcao(campos[0], campos[1]);
      break;
    case 'ANSWERED':
      $('pergunta-contagem').textContent = campos[0] + ' de ' + campos[1] + ' responderam';
      break;
    case 'RESULT':
      mostrarResultado(campos);
      break;
    case 'RANKING':
      estado.ranking = [];
      break;
    case 'RANK':
      estado.ranking.push({ posicao: campos[0], nome: campos[1], pontos: campos[2] });
      desenharRanking($('ranking-parcial'));
      break;
    case 'END':
      mostrarFinal(campos);
      break;
    case 'SHUTDOWN':
      mostrarQueda('Servidor desligado',
        'O servidor do quiz foi desligado. Quando ele voltar, inicie o cliente.py de novo.');
      break;
  }
}

// Recebe cada evento do fluxo /eventos (ver seção 10 do README).
function tratarEvento(evento) {
  registrarNoPainel(evento);
  if (evento.tipo === 'CONEXAO') {
    if (!estado.encerrado) {
      mostrarQueda('Conexão perdida',
        'A conexão TCP com o servidor caiu. Inicie o cliente.py de novo; para voltar a uma ' +
        'partida em andamento, use o mesmo nome e o código da sala.');
    }
    return;
  }
  if (evento.direcao !== 'recebida') {
    return;  // linhas enviadas só aparecem no painel
  }
  // Eventos antigos vêm do histórico (página recarregada) e não geram avisos.
  const recente = Date.now() - evento.hora < IDADE_MAXIMA_AVISO;
  if (evento.tipo === 'EVT') {
    tratarEventoDoServidor(evento, recente);
  } else {
    tratarResposta(evento);
  }
}

// ---------------------------------------------------------------------------
// Ações do jogador
// ---------------------------------------------------------------------------

function ligarControles() {
  $('form-nome').addEventListener('submit', (submissao) => {
    submissao.preventDefault();
    const nome = $('campo-nome').value.trim();
    // Um nome vazio ou com espaços nem formaria uma linha LOGIN válida.
    // O formato do nome em si é validado pelo servidor (ERR 11).
    if (!nome || /\s/.test(nome)) {
      mostrarErro(MENSAGENS_ERRO['11']);
      return;
    }
    enviar('LOGIN ' + nome);
  });

  $('botao-criar').addEventListener('click', () => enviar('CREATE'));

  $('form-codigo').addEventListener('submit', (submissao) => {
    submissao.preventDefault();
    const codigo = $('campo-codigo').value.trim().toUpperCase();
    if (!codigo || /\s/.test(codigo)) {
      mostrarErro(MENSAGENS_ERRO['21']);
      return;
    }
    enviar('JOIN ' + codigo);
  });

  $('botao-encerrar').addEventListener('click', () => enviar('QUIT'));
  $('botao-iniciar').addEventListener('click', () => enviar('START'));
  $('botao-sair-sala').addEventListener('click', () => enviar('LEAVE'));
  $('botao-sair-espera').addEventListener('click', () => enviar('LEAVE'));
  $('botao-voltar-sala').addEventListener('click', () => mostrarTela('sala'));

  for (const botao of botoesDeOpcao()) {
    botao.addEventListener('click', () => responder(botao.dataset.letra));
  }

  // Atalho: teclas A, B, C e D respondem a pergunta aberta.
  document.addEventListener('keydown', (tecla) => {
    const letra = tecla.key.toUpperCase();
    const botao = document.querySelector('.opcao[data-letra="' + letra + '"]');
    const digitando = tecla.target instanceof HTMLInputElement;
    if (estado.tela === 'pergunta' && botao && !botao.disabled && !digitando &&
        !tecla.ctrlKey && !tecla.altKey && !tecla.metaKey) {
      responder(letra);
    }
  });
}

// ---------------------------------------------------------------------------
// Início
// ---------------------------------------------------------------------------

function reiniciar() {
  Object.assign(estado, estadoInicial());
  $('protocolo-linhas').replaceChildren();
  $('protocolo-contagem').textContent = '';
  mostrarTela('entrada');
}

function conectarAoCliente() {
  const fonte = new EventSource('/eventos');
  // A cada (re)conexão o cliente.py reenvia o histórico recente, então a tela
  // é reconstruída do zero a partir dele.
  fonte.onopen = reiniciar;
  fonte.onmessage = (mensagem) => tratarEvento(JSON.parse(mensagem.data));
  fonte.onerror = () => {
    if (!estado.encerrado) {
      mostrarQueda('Cliente fechado',
        'O cliente.py desta máquina não está respondendo. Inicie-o de novo e recarregue a página.');
    }
  };
}

Object.assign(estado, estadoInicial());
ligarControles();
setInterval(atualizarTempo, 100);
conectarAoCliente();
