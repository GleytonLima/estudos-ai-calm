"""Validação determinística dos tipos declarados.

A tese em miniatura: o intérprete (LLM) EXTRAI valores da fala; quem decide se
um CPF é um CPF é código. O cartão de confirmação mostra o que o sistema
validou, nunca o que o modelo achou.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

_SO_DIGITOS = re.compile(r"\D")


def digitos(valor: object) -> str:
    return _SO_DIGITOS.sub("", str(valor or ""))


def _dv(numeros: list[int], pesos: list[int]) -> int:
    resto = sum(n * p for n, p in zip(numeros, pesos)) % 11
    return 0 if resto < 2 else 11 - resto


def cpf_valido(valor: object) -> bool:
    num = digitos(valor)
    if len(num) != 11 or num == num[0] * 11:
        return False
    d = [int(c) for c in num]
    return d[9] == _dv(d[:9], list(range(10, 1, -1))) and d[10] == _dv(
        d[:10], list(range(11, 1, -1))
    )


def cnpj_valido(valor: object) -> bool:
    num = digitos(valor)
    if len(num) != 14 or num == num[0] * 14:
        return False
    d = [int(c) for c in num]
    p1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    p2 = [6, *p1]
    return d[12] == _dv(d[:12], p1) and d[13] == _dv(d[:13], p2)


def moeda(valor: object) -> Decimal | None:
    """Reais, sem negativo, até 2 casas. Aceita '5.000,00', '5000', '5 mil'."""
    if isinstance(valor, bool) or valor is None:
        return None
    if isinstance(valor, (int, float, Decimal)):
        bruto = Decimal(str(valor))
    else:
        texto = str(valor).strip().lower().replace("r$", "").strip()
        m = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*(mil|k)", texto)
        if m:
            base = Decimal(m.group(1).replace(",", "."))
            bruto = base * 1000
        else:
            texto = texto.replace(" ", "")
            if not texto:
                return None
            if "," in texto:
                texto = texto.replace(".", "").replace(",", ".")
            try:
                bruto = Decimal(texto)
            except InvalidOperation:
                return None
    if bruto < 0 or -bruto.as_tuple().exponent > 2:
        return None
    return bruto


def data_iso(valor: object) -> date | None:
    """Aceita AAAA-MM-DD e dd/mm/aaaa; devolve date ou None."""
    texto = str(valor or "").strip()
    for formato in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            continue
    return None


# ------------------------------------------------------------------ validação

MENSAGENS = {
    "cpf": "CPF inválido — confira os 11 dígitos.",
    "cnpj": "CNPJ inválido — confira os 14 dígitos.",
    "moeda": "Valor inválido — use reais, sem negativo, até 2 casas.",
    "data_futura": "A data precisa ser válida e posterior a hoje.",
    "inteiro": "Informe um número inteiro.",
    "texto": "Campo obrigatório.",
}


def validar(formato: str, valor: object) -> tuple[bool, object]:
    """(ok, valor_normalizado). Normaliza para o formato canônico."""
    if formato == "cpf":
        return (cpf_valido(valor), digitos(valor))
    if formato == "cnpj":
        return (cnpj_valido(valor), digitos(valor))
    if formato == "moeda":
        v = moeda(valor)
        return (v is not None, str(v) if v is not None else valor)
    if formato == "data_futura":
        d = data_iso(valor)
        return (d is not None and d > date.today(), d.isoformat() if d else valor)
    if formato == "inteiro":
        try:
            return (True, int(str(valor).strip()))
        except (ValueError, TypeError):
            return (False, valor)
    texto = str(valor or "").strip()
    return (bool(texto), texto)


# ------------------------------------------------------------------ máscaras


def mascarar(formato: str, valor: object) -> str:
    """Como o valor VALIDADO aparece no cartão de confirmação."""
    if formato == "cpf" and cpf_valido(valor):
        d = digitos(valor)
        return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
    if formato == "cnpj" and cnpj_valido(valor):
        d = digitos(valor)
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
    if formato == "moeda":
        v = moeda(valor)
        if v is not None:
            inteiro, _, centavos = f"{v:.2f}".partition(".")
            milhar = f"{int(inteiro):,}".replace(",", ".")
            return f"R$ {milhar},{centavos}"
    if formato == "data_futura":
        d = data_iso(valor)
        if d:
            return d.strftime("%d/%m/%Y")
    return str(valor)
