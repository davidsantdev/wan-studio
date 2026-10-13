"""Nos do Wan Studio para o ComfyUI (vm/setup.sh copia esta pasta para custom_nodes).

WanStudioPersonMask: a mascara da pessoa ao longo de um video, pelo SAM2 de video, a partir da
deteccao de pose. O no pronto do pacote do SAM2 so aceita uma caixa (ou pontos) no primeiro
quadro; com isso o recorte dependia de um quadro so e, em testes reais, pegou so a cabeca ou
so o tronco, e o resto da pessoa original ficou no video trocado. Aqui o SAM2 recebe pistas em
varios quadros-chave, por um destes metodos:

  juntos  o "caixas" e o "pontos" ao mesmo tempo, como dois objetos; a mascara e a uniao. Cada
          um falha num lugar: medido na GPU, com a pessoa de costas enchendo o quadro, a caixa
          deixou de fora o miolo das costas da jaqueta e os pontos nao
  caixas  em cada quadro-chave, a caixa em volta de todos os pontos detectados (corpo, maos e
          rosto), com folga para cabelo e roupa, mais os pontos do corpo
  pontos  so os pontos do corpo em cada quadro-chave: o metodo do pre-processamento oficial do
          Wan 2.2 Animate
  partes  cabeca, tronco, bracos e pernas como objetos separados, cada um com seus pontos; a
          mascara e a uniao deles (para roupas em varias pecas, que o SAM2 tende a separar)
  caixa   so a caixa da pessoa no primeiro quadro (como o no do pacote do SAM2)

`cover` diz o que sai: "contorno" e a mascara do SAM2 como ela e; "caixa" e o que o
pre-processamento oficial do Wan 2.2 Animate faz por padrao na troca de pessoa (w_len = h_len =
1): a mascara engordada e trocada pelo retangulo em volta dela, quadro a quadro. Com o contorno,
o que o SAM2 nao pega da pessoa original (uma tranca, uma manga larga, a alca de uma bolsa)
continua no video; com a caixa, tudo o que esta em volta da pessoa e refeito.

`seal` fecha o recorte depois do SAM2 (veja `sealed` e `steady`). O motivo, medido
em 12/10/2026 num video em pe com a pessoa de meio corpo: um buraco no recorte so nos primeiros
quadros (o miolo e a barra de uma jaqueta) bastou para a jaqueta da pessoa original ficar no
video inteiro. O modelo ve o pedaco de roupa que sobrou, completa a roupa em volta dele e a
mantem dali em diante. Uma caixa com folga em volta da cabeca (para cabelo e enfeites) foi
testada no mesmo dia e saiu: num close ela cobria o quadro quase todo, e o cenario se perdia.
"""

import numpy as np
import torch

import comfy.model_management as mm
from comfy.utils import ProgressBar, common_upscale

# Indices no formato de 18 pontos (OpenPose): 0 nariz, 1 pescoco, 2-4 braco direito, 5-7 braco
# esquerdo, 8-10 perna direita, 11-13 perna esquerda, 14-17 olhos e orelhas.
OFFICIAL_POINTS = (0, 1, 2, 5, 8, 11, 10, 13)  # os que o codigo oficial usa
PARTS = {
    "cabeca": (0, 14, 15, 16, 17),
    "tronco": (1, 2, 5, 8, 11),
    "braco direito": (2, 3, 4),
    "braco esquerdo": (5, 6, 7),
    "perna direita": (8, 9, 10),
    "perna esquerda": (11, 12, 13),
}
# Um ponto mal detectado (junta fora do quadro ou escondida) apontaria para o fundo.
MIN_CONFIDENCE = 0.3
METHODS = ("juntos", "caixas", "pontos", "partes", "caixa")
COVERS = ("contorno", "caixa")
ANKLES = (10, 13)
# Para dizer que um tornozelo esta a vista a pose precisa ter mais certeza que para um ponto
# qualquer: de uma pessoa de meio corpo ela as vezes arrisca um na borda do quadro.
ANKLE_CONFIDENCE = 0.5
# A partir de que altura do quadro (fracao, de cima para baixo) o recorte de uma pessoa cortada
# pela borda de baixo e levado ate a borda.
REACH = 0.75
# E a partir de que altura as bordas dos lados contam como parede ao tampar o que o recorte
# cerca: medido, um bolsao no canto de baixo comecava a 65% da altura.
SIDES = 0.6


