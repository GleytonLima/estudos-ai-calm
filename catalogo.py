"""Catálogo mock de capacidades + mini-funil.

No projeto real isto é o registry (pgvector + FTS). Aqui, um dicionário e um
score por sobreposição de palavras — o suficiente para testar a arquitetura:
o intérprete NUNCA vê o catálogo inteiro, só as candidatas do funil.
"""

from __future__ import annotations

import math
import re
import unicodedata
import uuid
from collections import Counter

# Entrega declarada pela capacidade: o motor não sabe o que é um boleto.
BOLETO = {"url": "/boleto/{protocolo}", "rotulo": "Boleto {protocolo} (PDF)"}

CAPACIDADES: dict[str, dict] = {
    "emitir_boleto_pf": {
        "display": "Emitir boleto PF",
        "descricao": "Emite um boleto de cobrança para pessoa física (CPF).",
        "risco": "escrita",
        "espera_externa": True,
        "protocolo_prefixo": "BLT",
        "fala_concluida": "O boleto foi gerado! Segue o link para visualizar/baixar.",
        "entrega": BOLETO,
        "slots": [
            {"nome": "cpf", "rotulo": "CPF", "formato": "cpf",
             "pergunta": "Qual o CPF do sacado?"},
            {"nome": "valor", "rotulo": "Valor", "formato": "moeda",
             "pergunta": "De quanto é o boleto?"},
            {"nome": "vencimento", "rotulo": "Vencimento", "formato": "data_futura",
             "pergunta": "Para quando é o vencimento?"},
        ],
        "exemplos": ["gerar boleto", "emitir boleto pessoa fisica", "boleto cpf",
                     "quero gerar um boleto", "cobrar pessoa fisica"],
    },
    "emitir_boleto_cnpj": {
        "display": "Emitir boleto CNPJ",
        "descricao": "Emite um boleto de cobrança para pessoa jurídica (CNPJ).",
        "risco": "escrita",
        "espera_externa": True,
        "protocolo_prefixo": "BLT",
        "fala_concluida": "O boleto foi gerado! Segue o link para visualizar/baixar.",
        "entrega": BOLETO,
        "slots": [
            {"nome": "cnpj", "rotulo": "CNPJ", "formato": "cnpj",
             "pergunta": "Qual o CNPJ da empresa?"},
            {"nome": "valor", "rotulo": "Valor", "formato": "moeda",
             "pergunta": "De quanto é o boleto?"},
            {"nome": "vencimento", "rotulo": "Vencimento", "formato": "data_futura",
             "pergunta": "Para quando é o vencimento?"},
        ],
        "exemplos": ["gerar boleto", "emitir boleto empresa", "boleto cnpj",
                     "quero gerar um boleto", "cobrar empresa"],
    },
    "consultar_cliente": {
        "display": "Consultar cliente",
        "descricao": "Consulta o cadastro de um cliente pelo CPF ou CNPJ.",
        "risco": "leitura",
        "espera_externa": False,
        "slots": [{"nome": "documento", "rotulo": "CPF ou CNPJ", "formato": "texto"}],
        "exemplos": ["consultar cliente", "dados do cliente", "cadastro do cliente",
                     "quem e o cliente"],
    },
    "listar_boletos": {
        "display": "Listar boletos",
        "descricao": "Lista os boletos já emitidos para um cliente.",
        "risco": "leitura",
        "espera_externa": False,
        "slots": [{"nome": "documento", "rotulo": "CPF ou CNPJ", "formato": "texto"}],
        "exemplos": ["listar boletos", "boletos do cliente", "boletos emitidos",
                     "segunda via"],
    },
    "simular_emprestimo": {
        "display": "Simular empréstimo",
        "descricao": "Simula um empréstimo com valor e prazo em meses.",
        "risco": "leitura",
        "espera_externa": False,
        "slots": [
            {"nome": "valor", "rotulo": "Valor", "formato": "moeda"},
            {"nome": "prazo_meses", "rotulo": "Prazo (meses)", "formato": "inteiro"},
        ],
        "exemplos": ["simular emprestimo", "simulacao de credito", "quanto fica um emprestimo"],
    },
    # Capacidade "longa": existe para testar a política de affordance — com 10
    # campos, perguntar um a um seriam 10 turnos; com 1 faltando, um formulário
    # inteiro seria burocracia. Quem escolhe a forma é o motor.
    "cadastrar_fornecedor": {
        "display": "Cadastrar fornecedor",
        "descricao": "Cadastra um novo fornecedor com dados cadastrais e bancários.",
        "risco": "escrita",
        "espera_externa": True,
        "protocolo_prefixo": "FRN",
        "fala_concluida": "Fornecedor cadastrado! O cadastro já está ativo.",
        "slots": [
            {"nome": "cnpj", "rotulo": "CNPJ", "formato": "cnpj",
             "pergunta": "Qual o CNPJ do fornecedor?"},
            {"nome": "razao_social", "rotulo": "Razão social", "formato": "texto",
             "pergunta": "Qual a razão social?"},
            {"nome": "nome_fantasia", "rotulo": "Nome fantasia", "formato": "texto",
             "pergunta": "E o nome fantasia?"},
            {"nome": "email", "rotulo": "E-mail", "formato": "texto",
             "pergunta": "Qual o e-mail de contato?"},
            {"nome": "telefone", "rotulo": "Telefone", "formato": "texto",
             "pergunta": "Qual o telefone?"},
            {"nome": "cep", "rotulo": "CEP", "formato": "texto",
             "pergunta": "Qual o CEP?"},
            {"nome": "cidade", "rotulo": "Cidade", "formato": "texto",
             "pergunta": "Em qual cidade?"},
            {"nome": "banco", "rotulo": "Banco", "formato": "texto",
             "pergunta": "Qual o banco para pagamento?"},
            {"nome": "agencia", "rotulo": "Agência", "formato": "texto",
             "pergunta": "Qual a agência?"},
            {"nome": "conta", "rotulo": "Conta", "formato": "texto",
             "pergunta": "E o número da conta?"},
        ],
        "exemplos": ["cadastrar fornecedor", "novo fornecedor", "incluir fornecedor",
                     "cadastro de fornecedor", "abrir cadastro de parceiro"],
    },
    # A capacidade de estresse: tem slots que NÃO se digitam nem se extraem da
    # fala — saem de uma busca e o operador escolhe. E um deles é um CONJUNTO
    # com regra de acumulação (soma ≥ 50%). O modelo não pode preencher nenhum
    # dos dois: escolher conta e avalista é ato do operador, não do intérprete.
    "gerar_proposta_emprestimo": {
        "display": "Gerar proposta de empréstimo PJ",
        "descricao": "Gera proposta de empréstimo para empresa, com devedores "
                     "solidários, para assinatura eletrônica.",
        "risco": "escrita",
        "espera_externa": True,
        "protocolo_prefixo": "PRP",
        "fala_concluida": "A proposta foi assinada pelo sistema externo! "
                          "Segue o documento.",
        "entrega": {"url": "/proposta/{protocolo}",
                    "rotulo": "Proposta {protocolo} (PDF assinado)"},
        "slots": [
            {"nome": "cnpj", "rotulo": "CNPJ", "formato": "cnpj",
             "pergunta": "Qual o CNPJ da empresa?"},
            {"nome": "valor", "rotulo": "Valor", "formato": "moeda",
             "pergunta": "Qual o valor do empréstimo?"},
            {"nome": "primeira_parcela", "rotulo": "1ª parcela",
             "formato": "data_futura",
             "pergunta": "Quando vence a primeira parcela?"},
            {"nome": "parcelas", "rotulo": "Parcelas", "formato": "inteiro",
             "pergunta": "Em quantas parcelas?"},
            # `depende` é o que a busca consome — e o que o operador pode
            # refazer quando a busca não devolve nada aproveitável.
            {"nome": "conta", "rotulo": "Conta de crédito", "formato": "escolha",
             "origem": "contas_da_empresa", "depende": ["cnpj"],
             "pergunta": "Achei estas contas da empresa. Em qual creditar?"},
            {"nome": "avalistas", "rotulo": "Devedores solidários",
             "formato": "conjunto", "origem": "socios_da_empresa",
             "depende": ["cnpj"],
             "pergunta": "Quem entra como devedor solidário? "
                         "A soma das participações precisa fechar 50% ou mais.",
             "regra": {"campo": "percentual", "soma_minima": 50,
                       "unidade": "%"}},
        ],
        "exemplos": ["proposta de emprestimo", "emprestimo para empresa",
                     "emprestimo pj com avalista", "proposta de credito empresa",
                     "gerar proposta emprestimo cnpj", "devedor solidario"],
    },
}


