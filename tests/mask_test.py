"""O fechamento do recorte da pessoa (vm/nodes/wan_studio), sem GPU: a parte que e so conta.

  python tests/mask_test.py

O no roda dentro do ComfyUI; aqui o torch e o comfy sao trocados por modulos vazios e so as
funcoes de numpy sao chamadas. O SAM2 em si so se confere na GPU (tests/gpu_real.py, testes v_*).
"""
import importlib.util
import sys
import types
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
FAILED = []


def check(name, passed, detail=""):
    print(("ok   " if passed else "FALHA"), name, f" [{detail}]" if detail else "", flush=True)
    if not passed:
        FAILED.append(name)


def load_node():
    for name in ("torch", "comfy", "comfy.model_management", "comfy.utils"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["comfy.utils"].ProgressBar = sys.modules["comfy.utils"].common_upscale = None
    spec = importlib.util.spec_from_file_location("wan_studio", ROOT / "vm" / "nodes" / "wan_studio" / "__init__.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def body(**found):
    """18 pontos (x, y, confianca) com os dados em `found` (indice -> (x, y)); o resto sem confianca."""
    points = np.zeros((18, 3), dtype=np.float32)
    for index, (x, y) in found.items():
        points[int(index)] = (x, y, 0.9)
    return points


def main():
    node = load_node()
    width, height = 48, 80

    # Meio corpo, de costas: o SAM2 pegou a cabeca e os ombros, as laterais ate perto da borda
    # de baixo, e deixou o miolo e a barra (o caso medido em 12/10/2026).
    mask = np.zeros((height, width), dtype=bool)
    mask[5:30, 8:40] = True      # cabeca e ombros
    mask[30:70, 2:10] = True     # lateral esquerda, ate 87% da altura
    mask[30:66, 38:46] = True    # lateral direita, ate 82%
    closed = node.sealed(mask)
    check("R1 pessoa cortada pela borda: o miolo e a barra de baixo entram no recorte",
          closed[30:, 2:46].all() and closed[5:30, 8:40].all(), f"{closed.mean():.2f} do quadro")
    check("R2 o fundo ao lado e acima da pessoa continua fora", not closed[:5].any() and not closed[:, :2].any()
          and not closed[:, 46:].any() and not closed[5:30, :8].any())

    # Um braco estendido para o lado, no meio do quadro, nao desce ate a borda.
    arm = np.zeros((height, width), dtype=bool)
    arm[10:80, 18:30] = True     # tronco ate a borda
    arm[30:36, 30:46] = True     # braco estendido, a 45% da altura
    closed = node.sealed(arm)
    check("R3 o que acaba acima do quarto de baixo do quadro nao e esticado ate a borda",
          not closed[40:, 32:].any() and closed[30:36, 30:46].all())

    # Entre o tronco e um braco que desce ate a borda: o bolsao e tampado.
    pocket = np.zeros((height, width), dtype=bool)
    pocket[10:80, 14:26] = True
    pocket[20:26, 26:40] = True
    pocket[26:80, 36:40] = True
    closed = node.sealed(pocket)
    check("R4 o bolsao entre o tronco e o braco, aberto so para a borda de baixo, e tampado", closed[26:, 26:36].all())

    # Sem o scipy o resultado e o mesmo.
    holed = np.zeros((20, 20), dtype=bool)
    holed[4:16, 4:16] = True
    holed[8:12, 8:12] = False
    sys.modules["scipy"] = None  # faz o import falhar
    sys.modules["scipy.ndimage"] = None
    plain = node.fill_holes(holed)
    check("R5 buraco cercado e tampado mesmo sem o scipy, e o de fora nao", plain[8:12, 8:12].all() and not plain[:4].any())

    # Quem decide se a pessoa esta cortada e a pose: sem tornozelo a vista na maior parte do pedaco.
    half = {"keypoints_body": body(**{"1": (0.5, 0.3), "2": (0.3, 0.32), "5": (0.7, 0.32)})}
    whole = {"keypoints_body": body(**{"1": (0.5, 0.2), "2": (0.4, 0.22), "5": (0.6, 0.22), "10": (0.45, 0.9),
                                       "13": (0.55, 0.9)})}
    guess = {"keypoints_body": half["keypoints_body"].copy()}
    guess["keypoints_body"][10] = (0.4, 0.98, 0.35)  # um tornozelo arriscado na borda, com pouca certeza
    check("R6 meio corpo e 'cortada' (mesmo com um tornozelo arriscado pela pose), corpo inteiro nao",
          node.cut_below(half, width, height) and node.cut_below(guess, width, height)
          and not node.cut_below(whole, width, height))
    stack = np.stack([mask] * 4)
    closed = node.close(stack, [half, half, half, whole], width, height)
    check("R7 no pedaco em que a pessoa esta quase sempre cortada, todos os quadros sao fechados",
          all(frame[30:, 2:46].all() for frame in closed))
    closed = node.close(stack, [whole, whole, whole, half], width, height)
    check("R8 no pedaco de corpo inteiro o recorte nao muda (nem no quadro em que o tornozelo sumiu)",
          (closed == stack).all())

    # Um bolsao no canto de baixo, encostado na borda do lado (o caso medido no quadro 3 do
    # video em pe): tampado. Encostado na borda do lado mais acima, e fundo: fica.
    corner = np.zeros((height, width), dtype=bool)
    corner[10:64, :] = True      # o corpo, de borda a borda
    corner[76:80, :] = True      # a barra da roupa, la embaixo
    corner[10:64, 40:48] = False
    corner[20:26, 40:48] = True  # um braco ate a borda, mais acima
    corner[40:64, 40:48] = True
    closed = node.sealed(corner)
    check("R9 bolsao na parte de baixo encostado na borda do lado: tampado", closed[64:76].all())
    check("R10 o fundo cercado pelo corpo e pela borda do lado, mais acima, continua fora",
          not closed[26:40, 40:48].any() and not closed[10:20, 40:48].any())

    # O metodo padrao da duas pistas ao SAM2 em cada quadro-chave: com a caixa e sem ela.
    front = {"keypoints_body": body(**{"0": (0.5, 0.2), "1": (0.5, 0.3), "2": (0.35, 0.32), "5": (0.65, 0.32),
                                       "8": (0.4, 0.6), "11": (0.6, 0.6)})}
    found = node.WanStudioPersonMask().prompts("juntos", [front] * 12, 12, 480, 832, 6, None)
    objects = {(index, obj) for index, obj, _points, _box in found}
    check("R11 'juntos': em cada quadro-chave, um objeto com a caixa e outro so com os pontos",
          objects == {(index, obj) for index in range(0, 12, 2) for obj in (1, 2)}
          and all((box is not None) == (obj == 1) for _index, obj, _points, box in found), str(sorted(objects))[:80])
    old = node.WanStudioPersonMask().prompts("caixas", [front] * 12, 12, 480, 832, 6, None)
    check("R12 'caixas' continua como era: um objeto so", {obj for _index, obj, _points, _box in old} == {1})

    print(f"\n{len(FAILED)} falha(s)")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
