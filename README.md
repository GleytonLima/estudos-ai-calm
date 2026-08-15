# Laboratório do Canal Universal

Protótipo mínimo da arquitetura "perguntas + command generation" (estilo Rasa
CALM): o LLM **interpreta** e emite comandos; a plataforma valida, conduz o
fluxo e é a única que executa. O state da sessão é o conjunto de **perguntas
abertas** — clique responde por `pergunta_id`, webhook responde por `token`,
texto livre vai ao intérprete.

## Rodar

```
pip install -r requirements.txt
python -m uvicorn server:app --port 8123
```

Abra http://localhost:8123.

O cabeçalho mostra qual intérprete está ativo. A cadeia é, nesta ordem:

1. **`anthropic:<modelo>`** — se houver `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`
   ou `CHAVE_CLAUDE_TESTE` no ambiente. Modelo via `PROTO_MODEL` (default
   `claude-opus-5`), com structured outputs garantindo o JSON dos comandos.
2. **`lmstudio:<modelo>`** — se o LM Studio estiver servindo em
   `localhost:1234`. Sondado a cada 10s: abrir o LM Studio no meio da conversa
   troca o modo sem reiniciar o servidor.
3. **`deterministico`** — regex + funil, sem LLM: a arquitetura inteira funciona
   offline, e é para cá que a chamada cai se o LLM falhar.

No Windows, com a chave numa variável de usuário, o processo precisa herdá-la:

```powershell
$env:CHAVE_CLAUDE_TESTE = [Environment]::GetEnvironmentVariable('CHAVE_CLAUDE_TESTE','User')
python -m uvicorn server:app --port 8123
```

## O fluxo canônico para testar

1. "Olá" → conversa livre (state vazio)
2. "Quero gerar um boleto" → funil empata PF × CNPJ → **opções** (+ "Não é isso")
3. Clique em "Emitir boleto PF" → **formulário** dos campos
4. "CPF é 529.982.247-25, valor 5 mil, vencimento 22/01/2027" → o intérprete
   extrai; o código valida (tente um CPF inválido: `79012345612`)
5. Cartão **Aprovar / Editar / Reprovar** — Editar corrige e reconfirma
6. Aprovado → despacho ao "parceiro"; a pergunta EXTERNA fica aberta
7. ~6s depois (ou botão "Simular parceiro agora" no painel) → webhook resolve →
   **mensagem proativa** com o link do boleto

O painel direito mostra as perguntas abertas, a operação ativa e os comandos
crus que o intérprete emitiu — é o laboratório da tese.

## A política de affordance

Quantos campos faltam decide **a forma de pedir** — e quem decide é o motor,
nunca o modelo (`LIMIAR_COLETA` em `motor.py`):

| Pendências | Forma | Por quê |
|---|---|---|
| ≤ 2 | **coleta**: pergunta falada, um campo por vez, respondida na conversa | um formulário inteiro para 1 campo é burocracia |
| > 2 | **formulário** pré-preenchido | 8 turnos de chat é onde o operador desiste |

A pergunta de cada slot é **declarada no catálogo** (`"pergunta"`), como o
`utter_ask_<slot>` do Rasa: a plataforma escolhe *qual* campo cobrar, o modelo
só interpreta a resposta. Com uma coleta aberta o texto livre tem alvo
conhecido — se já valida no formato do campo, é preenchido **sem chamar LLM**
(aparece no painel como `[direto]`).

Teste com `cadastrar_fornecedor` (10 campos): informe alguns na fala, veja o
formulário se **atualizar no lugar** a cada envio e virar pergunta falada
quando sobrarem 2.

## O intérprete não vê o catálogo

Ele vê **as candidatas que o funil trouxe** — nunca a lista completa. Por isso
o prompt proíbe afirmar ausência: "não temos empréstimo" é uma frase que o
modelo não tem como saber se é verdade, e que ensina o operador a desistir de
algo que talvez exista. O certo é *"não encontrei com essas palavras, me diz
de outro jeito"*.

Isso apareceu num caso real: "Quero um emprestimo para o cliente X" trouxe só
capacidades de boleto, porque `para` e `quero` pesavam igual a `emprestimo` e
o empate foi cortado pelo `k=3` por ordem de dicionário. Três consertos no
funil: **stopwords** pt-BR (o que o FTS `portuguese` faz de graça no projeto
real), **IDF** sobre o catálogo, e **k como piso** — nunca cortar no meio de
um empate.

## Slots que não se digitam

Nem todo dado vem da fala. `gerar_proposta_emprestimo` tem dois slots com
`origem`: a plataforma **busca** e o operador **escolhe**.

- `conta` (`formato: escolha`) — busca as contas da empresa e abre uma pergunta
  de opção única.
- `avalistas` (`formato: conjunto`) — busca os sócios e abre seleção múltipla
  com `regra: {campo: percentual, soma_minima: 50}`. O total corre na tela, mas
  quem barra é o código: com 35% a pergunta continua aberta.

Slot de origem **nunca** entra no formulário, o intérprete **não pode**
preenchê-lo (se tentar, o operador é avisado) e ele não vira campo de texto no
"Editar" do cartão. Escolher uma conta real é ato do operador, não do modelo.

### O que a busca devolve decide o que acontece

| Resultado | Comportamento |
|---|---|
| **nada**, ou regra inatingível (tudo somado < mínimo) | **impasse**: "Corrigir CNPJ" ou "Cancelar". Não inventa, não segue, não cancela sozinho |
| **uma opção** | preenche e **avisa** — pedir um clique numa lista de um item é teatro; o cartão de confirmação segue sendo o controle |
| **duas ou mais** | pergunta, como sempre |

E `depende` amarra a invalidação: mudar o CNPJ (pelo impasse ou pelo "Editar"
do cartão) **derruba** a conta e os avalistas e refaz as buscas. Sem isso, o
cartão mostraria a conta de outra empresa — plausível, coerente e errada.

CNPJs de teste: `11.222.333/0001-81` (3 contas, 4 sócios), `22.333.444/0001-81`
(conta e sócio únicos), `33.444.555/0001-81` (sócios somam 40%),
`44.555.666/0001-81` (sem conta).

## Mapa

| Arquivo | Papel |
|---|---|
| `motor.py` | roteador de respostas + perguntas abertas (o "grafo") |
| `interprete.py` | LLM como consultor: comandos `iniciar/preencher/desambiguar/responder/cancelar` |
| `catalogo.py` | capacidades mock + mini-funil (top-k, recusadas descem) + origens de busca |
| `formats.py` | validação determinística (CPF/CNPJ/moeda/data) + máscaras |
| `server.py` | HTTP + SSE + webhook do parceiro |
| `static/index.html` | chat + painel de laboratório |
| `testes/` | roteiros dos experimentos, sem LLM (forçam o modo determinístico) |

Os testes rodam offline e são o registro de cada cenário estressado:

```
python testes/teste_affordance.py    # formulário × pergunta falada, 10 campos
python testes/teste_proposta.py      # slots de busca + regra dos 50%
python testes/teste_buscas.py        # busca vazia, opção única, dado derivado obsoleto
python testes/regressao_boleto.py    # o fluxo canônico ponta a ponta
python testes/diag_funil.py          # por que o funil escolheu o que escolheu
```
