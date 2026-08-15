"""Regressão: o boleto (que declara entrega) continua entregando o link."""
import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import interprete  # noqa: E402
import motor  # noqa: E402

interprete.modo = lambda: "deterministico"


async def main():
    s = motor.criar_sessao()
    await motor.entrada_texto(s, "quero gerar um boleto")
    op = next(p for p in s.perguntas.values() if p.tipo == "opcoes")
    print("desambiguou:", [o["rotulo"] for o in op.payload["opcoes"]])
    await motor.responder_pergunta(s, op.id, {"opcao": "cap:emitir_boleto_pf"})

    aberta = next(p for p in s.perguntas.values() if not p.resolvida)
    print("3 pendentes ->", aberta.tipo)  # formulario

    await motor.entrada_texto(s, "CPF 529.982.247-25, valor 5 mil, vence 22/01/2027")
    aberta = next(p for p in s.perguntas.values() if not p.resolvida)
    print("apos a fala ->", aberta.tipo, "| args:", s.ativa["args"] if s.ativa else None)

    conf = next(p for p in s.perguntas.values() if p.tipo == "confirmacao")
    print("cartao:", [(l["rotulo"], l["valor"]) for l in conf.payload["linhas"]])
    await motor.responder_pergunta(s, conf.id, {"acao": "aprovar"})
    ext = next(p for p in s.perguntas.values() if p.tipo == "externa")
    await motor.webhook(ext.token)

    link = [e for e in s.eventos if e["tipo"] == "link"]
    print("link:", link[0] if link else "NENHUM")
    print("cartoes totais:", [p.tipo for p in s.perguntas.values()])
    print("abertas no fim:", [p.tipo for p in s.perguntas.values() if not p.resolvida])


asyncio.run(main())