# ------------------------------------------------- origens (busca de opções)
#
# No projeto real cada uma destas é uma chamada MCP ao sistema do time dono.
# Aqui, mock — o que importa é o CONTRATO: recebe o que já foi coletado e
# devolve opções que o operador escolhe.

_CONTAS = {
    # Aurora: o caso feliz — três contas.
    "11222333000181": [
        {"id": "cc-4471", "rotulo": "Conta corrente 4471-2",
         "descricao": "Ag. 0001 · saldo R$ 82.400,00"},
        {"id": "cc-9930", "rotulo": "Conta corrente 9930-8",
         "descricao": "Ag. 0044 · saldo R$ 5.120,00"},
        {"id": "cx-1180", "rotulo": "Conta caução 1180-0",
         "descricao": "Ag. 0001 · vinculada a garantias"},
    ],
    # Boreal: conta única — não há decisão a tomar.
    "22333444000181": [
        {"id": "cc-2210", "rotulo": "Conta corrente 2210-6",
         "descricao": "Ag. 0007 · saldo R$ 14.900,00"},
    ],
    # Cerrado: sócios de menos — a busca de contas vai bem, a de sócios não.
    "33444555000181": [
        {"id": "cc-7788", "rotulo": "Conta corrente 7788-1",
         "descricao": "Ag. 0012 · saldo R$ 240.000,00"},
    ],
    # Dunas: empresa sem conta cadastrada — busca vazia.
    "44555666000181": [],
}

