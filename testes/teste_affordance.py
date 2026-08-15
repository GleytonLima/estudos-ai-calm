"""Exercita a política de affordance sem browser e sem LLM."""
import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import interprete  # noqa: E402
import motor  # noqa: E402

interprete.modo = lambda: "deterministico"  # força o offline


def abertas(s):
    return [(p.tipo, p.payload.get("campo", {}).get("nome") or len(p.payload.get("pendentes", [])))
            for p in s.perguntas.values() if not p.resolvida]


def cartoes(s):
    """Quantos cartões de pergunta o front teria empilhado."""
    return [p.tipo for p in s.perguntas.values()]


async def main():
    s = motor.criar_sessao()

    print("=== 1. fornecedor, nada informado (10 pendentes) ===")
    await motor.entrada_texto(s, "preciso cadastrar um fornecedor novo")
    print("abertas:", abertas(s), "| cartoes:", cartoes(s))

    print("\n=== 2. texto livre preenche 3 (form aberto) — nao pode duplicar ===")
    form = next(p for p in s.perguntas.values() if p.tipo == "formulario")
    await motor.responder_pergunta(s, form.id, {"valores": {
        "cnpj": "11.222.333/0001-81", "razao_social": "Aurora Insumos LTDA",
        "nome_fantasia": "Aurora", "email": "compras@aurora.com.br",
        "telefone": "11988887777", "cep": "01310-100", "cidade": "São Paulo"}})
    print("abertas:", abertas(s), "| cartoes:", cartoes(s))
    print("args:", s.ativa["args"])

    print("\n=== 3. caiu para 3 pendentes -> ainda formulario ===")
    form2 = next(p for p in s.perguntas.values() if p.tipo == "formulario" and not p.resolvida)
    await motor.responder_pergunta(s, form2.id, {"valores": {"banco": "Itaú"}})
    print("abertas:", abertas(s), "| cartoes:", cartoes(s))

    print("\n=== 4. 2 pendentes -> vira COLETA (pergunta falada) ===")
    ultimo = [e for e in s.eventos if e["tipo"] in ("coleta", "formulario", "atualizada")][-1]
    print("ultimo evento de pergunta:", ultimo["tipo"], ultimo.get("pergunta", ""))

    print("\n=== 5. responde a coleta por texto livre ===")
    await motor.entrada_texto(s, "1234")
    print("abertas:", abertas(s), "| args agencia:", s.ativa["args"].get("agencia"))
    ultimo = [e for e in s.eventos if e["tipo"] == "coleta"][-1]
    print("proxima pergunta:", ultimo["pergunta"], "| restantes:", ultimo["restantes"])

    await motor.entrada_texto(s, "00098765-4")
    print("abertas apos ultimo campo:", abertas(s))
    print("cartoes totais:", cartoes(s))

    print("\n=== 6. confirmacao -> aprovar -> externo ===")
    conf = next(p for p in s.perguntas.values() if p.tipo == "confirmacao" and not p.resolvida)
    await motor.responder_pergunta(s, conf.id, {"acao": "aprovar"})
    externa = next(p for p in s.perguntas.values() if p.tipo == "externa")
    print("protocolo:", externa.payload["protocolo"])
    await motor.webhook(externa.token)
    falas = [e["texto"] for e in s.eventos if e["tipo"] == "texto" and e["papel"] == "ia"]
    print("fala final:", falas[-1])
    print("tem link?", any(e["tipo"] == "link" for e in s.eventos))

    print("\n=== 7. boleto PF com CPF invalido no iniciar -> tem de FALAR o erro ===")
    s2 = motor.criar_sessao()
    await motor.entrada_texto(
        s2, "quero gerar um boleto pessoa fisica cpf 79012345678 valor 5 mil vencimento 01/01/2028")
    for e in s2.eventos:
        if e["tipo"] == "texto" and e["papel"] == "plataforma":
            print("plataforma disse:", e["texto"])
    print("args:", s2.ativa["args"], "| abertas:", abertas(s2))


asyncio.run(main())
