"""O intérprete: o LLM como CONSULTOR, nunca como orquestrador (estilo CALM).

A plataforma pergunta "o que a pessoa quer?" e o modelo responde com COMANDOS
estruturados — ele não chama tools, não conduz fluxo, não decide ordem. Tudo
que ele emite passa pela validação determinística antes de virar fato.

Dois modos:
- anthropic  — Claude via structured outputs (garante o JSON dos comandos)
- deterministico — regex + funil, sem LLM: prova a arquitetura offline
"""

from __future__ import annotations

import os
import re
import time

import formats
from catalogo import CAPACIDADES, funil

MODELO = os.environ.get("PROTO_MODEL", "claude-opus-5")
LMSTUDIO_URL = os.environ.get("LMSTUDIO_URL", "http://localhost:1234")

# A chave pode vir com nome próprio — este é um laboratório, não produção.
_NOMES_DE_CHAVE = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CHAVE_CLAUDE_TESTE")

_client = None
_lmstudio_cache: tuple[float, str | None] = (0.0, None)


def _chave() -> str | None:
    for nome in _NOMES_DE_CHAVE:
        valor = os.environ.get(nome)
        if valor:
            return valor
    return None


def _lmstudio_modelo() -> str | None:
    """Modelo carregado no LM Studio, com sondagem em cache (10s).

    A sondagem é barata e com TTL curto de propósito: abrir o LM Studio no
    meio da conversa liga o modo sem reiniciar o servidor.
    """
    global _lmstudio_cache
    agora = time.monotonic()
    if agora - _lmstudio_cache[0] < 10:
        return _lmstudio_cache[1]
    modelo = None
    try:
        import requests

        resposta = requests.get(f"{LMSTUDIO_URL}/v1/models", timeout=1)
        dados = resposta.json().get("data") or []
        if dados:
            modelo = dados[0]["id"]
    except Exception:
        modelo = None
    _lmstudio_cache = (agora, modelo)
    return modelo


def modo() -> str:
    if _chave():
        return f"anthropic:{MODELO}"
    local = _lmstudio_modelo()
    if local:
        return f"lmstudio:{local}"
    return "deterministico"


def _get_client():
    global _client
    if _client is None:
        import anthropic

        _client = anthropic.Anthropic(api_key=_chave())
    return _client


# Args como pares campo/valor: structured outputs exige additionalProperties
# false em todo objeto, então um mapa livre não cabe no schema.
_SCHEMA = {
    "type": "object",
    "properties": {
        "comandos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "tipo": {"type": "string",
                             "enum": ["iniciar", "preencher", "desambiguar", "responder", "cancelar"]},
                    "capacidade": {"type": ["string", "null"]},
                    "candidatas": {"type": "array", "items": {"type": "string"}},
                    "args": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"campo": {"type": "string"}, "valor": {"type": "string"}},
                            "required": ["campo", "valor"],
                            "additionalProperties": False,
                        },
                    },
                    "texto": {"type": ["string", "null"]},
                },
                "required": ["tipo"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["comandos"],
    "additionalProperties": False,
}

_SISTEMA = """\
Você é o INTÉRPRETE de uma plataforma de operações conversacionais. Você NUNCA
executa nada: você lê a conversa e devolve comandos que a plataforma aplica.
A plataforma valida todo valor com código (CPF, moeda, data) — você extrai,
ela confere.

Comandos disponíveis:
- iniciar   {capacidade, args}: o operador quer UMA capacidade candidata. Só
  use nomes da lista de candidatas. Extraia nos args tudo que já estiver na
  fala (normalize números: "5 mil" -> "5000"; datas podem ir como dd/mm/aaaa).
- preencher {args}: há uma capacidade ativa coletando dados e a fala traz
  valores para os campos pendentes.
- desambiguar {candidatas}: mais de uma candidata serve e a diferença importa.
- responder {texto}: fala curta ao operador (cumprimento, dúvida, explicação,
  ou quando nenhuma candidata serve — diga que NÃO ENCONTROU e peça outras
  palavras).
- cancelar: o operador desistiu da operação ativa.

Regras: nunca invente capacidade fora das candidatas; nunca afirme que algo
foi executado; não peça confirmação em texto — a plataforma tem cartão próprio
de confirmação; seja breve e direto, em pt-BR.

IMPORTANTE — o que você NÃO sabe: as candidatas são o resultado de uma BUSCA,
não o catálogo. Existem outras capacidades que você não está vendo. Então
NUNCA afirme que a plataforma não faz algo, que uma capacidade não existe ou
que "o que temos é X, Y, Z". Quando nada casa, diga que não encontrou com
aquelas palavras e peça para reformular — a busca pode ter falhado, e dizer
"não temos" ensina o operador a desistir de algo que talvez exista."""