def fill_holes(mask):
    """Tampa o que a mascara (A x L, bool) cerca por todos os lados."""
    try:
        from scipy.ndimage import binary_fill_holes
    except ImportError:  # sem o scipy: o fundo e o que se alcanca a partir das bordas
        outside = np.zeros_like(mask)
        outside[0], outside[-1], outside[:, 0], outside[:, -1] = ~mask[0], ~mask[-1], ~mask[:, 0], ~mask[:, -1]
        while True:
            grown = outside.copy()
            grown[1:] |= outside[:-1]
            grown[:-1] |= outside[1:]
            grown[:, 1:] |= outside[:, :-1]
            grown[:, :-1] |= outside[:, 1:]
            grown &= ~mask
            if (grown == outside).all():
                return ~outside
            outside = grown
    return binary_fill_holes(mask)


def cut_below(meta, width, height):
    """Se a pessoa esta cortada pela borda de baixo do quadro: a pose nao achou tornozelo."""
    return len(confident(meta["keypoints_body"], width, height, ANKLES, ANKLE_CONFIDENCE)) == 0


def sealed(mask, reach=REACH, sides=SIDES):
    """O recorte (A x L, bool) de uma pessoa cortada pela borda de baixo, fechado: onde ele ja
    chega ao quarto de baixo do quadro, desce ate a borda (o corpo continua para fora do
    quadro, e o SAM2 costuma parar num cinto ou numa costura); e o que ficar cercado por ele e
    pela borda de baixo e tampado. Na parte de baixo do quadro (`sides`), as bordas dos lados
    tambem cercam."""
    height, width = mask.shape
    result = mask.copy()
    columns = np.nonzero(mask.any(axis=0))[0]
    if not len(columns):
        return result
    lowest = height - 1 - np.argmax(mask[::-1][:, columns], axis=0)  # a ultima linha de cada coluna
    start = np.where(lowest >= reach * height, lowest, height)
    result[:, columns] |= np.arange(height)[:, None] >= start[None, :]
    walled = np.ones((height + 1, width + 2), dtype=bool)
    walled[:height, 1:-1] = result
    walled[:int(sides * height), [0, -1]] = False
    return fill_holes(walled)[:height, 1:-1]


def close(masks, metas, width, height):
    """Os recortes de um pedaco (N x A x L, bool) fechados, se a pessoa esta cortada pela borda
    de baixo do quadro; senao, como vieram."""
    metas = [metas[min(index, len(metas) - 1)] for index in range(len(masks))]
    # Vale para o pedaco todo o que acontece na maior parte dele: decidindo quadro a quadro, um
    # tornozelo escondido num passo de danca faria o recorte mudar de forma de repente.
    if 2 * sum(cut_below(meta, width, height) for meta in metas) < len(metas):
        return masks
    return np.stack([sealed(mask) if mask.any() else mask for mask in masks])


def steady(masks):
    """Cada quadro fica tambem com o recorte dos dois vizinhos (N x A x L, 0 ou 1): o SAM2 as
    vezes larga um pedaco da pessoa num quadro so."""
    return torch.nn.functional.max_pool3d(masks[None, None], (3, 1, 1), stride=1, padding=(1, 0, 0))[0, 0]