_SOCIOS = {
    "11222333000181": [
        {"id": "soc-1", "rotulo": "Ana Ribeiro", "percentual": 35,
         "descricao": "Sócia-administradora · CPF 529.982.247-25"},
        {"id": "soc-2", "rotulo": "Diego Alves", "percentual": 30,
         "descricao": "Sócio · CPF 168.995.350-09"},
        {"id": "soc-3", "rotulo": "Bruno Sales", "percentual": 20,
         "descricao": "Sócio · CPF 111.444.777-35"},
        {"id": "soc-4", "rotulo": "Carla Nunes", "percentual": 15,
         "descricao": "Sócia · CPF 390.533.447-05"},
    ],
    "22333444000181": [
        {"id": "soc-5", "rotulo": "Helena Prado", "percentual": 100,
         "descricao": "Sócia única · CPF 529.982.247-25"},
    ],
    # Só 40% de participação cadastrada: a regra dos 50% é INATINGÍVEL.
    "33444555000181": [
        {"id": "soc-6", "rotulo": "Ivo Martins", "percentual": 25,
         "descricao": "Sócio · CPF 168.995.350-09"},
        {"id": "soc-7", "rotulo": "Lia Moraes", "percentual": 15,
         "descricao": "Sócia · CPF 111.444.777-35"},
    ],
    "44555666000181": [],
}


def _contas_da_empresa(args: dict) -> list[dict]:
    return _CONTAS.get(_so_digitos(args.get("cnpj")), [])


def _socios_da_empresa(args: dict) -> list[dict]:
    return _SOCIOS.get(_so_digitos(args.get("cnpj")), [])


def _so_digitos(valor: object) -> str:
    return re.sub(r"\D", "", str(valor or ""))


ORIGENS = {
    "contas_da_empresa": _contas_da_empresa,
    "socios_da_empresa": _socios_da_empresa,
}


def buscar(origem: str, args: dict) -> list[dict]:
    return ORIGENS[origem](args)


# Palavras de intenção e ligação: em pt-BR aparecem em qualquer pedido e não
# distinguem capacidade nenhuma. No projeto real o FTS ('portuguese') faz isso
# de graça; aqui é lista mesmo. Sem elas, "quero" e "para" empatavam boleto
# com empréstimo e a capacidade certa era cortada pelo top-k.
_VAZIAS = {
    "quero", "queria", "gostaria", "preciso", "pode", "poderia", "consigo",
    "para", "pra", "por", "com", "sem", "dos", "das", "uma", "uns", "umas",
    "meu", "minha", "esse", "essa", "isso", "aqui", "favor", "fazer", "faz",
    "tem", "ter", "que", "qual", "quais", "onde", "algum", "alguma", "mesmo",
}

