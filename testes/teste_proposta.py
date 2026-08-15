"""Proposta PJ: slots de busca + conjunto com regra de 50%."""
import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import interprete  # noqa: E402
import motor  # noqa: E402

interprete.modo = lambda: "deterministico"


def viva(s):
    return next((p for p in s.perguntas.values() if not p.resolvida), None)


async def main():
    s = motor.criar_sessao()
    # O determinístico não sabe extrair "proposta"; vou direto na capacidade.
    s.ativa = {"capacidade": "gerar_proposta_emprestimo", "args": {}}
    await motor._avancar(s)
    p = viva(s)
    print("1. 6 pendentes (4 digitaveis) ->", p.tipo, "| pendentes:", p.payload.get("pendentes"))

    print("\n2. modelo tenta preencher a CONTA (nao pode)")
    erros = motor._mesclar_args(s, {"cnpj": "11.222.333/0001-81", "valor": "250 mil",
                                    "conta": "cc-4471", "avalistas": "Ana"})
    print("   erros:", erros, "| args:", s.ativa["args"])

    print("\n3. completa os digitaveis -> deve buscar as CONTAS")
    await motor._avancar(s)
    p = viva(s)
    motor._mesclar_args(s, {"primeira_parcela": "10/03/2027", "parcelas": "24"})
    await motor._avancar(s)
    p = viva(s)
    print("   pergunta:", p.tipo, "|", p.payload["titulo"],
          "| opcoes:", [o["rotulo"] for o in p.payload["opcoes"]])

    print("\n4. escolhe a conta -> deve buscar os SOCIOS")
    await motor.responder_pergunta(s, p.id, {"ids": ["cc-4471"]})
    p = viva(s)
    print("   pergunta:", p.tipo, "| regra:", p.payload["regra"])
    print("   socios:", [(o["rotulo"], o["percentual"]) for o in p.payload["opcoes"]])

    print("\n5. escolhe SO a Ana (35%) -> regra tem de barrar")
    await motor.responder_pergunta(s, p.id, {"ids": ["soc-1"]})
    erro = [e for e in s.eventos if e["tipo"] == "formulario_erros"][-1]
    print("   erro:", erro["erros"][0])
    print("   pergunta continua aberta?", viva(s).id == p.id)

    print("\n6. Ana + Bruno (35+20=55%) -> passa")
    await motor.responder_pergunta(s, p.id, {"ids": ["soc-1", "soc-3"]})
    p = viva(s)
    print("   agora:", p.tipo)
    for l in p.payload["linhas"]:
        print(f"     {l['rotulo']}: {l['valor']}  (editavel={l['editavel']})")

    print("\n7. aprova -> assinatura externa")
    await motor.responder_pergunta(s, p.id, {"acao": "aprovar"})
    ext = next(p for p in s.perguntas.values() if p.tipo == "externa")
    print("   protocolo:", ext.payload["protocolo"])
    await motor.webhook(ext.token)
    link = [e for e in s.eventos if e["tipo"] == "link"][-1]
    print("   fala:", [e["texto"] for e in s.eventos
                       if e["tipo"] == "texto" and e["papel"] == "ia"][-1])
    print("   link:", link["url"], "|", link["rotulo"])
    print("   abertas no fim:", [p.tipo for p in s.perguntas.values() if not p.resolvida])
    print("   cartoes:", [p.tipo for p in s.perguntas.values()])


asyncio.run(main())