def _contexto_texto(contexto: dict) -> str:
    partes = []
    if contexto.get("historico"):
        partes.append("Conversa recente:\n" + "\n".join(contexto["historico"][-12:]))
    ativa = contexto.get("ativa")
    if ativa:
        cap = CAPACIDADES[ativa["capacidade"]]
        pendentes = ", ".join(ativa["pendentes"]) or "nenhum"
        partes.append(
            f"Situação: coletando dados para '{cap['display']}' "
            f"({ativa['capacidade']}). Campos pendentes: {pendentes}. "
            f"Já preenchidos: {ativa['args'] or '{}'}."
        )
        if ativa.get("perguntando"):
            partes.append(
                f"A plataforma ACABOU DE PERGUNTAR o campo '{ativa['perguntando']}'. "
                "A fala do operador é, muito provavelmente, a resposta desse campo — "
                "emita preencher com ela. Só fuja disso se ele claramente mudou de "
                "assunto, corrigiu outro campo ou desistiu."
            )
    else:
        partes.append("Situação: conversa livre, nenhuma operação ativa.")
    candidatas = contexto.get("candidatas") or []
    if candidatas:
        linhas = []
        for c in candidatas:
            cap = CAPACIDADES[c["nome"]]
            slots = ", ".join(f"{s['nome']}({s['formato']})" for s in cap["slots"])
            linhas.append(f"- {c['nome']}: {cap['descricao']} Campos: {slots}. (score {c['score']})")
        partes.append("Capacidades candidatas (funil):\n" + "\n".join(linhas))
    else:
        partes.append("Capacidades candidatas: nenhuma (o funil não encontrou nada).")
    if contexto.get("recusadas"):
        partes.append(
            "O operador já respondeu 'não é isso' para: "
            + ", ".join(sorted(contexto["recusadas"]))
            + ". Não as ofereça de novo; pergunte o que ele precisa."
        )
    if contexto.get("resultado"):
        partes.append(
            "Uma leitura acabou de executar e a tela JÁ MOSTRA o resultado ao "
            f"operador: {contexto['resultado']}. Comente em UMA frase o que "
            "importa (comando responder) — não repita os dados."
        )
    partes.append(f"Fala do operador: {contexto.get('texto', '')!r}")
    return "\n\n".join(partes)


def interpretar(contexto: dict) -> dict:
    """Devolve {"comandos": [...], "modo": ..., "erro"?: ...}."""
    atual = modo()
    if atual == "deterministico":
        return _interpretar_deterministico(contexto)
    try:
        if atual.startswith("lmstudio:"):
            return _interpretar_lmstudio(contexto, atual.split(":", 1)[1])
        return _interpretar_anthropic(contexto)
    except Exception as exc:  # rede, chave inválida — a arquitetura não cai
        resultado = _interpretar_deterministico(contexto)
        resultado["erro"] = f"LLM indisponível ({type(exc).__name__}); usei o determinístico."
        return resultado


def _extrair_json(texto: str) -> dict:
    """Primeiro objeto JSON balanceado do texto — modelos locais enfeitam."""
    import json

    inicio = texto.find("{")
    if inicio < 0:
        raise ValueError("sem JSON na resposta")
    profundidade = 0
    em_string, escape = False, False
    for i, c in enumerate(texto[inicio:], start=inicio):
        if em_string:
            em_string = not (c == '"' and not escape)
            escape = c == "\\" and not escape
            continue
        if c == '"':
            em_string = True
        elif c == "{":
            profundidade += 1
        elif c == "}":
            profundidade -= 1
            if profundidade == 0:
                return json.loads(texto[inicio:i + 1])
    raise ValueError("JSON incompleto")


def _interpretar_lmstudio(contexto: dict, modelo_local: str) -> dict:
    """LM Studio via API OpenAI-compatible. Sem structured outputs garantidos:
    pede JSON no prompt e extrai com leniência — modelo local é imprevisível,
    e é exatamente por isso que a validação determinística fica DEPOIS dele."""
    import requests

    sistema = _SISTEMA + (
        '\n\nResponda APENAS com um objeto JSON no formato '
        '{"comandos": [{"tipo": ..., ...}]} — sem markdown, sem explicação.'
    )
    resposta = requests.post(
        f"{LMSTUDIO_URL}/v1/chat/completions",
        json={
            "model": modelo_local,
            "messages": [
                {"role": "system", "content": sistema},
                {"role": "user", "content": _contexto_texto(contexto)},
            ],
            "temperature": 0.2,
            "max_tokens": 700,
        },
        timeout=60,
    )
    resposta.raise_for_status()
    texto = resposta.json()["choices"][0]["message"]["content"]
    dados = _extrair_json(texto)
    comandos = dados.get("comandos")
    if not isinstance(comandos, list):
        raise ValueError("resposta sem lista de comandos")
    validos = []
    for comando in comandos:
        if not isinstance(comando, dict):
            continue
        if comando.get("tipo") not in ("iniciar", "preencher", "desambiguar",
                                       "responder", "cancelar"):
            continue
        if isinstance(comando.get("args"), list):  # aceita o formato campo/valor
            comando["args"] = {p.get("campo"): p.get("valor")
                               for p in comando["args"] if isinstance(p, dict)}
        validos.append(comando)
    if not validos:
        raise ValueError("nenhum comando válido")
    return {"comandos": validos, "modo": f"lmstudio:{modelo_local}"}


