"""Nos do Wan Studio para o ComfyUI (vm/setup.sh copia esta pasta para custom_nodes).

WanStudioFitReference: recorta a imagem da pessoa na caixa dela, na proporcao do quadro de
geracao (veja `fit_crop`), para ela nao entrar pequena entre faixas pretas. E opcional
(`ref=crop` na troca de pessoa) e so vira padrao depois de conferido na GPU.

WanStudioForegroundLoader e WanStudioWholePerson: o recorte da pessoa inteira (`whole` na
troca de pessoa, opcional e ainda sem conferencia na GPU): completa o do SAM2 com o de um
modelo de recorte de primeiro plano, que pega cabelo solto, trancas e roupa larga.

WanStudioHeadWindow e WanStudioFacePaste: o rosto em detalhe (`face` na troca de pessoa, tambem
opcional e sem conferencia na GPU): uma segunda passada do Animate so na cabeca, ampliada.

WanStudioWindow e WanStudioPaste: a "janela na pessoa" (`window` na troca de pessoa, tambem
opcional e ainda sem conferencia na GPU). O primeiro recorta do pedaco o retangulo em que a
pessoa esta, para o Animate gerar so ali; o segundo poe os quadros gerados de volta no video,
so onde o recorte da pessoa manda. Estao no fim do arquivo.

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

import copy

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


# Folga em volta da caixa da pessoa na imagem de referencia (fracao do tamanho dela, cada lado).
REFERENCE_MARGIN = 0.08
# O recorte nao e ampliado alem de 1 / REFERENCE_FLOOR vezes: a imagem de uma pessoa pequena num
# quadro grande ficaria borrada, e o modelo copia o que a imagem tem.
REFERENCE_FLOOR = 0.5
# Um recorte que ja pega quase a imagem toda nao vale a reamostragem.
REFERENCE_WHOLE = 0.95


def fit_crop(box, width, height, target_width, target_height, margin=REFERENCE_MARGIN, floor=REFERENCE_FLOOR):
    """O retangulo (x1, y1, x2, y2, em pixels) da imagem de referencia (`width` x `height`) que
    leva a pessoa (`box`) ao quadro de geracao (`target_width` x `target_height`) tao grande
    quanto o formato dele permite. None quando recortar nao muda nada (a caixa e invalida ou o
    recorte e quase a imagem inteira).

    A imagem inteira com faixas pretas (o que o fluxo fazia) deixa a pessoa pequena quando a
    proporcao difere da do video, e e o que o README conta do gorro que se perdia. O recorte tem
    a proporcao do quadro e contem a caixa; onde a imagem acaba antes disso, ele fica com o que
    ha, e as faixas pretas voltam so no que falta."""
    x1, y1, x2, y2 = (float(value) for value in box)
    if x2 - x1 < 10 or y2 - y1 < 10:
        return None
    pad_x, pad_y = (x2 - x1) * margin, (y2 - y1) * margin
    x1, x2 = max(0.0, x1 - pad_x), min(float(width), x2 + pad_x)
    y1, y2 = max(0.0, y1 - pad_y), min(float(height), y2 + pad_y)
    ratio = target_width / target_height
    need_width = max(x2 - x1, (y2 - y1) * ratio, floor * target_width)
    need_height = max(need_width / ratio, floor * target_height)
    need_width = need_height * ratio
    crop_width, crop_height = min(need_width, float(width)), min(need_height, float(height))
    if crop_width >= REFERENCE_WHOLE * width and crop_height >= REFERENCE_WHOLE * height:
        return None
    left = min(max((x1 + x2) / 2 - crop_width / 2, 0.0), width - crop_width)
    top = min(max((y1 + y2) / 2 - crop_height / 2, 0.0), height - crop_height)
    left, top = int(left), int(top)
    return left, top, min(width, left + max(1, int(round(crop_width)))), min(height, top + max(1, int(round(crop_height))))


class WanStudioFitReference:
    """Recorta a imagem da pessoa na caixa dela, no formato do video, antes de ela entrar no
    Animate. Sem pessoa achada, devolve a imagem como veio."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "image": ("IMAGE",),
            "pose_data": ("POSEDATA", {"tooltip": "A pose da imagem de referencia (PoseAndFaceDetection)."}),
            "width": ("INT", {"default": 832, "min": 64, "max": 2048}),
            "height": ("INT", {"default": 480, "min": 64, "max": 2048}),
        }, "optional": {
            "bboxes": ("BBOX", {"tooltip": "A caixa da pessoa pelo detector; junta-se a que sai dos pontos da pose."}),
            "region": (["pessoa", "cabeca"], {"default": "pessoa",
                                              "tooltip": "pessoa: a pessoa inteira; cabeca: so a cabeca, como a janela do rosto em detalhe."}),
        }}

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "fit"
    CATEGORY = "Wan Studio"
    DESCRIPTION = "Recorta a imagem da pessoa na caixa dela, na proporcao do quadro de geracao."

    def box(self, pose_data, bboxes, width, height):
        """A uniao da caixa em volta dos pontos da pose e da do detector; None se nao achou."""
        boxes = []
        metas = pose_data.get("pose_metas_original") or []
        around = person_box(metas[0], width, height) if metas else None
        if around is not None:
            boxes.append(around)
        if bboxes and len(bboxes[0]) >= 4:
            detected = np.array([float(value) for value in bboxes[0][:4]], dtype=np.float32)
            if detected[2] - detected[0] >= 10 and detected[3] - detected[1] >= 10:
                boxes.append(detected)
        if not boxes:
            return None
        stacked = np.stack(boxes)
        return stacked[:, 0].min(), stacked[:, 1].min(), stacked[:, 2].max(), stacked[:, 3].max()

    def fit(self, image, pose_data, width, height, bboxes=None, region="pessoa"):
        _, source_height, source_width, _ = image.shape
        if region == "cabeca":  # o mesmo enquadramento da janela da cabeca no video
            metas = pose_data.get("pose_metas_original") or []
            head = head_spot(metas[0], source_width, source_height) if metas else None
            if head is None:
                return (image,)
            left, top, side = square_box(head[0], head[1], FACE_SPAN * head[2], source_width, source_height)
            return (image[:, top:top + side, left:left + side, :],)
        box = self.box(pose_data, bboxes, source_width, source_height)
        crop = None if box is None else fit_crop(box, source_width, source_height, width, height)
        if crop is None:
            return (image,)
        left, top, right, bottom = crop
        return (image[:, top:bottom, left:right, :],)