def boxed(masks, kernel=7, iterations=3, margin=0.08):
    """Cada mascara (N x A x L, 0 ou 1) engordada (`iterations` dilatacoes de `kernel` pixels,
    os valores do codigo oficial) e trocada pelo retangulo que a contem, com uma folga de
    `margin` do tamanho dele para cada lado: sem a folga, medido na GPU, a ponta de uma tranca
    que o SAM2 nao pegou ficou de fora do retangulo."""
    grown = masks[:, None]
    for _ in range(iterations):
        grown = torch.nn.functional.max_pool2d(grown, kernel, stride=1, padding=kernel // 2)
    result = torch.zeros_like(masks)
    for index, mask in enumerate(grown[:, 0]):
        rows, columns = torch.nonzero(mask > 0.5, as_tuple=True)
        if len(rows):
            top, bottom, left, right = int(rows.min()), int(rows.max()) + 1, int(columns.min()), int(columns.max()) + 1
            pad_y, pad_x = int((bottom - top) * margin), int((right - left) * margin)
            result[index, max(0, top - pad_y):bottom + pad_y, max(0, left - pad_x):right + pad_x] = 1.0
    return result


def confident(points, width, height, indexes=None, floor=MIN_CONFIDENCE):
    """Os pontos confiaveis de uma matriz N x 3 (x, y de 0 a 1, confianca), em pixels: N x 2."""
    points = np.asarray(points, dtype=np.float32).reshape(-1, np.asarray(points).shape[-1])
    if indexes is not None:
        points = points[[index for index in indexes if index < len(points)]]
    keep = (points[:, 0] > 0) & (points[:, 0] < 1) & (points[:, 1] > 0) & (points[:, 1] < 1)
    if points.shape[1] > 2:
        keep &= points[:, 2] >= floor
    return points[keep][:, :2] * np.array([[width, height]], dtype=np.float32)


# Folga da caixa em volta dos pontos da pose, como fracao do tamanho dela: (lados, cima,
# baixo). Com a "justa" o SAM2 deixou de fora uma tranca que pendia atras das costas com a
# pessoa de lado (a caixa e um limite para ele), e a tranca voltava no video. A "larga" e a
# tentativa de resolver isso; ainda nao foi conferida na GPU, entao nao e o padrao.
BOXES = {"justa": (0.12, 0.20, 0.12), "larga": (0.30, 0.30, 0.12)}


def person_box(meta, width, height, margins=BOXES["justa"]):
    """A caixa em volta de tudo o que a pose achou da pessoa, com folga para cabelo, chapeu e
    roupa (`margins`: lados, cima, baixo). None se a pose achou pouco."""
    groups = [confident(meta[key], width, height) for key in
              ("keypoints_body", "keypoints_left_hand", "keypoints_right_hand", "keypoints_face") if key in meta]
    points = np.concatenate([group for group in groups if len(group)] or [np.zeros((0, 2), np.float32)])
    if len(points) < 3:
        return None
    x1, y1 = points.min(axis=0)
    x2, y2 = points.max(axis=0)
    side, top, bottom = margins
    box = np.array([x1 - side * (x2 - x1), y1 - top * (y2 - y1), x2 + side * (x2 - x1), y2 + bottom * (y2 - y1)],
                   dtype=np.float32)
    box[[0, 2]] = box[[0, 2]].clip(0, width - 1)
    box[[1, 3]] = box[[1, 3]].clip(0, height - 1)
    return box if box[2] - box[0] >= 10 and box[3] - box[1] >= 10 else None


class WanStudioPersonMask:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "sam2_model": ("SAM2MODEL",),
            "images": ("IMAGE",),
            "pose_data": ("POSEDATA",),
            "method": (list(METHODS), {"default": "juntos"}),
            "key_frames": ("INT", {"default": 4, "min": 1, "max": 32,
                                   "tooltip": "Em quantos quadros, espalhados pelo video, o SAM2 recebe as pistas."}),
        }, "optional": {
            "bboxes": ("BBOX", {"tooltip": "A caixa da pessoa no primeiro quadro, da deteccao (so o metodo 'caixa' usa)."}),
            "cover": (list(COVERS), {"default": "contorno",
                                     "tooltip": "contorno: a mascara do SAM2; caixa: o retangulo em volta dela."}),
            "box": (list(BOXES), {"default": "justa", "tooltip": "Folga da caixa do metodo 'caixas' em volta da pose."}),
            "seal": ("BOOLEAN", {"default": True,
                                 "tooltip": "Fecha o recorte: corpo ate a borda de baixo do quadro, quadros vizinhos."}),
        }}

    RETURN_TYPES = ("MASK",)
    RETURN_NAMES = ("mask",)
    FUNCTION = "segment"
    CATEGORY = "Wan Studio"
    DESCRIPTION = "Mascara da pessoa em todos os quadros, com pistas da pose em quadros-chave (SAM2 de video)."

    def prompts(self, method, metas, count, width, height, key_frames, bboxes, box="justa"):
        """As pistas do metodo: [(quadro, objeto, pontos ou None, caixa ou None), ...]."""
        if method == "caixa":
            if not bboxes or len(bboxes[0]) < 4:
                return []
            x1, y1, x2, y2 = (float(value) for value in bboxes[0][:4])
            return [(0, 1, None, np.array([x1, y1, x2, y2], np.float32))] if x2 - x1 >= 10 and y2 - y1 >= 10 else []
        found = []
        for index in range(0, count, max(1, count // key_frames)):
            meta = metas[min(index, len(metas) - 1)]
            if method == "partes":
                for number, indexes in enumerate(PARTS.values(), start=1):
                    points = confident(meta["keypoints_body"], width, height, indexes)
                    if len(points):
                        found.append((index, number, points, None))
                continue
            points = confident(meta["keypoints_body"], width, height,
                               OFFICIAL_POINTS if method == "pontos" else None)
            around = person_box(meta, width, height, BOXES[box]) if method in ("caixas", "juntos") else None
            if len(points) >= 2 or around is not None:
                found.append((index, 1, points if len(points) else None, around))
            if method == "juntos":  # o segundo objeto: so os pontos, sem a caixa que limita o SAM2
                loose = confident(meta["keypoints_body"], width, height, OFFICIAL_POINTS)
                if len(loose) >= 2:
                    found.append((index, 2, loose, None))
        return found

    def segment(self, sam2_model, images, pose_data, method, key_frames, bboxes=None, cover="contorno", box="justa",
                seal=True):
        model, device, dtype = sam2_model["model"], sam2_model["device"], sam2_model["dtype"]
        if sam2_model["segmentor"] != "video":
            raise ValueError("o modelo SAM2 precisa ser carregado como 'video'")
        count, height, width, _ = images.shape
        found = self.prompts(method, pose_data["pose_metas_original"], count, width, height, key_frames, bboxes, box)
        if not found:
            raise ValueError("não encontrei a pessoa neste trecho do vídeo")

        size = model.image_size
        frames = common_upscale(images.movedim(-1, 1), size, size, "bilinear", "disabled")
        model.to(device)
        masks = {}
        progress = ProgressBar(count)
        with torch.autocast(mm.get_autocast_device(device), dtype=dtype):
            state = model.init_state(frames.contiguous(), height, width, device=device)
            for index, obj_id, points, box in found:
                labels = None if points is None else np.ones(len(points), dtype=np.int32)
                model.add_new_points_or_box(inference_state=state, frame_idx=index, obj_id=obj_id,
                                            points=points, labels=labels, box=box)
            first = min(index for index, _, _, _ in found)
            passes = [{"start_frame_idx": first}]
            if first > 0:  # a pessoa so apareceu depois do comeco: acompanha tambem para tras
                passes.append({"start_frame_idx": first, "reverse": True})
            for options in passes:
                for frame, _ids, logits in model.propagate_in_video(state, **options):
                    # A uniao de todos os objetos (as partes do corpo, no metodo "partes").
                    masks[frame] = (logits[:, 0] > 0.0).any(dim=0).cpu()
                    progress.update(1)
            model.reset_state(state)
        model.to(mm.unet_offload_device())
        mm.soft_empty_cache()

        empty = torch.zeros((height, width), dtype=torch.bool)
        result = torch.stack([masks.get(index, empty) for index in range(count)])
        if seal:
            closed = close(result.numpy(), pose_data["pose_metas_original"], width, height)
            result = steady(torch.from_numpy(closed).float())
        result = result.float()
        return (boxed(result) if cover == "caixa" else result,)


NODE_CLASS_MAPPINGS = {"WanStudioPersonMask": WanStudioPersonMask}
NODE_DISPLAY_NAME_MAPPINGS = {"WanStudioPersonMask": "Wan Studio: máscara da pessoa"}
