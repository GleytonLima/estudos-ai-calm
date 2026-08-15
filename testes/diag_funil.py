import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from catalogo import CAPACIDADES, _tokens, funil  # noqa: E402

FALA = "Quero um emprestimo para o cliente 05419225000109"
consulta = _tokens(FALA)
print("tokens da fala:", sorted(consulta))
print()

for nome, cap in CAPACIDADES.items():
    alvo = _tokens(cap["descricao"] + " " + " ".join(cap["exemplos"]) + " " + cap["display"])
    comuns = consulta & alvo
    print(f"{len(comuns)}  {nome:28} <- {sorted(comuns)}")

print()
print("top-3 que o intérprete recebeu:")
for c in funil(FALA, set()):
    print("  ", c["nome"], c["score"])