# ---------------------------------------------------------------- janela na pessoa
#
# Com `window` (ajuste de teste da troca de pessoa) o Animate gera so o retangulo do quadro em
# que a pessoa esta, em vez do quadro inteiro. O tempo de cada passo cresce mais depressa que o
# numero de pixels (medido na T4: 1,5 vez os pixels, 1,9 vez o tempo), entao um video em que a
# pessoa ocupa metade do quadro sai em bem menos da metade do tempo. Os quadros gerados voltam
# depois para o lugar deles, so onde o recorte da pessoa manda: o cenario fica com os pixels do
# video de origem, em vez de passar pelo modelo. Nada disto foi conferido na GPU ainda.

# Cenario em volta do recorte, de cada lado: uma fracao do tamanho dele e, no minimo, tantos
# pixels. E nele que o modelo se apoia para a luz e para o que inventa atras da pessoa: num
# teste de 12/10/2026 com a pessoa enchendo o quadro, sem cenario a vista, ele inventou uma mesa.
WINDOW_CONTEXT = 0.2
WINDOW_MARGIN = 64
WINDOW_SIDE = 256      # a janela nao fica menor que isto em nenhum lado
WINDOW_RATIO = 2.5     # nem mais comprida que isto
WINDOW_WHOLE = 0.85    # acima desta fracao do quadro nao compensa: o pedaco e gerado inteiro
PASTE_GROW = 8         # os quadros gerados valem ate estes pixels alem do recorte...
PASTE_FEATHER = 8      # ...e se desfazem no video de origem ao longo do dobro destes
# A cor dos quadros gerados e trazida a do pedaco de que este continua (a saturacao costuma
# subir de um pedaco para o seguinte). Sao as contas de app/stitch.py (`match`), feitas aqui so
# dentro do recorte: na emenda, o quadro inteiro diluiria a diferenca no cenario, que e igual.
COLOR_SPAN = 4         # quadros de cada lado da emenda que entram na medida
COLOR_GAIN = 1.25      # limites da correcao: alem disso a diferenca nao e de cor, e de conteudo
COLOR_OFFSET = 24 / 255
SCENE_GAIN = 0.06      # se o proprio video muda isto ali (corte de cena), nao se corrige
SCENE_OFFSET = 4 / 255
NEUTRAL = (1.0, 0.0)
# RGB -> Y, U, V (BT.601, de 0 a 1, com a cor em torno de 0) e a volta.
YUV = np.array([[0.299, 0.587, 0.114], [-0.168736, -0.331264, 0.5], [0.5, -0.418688, -0.081312]], dtype=np.float32)
RGB = np.linalg.inv(YUV).astype(np.float32)


def mask_bounds(masks):
    """O retangulo (esquerda, cima, direita, baixo) que as mascaras de um pedaco (N x A x L)
    ocupam, somadas; None se estao vazias."""
    covered = (np.asarray(masks) > 0.5).any(axis=0)
    rows, columns = np.nonzero(covered.any(axis=1))[0], np.nonzero(covered.any(axis=0))[0]
    if not len(rows):
        return None
    return int(columns[0]), int(rows[0]), int(columns[-1]) + 1, int(rows[-1]) + 1


