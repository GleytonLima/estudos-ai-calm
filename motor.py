"""O motor: state = conjunto de perguntas abertas.

O roteador de entrada não pergunta "em que estado estou?" — pergunta "esta
entrada responde a qual pergunta aberta?". Clique carrega pergunta_id, webhook
carrega token, texto livre não responde a nada e vai ao intérprete. O modelo é
uma PARTE consultada; quem conduz é a plataforma.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field

import formats
import interprete
from catalogo import CAPACIDADES, buscar, despachar_externo, executar, funil

ESPERA_PARCEIRO_S = 6  # o "sistema externo" responde sozinho depois disto

# Política de affordance: com poucas pendências a plataforma PERGUNTA em
# linguagem natural (um campo por vez, como o `collect` do CALM); acima disto
# abre um formulário, porque 8 turnos de chat é onde o operador desiste.
# Quem escolhe a forma é o motor — o modelo nunca decide o que perguntar.
LIMIAR_COLETA = 2


@dataclass
class Pergunta:
    id: str
    tipo: str            # opcoes | formulario | confirmacao | externa
    capacidade: str | None = None
    payload: dict = field(default_factory=dict)
    token: str | None = None
    resolvida: bool = False


@dataclass
class Sessao:
    id: str
    eventos: list = field(default_factory=list)
    cond: asyncio.Condition = field(default_factory=asyncio.Condition)
    perguntas: dict[str, Pergunta] = field(default_factory=dict)
    ativa: dict | None = None            # {capacidade, args}
    recusadas: set[str] = field(default_factory=set)
    historico: list[str] = field(default_factory=list)


SESSOES: dict[str, Sessao] = {}
_POR_TOKEN: dict[str, tuple[str, str]] = {}   # token -> (sessao_id, pergunta_id)


def criar_sessao() -> Sessao:
    sessao = Sessao(id=uuid.uuid4().hex[:12])
    SESSOES[sessao.id] = sessao
    return sessao


async def _emitir(sessao: Sessao, tipo: str, **payload) -> None:
    async with sessao.cond:
        sessao.eventos.append({"seq": len(sessao.eventos), "tipo": tipo, **payload})
        sessao.cond.notify_all()


async def _falar(sessao: Sessao, texto: str, papel: str = "ia") -> None:
    sessao.historico.append(f"{'IA' if papel == 'ia' else 'Plataforma'}: {texto}")
    await _emitir(sessao, "texto", papel=papel, texto=texto)


async def _snapshot(sessao: Sessao) -> None:
    """O painel de laboratório: perguntas abertas + operação ativa."""
    await _emitir(
        sessao, "estado",
        perguntas=[{"id": p.id, "tipo": p.tipo, "capacidade": p.capacidade}
                   for p in sessao.perguntas.values() if not p.resolvida],
        ativa=sessao.ativa,
        recusadas=sorted(sessao.recusadas),
    )


def _pendentes(sessao: Sessao) -> list[str]:
    if not sessao.ativa:
        return []
    cap = CAPACIDADES[sessao.ativa["capacidade"]]
    return [s["nome"] for s in cap["slots"] if s["nome"] not in sessao.ativa["args"]]


def _pergunta_aberta(sessao: Sessao, tipos: tuple[str, ...],
                     capacidade: str | None = None) -> Pergunta | None:
    """A pergunta viva de um tipo — o motor nunca abre duas iguais."""
    for pergunta in sessao.perguntas.values():
        if pergunta.resolvida or pergunta.tipo not in tipos:
            continue
        if capacidade is not None and pergunta.capacidade != capacidade:
            continue
        return pergunta
    return None


async def _abrir_pergunta(sessao: Sessao, tipo: str, capacidade: str | None,
                          payload: dict, token: str | None = None) -> Pergunta:
    pergunta = Pergunta(id=uuid.uuid4().hex[:10], tipo=tipo,
                        capacidade=capacidade, payload=payload, token=token)
    sessao.perguntas[pergunta.id] = pergunta
    if token:
        _POR_TOKEN[token] = (sessao.id, pergunta.id)
    await _emitir(sessao, tipo, pergunta_id=pergunta.id, **payload)
    return pergunta


async def _resolver_pergunta(sessao: Sessao, pergunta: Pergunta, como: str) -> None:
    pergunta.resolvida = True
    await _emitir(sessao, "resolvida", pergunta_id=pergunta.id, como=como)


# ------------------------------------------------------------------ entradas


async def entrada_texto(sessao: Sessao, texto: str) -> None:
    """Texto livre: não responde a pergunta ROTULADA → vai ao intérprete.

    Exceção: se há uma coleta aberta, o texto tem um alvo conhecido. Quando ele
    já valida no formato do campo, a resposta é direta e não custa um LLM — é o
    que torna a coleta campo-a-campo viável em série.
    """
    sessao.historico.append(f"Operador: {texto}")
    await _emitir(sessao, "texto", papel="operador", texto=texto)

    if await _resposta_direta(sessao, texto):
        await _snapshot(sessao)
        return

    coleta = _pergunta_aberta(sessao, ("coleta",))
    contexto = {
        "texto": texto,
        "historico": sessao.historico,
        "recusadas": sessao.recusadas,
        "ativa": ({"capacidade": sessao.ativa["capacidade"],
                   "args": sessao.ativa["args"],
                   "pendentes": _pendentes(sessao),
                   "perguntando": coleta.payload["campo"]["nome"] if coleta else None}
                  if sessao.ativa else None),
        "candidatas": None if sessao.ativa else funil(texto, sessao.recusadas),
    }
    resultado = await asyncio.to_thread(interprete.interpretar, contexto)
    await _emitir(sessao, "comandos", modo=resultado.get("modo"),
                  comandos=resultado.get("comandos", []), erro=resultado.get("erro"))
    await _aplicar_comandos(sessao, resultado.get("comandos", []))
    await _snapshot(sessao)


async def responder_pergunta(sessao: Sessao, pergunta_id: str, resposta: dict) -> None:
    """Entrada rotulada: aplica a resposta à pergunta certa."""
    pergunta = sessao.perguntas.get(pergunta_id)
    if pergunta is None or pergunta.resolvida:
        await _falar(sessao, "Essa pergunta já foi respondida.", papel="plataforma")
        return

    if pergunta.tipo == "opcoes":
        await _responder_opcoes(sessao, pergunta, resposta)
    elif pergunta.tipo == "formulario":
        await _responder_formulario(sessao, pergunta, resposta)
    elif pergunta.tipo == "confirmacao":
        await _responder_confirmacao(sessao, pergunta, resposta)
    elif pergunta.tipo in ("escolha", "conjunto"):
        await _responder_selecao(sessao, pergunta, resposta)
    await _snapshot(sessao)


async def webhook(token: str, ok: bool = True) -> bool:
    """O parceiro respondeu: acha a pergunta pelo token e retoma a conversa."""
    alvo = _POR_TOKEN.pop(token, None)
    if alvo is None:
        return False
    sessao = SESSOES.get(alvo[0])
    pergunta = sessao.perguntas.get(alvo[1]) if sessao else None
    if sessao is None or pergunta is None or pergunta.resolvida:
        return False
    await _resolver_pergunta(sessao, pergunta, "externa")
    protocolo = pergunta.payload.get("protocolo", "")
    cap = CAPACIDADES.get(pergunta.capacidade or "", {})
    display = cap.get("display", "Operação")
    if ok:
        await _emitir(sessao, "tarefa", status="concluida", protocolo=protocolo,
                      texto=f"{display}: {protocolo} concluído pelo parceiro.")
        await _falar(sessao, cap.get("fala_concluida", "Pronto — o parceiro concluiu."))
        entrega = cap.get("entrega")  # a capacidade declara o que entrega
        if entrega:
            await _emitir(sessao, "link", url=entrega["url"].format(protocolo=protocolo),
                          rotulo=entrega["rotulo"].format(protocolo=protocolo))
    else:
        await _emitir(sessao, "tarefa", status="falhou", protocolo=protocolo,
                      texto=f"O parceiro não concluiu {protocolo}.")
        await _falar(sessao, f"O parceiro não conseguiu concluir "
                             f"{display.lower()}. Quer tentar de novo?")
    await _snapshot(sessao)
    return True


# ------------------------------------------------------------------ comandos


async def _aplicar_comandos(sessao: Sessao, comandos: list[dict]) -> None:
    for comando in comandos:
        tipo = comando.get("tipo")
        if tipo == "responder" and comando.get("texto"):
            await _falar(sessao, str(comando["texto"]))
        elif tipo == "iniciar" and comando.get("capacidade") in CAPACIDADES:
            sessao.ativa = {"capacidade": comando["capacidade"], "args": {}}
            erros = _mesclar_args(sessao, comando.get("args") or {})
            for erro in erros:  # o que o modelo extraiu e o código recusou
                await _falar(sessao, erro, papel="plataforma")
            await _avancar(sessao)
        elif tipo == "preencher" and sessao.ativa:
            erros = _mesclar_args(sessao, comando.get("args") or {})
            for erro in erros:
                await _falar(sessao, erro, papel="plataforma")
            await _avancar(sessao)
        elif tipo == "desambiguar":
            nomes = [n for n in (comando.get("candidatas") or []) if n in CAPACIDADES]
            if nomes:
                await _abrir_desambiguacao(sessao, nomes, comando.get("texto"))
        elif tipo == "cancelar":
            sessao.ativa = None
            await _falar(sessao, "Operação cancelada. Nada foi executado.", papel="plataforma")


def _mesclar_args(sessao: Sessao, args: dict) -> list[str]:
    """LLM extraiu; código valida e normaliza. Inválido não entra."""
    erros = []
    cap = CAPACIDADES[sessao.ativa["capacidade"]]
    slots = {s["nome"]: s for s in cap["slots"]}
    for nome, valor in args.items():
        slot = slots.get(nome)
        if slot is None or valor in (None, ""):
            continue
        # Slot de origem é ESCOLHA do operador sobre uma busca real: o modelo
        # não preenche, nem que a fala traga algo parecido com o rótulo. Mas
        # recusar em silêncio é o mesmo erro de sumir com um dado inválido.
        if slot.get("origem"):
            erros.append(f"{slot['rotulo']}: essa escolha é feita na lista, "
                         f"não por texto — clique na opção desejada.")
            continue
        ok, normalizado = formats.validar(slot["formato"], valor)
        if ok:
            sessao.ativa["args"][nome] = normalizado
        else:
            erros.append(f"{slot['rotulo']}: {formats.MENSAGENS[slot['formato']]} (recebi {valor!r})")
    return erros


async def _resposta_direta(sessao: Sessao, texto: str) -> bool:
    """Coleta aberta + valor que valida no formato = não precisa de intérprete.

    Só vale para formatos estruturados: um slot 'texto' aceitaria qualquer
    coisa — inclusive "deixa pra lá" — e aí quem tem de opinar é o modelo.
    """
    coleta = _pergunta_aberta(sessao, ("coleta",))
    if coleta is None or not sessao.ativa:
        return False
    campo = coleta.payload["campo"]
    if campo["formato"] == "texto" or not formats.validar(campo["formato"], texto)[0]:
        return False
    await _emitir(sessao, "comandos", modo="direto",
                  comandos=[{"tipo": "preencher", "args": {campo["nome"]: texto}}])
    _mesclar_args(sessao, {campo["nome"]: texto})
    await _avancar(sessao)
    return True


async def _pedir_pendentes(sessao: Sessao, capacidade: str, pendentes: list[str]) -> None:
    """Escolhe a FORMA de pedir o que falta: pergunta natural ou formulário."""
    cap = CAPACIDADES[capacidade]
    aberta = _pergunta_aberta(sessao, ("coleta", "formulario"), capacidade)
    # "Primeira vez" é nunca ter perguntado nada desta capacidade — voltar
    # de um impasse não é recomeçar.
    primeira_vez = not any(
        p.capacidade == capacidade
        and p.tipo in ("formulario", "coleta", "escolha", "conjunto", "opcoes")
        for p in sessao.perguntas.values())

    if len(pendentes) <= LIMIAR_COLETA:
        slot = next(s for s in cap["slots"] if s["nome"] == pendentes[0])
        texto = slot.get("pergunta") or f"Qual é o {slot['rotulo'].lower()}?"
        if primeira_vez:
            await _falar(sessao, f"Vamos de {cap['display']}.")
        # Cada pergunta falada é um TURNO: a anterior se resolve, não se
        # sobrescreve. Formulário é superfície; pergunta é evento.
        if aberta is not None:
            await _resolver_pergunta(sessao, aberta, "respondida")
        sessao.historico.append(f"IA: {texto}")
        await _abrir_pergunta(sessao, "coleta", capacidade,
                              {"pergunta": texto, "restantes": len(pendentes) - 1,
                               "campo": {"nome": slot["nome"], "rotulo": slot["rotulo"],
                                         "formato": slot["formato"]}})
        return

    payload = {
        "titulo": cap["display"],
        # Slot de origem nunca é campo de formulário: ele tem pergunta própria,
        # com a lista real. Digitar o nome de uma conta não escolhe uma conta.
        "campos": [{"nome": s["nome"], "rotulo": s["rotulo"], "formato": s["formato"],
                    "valor": str(sessao.ativa["args"].get(s["nome"], ""))}
                   for s in cap["slots"] if not s.get("origem")],
        "pendentes": pendentes,
    }
    if aberta is not None and aberta.tipo == "formulario":
        aberta.payload = payload  # mesma pergunta, novo estado: atualiza no lugar
        await _emitir(sessao, "atualizada", pergunta_id=aberta.id,
                      forma="formulario", **payload)
        return
    if aberta is not None:
        await _resolver_pergunta(sessao, aberta, "substituida")

    rotulos = ", ".join(s["rotulo"] for s in cap["slots"] if s["nome"] in pendentes)
    await _falar(sessao, f"Você escolheu {cap['display']}. Me dê: {rotulos}."
                 if primeira_vez else f"Me dê: {rotulos}.")
    await _abrir_pergunta(sessao, "formulario", capacidade, payload)


def _rotulo_do_slot(cap: dict, nome: str) -> str:
    return next((s["rotulo"] for s in cap["slots"] if s["nome"] == nome), nome)


def _invalidar_derivados(sessao: Sessao, alterados: list[str]) -> list[str]:
    """Mudou um insumo de busca? O que saiu daquela busca não vale mais.

    Sem isto, corrigir o CNPJ manteria a conta escolhida da OUTRA empresa —
    um dado plausível, coerente na tela e completamente errado.
    """
    if not sessao.ativa or not alterados:
        return []
    cap = CAPACIDADES[sessao.ativa["capacidade"]]
    caidos = []
    for slot in cap["slots"]:
        if not slot.get("origem") or slot["nome"] not in sessao.ativa["args"]:
            continue
        if set(slot.get("depende") or []) & set(alterados):
            sessao.ativa["args"].pop(slot["nome"])
            caidos.append(slot["rotulo"])
    return caidos


def _inviavel(slot: dict, opcoes: list[dict]) -> str | None:
    """A busca torna o slot impossível? Devolve o motivo, em português."""
    if not opcoes:
        return "a busca não devolveu nenhuma opção"
    regra = slot.get("regra")
    if regra:
        total = sum(o.get(regra["campo"], 0) for o in opcoes)
        if total < regra["soma_minima"]:
            unidade = regra.get("unidade", "")
            return (f"tudo que existe soma {total}{unidade}, abaixo do mínimo de "
                    f"{regra['soma_minima']}{unidade}")
    return None


async def _abrir_impasse(sessao: Sessao, capacidade: str, slot: dict,
                         motivo: str) -> None:
    """Sem saída pela busca: devolve o impasse ao operador como PERGUNTA.

    Não inventa dado, não segue em frente e não cancela sozinho — oferece
    refazer o que alimentou a busca ou desistir.
    """
    cap = CAPACIDADES[capacidade]
    await _falar(sessao, f"{slot['rotulo']}: {motivo}.", papel="plataforma")
    sessao.historico.append(f"Plataforma: impasse em {slot['nome']} — {motivo}.")
    opcoes = []
    depende = [d for d in (slot.get("depende") or []) if d in sessao.ativa["args"]]
    if depende:
        rotulos = ", ".join(_rotulo_do_slot(cap, d) for d in depende)
        valores = ", ".join(
            formats.mascarar(
                next(s["formato"] for s in cap["slots"] if s["nome"] == d),
                sessao.ativa["args"][d])
            for d in depende)
        opcoes.append({"id": "refazer:" + ",".join(depende),
                       "rotulo": f"Corrigir {rotulos}",
                       "descricao": f"A busca usou {valores}."})
    opcoes.append({"id": "cancelar", "rotulo": "Cancelar a operação",
                   "descricao": "Nada foi executado."})
    await _abrir_pergunta(sessao, "opcoes", capacidade, {"opcoes": opcoes})


async def _pedir_escolha(sessao: Sessao, capacidade: str, slot: dict) -> None:
    """Slot de origem: a plataforma BUSCA e o operador escolhe da lista real."""
    aberta = _pergunta_aberta(sessao, ("escolha", "conjunto"), capacidade)
    if aberta is not None and aberta.payload["campo"]["nome"] == slot["nome"]:
        return  # já está perguntando exatamente isto
    if _pergunta_aberta(sessao, ("opcoes",), capacidade) is not None:
        return  # há um impasse aberto esperando decisão

    opcoes = buscar(slot["origem"], sessao.ativa["args"])
    tipo = "conjunto" if slot.get("regra") else "escolha"
    anterior = _pergunta_aberta(sessao, ("coleta", "formulario"), capacidade)
    if anterior is not None:
        await _resolver_pergunta(sessao, anterior, "substituida")

    motivo = _inviavel(slot, opcoes)
    if motivo:
        await _abrir_impasse(sessao, capacidade, slot, motivo)
        return

    # Opção única não é decisão: pedir um clique aqui é teatro. Preenche e
    # AVISA — o cartão de confirmação continua sendo o ponto de controle.
    if len(opcoes) == 1:
        unica = opcoes[0]
        sessao.ativa["args"][slot["nome"]] = [unica] if tipo == "conjunto" else unica
        await _falar(sessao, f"{slot['rotulo']}: só havia uma opção — "
                             f"{unica['rotulo']}. Segui com ela.", papel="plataforma")
        sessao.historico.append(
            f"Plataforma: {slot['rotulo']} tinha opção única ({unica['rotulo']}), "
            f"preenchida sem perguntar.")
        await _avancar(sessao)
        return

    await _falar(sessao, slot["pergunta"])
    sessao.historico.append(f"IA: {slot['pergunta']}")
    await _abrir_pergunta(sessao, tipo, capacidade, {
        "titulo": slot["rotulo"], "opcoes": opcoes, "regra": slot.get("regra"),
        "campo": {"nome": slot["nome"], "rotulo": slot["rotulo"],
                  "formato": slot["formato"]},
    })


async def _avancar(sessao: Sessao) -> None:
    """Coleta completa? Escrita pede confirmação; leitura executa."""
    if not sessao.ativa:
        return
    nome = sessao.ativa["capacidade"]
    cap = CAPACIDADES[nome]
    pendentes = _pendentes(sessao)

    if pendentes:
        slots = {s["nome"]: s for s in cap["slots"]}
        # Digitáveis primeiro: a busca de opções costuma depender deles (as
        # contas saem do CNPJ). A ordem de declaração já garante isso.
        digitaveis = [n for n in pendentes if not slots[n].get("origem")]
        if digitaveis:
            await _pedir_pendentes(sessao, nome, digitaveis)
        else:
            await _pedir_escolha(sessao, nome, slots[pendentes[0]])
        return

    # Coleta completa: fecha o que estava perguntando.
    for pergunta in sessao.perguntas.values():
        if not pergunta.resolvida and pergunta.tipo in ("formulario", "coleta"):
            await _resolver_pergunta(sessao, pergunta, "completo")

    if cap["risco"] == "escrita":
        await _abrir_confirmacao(sessao)
    else:
        await _executar_leitura(sessao)


def _exibir(slot: dict, valor: object) -> str:
    """Como o valor validado/escolhido aparece no cartão."""
    if not slot.get("origem"):
        return formats.mascarar(slot["formato"], valor)
    if isinstance(valor, list):
        campo = (slot.get("regra") or {}).get("campo")
        unidade = (slot.get("regra") or {}).get("unidade", "")
        return " + ".join(
            f"{v['rotulo']} ({v[campo]}{unidade})" if campo else v["rotulo"]
            for v in valor)
    return valor["rotulo"] if isinstance(valor, dict) else str(valor)


async def _abrir_confirmacao(sessao: Sessao) -> None:
    nome = sessao.ativa["capacidade"]
    cap = CAPACIDADES[nome]
    linhas = [{"rotulo": s["rotulo"],
               "valor": _exibir(s, sessao.ativa["args"][s["nome"]]),
               "nome": s["nome"], "formato": s["formato"],
               # Escolha feita sobre busca não se edita digitando: para trocar
               # a conta, o operador refaz a escolha na lista real.
               "editavel": not s.get("origem"),
               "bruto": _exibir(s, sessao.ativa["args"][s["nome"]])}
              for s in cap["slots"]]
    await _abrir_pergunta(sessao, "confirmacao", nome,
                          {"titulo": f"Confirmar: {cap['display']}", "linhas": linhas,
                           "args": dict(sessao.ativa["args"])})


async def _abrir_desambiguacao(sessao: Sessao, nomes: list[str],
                               texto: str | None = None) -> None:
    opcoes = [{"id": f"cap:{n}", "rotulo": CAPACIDADES[n]["display"],
               "descricao": CAPACIDADES[n]["descricao"]} for n in nomes]
    opcoes.append({"id": "none", "rotulo": "Não é isso",
                   "descricao": "Nenhuma destas atende o que eu pedi."})
    # O modelo costuma redigir a pergunta melhor que um texto fixo — as OPÇÕES
    # continuam sendo da plataforma; só a frase é dele.
    await _falar(sessao, texto or "Tenho essas opções:")
    await _abrir_pergunta(sessao, "opcoes", None, {"opcoes": opcoes})


async def _executar_leitura(sessao: Sessao) -> None:
    nome, args = sessao.ativa["capacidade"], dict(sessao.ativa["args"])
    sessao.ativa = None
    resultado = executar(nome, args)
    await _emitir(sessao, "resultado", **resultado)
    sessao.historico.append(f"Plataforma: executou {nome}, resultado exibido.")
    comentario = await asyncio.to_thread(
        interprete.interpretar,
        {"texto": "", "historico": sessao.historico, "recusadas": sessao.recusadas,
         "ativa": None, "candidatas": [], "resultado": str(resultado)[:400]},
    )
    falas = [c for c in comentario.get("comandos", []) if c.get("tipo") == "responder"]
    if falas and falas[0].get("texto"):
        await _falar(sessao, str(falas[0]["texto"]))


async def _executar_escrita(sessao: Sessao, capacidade: str, args: dict) -> None:
    protocolo = despachar_externo(capacidade, args)
    await _emitir(sessao, "tarefa", status="aguardando_externo",
                  texto=f"Pedido {protocolo} enviado ao parceiro.",
                  protocolo=protocolo)
    await _falar(sessao, "Seu pedido foi enviado. Isso pode demorar — avisaremos aqui.")
    token = uuid.uuid4().hex
    await _abrir_pergunta(sessao, "externa", capacidade,
                          {"protocolo": protocolo, "oculta": True}, token=token)
    await _emitir(sessao, "externa_pendente", token=token, protocolo=protocolo,
                  segundos=ESPERA_PARCEIRO_S)
    asyncio.get_running_loop().create_task(_parceiro_automatico(token))


async def _parceiro_automatico(token: str) -> None:
    await asyncio.sleep(ESPERA_PARCEIRO_S)
    await webhook(token)  # idempotente: se o botão do lab já disparou, é no-op


# ---------------------------------------------------------------- respostas


async def _responder_opcoes(sessao: Sessao, pergunta: Pergunta, resposta: dict) -> None:
    opcao = str(resposta.get("opcao", ""))
    await _resolver_pergunta(sessao, pergunta, opcao)

    if opcao == "cancelar":
        sessao.ativa = None
        sessao.historico.append("Operador: (clique) desistiu diante do impasse.")
        await _falar(sessao, "Operação cancelada. Nada foi executado.",
                     papel="plataforma")
        return
    if opcao.startswith("refazer:") and sessao.ativa:
        # Refazer o dado que alimentou a busca: some do state e volta a ser
        # pendente. O _avancar simplesmente pergunta de novo.
        campos = [c for c in opcao.removeprefix("refazer:").split(",") if c]
        for campo in campos:
            sessao.ativa["args"].pop(campo, None)
        caidos = _invalidar_derivados(sessao, campos)
        if caidos:
            await _falar(sessao, f"Descartei {', '.join(caidos)}: dependia "
                                 f"do dado que você vai corrigir.", papel="plataforma")
        sessao.historico.append(
            f"Operador: (clique) pediu para corrigir {', '.join(campos)}.")
        await _avancar(sessao)
        return

    if opcao == "none":
        ofertadas = [o["id"].removeprefix("cap:") for o in pergunta.payload["opcoes"]
                     if o["id"].startswith("cap:")]
        sessao.recusadas.update(ofertadas)
        sessao.historico.append("Operador: (clique) nenhuma das opções serve.")
        await _falar(sessao, "Entendi — nenhuma dessas. Me diga com outras palavras o que você precisa.")
        return
    nome = opcao.removeprefix("cap:")
    if nome in CAPACIDADES:
        sessao.historico.append(f"Operador: (clique) escolheu {nome}.")
        sessao.ativa = {"capacidade": nome, "args": {}}
        await _avancar(sessao)


async def _responder_formulario(sessao: Sessao, pergunta: Pergunta, resposta: dict) -> None:
    if not sessao.ativa or sessao.ativa["capacidade"] != pergunta.capacidade:
        await _resolver_pergunta(sessao, pergunta, "obsoleta")
        return
    erros = _mesclar_args(sessao, resposta.get("valores") or {})
    if erros:
        await _emitir(sessao, "formulario_erros", pergunta_id=pergunta.id, erros=erros)
        return
    sessao.historico.append("Operador: (formulário) enviou os dados.")
    await _avancar(sessao)  # quem fecha (ou atualiza) o formulário é o _avancar


async def _responder_selecao(sessao: Sessao, pergunta: Pergunta, resposta: dict) -> None:
    """Escolha única ou conjunto com regra. A regra é conferida por CÓDIGO."""
    if not sessao.ativa or sessao.ativa["capacidade"] != pergunta.capacidade:
        await _resolver_pergunta(sessao, pergunta, "obsoleta")
        return
    campo = pergunta.payload["campo"]
    por_id = {o["id"]: o for o in pergunta.payload["opcoes"]}
    escolhidos = [por_id[i] for i in (resposta.get("ids") or []) if i in por_id]

    if pergunta.tipo == "escolha":
        if not escolhidos:
            await _emitir(sessao, "formulario_erros", pergunta_id=pergunta.id,
                          erros=["Escolha uma opção da lista."])
            return
        valor: object = escolhidos[0]
    else:
        regra = pergunta.payload["regra"]
        soma = sum(o.get(regra["campo"], 0) for o in escolhidos)
        if soma < regra["soma_minima"]:
            faltam = regra["soma_minima"] - soma
            await _emitir(sessao, "formulario_erros", pergunta_id=pergunta.id, erros=[
                f"A soma escolhida é {soma}{regra['unidade']} — o mínimo é "
                f"{regra['soma_minima']}{regra['unidade']}. Faltam "
                f"{faltam}{regra['unidade']}: inclua mais um."])
            return  # a pergunta continua aberta, com o que já foi marcado
        valor = escolhidos

    sessao.ativa["args"][campo["nome"]] = valor
    resumo = _exibir({"origem": True, "regra": pergunta.payload.get("regra")}, valor)
    sessao.historico.append(
        f"Operador: (clique) escolheu {resumo} para {campo['rotulo']}.")
    await _resolver_pergunta(sessao, pergunta, "escolhida")
    await _avancar(sessao)


async def _responder_confirmacao(sessao: Sessao, pergunta: Pergunta, resposta: dict) -> None:
    acao = str(resposta.get("acao", ""))
    capacidade = pergunta.capacidade
    if acao == "aprovar":
        await _resolver_pergunta(sessao, pergunta, "aprovada")
        sessao.historico.append("Operador: (clique) aprovou a operação.")
        args = dict(pergunta.payload["args"])  # executa os args APROVADOS
        sessao.ativa = None
        await _executar_escrita(sessao, capacidade, args)
    elif acao == "reprovar":
        await _resolver_pergunta(sessao, pergunta, "reprovada")
        sessao.historico.append("Operador: (clique) reprovou a operação.")
        sessao.ativa = None
        await _falar(sessao, "Operação cancelada pelo operador. Nada foi executado.",
                     papel="plataforma")
    elif acao == "editar":
        valores = resposta.get("valores") or {}
        cap = CAPACIDADES[capacidade]
        slots = {s["nome"]: s for s in cap["slots"]}
        novos, erros = dict(pergunta.payload["args"]), []
        for nome, valor in valores.items():
            slot = slots.get(nome)
            if slot is None or slot.get("origem"):
                continue  # chave fora do cartão (ou escolha) não entra
            ok, normalizado = formats.validar(slot["formato"], valor)
            if ok:
                novos[nome] = normalizado
            else:
                erros.append(f"{slot['rotulo']}: {formats.MENSAGENS[slot['formato']]}")
        if erros:
            await _emitir(sessao, "formulario_erros", pergunta_id=pergunta.id, erros=erros)
            return
        alterados = [n for n, v in novos.items()
                     if pergunta.payload["args"].get(n) != v]
        await _resolver_pergunta(sessao, pergunta, "editada")
        sessao.historico.append("Operador: (clique) corrigiu valores e pediu nova confirmação.")
        sessao.ativa = {"capacidade": capacidade, "args": novos}
        caidos = _invalidar_derivados(sessao, alterados)
        if caidos:
            # Insumo de busca mudou: refaz a busca em vez de reconfirmar um
            # cartão com dado derivado do valor antigo.
            await _falar(sessao, f"Isso muda {', '.join(caidos)} — vou buscar "
                                 f"de novo.", papel="plataforma")
            await _avancar(sessao)
            return
        await _abrir_confirmacao(sessao)
        await _falar(sessao, "Valores corrigidos — confira e confirme.", papel="plataforma")
