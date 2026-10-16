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

    # O recorte da imagem da pessoa (ref=crop): a caixa dela, na proporcao do quadro de geracao.
    # Corpo inteiro numa imagem quadrada de 1000 px, para um quadro em pe de 480 x 832: antes,
    # a imagem inteira com faixas pretas (a pessoa com 52% da altura do quadro).
    crop = node.fit_crop((350, 50, 650, 950), 1000, 1000, 480, 832)
    ratio = (crop[2] - crop[0]) / (crop[3] - crop[1])
    check("F1 corpo inteiro em imagem quadrada: recorte na proporcao do quadro, com a pessoa dentro",
          crop is not None and abs(ratio - 480 / 832) < 0.01
          and crop[0] <= 350 and crop[1] <= 50 and crop[2] >= 650 and crop[3] >= 950, str(crop))
    crop = node.fit_crop((100, 100, 400, 700), 2000, 1000, 832, 480)
    check("F2 pessoa em pe num quadro deitado: recorte mais largo, ainda com a pessoa inteira",
          crop is not None and abs((crop[2] - crop[0]) / (crop[3] - crop[1]) - 832 / 480) < 0.02
          and crop[0] <= 100 and crop[1] <= 100 and crop[2] >= 400 and crop[3] >= 700, str(crop))
    # Imagem estreita demais para a proporcao: o recorte pega a pessoa inteira e as faixas
    # pretas ficam dos lados, como antes, mas ela ja ocupa a altura toda do quadro.
    crop = node.fit_crop((100, 100, 400, 700), 1000, 1000, 832, 480)
    check("F2b imagem estreita: recorte limitado pela imagem, com a pessoa inteira",
          crop is not None and crop[0] == 0 and crop[2] == 1000 and crop[1] <= 100 and crop[3] >= 700, str(crop))
    # Pessoa encostada no canto: o recorte desliza para dentro da imagem, sem sair dela.
    crop = node.fit_crop((0, 0, 200, 400), 1000, 1000, 480, 832)
    check("F3 pessoa no canto: o recorte fica dentro da imagem e a contem",
          crop is not None and crop[0] == 0 and crop[1] == 0 and crop[2] >= 200 and crop[3] >= 400
          and crop[2] <= 1000 and crop[3] <= 1000, str(crop))
    # Quase a imagem toda, ou caixa invalida: nada a recortar.
    check("F4 pessoa que ja enche a imagem, ou caixa invalida: sem recorte",
          node.fit_crop((5, 5, 995, 995), 1000, 1000, 640, 640) is None
          and node.fit_crop((10, 10, 12, 12), 1000, 1000, 640, 640) is None)
    # Pessoa minuscula: nao ampliar alem do dobro (borraria o que a imagem tem).
    crop = node.fit_crop((480, 480, 520, 540), 2000, 2000, 640, 640)
    check("F5 pessoa pequena num quadro grande: o recorte nao passa a ser menor que a metade do quadro",
          crop is not None and crop[2] - crop[0] >= 320 and crop[3] - crop[1] >= 320, str(crop))
    # Imagem estreita: o recorte nao inventa o que nao ha, ficam as faixas pretas no que falta.
    crop = node.fit_crop((20, 300, 180, 700), 200, 1000, 1000, 400)
    check("F6 imagem mais estreita que o quadro: o recorte nao sai da imagem e contem a pessoa",
          crop is not None and crop[0] == 0 and crop[2] == 200 and 0 <= crop[1] <= 300 and 700 <= crop[3] <= 1000,
          str(crop))
    check("F6b pessoa que ocupa a imagem estreita toda: sem recorte",
          node.fit_crop((20, 50, 180, 950), 200, 1000, 1000, 400) is None)

    # O no: a uniao da caixa dos pontos com a do detector; sem pessoa, devolve a imagem.
    class Image:  # no lugar do tensor: so o formato e o corte importam aqui
        shape = (1, 1000, 1000, 3)
        sliced = None

        def __getitem__(self, item):
            Image.sliced = item
            return "cortada"

    legs = {"keypoints_body": body(**{"0": (0.5, 0.1), "1": (0.5, 0.2), "10": (0.45, 0.95), "13": (0.55, 0.95)})}
    fitter = node.WanStudioFitReference()
    image = Image()
    check("F7 com pose ou caixa do detector, a imagem e recortada",
          fitter.fit(image, {"pose_metas_original": [legs]}, 480, 832)[0] == "cortada"
          and fitter.fit(image, {"pose_metas_original": []}, 480, 832, [(300, 100, 700, 950)])[0] == "cortada")
    check("F8 sem pose nem caixa, a imagem sai como entrou",
          fitter.fit(image, {"pose_metas_original": []}, 480, 832)[0] is image
          and fitter.fit(image, {"pose_metas_original": []}, 480, 832, [(0, 0, 0, 0)])[0] is image)

    # O grafo: o padrao nao muda; com ref=crop entram a pose da imagem e o no de recorte, e o
    # carregador de modelos de pose e um so.
    sys.path.insert(0, str(ROOT / "app"))
    import workflows
    files = {"clip": "umt5.safetensors", "vae": "vae.safetensors", "unet": "Wan2.2-Animate-14B-Q4_K_M.gguf",
             "lora": "lightx2v.safetensors", "relight": "relight.safetensors", "clip_vision": "cv.safetensors",
             "vitpose": "vp.onnx", "yolo": "y.onnx"}

    def graph(ref):
        spec = workflows.resolve({"mode": "swap", "quality": "boa", "seed": 1, "expert": {"ref": ref}}, True, False, True)
        spec.update(width=480, height=832, length=49, chunk=49)
        nodes = workflows.build(spec, files, "p", "n", "x", start_image="person.png", video="v.mp4")[0]
        types = [item["class_type"] for item in nodes.values()]
        pad = next(item for item in nodes.values() if item["class_type"] == "ResizeAndPadImage")
        return nodes, types, nodes[pad["inputs"]["image"][0]]["class_type"]

    _nodes, types, feeds = graph("pad")
    check("W1 padrao (ref=pad): a imagem entra inteira no encaixe, sem o no de recorte",
          feeds == "LoadImage" and "WanStudioFitReference" not in types and types.count("PoseAndFaceDetection") == 1)
    nodes, types, feeds = graph("crop")
    detectors = {tuple(item["inputs"]["model"]) for item in nodes.values() if item["class_type"] == "PoseAndFaceDetection"}
    check("W2 ref=crop: a imagem passa pela pose e pelo recorte antes do encaixe, com um carregador so",
          feeds == "WanStudioFitReference" and types.count("PoseAndFaceDetection") == 2
          and types.count("OnnxDetectionModelLoader") == 1 and len(detectors) == 1, str(types.count("OnnxDetectionModelLoader")))
    try:
        workflows.resolve({"mode": "swap", "expert": {"ref": "zoom"}}, True, False, True)
        refused = False
    except ValueError:
        refused = True
    check("W3 ajuste de recorte desconhecido e recusado", refused)

    # Sem ajuste de teste nenhum, o grafo de um pedaco e, no por no, o do codigo que foi medido
    # na GPU (tests/padrao_troca.json guarda um resumo de cada um; quem mudar um padrao de
    # proposito, depois de medir, refaz o arquivo).
    import hashlib
    import json
    golden = json.loads((ROOT / "tests" / "padrao_troca.json").read_text(encoding="utf-8"))
    changed = []
    for label, params in golden["casos"].items():
        for motion in (None, "continua.mp4"):
            found = workflows.available({**golden["listas"], "nodes": workflows.CATALOG["animate"]["nodes"]})["animate"]["files"]
            spec = workflows.resolve({"mode": "swap", "seed": 3, **params}, True, False, True)
            spec.update(width=832, height=480, length=49)
            spec["factor"] = workflows.smooth_factor(spec, found)
            built = workflows.build(spec, found, "p", "n", "job_000", start_image="pessoa.png", video="pedaco.mp4", motion=motion)[0]
            name = label + (", continuando" if motion else "")
            if hashlib.sha256(json.dumps(built, sort_keys=True).encode()).hexdigest()[:16] != golden["grafos"][name]:
                changed.append(name)
    check("W4 sem ajustes de teste, o grafo de cada pedaco e exatamente o que foi medido na GPU",
          not changed and len(golden["grafos"]) == 12, ", ".join(changed))

    # ---- A janela na pessoa (`window`): o retangulo do quadro que o Animate gera.
    # Pessoa em pe num video deitado de 832 x 480: o recorte dela vai de 300 a 500 na largura.
    box = node.window_box(300, 40, 500, 470, 832, 480)
    check("J1 pessoa em pe num video deitado: a janela a contem com folga, em multiplos de 16, e e bem menor que o quadro",
          box is not None and all(value % 16 == 0 for value in box) and box[0] <= 300 - 64 and box[0] + box[2] >= 500 + 64
          and box[1] == 0 and box[3] == 480 and box[2] * box[3] < 0.5 * 832 * 480, str(box))
    tight = node.window_box(300, 40, 500, 470, 832, 480, margin=16)
    check("J1b com menos folga pedida (ajuste de teste), a janela encolhe, sem perder o cenario proporcional ao recorte",
          tight is not None and tight[2] < box[2] and tight[0] <= 300 - 40 and tight[0] + tight[2] >= 500 + 40, str(tight))
    check("J2 pessoa que enche o quadro, ou recorte vazio: sem janela (o pedaco e gerado inteiro)",
          node.window_box(40, 150, 440, 832, 480, 832) is None and node.window_box(10, 10, 10, 10, 832, 480) is None)
    small = node.window_box(400, 200, 440, 280, 832, 480)
    tall = node.window_box(200, 0, 260, 832, 480, 832)
    check("J3 a janela nao fica menor que 256 px de lado nem mais comprida que 2,5 para 1",
          small is not None and small[2] >= 256 and small[3] >= 256 and small[0] <= 400 and small[0] + small[2] >= 440
          and tall is not None and tall[3] / tall[2] <= 2.5 and tall[2] >= 60 + 128, f"{small} {tall}")
    corner = node.window_box(0, 0, 150, 300, 832, 480)
    check("J4 pessoa no canto: a janela fica dentro do quadro e a contem",
          corner is not None and corner[0] == 0 and corner[1] == 0 and corner[2] >= 150 + 64 and corner[3] >= 300 + 64
          and corner[0] + corner[2] <= 832 and corner[1] + corner[3] <= 480, str(corner))
    moving = np.zeros((3, 60, 80), dtype=np.float32)
    moving[0, 10:30, 5:20] = moving[1, 12:40, 30:50] = moving[2, 20:45, 55:70] = 1
    check("J5 o retangulo do recorte e o de todos os quadros do pedaco somados",
          node.mask_bounds(moving) == (5, 10, 70, 45) and node.mask_bounds(moving * 0) is None)

    # A colagem: quanto os quadros gerados valem em volta do recorte.
    block = np.zeros((120, 160), dtype=np.float32)
    block[40:80, 60:100] = 1
    weight = node.soften(block)
    row = weight[60]
    check("J6 na colagem os gerados valem tudo no recorte e ate 8 px em volta, e somem aos poucos nos 16 seguintes",
          weight[40:80, 60:100].min() == 1 and row[100 + 7] == 1 and 0.3 < row[100 + 15] < 0.7 and row[100 + 25] == 0
          and row[60 - 26] == 0 and all(row[100 + index] >= row[101 + index] for index in range(30)), f"{row[115]:.2f}")
    edge = np.zeros((40, 40), dtype=np.float32)
    edge[20:, 10:30] = 1  # pessoa cortada pela borda de baixo
    check("J6b recorte encostado na borda do quadro: vale tudo ate a borda", node.soften(edge)[39, 10:30].min() == 1)

    rng = np.random.default_rng(7)
    frames = rng.uniform(0.2, 0.8, (12, 160, 240, 3)).astype(np.float32)
    x, y = 48, 16
    masks = np.zeros((12, 128, 128), dtype=np.float32)
    masks[:, 40:88, 40:88] = 1
    made = rng.uniform(0.3, 0.7, (12, 128, 128, 3)).astype(np.float32)
    out, fix = node.pasted(frames, made, masks, x, y)
    outside = out.copy()
    outside[:, y:y + 128, x:x + 128] = frames[:, y:y + 128, x:x + 128]
    check("J7 colagem: fora da janela e longe do recorte o video fica como e; no recorte entram os gerados",
          fix is None and np.array_equal(outside, frames) and np.allclose(out[:, y + 40:y + 88, x + 40:x + 88], made[:, 40:88, 40:88])
          and np.array_equal(out[:, y:y + 14, x:x + 128], frames[:, y:y + 14, x:x + 128])
          and not np.allclose(out[:, y + 30:y + 40, x + 40:x + 88], frames[:, y + 30:y + 40, x + 40:x + 88]))
    # A cor: o pedaco saiu mais contrastado e mais claro que o anterior, de que repete 5 quadros.
    drifted = np.clip((made - 0.5) * 1.15 + 0.53, 0, 1).astype(np.float32)
    fixed, fix = node.pasted(frames, drifted, masks, x, y, previous=made[:5], overlap=5)
    inside = (slice(None), slice(y + 44, y + 84), slice(x + 44, x + 84))
    before, after = np.abs(drifted[:, 44:84, 44:84] - made[:, 44:84, 44:84]).mean(), np.abs(fixed[inside] - made[:, 44:84, 44:84]).mean()
    check("J8 a cor dos gerados e trazida a do pedaco de que continuam, dentro do recorte",
          fix is not None and 0.84 < fix[0][0] < 0.9 and after < before / 5, f"{before:.4f} -> {after:.4f}; ganho {fix and fix[0][0]:.3f}")
    scene = frames.copy()
    scene[5:] = np.clip(scene[5:] * 0.5, 0, 1)  # corte de cena no video de origem, bem na emenda
    _, cut_fix = node.pasted(scene, drifted, masks, x, y, previous=made[:5], overlap=5)
    # Um video parado e um pedaco igual ao anterior (quadros repetidos: nenhuma diferenca a medir).
    calm, still = np.repeat(frames[:1], 12, axis=0), np.repeat(made[:1], 12, axis=0)
    _, same_fix = node.pasted(calm, still, masks, x, y, previous=still[:5], overlap=5)
    check("J8b corte de cena na emenda, ou pedaco que ja saiu na cor do anterior: nada a corrigir",
          cut_fix is None and same_fix is None)

    class Meta:  # no lugar do AAPoseMeta do pacote de pose: so o que a janela usa
        def __init__(self):
            self.points = np.array([[100.0, 200.0]])
            self.width, self.height = 832, 480

        def crop(self, x0, y0, x1, y1):
            self.points = self.points - (x0, y0)
            self.width, self.height = x1 - x0, y1 - y0
            return self

    original = {"pose_metas": [Meta()], "pose_metas_original": ["como veio"]}
    moved = node.shifted(original, 64, 32, 304, 448)
    check("J9 a pose passa a ser contada do canto da janela, sem mexer na que veio da deteccao",
          moved["pose_metas"][0].points.tolist() == [[36.0, 168.0]] and moved["pose_metas"][0].width == 304
          and original["pose_metas"][0].points.tolist() == [[100.0, 200.0]] and moved["pose_metas_original"] == ["como veio"])

    # O grafo: com a janela, a pose e o recorte saem do quadro inteiro, e o Animate, a imagem da
    # pessoa e os quadros de que o pedaco continua passam a ter o tamanho que o no da janela diz.
    def windowed(expert, motion=None, **more):
        spec = workflows.resolve({"mode": "swap", "quality": "boa", "seed": 1, "expert": expert, **more}, True, False, True)
        spec.update(width=832, height=480, length=49, chunk=49)
        nodes = workflows.build(spec, files, "p", "n", "x", start_image="person.png", video="v.mp4", motion=motion)[0]
        return nodes, {item["class_type"]: (key, item["inputs"]) for key, item in nodes.items()}

    nodes, kinds = windowed({"window": True}, motion="m.mp4")
    cut = kinds["WanStudioWindow"][0]
    animate, paste = kinds["WanAnimateToVideo"][1], kinds["WanStudioPaste"][1]
    check("J10 grafo com a janela: o Animate gera no tamanho dela, com o que foi cortado nela",
          animate["width"] == [cut, 6] and animate["height"] == [cut, 7] and animate["character_mask"] == [cut, 1]
          and animate["continue_motion"] == [cut, 3] and kinds["DrawViTPose"][1]["pose_data"] == [cut, 2]
          and kinds["ResizeAndPadImage"][1]["target_width"] == [cut, 6] and kinds["ImageCompositeMasked"][1]["destination"] == [cut, 0]
          and nodes[kinds["WanStudioWindow"][1]["mask"][0]]["class_type"] == "ImageToMask"
          and kinds["PoseAndFaceDetection"][1]["width"] == 832, str(animate["width"]))
    check("J11 e os quadros gerados voltam para o video antes de serem gravados, com a cor trazida a do pedaco anterior",
          paste["patch"][0] == kinds["VAEDecode"][0] and paste["x"] == [cut, 4] and paste["y"] == [cut, 5]
          and paste["previous"] == [cut, 3] and paste["overlap"] == 5 and paste["mask"] == [cut, 1]
          and nodes[paste["images"][0]]["class_type"] == "GetVideoComponents"
          and kinds["SaveImage"][1]["images"] == [kinds["WanStudioPaste"][0], 0])
    # ---- A pessoa inteira (`whole`): o recorte do SAM2 mais o que o modelo de primeiro plano acha
    # ligado a ele. O caso do exemplo oficial: a tranca sai da cabeca e pende solta, longe do corpo.
    person = np.zeros((100, 100), dtype=bool)
    person[10:90, 40:60] = True    # o que o SAM2 pegou; a cabeca em cima, perto de (50, 18)
    head = (50.0, 18.0, 16.0)
    found = person.copy()
    found[10:14, 30:40] = True     # a tranca, presa a cabeca...
    found[14:95, 30:33] = True     # ...pendendo longe do corpo
    found[6:10, 42:58] = True      # o chapeu, em cima da cabeca
    found[60:64, 60:75] = True     # um bastao na mao, a meia altura
    found[20:90, 80:95] = True     # outra pessoa, separada
    whole = node.completed(person, found, head)
    check("P1 a tranca solta e o chapeu, presos a cabeca, entram no recorte; o bastao na mao e a outra pessoa nao",
          whole[14:95, 30:33].all() and whole[6:10, 42:58].all() and whole[10:90, 40:60].all()
          and not whole[60:64, 61:75].any() and not whole[20:90, 80:95].any())
    glued = person.copy()
    glued[5:100, 60:100] = True    # alguem encostado nela, ate na altura da cabeca, maior que ela
    check("P2 um vizinho encostado (grande perto da pessoa) fica como esta, mesmo encostando perto da cabeca",
          (node.completed(person, glued, head) == person).all())
    top = node.completed(person, found)
    check("P3 sem a cabeca pela pose vale o terco de cima do recorte; sem pessoa nada entra; sem nada a mais, nada muda",
          top[14:95, 30:33].all() and not top[60:64, 61:75].any()
          and not node.completed(np.zeros((20, 20), dtype=bool), np.ones((20, 20), dtype=bool)).any()
          and (node.completed(person, person, head) == person).all())
    meta = {"keypoints_body": body(**{"0": (0.5, 0.18), "14": (0.47, 0.16), "15": (0.53, 0.16), "1": (0.5, 0.3)})}
    spot = node.head_spot(meta, 100, 100)
    check("P3b a cabeca pela pose: no meio dos pontos do rosto, com raio que chega ao pescoco",
          spot is not None and abs(spot[0] - 50) < 1 and abs(spot[1] - 17.3) < 1 and spot[2] >= 12
          and node.head_spot({"keypoints_body": body()}, 100, 100) is None, str(spot))
    import builtins
    real_import = builtins.__import__

    def without_scipy(name, *args, **kwargs):
        if name.startswith("scipy"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    builtins.__import__ = without_scipy
    try:
        plain = node.completed(person, found, head)
    finally:
        builtins.__import__ = real_import
    check("P4 sem o scipy o resultado e o mesmo", (plain == whole).all())

    def masked(expert, **more):
        spec = workflows.resolve({"mode": "swap", "quality": "boa", "seed": 1, "expert": expert, **more}, True, False, True)
        spec.update(width=832, height=480, length=49, chunk=49)
        nodes = workflows.build(spec, {**files, "foreground": "BiRefNet-portrait"}, "p", "n", "x", start_image="person.png",
                                video="v.mp4")[0]
        return nodes, {item["class_type"]: (key, item["inputs"]) for key, item in nodes.items()}

    whole_nodes, whole_kinds = masked({"whole": True})
    grown = whole_nodes[whole_kinds["GrowMask"][1]["mask"][0]]
    check("P5 grafo com a pessoa inteira: o recorte do SAM2 passa pelo no que o completa antes de ser engordado",
          grown["class_type"] == "WanStudioWholePerson" and grown["inputs"]["mask"] == [whole_kinds["WanStudioPersonMask"][0], 0]
          and grown["inputs"]["pose_data"] == [whole_kinds["PoseAndFaceDetection"][0], 0]
          and whole_kinds["WanStudioForegroundLoader"][1]["model"] == "BiRefNet-portrait"
          and whole_nodes[grown["inputs"]["model"][0]]["class_type"] == "WanStudioForegroundLoader")
    plain_kinds = masked({})[1]
    window_kinds = masked({"whole": True, "window": True})[1]
    check("P6 sem pedir, nada muda; com a janela, a janela recebe o recorte ja completo",
          "WanStudioWholePerson" not in plain_kinds and "WanStudioForegroundLoader" not in plain_kinds
          and "WanStudioWholePerson" in window_kinds and "WanStudioWindow" in window_kinds)

    # ---- O rosto em detalhe (`face`): a janela da cabeca e a segunda passada.
    def face_meta(x, y):  # o rosto em (x, y) num quadro de 480 x 832, o pescoco 40 px abaixo
        return {"keypoints_body": body(**{"0": (x / 480, y / 832), "14": ((x - 8) / 480, (y - 6) / 832),
                                          "15": ((x + 8) / 480, (y - 6) / 832), "1": (x / 480, (y + 40) / 832)})}

    metas = [face_meta(200, 150), face_meta(230, 160), face_meta(260, 150)]
    box, heads = node.head_window(metas, 480, 832)
    radius = heads[0][2]
    check("H1 a janela da cabeca: um quadrado dentro do quadro, com a cabeca de todos os quadros e folga em volta",
          box is not None and all(head for head in heads) and box[0] <= 200 - radius and box[0] + box[2] >= 260 + radius
          and box[1] <= 150 - radius and box[1] + box[2] >= 160 + radius and box[0] + box[2] <= 480 and box[1] + box[2] <= 832,
          f"{box} raio {radius:.0f}")
    check("H2 a cabeca e ampliada ate 3 vezes, para um lado entre 256 e 512 (multiplo de 16); sem rosto, sem janela",
          node.zoom_size(100) == 304 and node.zoom_size(40) == 256 and node.zoom_size(300) == 512
          and node.head_window([{"keypoints_body": body()}], 480, 832)[0] is None)
    check("H3 no canto do quadro a janela desliza para dentro dele",
          node.square_box(10, 10, 100, 480, 832) == (0, 0, 100) and node.square_box(475, 830, 100, 480, 832) == (380, 732, 100))
    people = np.ones((3, 832, 480), dtype=np.float32)
    only = node.head_masks(people, [heads[0], None, heads[2]])
    check("H4 o que a segunda passada refaz: o recorte da pessoa so em volta da cabeca (onde a pose nao achou o rosto, "
          "vale a do quadro vizinho)", only[0, 150, 200] and not only[0, 600, 200] and only[1].sum() > 0
          and (only[1] == only[0]).all() and not node.head_masks(people, [None, None, None]).any())

    class Frames:  # no lugar do tensor da imagem da pessoa
        shape = (1, 832, 480, 3)

        def __getitem__(self, item):
            return item

    cropped = node.WanStudioFitReference().fit(Frames(), {"pose_metas_original": [face_meta(240, 200)]}, 512, 512,
                                               region="cabeca")[0]
    rows, columns = cropped[1], cropped[2]
    check("H5 a referencia da segunda passada e a cabeca da imagem da pessoa, no mesmo enquadramento da janela",
          rows.stop - rows.start == columns.stop - columns.start and rows.start <= 200 - 20 <= 200 + 20 <= rows.stop
          and columns.start <= 240 <= columns.stop, str(cropped[1:3]))
    ring = np.zeros((40, 40), dtype=np.float32)
    ring[5:35, 5:35] = 1           # um recorte que chega a 5 px da borda da janela
    tapered = node.edged(node.soften(ring, 0, 2), 2)
    check("H5b na colagem da cabeca, a passagem e curta e acaba antes da borda da janela",
          tapered[0].max() == 0 and tapered[:, 0].max() == 0 and tapered[20, 20] == 1 and 0 < tapered[20, 1] < 1)
    paster = node.WanStudioFacePaste()
    check("H6 com o rosto ja grande, o no de colar nem pede a cabeca refeita",
          paster.check_lazy_status("quadros", False, 0, 0) == [] and paster.check_lazy_status("quadros", True, 0, 0) == ["patch", "mask"]
          and paster.paste("quadros", False, 0, 0) == ("quadros",))

    face_nodes, face_kinds = windowed({"face": True}, motion="m.mp4")
    animates = [key for key, item in face_nodes.items() if item["class_type"] == "WanAnimateToVideo"]
    decodes = [key for key, item in face_nodes.items() if item["class_type"] == "VAEDecode"]
    head_node = face_kinds["WanStudioHeadWindow"]
    second = face_nodes[animates[-1]]["inputs"]
    final = face_kinds["WanStudioFacePaste"][1]
    check("H7 grafo com o rosto em detalhe: um segundo Animate na cabeca ampliada, com a cabeca da imagem como referencia, "
          "continuando dos quadros recebidos, e o resultado colado antes de gravar",
          len(animates) == 2 and second["width"] == [head_node[0], 7] and second["character_mask"] == [head_node[0], 1]
          and second["continue_motion"] == [head_node[0], 3] and face_kinds["WanStudioFitReference"][1]["region"] == "cabeca"
          and final["active"] == [head_node[0], 8] and face_kinds["SaveImage"][1]["images"] == [face_kinds["WanStudioFacePaste"][0], 0]
          and head_node[1]["images"] == [decodes[0], 0], str(len(animates)))
    both = windowed({"face": True, "window": True})[1]
    check("H8 com a janela tambem: a cabeca e refeita sobre os quadros ja colados no video",
          both["WanStudioHeadWindow"][1]["images"] == [both["WanStudioPaste"][0], 0]
          and "WanStudioFacePaste" not in windowed({})[1])

    # ---- Trocar rosto numa foto (modo "face"): so o rosto, da testa ao queixo.
    face = np.zeros((68, 3), dtype=np.float32)
    for index in range(68):  # um rosto oval em volta de (0.5, 0.3) num quadro de 100 x 100
        angle = 2 * np.pi * index / 68
        face[index] = (0.5 + 0.08 * np.cos(angle), 0.3 + 0.1 * np.sin(angle), 0.9)
    ellipse = node.face_ellipse({"keypoints_face": face}, 100, 100)
    masks = node.face_masks((2, 100, 100), [{"keypoints_face": face}, {}], 100, 100)
    check("U1 o recorte do rosto: a elipse dos pontos do rosto, com a testa, e o quadro sem rosto fica com a do vizinho",
          ellipse is not None and abs(ellipse[0] - 50) < 1 and ellipse[1] < 30 and masks[0, 30, 50] and masks[0, 16, 50]
          and not masks[0, 45, 50] and not masks[0, 30, 65] and (masks[1] == masks[0]).all()
          and node.face_ellipse({"keypoints_face": face[:4]}, 100, 100) is None, str(ellipse))
    front = body(**{"0": (0.5, 0.3), "14": (0.47, 0.27), "15": (0.53, 0.27)})
    back = body(**{"1": (0.5, 0.45), "2": (0.4, 0.45), "5": (0.6, 0.45)})  # de costas: sem nariz nem olhos
    check("U1b de costas (a pose nao acha nariz nem olho) nao ha rosto a trocar, mesmo com pontos do rosto arriscados",
          node.face_ellipse({"keypoints_face": face, "keypoints_body": front}, 100, 100) is not None
          and node.face_ellipse({"keypoints_face": face, "keypoints_body": back}, 100, 100) is None)
    # ---- Trocar rosto numa foto com o FLUX.2 Klein (o padrao): a cabeca refeita numa janela.
    klein_files = {"unet": "klein.gguf", "klein_clip": "qwen3.safetensors", "head_lora": "bfs.safetensors",
                   "vae": "flux2.safetensors", "vitpose": "vp.onnx", "yolo": "y.onnx"}
    photo = workflows.resolve({"mode": "face", "seed": 1}, True, True)
    photo_nodes = workflows.build(photo, klein_files, "p", "n", "x", start_image="rosto.png", end_image="corpo.png")[0]
    listed = {}
    for key, item in photo_nodes.items():
        listed.setdefault(item["class_type"], []).append((key, item["inputs"]))
    loads = {inputs["image"]: key for key, inputs in listed["LoadImage"]}
    crops = {inputs["photo"]: (key, inputs) for key, inputs in listed["WanStudioHeadCrop"]}
    encoded = {inputs["pixels"][0]: key for key, inputs in listed["VAEEncode"]}
    references = dict(listed["ReferenceLatent"])
    sampler = listed["SamplerCustomAdvanced"][0][1]
    guider = photo_nodes[sampler["guider"][0]]["inputs"]
    second = references[guider["positive"][0]]  # a ultima referencia posta: a da foto do rosto
    first = references[second["conditioning"][0]]
    prompt = photo_nodes[first["conditioning"][0]]["inputs"]
    paste_key, paste = listed["WanStudioHeadPaste"][0]
    check("U2 grafo da troca de rosto: a cabeca da foto da pessoa, com folga, refeita pelo Klein com a LoRA, a foto da "
          "pessoa como primeira referencia e a do rosto como segunda, e colada de volta na foto",
          photo["model"] == "klein" and photo["hair"] is True and photo["kind"] == "image"
          and crops["pessoa"][1]["image"] == [loads["corpo.png"], 0] and crops["rosto"][1]["image"] == [loads["rosto.png"], 0]
          and crops["rosto"][1]["span"] == workflows.KLEIN_SOURCE_SPAN and "span" not in crops["pessoa"][1]
          and first["latent"] == [encoded[crops["pessoa"][0]], 0] and second["latent"] == [encoded[crops["rosto"][0]], 0]
          and prompt["text"] == workflows.KLEIN_PROMPT and "head_swap" in prompt["text"]
          and listed["CLIPLoader"][0][1]["type"] == "flux2" and listed["LoraLoaderModelOnly"][0][1]["lora_name"] == "bfs.safetensors"
          and photo_nodes[sampler["sigmas"][0]]["inputs"]["steps"] == 4 and guider["cfg"] == 1.0
          and photo_nodes[sampler["sampler"][0]]["inputs"]["sampler_name"] == "lcm"
          and photo_nodes[sampler["latent_image"][0]]["inputs"]["width"] == [crops["pessoa"][0], 5]
          and paste["images"] == [loads["corpo.png"], 0] and paste["x"] == [crops["pessoa"][0], 1] and paste["region"] == "cabeca"
          and listed["SaveImage"][0][1]["images"] == [paste_key, 0] and workflows.required_set(photo["model"], "face") == "klein",
          str(sorted(listed)))
    bare = workflows.build({**photo, "hair": False}, klein_files, "p", "n", "x", start_image="r.png", end_image="c.png")[0]
    bare_paste = next(item for item in bare.values() if item["class_type"] == "WanStudioHeadPaste")["inputs"]
    bare_texts = [item["inputs"]["text"] for item in bare.values() if item["class_type"] == "CLIPTextEncode"]
    old = workflows.resolve({"mode": "face", "seed": 1, "hair": False, "expert": {"engine": "animate"}}, True, True)
    old_nodes = workflows.build(old, {**files, "lora": "l.safetensors"}, "p", "n", "x", start_image="r.png", end_image="c.png")[0]
    old_head = next(item for item in old_nodes.values() if item["class_type"] == "WanStudioHeadWindow")["inputs"]
    refused = []
    for has_face, has_body in ((False, True), (True, False)):
        try:
            workflows.resolve({"mode": "face"}, has_face, has_body)
        except ValueError as error:
            refused.append(str(error))
    try:
        workflows.resolve({"mode": "face", "expert": {"engine": "outro"}}, True, True)
    except ValueError as error:
        refused.append(str(error))
    check("U3 sem o cabelo so o rosto volta para a foto e o pedido diz para manter o penteado; o Animate fica como ajuste "
          "de teste; sem uma das fotos, recusa dizendo qual falta",
          bare_paste["region"] == "rosto" and workflows.KLEIN_PROMPT_FACE in bare_texts
          and old["model"] == "animate" and workflows.required_set(old["model"], "face") == "animate"
          and old_head["region"] == "rosto" and old_head["always"] is True
          and len(refused) == 3 and "rosto" in refused[0] and "receber o rosto" in refused[1], str(refused))

    # A janela da cabeca numa foto e a colagem de volta (vm/nodes/wan_studio, WanStudioHeadCrop/HeadPaste).
    small = {"keypoints_body": body(**{"0": (0.5, 0.3), "14": (0.49, 0.29), "15": (0.51, 0.29), "1": (0.5, 0.36)})}
    close = {"keypoints_body": body(**{"0": (0.5, 0.45), "14": (0.4, 0.35), "15": (0.6, 0.35), "1": (0.5, 0.9)})}
    region = node.photo_region(small, 1000, 1000)
    check("K1 a janela: um quadrado de 4,5 raios em volta da cabeca pequena; a foto inteira quando a cabeca ja a enche",
          region is not None and region[2] == region[3] and 250 < region[2] < 500 and region[0] < 500 < region[0] + region[2]
          and region[1] < 300 < region[1] + region[3] and node.photo_region(close, 1000, 1000) == (0, 0, 1000, 1000)
          and node.photo_region({"keypoints_body": body()}, 1000, 1000) is None, str(region))
    sizes = node.work_size(360, 360), node.work_size(1000, 1500), node.work_size(3000, 4000)
    check("K2 refeita com uns 1024 x 1024 pixels, na proporcao da janela, lados multiplos de 16",
          sizes[0] == (1024, 1024) and all(side % 16 == 0 for size in sizes for side in size)
          and abs(sizes[1][1] / sizes[1][0] - 1.5) < 0.02 and abs(sizes[2][0] * sizes[2][1] / 1024 ** 2 - 1) < 0.05, str(sizes))
    rng = np.random.default_rng(3)
    frame = np.clip(0.45 + 0.1 * rng.random((200, 200, 3)), 0, 1).astype(np.float32)
    piece = frame[50:150, 50:150].copy() + 0.05  # o modelo clareou tudo um pouco...
    piece[30:70, 30:70] = 0.9                     # ...e trocou a cabeca
    result, fix = node.photo_pasted(frame, piece, 50, 50)
    check("K3 a colagem: a cabeca nova no lugar, a cor trazida a do original pela borda da janela, sem degrau na borda e "
          "nada mudado fora dela",
          fix is not None and abs(fix[0][1] + 0.05) < 0.01 and abs(result[100, 100, 0] - 0.85) < 0.02
          and np.abs(result[:50] - frame[:50]).max() == 0 and np.abs(result[50, 50:150] - frame[50, 50:150]).max() < 0.01
          and np.abs(result[60:140, 60:65] - frame[60:140, 60:65]).max() < 0.02, str(fix))
    edge, _ = node.photo_pasted(frame, piece, 0, 0)
    only, _ = node.photo_pasted(frame, piece, 50, 50, face=(100.0, 100.0, 12.0, 15.0))
    check("K4 na borda da foto a janela vale ate a beirada; so o rosto: fora da elipse fica o original",
          abs(edge[0, 0, 0] - (piece[0, 0, 0] - 0.05)) < 0.02 and abs(only[100, 100, 0] - 0.85) < 0.02
          and np.abs(only[100, 125] - frame[100, 125]).max() < 1e-6 and np.abs(only[75, 100] - frame[75, 100]).max() < 1e-6)

    first = windowed({"window": True, "window_margin": 96})[1]
    loose = windowed({"window": True}, replace=False)[1]
    check("J12 primeiro pedaco: sem cor a igualar; sem manter o cenario nao ha recorte, e a janela nao entra",
          "previous" not in first["WanStudioPaste"][1] and "continue_motion" not in first["WanAnimateToVideo"][1]
          and first["WanStudioWindow"][1]["margin"] == 96 and "margin" not in kinds["WanStudioWindow"][1]
          and "WanStudioWindow" not in loose and "WanStudioPaste" not in loose and loose["WanAnimateToVideo"][1]["width"] == 832)

    print(f"\n{len(FAILED)} falha(s)")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