def window_box(left, top, right, bottom, width, height, context=WINDOW_CONTEXT, margin=WINDOW_MARGIN,
               side=WINDOW_SIDE, ratio=WINDOW_RATIO, whole=WINDOW_WHOLE):
    """A janela (x, y, largura, altura, em multiplos de 16) com o retangulo que o recorte da
    pessoa ocupa ao longo do pedaco e cenario em volta dele, num quadro de `width` x `height`.
    None quando ela pegaria quase o quadro todo, ou o retangulo e vazio: ai o pedaco e gerado
    inteiro, como sempre."""
    if right - left < 1 or bottom - top < 1:
        return None
    limit_x, limit_y = width // 16 * 16, height // 16 * 16
    pad_x, pad_y = max(margin, context * (right - left)), max(margin, context * (bottom - top))
    x1, x2 = max(0, int(left - pad_x) // 16 * 16), min(limit_x, _up16(right + pad_x))
    y1, y2 = max(0, int(top - pad_y) // 16 * 16), min(limit_y, _up16(bottom + pad_y))
    x1, x2 = _widened(x1, x2, max(min(side, limit_x), _up16((y2 - y1) / ratio)), limit_x)
    y1, y2 = _widened(y1, y2, max(min(side, limit_y), _up16((x2 - x1) / ratio)), limit_y)
    if (x2 - x1) * (y2 - y1) >= whole * width * height:
        return None
    return x1, y1, x2 - x1, y2 - y1


def _up16(value):
    return -(-int(np.ceil(value)) // 16) * 16


def _widened(first, last, size, limit):
    """O trecho [first, last) alargado ate `size` em volta do meio dele, sem sair de [0, limit)
    (tudo em multiplos de 16)."""
    size = min(limit, max(size, last - first))
    first = min(max(0, (first + last - size) // 2 // 16 * 16), limit - size)
    return first, first + size


def shifted(pose_data, x, y, width, height):
    """Os dados de pose com os pontos contados a partir do canto da janela. O desenho da pose
    usa `pose_metas` (em pixels); os originais, que so o recorte usa, ficam como estao."""
    moved = dict(pose_data)
    moved["pose_metas"] = [copy.deepcopy(meta).crop(x, y, x + width, y + height) if hasattr(meta, "crop") else meta
                           for meta in pose_data.get("pose_metas") or []]
    return moved


def box_sum(values, radius):
    """A soma de cada quadrado de lado 2 * `radius` + 1 (cortado nas bordas) de uma matriz A x L."""
    for axis in (0, 1):
        size = values.shape[axis]
        total = np.cumsum(values, axis=axis, dtype=np.float64)
        total = np.concatenate([np.zeros_like(total.take([0], axis=axis)), total], axis=axis)
        last = np.minimum(np.arange(size) + radius + 1, size)
        first = np.maximum(np.arange(size) - radius, 0)
        values = total.take(last, axis=axis) - total.take(first, axis=axis)
    return values


def soften(mask, grow=PASTE_GROW, feather=PASTE_FEATHER):
    """Quanto os quadros gerados valem em cada pixel ao voltarem para o video, a partir do
    recorte da pessoa (A x L, 0 ou 1): 1 no recorte e ate `grow` pixels em volta dele, caindo
    a 0 ao longo dos 2 * `feather` seguintes. A passagem fica toda no cenario, que os dois
    lados tem igual."""
    solid = (np.asarray(mask) > 0.5).astype(np.float32)
    spread = (box_sum(solid, grow + feather) > 0.5).astype(np.float32)
    return (box_sum(spread, feather) / box_sum(np.ones_like(solid), feather)).astype(np.float32)


def color_stats(frames, weights):
    """Media e desvio de Y, U e V dos quadros (N x A x L x 3, de 0 a 1), contando cada pixel
    pelo peso dele; None se quase nao ha o que medir."""
    total = float(sum(weight.sum() for weight in weights))
    if total < 16:
        return None
    sums, squares = np.zeros(3), np.zeros(3)
    for frame, weight in zip(frames, weights):
        yuv = np.asarray(frame)[..., :3] @ YUV.T
        sums += (yuv * weight[..., None]).sum(axis=(0, 1))
        squares += (yuv * yuv * weight[..., None]).sum(axis=(0, 1))
    means = sums / total
    return list(zip(means, np.sqrt(np.maximum(squares / total - means * means, 0))))


def color_match(reference, current):
    """Ganho e deslocamento, por canal, que levam as estatisticas `current` ate `reference`."""
    fixes = []
    for (ref_mean, ref_spread), (mean, spread) in zip(reference, current):
        gain = ref_spread / spread if spread > 1 / 255 and ref_spread > 1 / 255 else 1.0
        gain = min(COLOR_GAIN, max(1 / COLOR_GAIN, gain))
        offset = min(COLOR_OFFSET, max(-COLOR_OFFSET, ref_mean - gain * mean))
        fixes.append(NEUTRAL if abs(gain - 1) < 0.01 and abs(offset) < 0.75 / 255 else (float(gain), float(offset)))
    return fixes


def color_fix(patch, previous, originals, weights, overlap, span=COLOR_SPAN):
    """A correcao de cor (ganho e deslocamento de Y, U e V) que leva os quadros gerados
    (`patch`) a cor do pedaco de que este continua, ou None se nao ha o que corrigir.

    `previous` sao os quadros desse pedaco que este repete (os `overlap` primeiros dele, ja na
    janela); `originals`, os do video de origem na janela, quadro a quadro com `patch`. Os
    quadros logo depois da emenda sao comparados com os de logo antes: como sao instantes
    diferentes, o que o video de origem muda entre uns e outros nao e corrigido, e, se ele
    muda demais (corte de cena), nao ha o que comparar."""
    count = min(span, overlap, len(previous), len(patch) - overlap)
    if count < 1 or tuple(previous.shape[1:3]) != tuple(patch.shape[1:3]):
        return None
    after, before = slice(overlap, overlap + count), slice(overlap - count, overlap)
    ours = color_stats(patch[after], weights[after])
    base = color_stats(previous[-count:], weights[before])
    scene_after, scene_before = color_stats(originals[after], weights[after]), color_stats(originals[before], weights[before])
    if not (ours and base and scene_after and scene_before):
        return None
    change = color_match(scene_after, scene_before)
    if any(abs(gain - 1) > SCENE_GAIN or abs(offset) > SCENE_OFFSET for gain, offset in change):
        return None
    expected = [(gain * mean + offset, gain * spread) for (mean, spread), (gain, offset) in zip(base, change)]
    fix = color_match(expected, ours)
    return None if all(item == NEUTRAL for item in fix) else fix


def recolored(frame, fix):
    """O quadro (A x L x 3, de 0 a 1) com a correcao de `color_fix` aplicada."""
    gains = np.array([gain for gain, _ in fix], dtype=np.float32)
    offsets = np.array([offset for _, offset in fix], dtype=np.float32)
    return np.clip(((frame @ YUV.T) * gains + offsets) @ RGB.T, 0.0, 1.0)


def edged(weight, feather):
    """O peso (A x L) caindo a 0 nos `feather` pixels junto das bordas: nada da colagem encosta
    na borda da janela (onde, sem isso, haveria um degrau)."""
    height, width = weight.shape
    rows = np.minimum(np.arange(height), np.arange(height)[::-1]) / max(1, feather)
    columns = np.minimum(np.arange(width), np.arange(width)[::-1]) / max(1, feather)
    return weight * np.clip(np.minimum(rows[:, None], columns[None, :]), 0, 1).astype(np.float32)


def pasted(frames, patch, masks, x, y, previous=None, overlap=0, grow=PASTE_GROW, feather=PASTE_FEATHER, edge=False):
    """Os quadros do video (N x A x L x 3) com os gerados (`patch`, do tamanho da janela)
    de volta no lugar, a partir do canto (`x`, `y`), so onde o recorte da pessoa (`masks`, na
    janela) manda. Com `previous` e `overlap`, a cor dos gerados e antes trazida a do pedaco
    de que este continua (veja `color_fix`). `grow` e `feather` sao os de `soften`; com `edge`,
    o peso cai a 0 junto da borda da janela (`edged`). Devolve (quadros, correcao de cor ou None)."""
    count = min(len(frames), len(patch))
    result = np.array(frames[:count, :, :, :3], dtype=np.float32)
    # A janela sempre cabe no quadro; o corte e so para um tamanho inesperado nao derrubar o pedaco.
    height, width = min(patch.shape[1], result.shape[1] - y), min(patch.shape[2], result.shape[2] - x)
    patch = patch[:, :height, :width]
    if tuple(masks.shape[1:3]) == (height, width):
        weights = [soften(masks[min(index, len(masks) - 1)], grow, feather) for index in range(count)]
        if edge:
            weights = [edged(weight, feather) for weight in weights]
    else:
        weights = [np.ones((height, width), dtype=np.float32)] * count
    fix = None
    if previous is not None and overlap > 0:
        fix = color_fix(patch, previous, result[:, y:y + height, x:x + width], weights, overlap)
    for index in range(count):
        new = np.asarray(patch[index, :, :, :3], dtype=np.float32)
        if fix:
            new = recolored(new, fix)
        alpha = weights[index][..., None]
        region = result[index, y:y + height, x:x + width]
        region[:] = region * (1 - alpha) + new * alpha
    return result, fix


class WanStudioWindow:
    """Recorta de um pedaco a janela em que a pessoa esta: os quadros, o recorte dela, a pose e
    os quadros de que o pedaco continua. O Animate gera so ali."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "images": ("IMAGE",),
            "mask": ("MASK", {"tooltip": "O recorte da pessoa em cada quadro, ja engordado, como vai para o Animate."}),
            "pose_data": ("POSEDATA",),
        }, "optional": {
            "motion": ("IMAGE", {"tooltip": "Os quadros de que o pedaco continua, no quadro inteiro."}),
            "margin": ("INT", {"default": WINDOW_MARGIN, "min": 16, "max": 512,
                               "tooltip": "Cenario em volta do recorte, de cada lado, em pixels (no minimo)."}),
        }}

    RETURN_TYPES = ("IMAGE", "MASK", "POSEDATA", "IMAGE", "INT", "INT", "INT", "INT")
    RETURN_NAMES = ("images", "mask", "pose_data", "motion", "x", "y", "width", "height")
    FUNCTION = "cut"
    CATEGORY = "Wan Studio"
    DESCRIPTION = "A janela do quadro em que a pessoa esta, para gerar so ali."

    def cut(self, images, mask, pose_data, motion=None, margin=WINDOW_MARGIN):
        _, height, width, _ = images.shape
        box = None
        if tuple(mask.shape[-2:]) == (height, width):
            bounds = mask_bounds(mask.cpu().numpy())
            box = window_box(*bounds, width, height, margin=margin) if bounds else None
        x, y, wide, tall = box or (0, 0, width, height)
        # Fica no registro do ComfyUI: e por aqui que se sabe quanto do quadro cada pedaco gerou.
        print(f"[wan_studio] janela: {wide}x{tall} em ({x}, {y}) de {width}x{height}, "
              f"{100 * wide * tall // (width * height)}% do quadro" + ("" if box else " (quadro inteiro)"), flush=True)
        if motion is None:
            motion = images[:1]
        elif tuple(motion.shape[1:3]) != (height, width):  # quadros ja ampliados: voltam ao tamanho de geracao
            motion = common_upscale(motion.movedim(-1, 1), width, height, "area", "disabled").movedim(1, -1)
        return (images[:, y:y + tall, x:x + wide, :].contiguous(), mask[:, y:y + tall, x:x + wide].contiguous(),
                shifted(pose_data, x, y, wide, tall), motion[:, y:y + tall, x:x + wide, :].contiguous(), x, y, wide, tall)


class WanStudioPaste:
    """Poe os quadros gerados na janela de volta no video, so onde o recorte da pessoa manda."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "images": ("IMAGE", {"tooltip": "Os quadros do video de origem, inteiros."}),
            "patch": ("IMAGE", {"tooltip": "Os quadros gerados, do tamanho da janela."}),
            "mask": ("MASK", {"tooltip": "O recorte da pessoa na janela."}),
            "x": ("INT", {"default": 0, "min": 0, "max": 8192}),
            "y": ("INT", {"default": 0, "min": 0, "max": 8192}),
        }, "optional": {
            "previous": ("IMAGE", {"tooltip": "Os quadros de que o pedaco continua, na janela: a cor e trazida a deles."}),
            "overlap": ("INT", {"default": 0, "min": 0, "max": 64,
                                "tooltip": "Quantos quadros o pedaco repete do anterior (0: nao mexe na cor)."}),
        }}

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("images",)
    FUNCTION = "paste"
    CATEGORY = "Wan Studio"
    DESCRIPTION = "Cola os quadros gerados na janela de volta no video de origem."

    def paste(self, images, patch, mask, x, y, previous=None, overlap=0):
        frames, fix = pasted(images.cpu().numpy(), patch.cpu().numpy(), mask.cpu().numpy(), x, y,
                             None if previous is None else previous.cpu().numpy(), overlap)
        if fix:
            print("[wan_studio] cor trazida a do pedaco anterior (ganho, deslocamento de Y, U e V): "
                  + ", ".join(f"{gain:.3f} {offset:+.3f}" for gain, offset in fix), flush=True)
        return (torch.from_numpy(frames),)


# ---------------------------------------------------------------- a pessoa inteira
#
# Com `whole` (ajuste de teste da troca de pessoa) o recorte do SAM2 e completado pelo de um
# modelo de recorte de primeiro plano (BiRefNet-portrait, licenca MIT): no exemplo oficial, as
# trancas da pessoa original, que pendem soltas ao lado do corpo, ficavam fora do recorte e
# voltavam no video trocado; o BiRefNet as pega em todos os quadros.
#
# Mas ele nao separa uma pessoa da outra: rodado em fotos (13/10/2026), marcou como um bloco so
# dois jogadores sobrepostos e um grupo de criancas, e marcou junto da pessoa os bastoes e os
# esquis de um esquiador. Por isso, do que ele acha a mais, so entram os pedacos que encostam no
# recorte do SAM2 perto da cabeca (cabelo, trancas, chapeu, veu) e sao pequenos perto da pessoa
# (`FOREGROUND_LIMIT` da area dela): um vizinho encostado ou o que ela segura nas maos fica como
# esta no video. Ainda sem conferencia na GPU.

FOREGROUND_SIZE = 1024      # lado do quadro que o modelo ve (o tamanho em que foi treinado)
FOREGROUND_THRESHOLD = 0.5
FOREGROUND_LIMIT = 0.35
HEAD_POINTS = (0, 14, 15, 16, 17)  # nariz, olhos e orelhas, no formato de 18 pontos


def _neighbors(mask):
    """Os pixels vizinhos (4) de uma mascara A x L, ela inclusive."""
    grown = mask.copy()
    grown[1:] |= mask[:-1]
    grown[:-1] |= mask[1:]
    grown[:, 1:] |= mask[:, :-1]
    grown[:, :-1] |= mask[:, 1:]
    return grown


def pieces(region):
    """Cada parte ligada (4 vizinhos) de `region` (A x L, bool), como mascaras."""
    try:
        from scipy.ndimage import label
    except ImportError:  # sem o scipy: uma parte de cada vez, crescendo por dentro da regiao
        left, found = region.copy(), []
        while left.any():
            reached = np.zeros_like(left)
            reached.flat[np.flatnonzero(left)[0]] = True
            while True:
                grown = _neighbors(reached) & left
                if (grown == reached).all():
                    break
                reached = grown
            found.append(reached)
            left &= ~reached
        return found
    labels, count = label(region)
    return [labels == number for number in range(1, count + 1)]


def head_spot(meta, width, height):
    """(x, y, raio) da cabeca da pessoa, em pixels, pela pose; None se a pose nao a achou."""
    body = meta.get("keypoints_body") if meta else None
    if body is None:
        return None
    points = confident(body, width, height, HEAD_POINTS)
    neck = confident(body, width, height, (1,))
    if not len(points):
        return None
    center = points.mean(axis=0)
    reach = float(np.ptp(points, axis=0).max()) * 1.5 if len(points) > 1 else 0.0
    if len(neck):
        reach = max(reach, float(np.linalg.norm(neck[0] - center)) * 1.2)
    return float(center[0]), float(center[1]), max(reach, 0.04 * height)


def completed(person, foreground, head=None, limit=FOREGROUND_LIMIT):
    """O recorte da pessoa (A x L, bool) mais os pedacos do que o modelo de primeiro plano
    (`foreground`, A x L, bool) acha a mais que encostam nele perto da cabeca (`head`: x, y,
    raio) e tem menos de `limit` da area dele. Sem a cabeca pela pose, vale o terco de cima
    do recorte."""
    person, foreground = np.asarray(person, dtype=bool), np.asarray(foreground, dtype=bool)
    if not person.any():
        return person
    rows = np.nonzero(person.any(axis=1))[0]
    near = np.zeros_like(person)
    if head is not None:
        x, y, radius = head
        yy, xx = np.ogrid[:person.shape[0], :person.shape[1]]
        near = (xx - x) ** 2 + (yy - y) ** 2 <= radius ** 2
    else:
        near[rows[0]:rows[0] + max(1, (rows[-1] - rows[0] + 1) // 3)] = True
    border = _neighbors(_neighbors(person)) & near
    result = person.copy()
    for piece in pieces(foreground & ~person):
        if (piece & border).any() and piece.sum() <= limit * person.sum():
            result |= piece
    return result


class WanStudioForegroundLoader:
    """Carrega o modelo de recorte de primeiro plano (BiRefNet) de models/BiRefNet/<pasta>."""

    @classmethod
    def INPUT_TYPES(cls):
        import os

        import folder_paths
        root = os.path.join(folder_paths.models_dir, "BiRefNet")
        found = sorted(name for name in (os.listdir(root) if os.path.isdir(root) else [])
                       if os.path.isfile(os.path.join(root, name, "config.json")))
        return {"required": {
            "model": (found or ["(nenhum em models/BiRefNet)"],),
            "device": (["cuda", "cpu"], {"default": "cuda"}),
        }}

    RETURN_TYPES = ("WANSTUDIO_FOREGROUND",)
    RETURN_NAMES = ("model",)
    FUNCTION = "load"
    CATEGORY = "Wan Studio"
    DESCRIPTION = "Modelo de recorte de primeiro plano, para completar o recorte da pessoa."

    def load(self, model, device):
        import os

        import folder_paths
        from transformers import AutoModelForImageSegmentation

        path = os.path.join(folder_paths.models_dir, "BiRefNet", model)
        # O codigo do modelo vem junto dos pesos, na versao fixada em vm/setup.sh.
        network = AutoModelForImageSegmentation.from_pretrained(path, trust_remote_code=True)
        dtype = torch.float16 if device == "cuda" else torch.float32
        return ({"model": network.to(dtype).eval(), "device": device, "dtype": dtype},)


class WanStudioWholePerson:
    """Completa o recorte da pessoa (do SAM2) com o que o modelo de primeiro plano acha dela:
    cabelo solto, trancas, chapeu, roupa larga."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "images": ("IMAGE",),
            "mask": ("MASK", {"tooltip": "O recorte da pessoa pelo SAM2."}),
            "model": ("WANSTUDIO_FOREGROUND",),
        }, "optional": {
            "pose_data": ("POSEDATA", {"tooltip": "A pose da pessoa: so entra o que encosta nela perto da cabeca."}),
            "size": ("INT", {"default": FOREGROUND_SIZE, "min": 256, "max": 2048, "step": 32}),
        }}

    RETURN_TYPES = ("MASK",)
    RETURN_NAMES = ("mask",)
    FUNCTION = "complete"
    CATEGORY = "Wan Studio"
    DESCRIPTION = "Completa o recorte da pessoa com o que o BiRefNet acha ligado a ele."

    def complete(self, images, mask, model, pose_data=None, size=FOREGROUND_SIZE):
        count, height, width, _ = images.shape
        network, dtype = model["model"], model["dtype"]
        device = mm.get_torch_device() if model["device"] == "cuda" else torch.device("cpu")
        network.to(device)
        mean = torch.tensor([0.485, 0.456, 0.406], device=device, dtype=dtype).view(1, 3, 1, 1)
        std = torch.tensor([0.229, 0.224, 0.225], device=device, dtype=dtype).view(1, 3, 1, 1)
        people = (mask > 0.5).cpu().numpy()
        metas = (pose_data or {}).get("pose_metas_original") or []
        result = []
        progress = ProgressBar(count)
        added = 0
        for index in range(count):
            frame = images[index:index + 1, :, :, :3].movedim(-1, 1).to(device, dtype)
            batch = (torch.nn.functional.interpolate(frame, (size, size), mode="bilinear", align_corners=False) - mean) / std
            with torch.inference_mode():
                guess = network(batch)[-1].sigmoid()
            guess = torch.nn.functional.interpolate(guess.float(), (height, width), mode="bilinear", align_corners=False)
            person = people[min(index, len(people) - 1)]
            head = head_spot(metas[min(index, len(metas) - 1)], width, height) if metas else None
            whole = completed(person, guess[0, 0].cpu().numpy() > FOREGROUND_THRESHOLD, head)
            added += int(whole.sum() - person.sum())
            result.append(torch.from_numpy(whole))
            progress.update(1)
        network.to(mm.unet_offload_device())
        mm.soft_empty_cache()
        # Fica no registro do ComfyUI: quanto o recorte cresceu.
        print(f"[wan_studio] pessoa inteira: +{100 * added / max(1, people.sum()):.1f}% no recorte", flush=True)
        return (torch.stack(result).float(),)


# ---------------------------------------------------------------- o rosto em detalhe
#
# Com `face` (ajuste de teste da troca de pessoa), depois de gerar o pedaco o Animate passa de
# novo, so na cabeca: a regiao dela e ampliada (ate 3 vezes), refeita com o recorte da cabeca da
# imagem da pessoa como referencia e posta de volta no lugar. Num plano aberto o rosto gerado
# em 480p tem 60 a 90 px, e e ai que a semelhanca com a foto se perde; ampliado, o modelo tem
# 3 vezes mais pixels para ele. Quando o rosto ja e grande (ampliar menos que FACE_MIN_ZOOM), a
# segunda passada nao roda (`WanStudioFacePaste` nem pede o resultado dela). Sem conferencia na GPU.

FACE_ZOOM = 3.0          # quanto a janela da cabeca e ampliada...
FACE_SIZES = (256, 512)  # ...para um quadro com este lado (o minimo e o maximo)
FACE_MIN_ZOOM = 1.5      # abaixo disto o rosto ja e grande: nao ha segunda passada
FACE_SPAN = 3.5          # lado da janela, em raios da cabeca (cabeca, cabelo e um pouco dos ombros)
FACE_REACH = 1.4         # o que e refeito: o recorte da pessoa ate este tanto do raio da cabeca


def square_box(x, y, side, width, height):
    """O quadrado de lado `side` (no maximo o menor lado do quadro) em volta de (x, y), sem
    sair do quadro: (esquerda, cima, lado), em pixels inteiros."""
    side = int(max(2, min(side, width, height)))
    left = int(min(max(round(x - side / 2), 0), width - side))
    top = int(min(max(round(y - side / 2), 0), height - side))
    return left, top, side


def head_window(metas, width, height, span=FACE_SPAN):
    """A janela quadrada (esquerda, cima, lado) que contem a cabeca da pessoa em todos os
    quadros do pedaco, e as cabecas [(x, y, raio) ou None, ...] quadro a quadro. None se a pose
    nao achou o rosto em nenhum."""
    heads = [head_spot(meta, width, height) for meta in metas]
    found = [head for head in heads if head]
    if not found:
        return None, heads
    left = min(x - span / 2 * radius for x, _y, radius in found)
    right = max(x + span / 2 * radius for x, _y, radius in found)
    top = min(y - span / 2 * radius for _x, y, radius in found)
    bottom = max(y + span / 2 * radius for _x, y, radius in found)
    return square_box((left + right) / 2, (top + bottom) / 2, max(right - left, bottom - top), width, height), heads


def zoom_size(side, zoom=FACE_ZOOM, sizes=FACE_SIZES):
    """O lado (multiplo de 16) do quadro em que a cabeca e refeita, para uma janela de `side` px."""
    return int(min(max(round(side * zoom / 16) * 16, sizes[0]), sizes[1]))


def head_masks(masks, heads, reach=FACE_REACH):
    """O recorte da pessoa (N x A x L) so na cabeca de cada quadro (o disco de `reach` raios em
    volta dela); onde a pose nao achou o rosto, vale a cabeca do quadro mais proximo."""
    masks = np.asarray(masks) > 0.5
    known = [index for index, head in enumerate(heads) if head]
    result = np.zeros_like(masks)
    if not known:
        return result
    yy, xx = np.ogrid[:masks.shape[1], :masks.shape[2]]
    for index in range(len(masks)):
        x, y, radius = heads[min(known, key=lambda other: abs(other - index))] if index >= len(heads) or not heads[index] \
            else heads[index]
        result[index] = masks[index] & ((xx - x) ** 2 + (yy - y) ** 2 <= (reach * radius) ** 2)
    return result


# O rosto so conta como a vista se a pose acha o nariz e um olho com esta certeza: de costas, ela
# ainda arrisca os pontos do rosto (no exemplo oficial, na parte de tras da cabeca).
FACE_VISIBLE = 0.5


def face_ellipse(meta, width, height):
    """(x, y, raio na largura, raio na altura) da elipse do rosto, em pixels, pelos pontos do
    rosto da pose (sobrancelhas, olhos, nariz, boca e queixo), com a testa acima deles; None se
    a pose achou pouco do rosto, ou se ele nao esta a vista (pessoa de costas)."""
    face = meta.get("keypoints_face") if meta else None
    if face is None:
        return None
    body = meta.get("keypoints_body")
    if body is not None and not (len(confident(body, width, height, (0,), FACE_VISIBLE))
                                 and len(confident(body, width, height, (14, 15), FACE_VISIBLE))):
        return None
    points = confident(face, width, height)
    if len(points) < 8:
        return None
    (left, top), (right, bottom) = points.min(axis=0), points.max(axis=0)
    wide, tall = right - left, bottom - top
    top -= 0.35 * tall  # a testa: os pontos param nas sobrancelhas
    bottom += 0.05 * tall
    left, right = left - 0.08 * wide, right + 0.08 * wide
    return (left + right) / 2, (top + bottom) / 2, max(2.0, (right - left) / 2), max(2.0, (bottom - top) / 2)


def face_masks(shape, metas, width, height):
    """So o rosto (a elipse de `face_ellipse`) em cada quadro (N x A x L); onde a pose nao achou o
    rosto, vale o do quadro mais proximo. Tudo vazio se nao achou em nenhum."""
    ellipses = [face_ellipse(meta, width, height) for meta in metas]
    known = [index for index, ellipse in enumerate(ellipses) if ellipse]
    result = np.zeros(shape, dtype=bool)
    if not known:
        return result
    yy, xx = np.ogrid[:shape[1], :shape[2]]
    for index in range(shape[0]):
        own = ellipses[index] if index < len(ellipses) else None
        x, y, rx, ry = own or ellipses[min(known, key=lambda other: abs(other - index))]
        result[index] = ((xx - x) / rx) ** 2 + ((yy - y) / ry) ** 2 <= 1
    return result


def _zoomed(frames, left, top, side, size, mode="bicubic"):
    """O quadrado (`left`, `top`, `side`) de cada quadro (N x A x L x C), ampliado a `size`."""
    part = frames[:, top:top + side, left:left + side, :].movedim(-1, 1)
    kwargs = {} if mode == "nearest" else {"align_corners": False}
    return torch.nn.functional.interpolate(part, (size, size), mode=mode, **kwargs).clamp(0, 1).movedim(1, -1)


class WanStudioHeadWindow:
    """Recorta e amplia a cabeca da pessoa nos quadros ja gerados de um pedaco, para a segunda
    passada do Animate: os quadros, o recorte so da cabeca, a pose e os quadros de que o pedaco
    continua, todos na janela ampliada."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "images": ("IMAGE", {"tooltip": "Os quadros ja gerados do pedaco, inteiros."}),
            "pose_data": ("POSEDATA", {"tooltip": "A pose do video de origem, no quadro inteiro."}),
        }, "optional": {
            "mask": ("MASK", {"tooltip": "O recorte da pessoa que a primeira passada usou (sem ele: o quadro todo)."}),
            "motion": ("IMAGE", {"tooltip": "Os quadros de que o pedaco continua, inteiros."}),
            "region": (["cabeca", "rosto"], {"default": "cabeca",
                                             "tooltip": "cabeca: o rosto e o cabelo em volta; rosto: so o rosto, da testa ao queixo."}),
            "always": ("BOOLEAN", {"default": False,
                                   "tooltip": "Refaz mesmo com o rosto grande, e para com erro se nao achar rosto (troca de rosto em foto)."}),
            "max_size": ("INT", {"default": FACE_SIZES[1], "min": 256, "max": 1024, "step": 16}),
        }}

    RETURN_TYPES = ("IMAGE", "MASK", "POSEDATA", "IMAGE", "INT", "INT", "INT", "INT", "BOOLEAN", "MASK")
    RETURN_NAMES = ("images", "mask", "pose_data", "motion", "x", "y", "side", "size", "active", "small_mask")
    FUNCTION = "cut"
    CATEGORY = "Wan Studio"
    DESCRIPTION = "A cabeca da pessoa, ampliada, para refazer o rosto com mais detalhe."

    def cut(self, images, pose_data, mask=None, motion=None, region="cabeca", always=False, max_size=FACE_SIZES[1]):
        count, height, width, _ = images.shape
        metas = (pose_data.get("pose_metas_original") or [])[:count]
        if mask is None:
            mask = torch.ones((count, height, width))
        box, heads = head_window(metas, width, height)
        if always and box is None:
            raise ValueError("não encontrei um rosto na imagem")
        if box is None or tuple(mask.shape[-2:]) != (height, width):
            box, heads = (0, 0, min(width, height)), []
        left, top, side = box
        size = zoom_size(side, sizes=(FACE_SIZES[0], max_size))
        active = bool(heads) and (always or size / side >= FACE_MIN_ZOOM)
        heads = heads + [None] * (count - len(heads))
        people = mask[:count].cpu().numpy()
        if region == "rosto":
            only = face_masks(people.shape, metas, width, height) & (people > 0.5)
            if always and not only.any():
                raise ValueError("não encontrei o rosto inteiro na imagem (de frente funciona melhor)")
        else:
            only = head_masks(people, heads)
        small = torch.from_numpy(only[:, top:top + side, left:left + side].astype(np.float32))
        big = torch.nn.functional.interpolate(small[:, None], (size, size), mode="nearest")[:, 0]
        moved = dict(pose_data)
        moved["pose_metas"] = [copy.deepcopy(meta).crop(left, top, left + side, top + side).resize(size, size)
                               if hasattr(meta, "crop") else meta for meta in (pose_data.get("pose_metas") or [])]
        source = images[:1] if motion is None else motion
        if tuple(source.shape[1:3]) != (height, width):
            source = common_upscale(source.movedim(-1, 1), width, height, "area", "disabled").movedim(1, -1)
        print(f"[wan_studio] rosto: janela {side}x{side} em ({left}, {top}), ampliada a {size}"
              + ("" if active else " (o rosto ja e grande: sem segunda passada)"), flush=True)
        return (_zoomed(images[:, :, :, :3], left, top, side, size), big, moved, _zoomed(source[:, :, :, :3], left, top, side, size),
                left, top, side, size, active, small)


class WanStudioFacePaste:
    """Poe a cabeca refeita de volta nos quadros, so onde o recorte da cabeca manda. Sem a
    segunda passada (`active` falso), devolve os quadros como vieram e nem pede o resultado dela."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "images": ("IMAGE",),
            "active": ("BOOLEAN", {"default": True}),
            "x": ("INT", {"default": 0, "min": 0, "max": 8192}),
            "y": ("INT", {"default": 0, "min": 0, "max": 8192}),
        }, "optional": {
            "patch": ("IMAGE", {"lazy": True, "tooltip": "A cabeca refeita, ja no tamanho da janela."}),
            "mask": ("MASK", {"lazy": True, "tooltip": "O recorte da cabeca na janela."}),
        }}

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("images",)
    FUNCTION = "paste"
    CATEGORY = "Wan Studio"
    DESCRIPTION = "Cola a cabeca refeita de volta nos quadros."

    def check_lazy_status(self, images, active, x, y, patch=None, mask=None):
        return ["patch", "mask"] if active else []

    def paste(self, images, active, x, y, patch=None, mask=None):
        if not active or patch is None or mask is None:
            return (images,)
        # A janela da cabeca e pequena: a passagem e curta (proporcional a ela) e acaba antes da borda.
        feather = max(2, patch.shape[1] // 32)
        frames, _fix = pasted(images.cpu().numpy(), patch.cpu().numpy(), mask.cpu().numpy(), x, y,
                              grow=0, feather=feather, edge=True)
        return (torch.from_numpy(frames),)


NODE_CLASS_MAPPINGS = {"WanStudioPersonMask": WanStudioPersonMask, "WanStudioFitReference": WanStudioFitReference,
                       "WanStudioWindow": WanStudioWindow, "WanStudioPaste": WanStudioPaste,
                       "WanStudioForegroundLoader": WanStudioForegroundLoader, "WanStudioWholePerson": WanStudioWholePerson,
                       "WanStudioHeadWindow": WanStudioHeadWindow, "WanStudioFacePaste": WanStudioFacePaste}
NODE_DISPLAY_NAME_MAPPINGS = {"WanStudioPersonMask": "Wan Studio: máscara da pessoa",
                              "WanStudioFitReference": "Wan Studio: enquadrar a imagem da pessoa",
                              "WanStudioWindow": "Wan Studio: janela na pessoa",
                              "WanStudioPaste": "Wan Studio: colar a janela no vídeo",
                              "WanStudioForegroundLoader": "Wan Studio: modelo de primeiro plano",
                              "WanStudioWholePerson": "Wan Studio: a pessoa inteira",
                              "WanStudioHeadWindow": "Wan Studio: a cabeça ampliada",
                              "WanStudioFacePaste": "Wan Studio: colar a cabeça refeita"}
