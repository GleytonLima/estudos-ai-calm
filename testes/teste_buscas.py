"""Busca vazia, opção única e regra inatingível."""
import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import interprete  # noqa: E402
import motor  # noqa: E402

interprete.modo = lambda: "deterministico"

BASE = {"valor": "250000", "primeira_parcela": "2027-03-10", "parcelas": 24}


def viva(s):
    return next((p for p in s.perguntas.values() if not p.resolvida), None)


def falas(s, desde=0):
    return [e["texto"] for e in s.eventos[desde:] if e["tipo"] == "texto"
            and e["papel"] in ("ia", "plataforma")]


async def montar(cnpj):
    s = motor.criar_sessao()
    s.ativa = {"capacidade": "gerar_proposta_emprestimo",
               "args": {"cnpj": cnpj, **BASE}}
    await motor._avancar(s)
    return s


async def main():
    print("=== A. Dunas 44.555.666/0001-81: SEM conta cadastrada ===")
    s = await montar("44555666000181")
    print("  falas:", falas(s))
    p = viva(s)
    print("  pergunta:", p.tipo, "| opcoes:", [(o["id"], o["rotulo"]) for o in p.payload["opcoes"]])

    print("\n  A1. escolhe Corrigir CNPJ -> volta a perguntar o CNPJ")
    marca = len(s.eventos)
    await motor.responder_pergunta(s, p.id, {"opcao": "refazer:cnpj"})
    print("     falas:", falas(s, marca))
    p = viva(s)
    print("     pergunta:", p.tipo, "|", p.payload.get("pergunta"))
    print("     cnpj saiu do state?", "cnpj" not in s.ativa["args"])

    print("\n  A2. informa o CNPJ da Boreal -> conta unica, sem clique")
    marca = len(s.eventos)
    await motor.entrada_texto(s, "22.333.444/0001-81")
    print("     falas:", falas(s, marca))
    print("     conta escolhida:", s.ativa["args"].get("conta", {}).get("rotulo") if s.ativa else None)
    print("     avalistas:", [(v["rotulo"], v["percentual"])
                              for v in (s.ativa or {}).get("args", {}).get("avalistas", [])])
    p = viva(s)
    print("     agora:", p.tipo)
    for l in p.payload["linhas"]:
        print(f"       {l['rotulo']}: {l['valor']}")

    print("\n=== B. Cerrado 33.444.555/0001-81: socios somam 40%, minimo 50% ===")
    s2 = await montar("33444555000181")
    print("  falas:", falas(s2))
    p2 = viva(s2)
    print("  pergunta:", p2.tipo, "| opcoes:", [o["rotulo"] for o in p2.payload["opcoes"]])
    print("  conta (unica) ja preenchida?", s2.ativa["args"].get("conta", {}).get("rotulo"))

    print("\n  B1. cancela")
    marca = len(s2.eventos)
    await motor.responder_pergunta(s2, p2.id, {"opcao": "cancelar"})
    print("     falas:", falas(s2, marca), "| ativa:", s2.ativa,
          "| abertas:", [p.tipo for p in s2.perguntas.values() if not p.resolvida])

    print("\n=== C. Aurora (3 contas, 4 socios) segue perguntando ===")
    s3 = await montar("11222333000181")
    p3 = viva(s3)
    print("  pergunta:", p3.tipo, "| n opcoes:", len(p3.payload["opcoes"]))

    print("\n=== D. impasse aberto nao duplica com texto livre ===")
    s4 = await montar("44555666000181")
    antes = len(s4.perguntas)
    await motor.entrada_texto(s4, "e aí, tudo certo?")
    print("  perguntas antes/depois:", antes, "/", len(s4.perguntas))

    print("\n=== E. STALE: impasse na Cerrado, corrige CNPJ -> conta velha cai ===")
    s5 = await montar("33444555000181")
    print("  conta preenchida (Cerrado):", s5.ativa["args"]["conta"]["rotulo"])
    p5 = viva(s5)
    marca = len(s5.eventos)
    await motor.responder_pergunta(s5, p5.id, {"opcao": "refazer:cnpj"})
    print("  falas:", falas(s5, marca))
    print("  conta ainda no state?", "conta" in s5.ativa["args"])
    await motor.entrada_texto(s5, "11.222.333/0001-81")
    print("  apos novo CNPJ ->", viva(s5).tipo,
          "| n contas ofertadas:", len(viva(s5).payload["opcoes"]))

    print("\n=== F. STALE: editar o CNPJ no cartao refaz as buscas ===")
    s6 = await montar("22333444000181")   # Boreal: tudo automatico -> confirmacao
    conf = viva(s6)
    print("  cartao inicial:", [(l["rotulo"], l["valor"]) for l in conf.payload["linhas"]][-2:])
    marca = len(s6.eventos)
    await motor.responder_pergunta(s6, conf.id, {"acao": "editar", "valores": {
        "cnpj": "11.222.333/0001-81", "valor": "250000",
        "primeira_parcela": "2027-03-10", "parcelas": "24"}})
    print("  falas:", falas(s6, marca))
    p6 = viva(s6)
    print("  agora:", p6.tipo, "| titulo:", p6.payload.get("titulo"),
          "| n opcoes:", len(p6.payload.get("opcoes", [])))
    print("  conta stale sobreviveu?", "conta" in s6.ativa["args"])


asyncio.run(main())
