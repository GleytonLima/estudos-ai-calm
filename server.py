"""Servidor do protótipo: HTTP + SSE sobre o motor de perguntas.

Rodar: python -m uvicorn server:app --port 8123
"""

from __future__ import annotations

import asyncio
import json

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel

import interprete
import motor

app = FastAPI(title="Protótipo — canal universal (CALM)")


class Entrada(BaseModel):
    texto: str | None = None
    pergunta_id: str | None = None
    resposta: dict | None = None


@app.get("/")
def index() -> FileResponse:
    return FileResponse("static/index.html")


@app.get("/api/info")
def info() -> dict:
    return {"interprete": interprete.modo()}


@app.post("/api/sessoes")
def criar_sessao() -> dict:
    sessao = motor.criar_sessao()
    return {"id": sessao.id}


def _sessao(sessao_id: str) -> motor.Sessao:
    sessao = motor.SESSOES.get(sessao_id)
    if sessao is None:
        raise HTTPException(404, "sessão desconhecida")
    return sessao


@app.post("/api/sessoes/{sessao_id}/entradas")
async def entrada(sessao_id: str, corpo: Entrada) -> dict:
    sessao = _sessao(sessao_id)
    if corpo.pergunta_id and corpo.resposta is not None:
        await motor.responder_pergunta(sessao, corpo.pergunta_id, corpo.resposta)
    elif corpo.texto and corpo.texto.strip():
        await motor.entrada_texto(sessao, corpo.texto.strip())
    else:
        raise HTTPException(422, "mande texto ou (pergunta_id, resposta)")
    return {"ok": True}


@app.get("/api/sessoes/{sessao_id}/eventos")
async def eventos(sessao_id: str) -> StreamingResponse:
    sessao = _sessao(sessao_id)

    async def stream():
        indice = 0
        while True:
            async with sessao.cond:
                while indice >= len(sessao.eventos):
                    try:
                        await asyncio.wait_for(sessao.cond.wait(), timeout=20)
                    except asyncio.TimeoutError:
                        break
                pendentes = sessao.eventos[indice:]
                indice = len(sessao.eventos)
            if not pendentes:
                yield ": heartbeat\n\n"
                continue
            for evento in pendentes:
                yield f"data: {json.dumps(evento, ensure_ascii=False)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


@app.post("/api/webhook/{token}")
async def webhook(token: str) -> dict:
    """O 'parceiro externo' conclui o trabalho. O lab pode disparar antes do timer."""
    ok = await motor.webhook(token)
    return {"ok": ok, "detalhe": None if ok else "token desconhecido ou já resolvido"}


@app.get("/proposta/{protocolo}")
def proposta(protocolo: str) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html><meta charset="utf-8">
<title>Proposta {protocolo}</title>
<body style="font-family: ui-monospace, monospace; max-width: 40rem; margin: 3rem auto; line-height: 1.7">
<h2>PROPOSTA SIMULADA — {protocolo}</h2>
<p>Assinada eletronicamente pelo sistema externo do protótipo.</p>
<p>Hash: <b>{protocolo.lower()}-a91f4c2e77b0</b></p>
<p><i>Documento fictício. Não vale como proposta de crédito.</i></p>
</body>""")


@app.get("/boleto/{protocolo}")
def boleto(protocolo: str) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html><meta charset="utf-8">
<title>Boleto {protocolo}</title>
<body style="font-family: ui-monospace, monospace; max-width: 40rem; margin: 3rem auto; line-height: 1.7">
<h2>BOLETO SIMULADO — {protocolo}</h2>
<p>Linha digitável:<br><b>23790.12345 60000.123456 78901.234567 8 99990000500000</b></p>
<p>|||‖|‖‖|||‖|||‖‖||‖|||‖||‖‖|||‖||‖|||‖‖||‖|||</p>
<p><i>Gerado pelo parceiro externo do protótipo. Não vale como cobrança.</i></p>
</body>""")