_PESOS: dict[str, float] | None = None


def _tokens(texto: str, uteis: bool = True) -> set[str]:
    sem_acento = "".join(
        c for c in unicodedata.normalize("NFD", texto.lower())
        if unicodedata.category(c) != "Mn"
    )
    brutos = {t for t in re.findall(r"[a-z0-9]+", sem_acento) if len(t) >= 3}
    return brutos - _VAZIAS if uteis else brutos


def _alvo(cap: dict) -> set[str]:
    return _tokens(cap["descricao"] + " " + " ".join(cap["exemplos"]) + " "
                   + cap["display"])


def _pesos() -> dict[str, float]:
    """IDF sobre o catálogo: termo que está em toda capacidade não informa."""
    global _PESOS
    if _PESOS is None:
        total = len(CAPACIDADES)
        frequencia: Counter[str] = Counter()
        for cap in CAPACIDADES.values():
            frequencia.update(_alvo(cap))
        _PESOS = {t: math.log(1 + total / f) for t, f in frequencia.items()}
    return _PESOS


def funil(texto: str, recusadas: set[str] | None = None, k: int = 3,
          teto: int = 6) -> list[dict]:
    """Candidatas por sobreposição ponderada. Recusadas descem (não somem)."""
    consulta = _tokens(texto)
    if not consulta:
        return []
    pesos = _pesos()
    pontuadas = []
    for nome, cap in CAPACIDADES.items():
        score = sum(pesos.get(t, 1.0) for t in consulta & _alvo(cap))
        if recusadas and nome in recusadas:
            score -= 2  # reforço negativo: desce, mas continua alcançável
        if score > 0:
            pontuadas.append({"nome": nome, "score": round(score, 2),
                              "display": cap["display"],
                              "descricao": cap["descricao"]})
    pontuadas.sort(key=lambda c: -c["score"])
    # k é PISO, não corte cego: cortar no meio de um empate escolhe candidata
    # por ordem de dicionário — foi exatamente assim que "proposta de
    # empréstimo" sumiu de um pedido de empréstimo.
    corte = min(k, len(pontuadas))
    while corte < min(len(pontuadas), teto) and \
            pontuadas[corte]["score"] == pontuadas[corte - 1]["score"]:
        corte += 1
    return pontuadas[:corte]


# ------------------------------------------------------------------ execução


def executar(nome: str, args: dict) -> dict:
    """Executa (mock) uma capacidade de LEITURA e devolve o payload."""
    if nome == "consultar_cliente":
        doc = str(args.get("documento", ""))
        return {"tipo": "ficha", "titulo": "Cliente encontrado",
                "dados": {"Documento": doc, "Nome": "Maria Exemplo LTDA" if len(doc) > 11 else "Maria Exemplo",
                          "Situação": "Ativa", "Limite": "R$ 25.000,00"}}
    if nome == "listar_boletos":
        return {"tipo": "tabela", "titulo": "Boletos do cliente",
                "colunas": ["Vencimento", "Valor", "Situação"],
                "linhas": [["10/07/2026", "R$ 1.200,00", "Pago"],
                           ["10/08/2026", "R$ 1.200,00", "Em aberto"]]}
    if nome == "simular_emprestimo":
        valor = float(args.get("valor", 0) or 0)
        meses = int(args.get("prazo_meses", 12) or 12)
        parcela = valor * 1.018 ** meses / meses if meses else 0
        return {"tipo": "ficha", "titulo": "Simulação",
                "dados": {"Valor": f"R$ {valor:,.2f}", "Prazo": f"{meses} meses",
                          "Taxa": "1,8% a.m.", "Parcela estimada": f"R$ {parcela:,.2f}"}}
    return {"tipo": "ficha", "titulo": nome, "dados": dict(args)}


def despachar_externo(nome: str, args: dict) -> str:
    """Despacha uma escrita para o 'parceiro externo' (mock). Devolve protocolo."""
    prefixo = CAPACIDADES.get(nome, {}).get("protocolo_prefixo", "OPR")
    return f"{prefixo}-{uuid.uuid4().hex[:8].upper()}"