def _interpretar_anthropic(contexto: dict) -> dict:
    import json

    client = _get_client()
    resposta = client.messages.create(
        model=MODELO,
        max_tokens=1024,
        system=_SISTEMA,
        output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
        messages=[{"role": "user", "content": _contexto_texto(contexto)}],
    )
    if resposta.stop_reason == "refusal":
        return {"comandos": [{"tipo": "responder", "texto": "Não posso ajudar com isso."}],
                "modo": modo()}
    texto = next(b.text for b in resposta.content if b.type == "text")
    dados = json.loads(texto)
    for comando in dados.get("comandos", []):
        if isinstance(comando.get("args"), list):
            comando["args"] = {p["campo"]: p["valor"] for p in comando["args"]}
    dados["modo"] = modo()
    return dados


# ------------------------------------------------ intérprete determinístico


def _extrair_args(texto: str, capacidade: str) -> dict:
    """Extração por regex, por formato de slot — o CALM de bolso."""
    args: dict = {}
    numeros = re.findall(r"\d[\d./,-]*\d|\d", texto)
    datas = re.findall(r"\b(\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2})\b", texto)
    for slot in CAPACIDADES[capacidade]["slots"]:
        formato, nome = slot["formato"], slot["nome"]
        # 'texto' só entra pela heurística de documento: sem isso, um cadastro
        # com 8 campos livres receberia o mesmo número em todos eles.
        documental = formato == "texto" and "document" in nome
        if formato in ("cpf", "cnpj") or documental:
            alvo = 11 if formato == "cpf" else 14
            for n in numeros:
                d = formats.digitos(n)
                if (documental and len(d) in (11, 14)) or len(d) == alvo:
                    args[nome] = d
                    break
        elif formato == "data_futura":
            if datas:
                args[nome] = datas[0]
        elif formato == "moeda":
            m = re.search(r"(\d+(?:[.,]\d+)?)\s*(mil|k)\b", texto, re.I)
            if m:
                args[nome] = f"{m.group(1)} mil"
            else:
                candidatos = [n for n in numeros
                              if formats.digitos(n) not in {formats.digitos(d) for d in datas}
                              and len(formats.digitos(n)) not in (11, 14)]
                if candidatos:
                    args[nome] = candidatos[0]
        elif formato == "inteiro":
            m = re.search(r"(\d+)\s*(meses|x)\b", texto, re.I)
            if m:
                args[nome] = m.group(1)
    return args


def _interpretar_deterministico(contexto: dict) -> dict:
    texto = contexto.get("texto", "")
    ativa = contexto.get("ativa")

    if ativa:
        if re.search(r"\b(cancela|desist|deixa pra la)\w*", texto, re.I):
            return {"comandos": [{"tipo": "cancelar"}], "modo": "deterministico"}
        args = _extrair_args(texto, ativa["capacidade"])
        pendentes = set(ativa["pendentes"])
        uteis = {k: v for k, v in args.items() if k in pendentes}
        if uteis:
            return {"comandos": [{"tipo": "preencher", "args": uteis}], "modo": "deterministico"}
        # Pergunta específica aberta: a fala inteira é a resposta candidata.
        # É o que faz um slot de texto livre funcionar sem LLM nenhum.
        alvo = ativa.get("perguntando")
        if alvo and texto.strip():
            return {"comandos": [{"tipo": "preencher", "args": {alvo: texto.strip()}}],
                    "modo": "deterministico"}
        return {"comandos": [{"tipo": "responder",
                              "texto": "Não reconheci os dados. Preencha o formulário acima ou informe os campos pendentes."}],
                "modo": "deterministico"}

    candidatas = contexto.get("candidatas") or []
    if not candidatas:
        return {"comandos": [{"tipo": "responder",
                              "texto": "Posso ajudar com: boletos (PF/CNPJ), consulta de cliente, "
                                       "lista de boletos e simulação de empréstimo. O que você precisa?"}],
                "modo": "deterministico"}
    # Empate no topo → devolver a escolha ao operador (nunca chutar).
    if len(candidatas) > 1 and candidatas[0]["score"] == candidatas[1]["score"]:
        empatadas = [c["nome"] for c in candidatas if c["score"] == candidatas[0]["score"]]
        return {"comandos": [{"tipo": "desambiguar", "candidatas": empatadas}],
                "modo": "deterministico"}
    vencedora = candidatas[0]["nome"]
    return {"comandos": [{"tipo": "iniciar", "capacidade": vencedora,
                          "args": _extrair_args(texto, vencedora)}],
            "modo": "deterministico"}
