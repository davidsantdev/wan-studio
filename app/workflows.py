"""Monta os grafos do ComfyUI (formato API) para o Wan 2.2."""

import os
import random

TEXT_ENCODER = "umt5_xxl_fp8_e4m3fn_scaled.safetensors"

# Para cada papel, os arquivos aceitos em ordem de preferencia: fp8 (GPU de 24 GB) e depois
# GGUF Q4 (cabe numa T4 de 16 GB). "turbo" sao as LoRAs de poucos passos de cada conjunto.
# Numa placa com menos que isto de memoria a ordem se inverte: o GGUF vem primeiro (quem troca
# de placa entre uma sessao e outra pode ter os dois instalados).
FP8_MIN_VRAM = 20
CATALOG = {
    "5b": {
        "unet": ("wan2.2_ti2v_5B_fp16.safetensors",),
        "vae": "wan2.2_vae.safetensors",
        "turbo": {"lora": "Wan2_2_5B_FastWanFullAttn_lora_rank_128_bf16.safetensors"},
    },
    "14b_t2v": {
        "high": ("wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors",
                 "Wan2.2-T2V-A14B-HighNoise-Q4_K_M.gguf", "Wan2.2-T2V-A14B-HighNoise-Q4_K_S.gguf"),
        "low": ("wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors",
                "Wan2.2-T2V-A14B-LowNoise-Q4_K_M.gguf", "Wan2.2-T2V-A14B-LowNoise-Q4_K_S.gguf"),
        "vae": "wan_2.1_vae.safetensors",
        "turbo": {"lora_high": "wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors",
                  "lora_low": "wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors"},
    },
    "14b_i2v": {
        "high": ("wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
                 "Wan2.2-I2V-A14B-HighNoise-Q4_K_M.gguf", "Wan2.2-I2V-A14B-HighNoise-Q4_K_S.gguf"),
        "low": ("wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
                "Wan2.2-I2V-A14B-LowNoise-Q4_K_M.gguf", "Wan2.2-I2V-A14B-LowNoise-Q4_K_S.gguf"),
        "vae": "wan_2.1_vae.safetensors",
        "turbo": {"lora_high": "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors",
                  "lora_low": "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors"},
    },
    # Wan 2.2 Animate: troca a pessoa de um video pela pessoa de uma imagem.
    "animate": {
        "unet": ("Wan2_2-Animate-14B_fp8_e4m3fn_scaled_KJ.safetensors",
                 "Wan2.2-Animate-14B-Q4_K_M.gguf", "Wan2.2-Animate-14B-Q4_K_S.gguf"),
        "vae": "wan_2.1_vae.safetensors",
        "turbo": {"lora": "lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors"},
        # Arquivos de outros tipos que o conjunto exige: papel -> (lista do ComfyUI, nome).
        "extra": {
            "clip_vision": ("clip_vision", "clip_vision_h.safetensors"),
            "vitpose": ("detection", "vitpose-l-wholebody.onnx"),
            "yolo": ("detection", "yolov10m.onnx"),
        },
        # Melhoram o resultado, mas o conjunto funciona sem eles.
        "optional": {"relight": ("lora", "WanAnimate_relight_lora_fp16.safetensors"),
                     "upscaler": ("upscale", "RealESRGAN_x2plus.pth"),
                     "interpolator": ("frame_interpolation", "rife_v4.25.safetensors"),
                     "restorer": ("unet", "seedvr2_3b_fp8_e4m3fn.safetensors"),
                     "restorer_vae": ("vae", "seedvr2_ema_vae_fp16.safetensors"),
                     # O recorte da pessoa inteira (`whole`): a pasta do BiRefNet em models/BiRefNet.
                     "foreground": ("foreground", "BiRefNet-portrait")},
        # Outras compressoes do mesmo modelo, se instaladas: um pedido de teste escolhe pelo nome.
        # A "int8_convrot" (a do proprio ComfyUI) e a candidata a padrao na T4: mais fiel que o
        # GGUF Q4 e, com os nucleos de INT8 ligados (PyTorch de CUDA 13), mais rapida. A medir.
        "alternatives": ("Wan2.2-Animate-14B-Q4_K_S.gguf", "Wan2.2-Animate-14B-Q4_K_M.gguf",
                         "Wan2.2-Animate-14B-Q5_K_M.gguf", "Wan2.2-Animate-14B-Q6_K.gguf",
                         "Wan2.2-Animate-14B-Q8_0.gguf", "wan2.2_animate_14B_int8_convrot.safetensors"),
        # Nos que precisam existir: o Animate nativo, os pre-processadores do Kijai e o recorte
        # da pessoa do proprio Wan Studio (vm/nodes).
        "nodes": ("WanAnimateToVideo", "PoseAndFaceDetection", "DrawViTPose",
                  "DownloadAndLoadSAM2Model", "WanStudioPersonMask", "WanStudioFitReference",
                  "WanStudioWindow", "WanStudioPaste", "WanStudioWholePerson", "WanStudioHeadWindow",
                  "WanStudioFacePaste"),
    },
}

# O 5B trabalha em multiplos de 32 e 24 fps; o 14B em multiplos de 16 e 16 fps.
FPS = {"5b": 24, "14b": 16, "animate": 16}
SIZES = {
    "5b": {
        "720p": {"16:9": (1280, 704), "9:16": (704, 1280), "1:1": (960, 960)},
        "480p": {"16:9": (832, 480), "9:16": (480, 832), "1:1": (640, 640)},
    },
    "14b": {
        "720p": {"16:9": (1280, 720), "9:16": (720, 1280), "1:1": (960, 960)},
        "480p": {"16:9": (832, 480), "9:16": (480, 832), "1:1": (640, 640)},
    },
}
DURATIONS = (3, 5)
MODES = ("t2v", "i2v", "flf", "img", "swap", "face")
MAX_SEED = 2**48

# O FastWan 5B foi destilado (DMD) para exatamente estes tres instantes, sem CFG.
TURBO_5B_SIGMAS = "1.0, 0.757, 0.522, 0.0"

# Troca de pessoa (Wan 2.2 Animate). O video e gerado em pedacos que continuam um do outro,
# do primeiro quadro em diante, e que o servidor emenda (stitch.py explica o desenho): a
# duracao deixa de ter teto.
#
# Estes valores sao os da primeira versao, a que saia fiel, e voltaram em 12/10/2026 depois de
# uma comparacao na GPU (2x T4, os mesmos 10 s do exemplo oficial, uma coisa mudada por vez):
#
#   - Comecar por um pedaco curto no meio do video, para as duas GPUs trabalharem ao mesmo
#     tempo (uma avancando, outra recuando), era o que mais estragava: nesse pedaco, sem os
#     quadros de antes, o modelo inventou um objeto na cabeca da pessoa, e todos os outros
#     pedacos o herdaram. Com um pedaco inteiro no meio (77 quadros) tambem. So avancando, nao.
#   - Gerar 12 quadros por segundo e interpolar o resto saiu pior que gerar os 16 do modelo:
#     desde o primeiro quadro, cabelo da pessoa original no lugar do gorro da nova.
#   - 6 passos em vez de 4 nao mudaram nada que se veja, e dobraram o tempo.
#   - Pedacos de 77 quadros (o tamanho do exemplo oficial) custam mais por passo que os de 49,
#     mas menos por quadro novo, porque ha menos emendas: 10 s em 27 min, contra uns 36.
#   - O recorte da pessoa em retangulo (o padrao do pre-processamento oficial) deixa o modelo
#     livre demais: inventou oculos e objetos. Ficou o de contorno.
SWAP_FPS = 16          # o fps do modelo: todos os quadros sao gerados
SWAP_CHUNK = 77        # quadros por pedaco, sempre 4n+1
SWAP_OVERLAP = 5       # quadros que um pedaco copia do vizinho para continuar dele
# Quadros do pedaco de partida no meio do video (0: nao ha; o video e gerado do comeco). O
# servidor ainda sabe fazer isso, para medir; os resultados estao acima.
SWAP_MIDDLE = 0
# Em quantas cadeias de pedacos o video e gerado quando ha mais de uma GPU: 1 (so avancando, o
# que foi medido) ou 2 (duas que avancam ao mesmo tempo, uma em cada GPU; a segunda parte de
# quadros ja gerados do primeiro pedaco: app/stitch.py `chains`). Pelo plano, um video de 23 s
# cai a uns 57% do tempo. O 2 so vira padrao depois de conferido na GPU: falta ver se a emenda
# entre as cadeias aparece.
SWAP_CHAINS = 1
SWAP_MIN_FRAMES = 17
SWAP_MAX_SECONDS = 60
# A LoRA lightx2v foi destilada para 4 passos. "rapida" gera com 59% dos pixels (metade do
# tempo por passo) e o arquivo final e ampliado: a pessoa sai coerente, com menos detalhe.
# "maxima" gera com 44% mais pixels que "boa": 1,75 vez o tempo por passo, e os detalhes da
# imagem da pessoa (no teste, um braco mecanico) saem como na imagem; nesse tamanho o pedaco de
# 77 quadros nao cabe bem na T4, e ela usa os de 49.
#
# `fps` e quantos quadros por segundo o modelo gera, e `out_fps` os do arquivo. Quando o
# segundo e multiplo do primeiro, os que faltam sao calculados a partir dos vizinhos
# (interpolacao); nenhuma qualidade usa isso hoje, so os ajustes de teste.
SWAP_QUALITIES = {
    "rapida": {"steps": 4, "fps": 16, "out_fps": 16,
               "sizes": {"16:9": (640, 368), "9:16": (368, 640), "1:1": (480, 480)}},
    "boa": {"steps": 4, "fps": 16, "out_fps": 16, "sizes": SIZES["14b"]["480p"]},
    "maxima": {"steps": 4, "fps": 16, "out_fps": 16, "chunk": 49,
               "sizes": {"16:9": (1024, 576), "9:16": (576, 1024), "1:1": (768, 768)}},
    # So em placas de 24 GB ou mais (`min_vram`, em GB): gera direto em 720p, com os 6 passos
    # do exemplo oficial do ComfyUI. Na T4 isto levava horas; nunca foi medido numa placa maior.
    "ultra": {"steps": 6, "fps": 16, "out_fps": 16, "min_vram": FP8_MIN_VRAM, "sizes": SIZES["14b"]["720p"]},
}
# Quem calcula os intermediarios: o modelo de interpolacao (RIFE), em cada pedaco, na GPU; ou,
# sem ele, o ffmpeg na emenda, por estimativa de movimento (pior em movimento rapido).
SMOOTH_METHODS = ("rife", "ffmpeg")
# Alta definicao: o SeedVR2 restaura (recria detalhe de pele, cabelo e tecido ao ampliar); o
# Real-ESRGAN so amplia. Vale o primeiro que estiver instalado.
HD_METHODS = ("seedvr2", "esrgan")
SWAP_OUTPUT = SIZES["14b"]["480p"]  # tamanho do arquivo final em qualquer qualidade
SWAP_HD_SIDE = 720                  # com a ampliacao: o lado menor do arquivo final
# Com a restauracao, numa placa pequena, os pedacos nao passam disto: ela recebe um pedaco
# inteiro de uma vez, e so foi medida na T4 com pedacos de 49 quadros.
SWAP_HD_CHUNK = 49
ANIMATE_PROMPT = "The person in the video is performing the action"
# O no do SAM2 acrescenta "-fp16" ao nome do arquivo quando a precisao e fp16.
# (WAN_SAM2_DEVICE=cpu: so para rodar o grafo numa maquina sem GPU, que e como o codigo novo e
# conferido de graca numa sessao de CPU do Kaggle; la a precisao e fp32.)
SAM2_DEVICE = os.environ.get("WAN_SAM2_DEVICE", "cuda")
SAM2 = {"model": "sam2.1_hiera_base_plus.safetensors", "device": SAM2_DEVICE,
        "precision": "fp16" if SAM2_DEVICE == "cuda" else "fp32"}
SAM2_MODELS = {"base": SAM2["model"], "grande": "sam2.1_hiera_large.safetensors"}
# Como o recorte da pessoa e pedido ao SAM2 (vm/nodes/wan_studio explica cada um).
MASK_METHODS = ("juntos", "caixas", "pontos", "partes", "caixa")
# O que a mascara cobre: o contorno da pessoa (engordado e em blocos), ou o retangulo em volta
# dela, que e o padrao do pre-processamento oficial (vm/nodes/wan_studio explica).
MASK_COVERS = ("contorno", "caixa")
# Folga da caixa que o recorte da em volta da pose, e quantos pixels o contorno e engordado.
# "larga" e um engorde maior (16) sao a tentativa de cobrir o que sobra da pessoa original (uma
# tranca voltava no fim do video); so viram padrao depois de conferidos na GPU.
MASK_BOXES = ("justa", "larga")
MASK_GROW = 10
# Como a imagem da pessoa entra no quadro de geracao: "pad" a poe inteira, com faixas pretas
# onde a proporcao difere (o padrao, medido); "crop" a recorta antes na caixa da pessoa, na
# proporcao do quadro (vm/nodes/wan_studio `fit_crop`), para ela nao ficar pequena. O "crop" so
# vira padrao depois de conferido na GPU.
REF_FITS = ("pad", "crop")
# A janela na pessoa: o Animate gera so o retangulo do quadro em que a pessoa esta (com cenario
# em volta), e os quadros gerados voltam para o video so onde o recorte dela manda; o resto fica
# com os pixels do video de origem (vm/nodes/wan_studio, WanStudioWindow e WanStudioPaste). O
# passo custa mais que proporcionalmente aos pixels (na T4, 1,5 vez os pixels levou 1,9 vez o
# tempo), entao numa cena em que a pessoa ocupa metade do quadro o pedaco sai em bem menos da
# metade do tempo; quando ela enche o quadro, nada muda. So vira padrao depois de conferida na GPU.
SWAP_WINDOW = False
# A pessoa inteira: o recorte do SAM2 e completado pelo de um modelo de recorte de primeiro
# plano (BiRefNet), so no que esta ligado a ele (vm/nodes/wan_studio, WanStudioWholePerson). No
# exemplo oficial as trancas da pessoa original ficavam fora do recorte e voltavam no video
# trocado; o BiRefNet as pega em todos os quadros. So vira padrao depois de conferido na GPU.
SWAP_WHOLE = False
# O rosto em detalhe: depois de gerar o pedaco, uma segunda passada do Animate so na cabeca,
# ampliada ate 3 vezes, com a cabeca da imagem da pessoa como referencia (vm/nodes/wan_studio,
# WanStudioHeadWindow). Num plano aberto o rosto gerado em 480p tem 60 a 90 px, e e ai que a
# semelhanca com a foto se perde. Custa uns 25 a 55% a mais de tempo quando roda; com o rosto
# ja grande (num close) ela nao roda. So vira padrao depois de conferido na GPU.
SWAP_FACE = False


def required_set(model, mode):
    """Qual conjunto de arquivos (chave de CATALOG) o job precisa."""
    if mode in ("swap", "face"):
        return "animate"
    if model == "5b":
        return "5b"
    return "14b_t2v" if mode in ("t2v", "img") else "14b_i2v"


def available(installed):
    """Cruza o catalogo com o que o ComfyUI enxerga.

    `installed` tem as listas do ComfyUI ({"unet", "gguf", "lora", "vae", "clip", ...}), em
    "nodes" os nos existentes e, em "vram", a memoria da placa em GB (None se desconhecida).
    Devolve, por conjunto, {"ok", "turbo", "files"}; `files` traz os nomes como o ComfyUI os
    conhece.
    """
    small = installed.get("vram") is not None and installed["vram"] < FP8_MIN_VRAM

    def find(kind, name):
        for listed in installed.get(kind, ()):
            if listed.replace("\\", "/").rsplit("/", 1)[-1] == name:
                return listed
        return None

    def find_unet(candidates):
        if small:  # o fp8 nao cabe inteiro: funcionaria, mas descarregando partes a cada passo
            candidates = sorted(candidates, key=lambda name: not name.endswith(".gguf"))
        for name in candidates:
            found = find("gguf" if name.endswith(".gguf") else "unet", name)
            if found:
                return found
        return None

    encoder = find("clip", TEXT_ENCODER)
    nodes = set(installed.get("nodes", ()))
    result = {}
    for set_name, entry in CATALOG.items():
        files = {"clip": encoder, "vae": find("vae", entry["vae"])}
        for role in ("unet", "high", "low"):
            if role in entry:
                files[role] = find_unet(entry[role])
        for role, (kind, name) in entry.get("extra", {}).items():
            files[role] = find(kind, name)
        ok = all(files.values()) and nodes.issuperset(entry.get("nodes", ()))
        turbo = {role: find("lora", name) for role, name in entry["turbo"].items()}
        has_turbo = ok and all(turbo.values())
        if has_turbo:
            files.update(turbo)
        for role, (kind, name) in entry.get("optional", {}).items():
            found = find(kind, name)
            if found:
                files[role] = found
        others = {name: find_unet([name]) for name in entry.get("alternatives", ())}
        if any(others.values()):
            files["alternatives"] = {name: found for name, found in others.items() if found}
        result[set_name] = {"ok": ok, "turbo": has_turbo, "files": files}
    return result


def _seed(params):
    seed = params.get("seed")
    if seed in (None, "", -1):
        return random.randrange(MAX_SEED)
    try:
        return int(seed) % MAX_SEED
    except (TypeError, ValueError):
        raise ValueError("seed inválida") from None


def _choice(params, key, default, options, message):
    value = params.get(key, default)
    try:
        value = type(default)(value)
    except (TypeError, ValueError):
        raise ValueError(message) from None
    if value not in options:
        raise ValueError(message)
    return value


def resolve(params, has_start, has_end, has_video=False):
    """Valida os parametros vindos da interface e devolve a especificacao do job."""
    mode = params.get("mode", "t2v")
    if mode not in MODES:
        raise ValueError("modo inválido")
    if mode == "swap":
        return _resolve_swap(params, has_start, has_video)
    if mode == "face":
        return _resolve_face(params, has_start, has_end)

    model = params.get("model", "5b")
    if model not in SIZES:
        raise ValueError("modelo inválido")
    if not str(params.get("prompt", "")).strip():
        raise ValueError("escreva um prompt")
    if mode in ("i2v", "flf") and not has_start:
        raise ValueError("envie a imagem inicial")
    if mode == "flf":
        if not has_end:
            raise ValueError("envie a imagem final")
        if model != "14b":
            raise ValueError("frame inicial + final só funciona no modelo 14B")

    try:
        width, height = SIZES[model][params.get("resolution", "480p")][params.get("aspect", "16:9")]
    except KeyError:
        raise ValueError("resolução ou proporção inválida") from None
    duration = _choice(params, "duration", 5, DURATIONS, "duração inválida")

    fps = FPS[model]
    image = mode == "img"
    return {
        "mode": mode,
        "kind": "image" if image else "video",
        "model": model,
        # Imagem vai sempre com os passos completos: e um quadro so, e com as LoRAs turbo (feitas
        # para video) ela saiu borrada no 5B e com trama no 14B nos testes na GPU.
        "fast": bool(params.get("fast", True)) and not image,
        "width": width,
        "height": height,
        "fps": fps,
        "length": 1 if image else fps * duration + 1,
        "duration": 0 if image else duration,
        "seed": _seed(params),
    }


def _number(source, key, default, low, high, message, kind=float):
    """Um numero dentro de [low, high]; `default` quando a chave falta ou vem vazia."""
    value = source.get(key)
    if value in (None, ""):
        return default
    try:
        value = kind(value)
    except (TypeError, ValueError):
        raise ValueError(message) from None
    if not low <= value <= high:
        raise ValueError(message)
    return value


def _resolve_swap(params, has_person, has_video):
    """Troca de pessoa: tamanho e quantidade de quadros so sao conhecidos depois que o
    servidor examina o video (veja `prepare_video` no servidor).

    `expert` troca os valores da qualidade escolhida, um a um; serve para medir na GPU o que
    cada ajuste custa e rende, nao para a interface.
    """
    if not has_person:
        raise ValueError("envie a imagem da pessoa")
    if not has_video:
        raise ValueError("envie o vídeo")
    quality = params.get("quality", "boa")
    if quality not in SWAP_QUALITIES:
        raise ValueError("qualidade inválida")
    seconds = _number(params, "duration", 5.0, 1, SWAP_MAX_SECONDS, "duração inválida")
    start = _number(params, "start", 0.0, 0, 24 * 3600, "início do trecho inválido")

    expert = params.get("expert") if isinstance(params.get("expert"), dict) else {}
    bad = "ajuste de teste inválido"
    chunk = _number(expert, "chunk", SWAP_QUALITIES[quality].get("chunk", SWAP_CHUNK), SWAP_MIN_FRAMES, 81,
                    bad, int)
    overlap = _number(expert, "overlap", SWAP_OVERLAP, 1, 16, bad, int)
    size = expert.get("size")
    if size is not None:
        ok = (isinstance(size, list) and len(size) == 2
              and all(isinstance(side, int) and 128 <= side <= 1280 and side % 16 == 0 for side in size))
        if not ok:
            raise ValueError(bad)
    if chunk % 4 != 1 or overlap > chunk - 12 or expert.get("onnx", "gpu") not in ("cpu", "gpu"):
        raise ValueError(bad)
    if expert.get("mask", MASK_METHODS[0]) not in MASK_METHODS or expert.get("sam", "base") not in SAM2_MODELS:
        raise ValueError(bad)
    if expert.get("smooth", SMOOTH_METHODS[0]) not in SMOOTH_METHODS:
        raise ValueError(bad)
    if expert.get("cover", MASK_COVERS[0]) not in MASK_COVERS or expert.get("box", MASK_BOXES[0]) not in MASK_BOXES:
        raise ValueError(bad)
    if expert.get("ref", REF_FITS[0]) not in REF_FITS:
        raise ValueError(bad)
    fluid = bool(params.get("fluid", False))
    if expert.get("hd_method", HD_METHODS[0]) not in HD_METHODS:
        raise ValueError(bad)
    if not (isinstance(expert.get("unet", ""), str) and len(expert.get("unet", "")) <= 24):
        raise ValueError(bad)
    fps = _number(expert, "fps", SWAP_QUALITIES[quality]["fps"], 4, 30, bad, int)
    # Quem muda so o fps num teste quer todos os quadros gerados, nao os da qualidade.
    out_fps = fps if "fps" in expert else SWAP_QUALITIES[quality]["out_fps"] * (2 if fluid else 1)
    return {
        "mode": "swap",
        "kind": "video",
        "model": "animate",
        "fast": True,  # sempre com a LoRA de poucos passos: sem ela seriam 20 passos por pedaco
        "quality": quality,
        "steps": _number(expert, "steps", SWAP_QUALITIES[quality]["steps"], 1, 30, bad, int),
        "replace": bool(params.get("replace", True)),
        # Amplia os quadros gerados com um modelo proprio para isso (se estiver instalado).
        "hd": bool(params.get("hd", False)),
        "hd_method": expert.get("hd_method", HD_METHODS[0]),
        # Trecho do nome de outra compressao do modelo (ex.: "Q6_K"); vazio: a padrao.
        "unet": expert.get("unet", ""),
        "start": start,
        "seconds": seconds,
        "fps": fps,
        "out_fps": _number(expert, "out_fps", out_fps, 4, 60, bad, int),
        "smooth": expert.get("smooth", SMOOTH_METHODS[0]),
        # Quantos quadros cada pedaco entrega por quadro gerado (veja `smooth_factor`).
        "factor": 1,
        "chunk": chunk,
        "chunk_asked": "chunk" in expert,  # um ajuste de teste vale mais que as regras do servidor
        "overlap": overlap,
        # A deteccao de pose pede a GPU; sem o onnxruntime de GPU ela roda na CPU do mesmo jeito.
        "onnx": expert.get("onnx", "gpu"),
        "size": size,
        "pin": _number(expert, "worker", None, 0, 16, bad, int),
        "mask": expert.get("mask", MASK_METHODS[0]),
        "cover": expert.get("cover", MASK_COVERS[0]),
        "box": expert.get("box", MASK_BOXES[0]),
        "ref": expert.get("ref", REF_FITS[0]),
        "window": bool(expert.get("window", SWAP_WINDOW)),
        "whole": bool(expert.get("whole", SWAP_WHOLE)),
        "face": bool(expert.get("face", SWAP_FACE)),
        # Cenario em volta da pessoa, na janela, em pixels (None: o padrao do no).
        "window_margin": _number(expert, "window_margin", None, 16, 512, bad, int),
        "grow": _number(expert, "grow", MASK_GROW, 0, 64, bad, int),
        # Fecha o recorte depois do SAM2 (vm/nodes/wan_studio explica); desligar e so para medir.
        "seal": bool(expert.get("seal", True)),
        # Movimento fluido: o arquivo sai com o dobro dos quadros gerados; os que faltam sao
        # calculados entre cada dois (interpolacao), em segundos.
        "fluid": fluid,
        "sam": expert.get("sam", "base"),
        # So mostra o recorte (a pessoa coberta de vermelho), sem gerar: segundos por pedaco.
        "preview": bool(expert.get("preview", False)),
        # None: o servidor decide pelo numero de GPUs; 0: sem pedaco do meio, todos avancam.
        "middle": _number(expert, "middle", None, 0, chunk, bad, int),
        # Cadeias de pedacos (None: SWAP_CHAINS, se houver mais de uma GPU) e se a segunda
        # parte de quadros do primeiro pedaco (a ancora) ou so da imagem da pessoa.
        "chains": _number(expert, "chains", None, 1, 2, bad, int),
        "anchor": bool(expert.get("anchor", True)),
        "width": 0,
        "height": 0,
        "length": 0,
        "chunks": 0,
        "duration": seconds,
        "seed": _seed(params),
    }


# Trocar rosto numa foto (modo "face"): o rosto da primeira imagem entra na pessoa da segunda,
# que fica com o corpo, a roupa, o cabelo (a menos que `hair`), a expressao e o cenario dela. E a
# segunda passada do rosto em detalhe feita sobre uma foto: o Animate refaz so o rosto, ampliado
# ate FACE_IMAGE_SIZE, com o rosto da primeira imagem como referencia, e o resto da foto fica
# com os pixels dela. A foto vira um video parado de FACE_FRAMES quadros (o Animate gera video)
# e sai o do meio.
FACE_IMAGE_SIZE = 768
FACE_FRAMES = 5


def _resolve_face(params, has_face, has_body):
    if not has_face:
        raise ValueError("envie a imagem do rosto")
    if not has_body:
        raise ValueError("envie a imagem da pessoa que vai receber o rosto")
    expert = params.get("expert") if isinstance(params.get("expert"), dict) else {}
    bad = "ajuste de teste inválido"
    if expert.get("onnx", "gpu") not in ("cpu", "gpu"):
        raise ValueError(bad)
    return {
        "mode": "face",
        "kind": "image",
        "model": "animate",
        "fast": True,
        "steps": _number(expert, "steps", 4, 1, 30, bad, int),
        # O cabelo vem junto do rosto (a cabeca inteira), em vez de ficar o da segunda imagem.
        "hair": bool(params.get("hair", False)),
        "onnx": expert.get("onnx", "gpu"),
        "max_size": _number(expert, "max_size", FACE_IMAGE_SIZE, 256, 1024, bad, int),
        "pin": _number(expert, "worker", None, 0, 16, bad, int),
        "replace": True,
        "length": FACE_FRAMES,
        "overlap": SWAP_OVERLAP,
        "fps": SWAP_FPS,
        # O tamanho e o da segunda imagem: o servidor o le dela (e confere no resultado).
        "width": 0,
        "height": 0,
        "duration": 0,
        "seed": _seed(params),
    }


def warm_spec():
    """O menor pedaco de troca de pessoa que faz o ComfyUI carregar o que um pedaco de verdade
    usa (modelo, codificador de texto, VAE, deteccao de pose): o servidor roda um em cada GPU
    antes do primeiro pedido (veja `warm_up` la). Sem o recorte da pessoa, porque o video de
    aquecimento nao tem ninguem."""
    spec = _resolve_swap({"quality": "rapida", "replace": False, "seed": 1}, True, True)
    spec.update(width=256, height=256, length=SWAP_MIN_FRAMES, steps=1, warm=True)
    return spec


def min_vram(spec):
    """Quantos GB de memoria a placa precisa ter para a qualidade deste job (0: qualquer uma)."""
    return SWAP_QUALITIES[spec["quality"]].get("min_vram", 0)


def hd_method(spec, files):
    """Com que a alta definicao sera feita neste job: o metodo pedido se os arquivos dele estao
    instalados, senao o outro; None se nenhum esta (ou se nao foi pedida). Quem ja gera em
    720p nao passa por ela: o arquivo nao ficaria maior."""
    ready = {"seedvr2": bool(files.get("restorer") and files.get("restorer_vae")),
             "esrgan": bool(files.get("upscaler"))}
    sizes = SWAP_QUALITIES.get(spec.get("quality"), {}).get("sizes")
    native = not spec.get("size") and sizes and min(min(size) for size in sizes.values()) >= SWAP_HD_SIDE
    if not spec.get("hd") or native:
        return None
    order = sorted(HD_METHODS, key=lambda method: method != spec.get("hd_method", HD_METHODS[0]))
    return next((method for method in order if ready[method]), None)


def animate_unet(spec, files):
    """O arquivo do modelo para este job; None se o pedido de teste quer um que nao esta instalado."""
    if not spec.get("unet"):
        return files["unet"]
    return next((found for name, found in files.get("alternatives", {}).items() if spec["unet"] in name), None)


def smooth_factor(spec, files):
    """Por quanto o modelo de interpolacao multiplica os quadros de cada pedaco: so quando ele
    esta instalado e a saida pede um multiplo inteiro do fps gerado. Com 1, o que faltar ate
    `out_fps` fica para o ffmpeg, na emenda."""
    factor, rest = divmod(spec["out_fps"], spec["fps"])
    usable = spec["smooth"] == "rife" and files.get("interpolator") and not spec.get("preview")
    return factor if usable and rest == 0 and 2 <= factor <= 4 else 1


def swap_size(spec, width, height):
    """Para um video de `width` x `height`: (tamanho de geracao, tamanho do arquivo final,
    nome da proporcao). Vale a proporcao mais proxima entre deitado, em pe e quadrado."""
    ratio = width / height
    aspect = "16:9" if ratio > 1.2 else "9:16" if ratio < 1 / 1.2 else "1:1"
    size = tuple(spec["size"]) if spec.get("size") else SWAP_QUALITIES[spec["quality"]]["sizes"][aspect]
    if spec.get("hd"):
        # O ampliador dobra o quadro; o arquivo fica com 720 px no lado menor, sem passar do dobro.
        factor = min(2.0, SWAP_HD_SIDE / min(size))
        return size, tuple(int(side * factor / 2 + 0.5) * 2 for side in size), aspect
    # Sem ela: o tamanho padrao de saida, ou o gerado quando ele ja e maior ("maxima").
    larger = size[0] * size[1] > SWAP_OUTPUT[aspect][0] * SWAP_OUTPUT[aspect][1]
    return size, (size if spec.get("size") or larger else SWAP_OUTPUT[aspect]), aspect


class _Graph:
    def __init__(self):
        self.nodes = {}
        self.stages = {}  # id do no -> o que mostrar na interface enquanto ele executa

    def add(self, class_type, **inputs):
        node_id = str(len(self.nodes) + 1)
        self.nodes[node_id] = {"class_type": class_type, "inputs": inputs}
        return node_id

    def stage(self, label, *node_ids):
        for node_id in node_ids:
            self.stages[node_id] = label

    def stage_since(self, label, count):
        """Rotula os nos criados depois que o grafo tinha `count` nos."""
        self.stage(label, *list(self.nodes)[count:])


def build(spec, files, positive, negative, prefix, start_image=None, end_image=None, video=None,
          motion=None):
    """Devolve (grafo, ids dos samplers na ordem de execucao, etapas por no).

    `files` e o mapa resolvido por `available()` para o conjunto que o job usa. `prefix` e o
    comeco do nome dos arquivos gravados: o id do job e, na troca de pessoa, tambem o pedaco.
    """
    if spec["mode"] == "swap":
        return _build_animate(spec, files, positive, negative, prefix, start_image, video, motion)
    if spec["mode"] == "face":
        return _build_face_image(spec, files, positive, negative, prefix, start_image, end_image)
    builder = _build_5b if spec["model"] == "5b" else _build_14b
    return builder(spec, files, positive, negative, prefix, start_image, end_image)


def _load_unet(g, name):
    if name.lower().endswith(".gguf"):
        return g.add("UnetLoaderGGUF", unet_name=name)
    return g.add("UNETLoader", unet_name=name, weight_dtype="default")


def _text(g, files, positive, negative):
    clip = g.add("CLIPLoader", clip_name=files["clip"], type="wan", device="default")
    pos = g.add("CLIPTextEncode", text=positive, clip=[clip, 0])
    neg = g.add("CLIPTextEncode", text=negative, clip=[clip, 0])
    return pos, neg


def _write(g, images, spec, job_id):
    """Grava as imagens ja decodificadas como PNG ou MP4."""
    prefix = f"wanstudio/{job_id}"
    if spec["kind"] == "image":
        g.stage("finalizando", g.add("SaveImage", images=images, filename_prefix=prefix))
        return
    video = g.add("CreateVideo", images=images, fps=spec["fps"])
    # "format.codec" e a chave do ComfyUI atual; "codec" a das versoes anteriores.
    save = g.add("SaveVideo", video=[video, 0], filename_prefix=prefix,
                 **{"format": "mp4", "format.codec": "h264", "codec": "h264"})
    g.stage("finalizando", video, save)


def _save(g, samples, vae, spec, job_id):
    images = g.add("VAEDecode", samples=samples, vae=[vae, 0])
    g.stage("finalizando", images)
    _write(g, [images, 0], spec, job_id)


def _build_5b(spec, files, positive, negative, job_id, start_image, _end_image):
    g = _Graph()
    model = _load_unet(g, files["unet"])
    vae = g.add("VAELoader", vae_name=files["vae"])
    pos, neg = _text(g, files, positive, negative)

    latent_inputs = dict(vae=[vae, 0], width=spec["width"], height=spec["height"],
                         length=spec["length"], batch_size=1)
    if start_image:
        image = g.add("LoadImage", image=start_image)
        latent_inputs["start_image"] = [image, 0]
    latent = g.add("Wan22ImageToVideoLatent", **latent_inputs)

    if spec["fast"]:
        model = g.add("LoraLoaderModelOnly", model=[model, 0], lora_name=files["lora"],
                      strength_model=1.0)
        model = g.add("ModelSamplingSD3", model=[model, 0], shift=8.0)
        select = g.add("KSamplerSelect", sampler_name="lcm")
        sigmas = g.add("ManualSigmas", sigmas=TURBO_5B_SIGMAS)
        sampler = g.add("SamplerCustom", model=[model, 0], add_noise=True,
                        noise_seed=spec["seed"], cfg=1.0, positive=[pos, 0], negative=[neg, 0],
                        sampler=[select, 0], sigmas=[sigmas, 0], latent_image=[latent, 0])
    else:
        model = g.add("ModelSamplingSD3", model=[model, 0], shift=8.0)
        sampler = g.add("KSampler", model=[model, 0], seed=spec["seed"], steps=20, cfg=5.0,
                        sampler_name="uni_pc", scheduler="simple", positive=[pos, 0],
                        negative=[neg, 0], latent_image=[latent, 0], denoise=1.0)
    _save(g, [sampler, 0], vae, spec, job_id)
    return g.nodes, [sampler], g.stages


def _build_14b(spec, files, positive, negative, job_id, start_image, end_image):
    fast = spec["fast"]
    # Valores dos templates oficiais do ComfyUI. Com a LoRA lightx2v: 4 passos sem CFG.
    # Sem ela: 20 passos, metade em cada modelo (CFG 4 no inicio+fim, 3.5 nos demais).
    if fast:
        steps, split, cfg, shift = 4, 2, 1.0, 5.0
    else:
        steps, split, cfg, shift = 20, 10, (4.0 if spec["mode"] == "flf" else 3.5), 8.0

    g = _Graph()
    vae = g.add("VAELoader", vae_name=files["vae"])
    pos, neg = _text(g, files, positive, negative)

    models = {}
    for stage in ("high", "low"):
        model = _load_unet(g, files[stage])
        if fast:
            model = g.add("LoraLoaderModelOnly", model=[model, 0],
                          lora_name=files[f"lora_{stage}"], strength_model=1.0)
        models[stage] = g.add("ModelSamplingSD3", model=[model, 0], shift=shift)

    size = dict(width=spec["width"], height=spec["height"], length=spec["length"], batch_size=1)
    if spec["mode"] in ("t2v", "img"):
        latent = [g.add("EmptyHunyuanLatentVideo", **size), 0]
        cond_pos, cond_neg = [pos, 0], [neg, 0]
    else:
        start = g.add("LoadImage", image=start_image)
        inputs = dict(positive=[pos, 0], negative=[neg, 0], vae=[vae, 0],
                      start_image=[start, 0], **size)
        if spec["mode"] == "flf":
            end = g.add("LoadImage", image=end_image)
            cond = g.add("WanFirstLastFrameToVideo", end_image=[end, 0], **inputs)
        else:
            cond = g.add("WanImageToVideo", **inputs)
        cond_pos, cond_neg, latent = [cond, 0], [cond, 1], [cond, 2]

    common = dict(steps=steps, cfg=cfg, sampler_name="euler", scheduler="simple",
                  positive=cond_pos, negative=cond_neg)
    high = g.add("KSamplerAdvanced", model=[models["high"], 0], add_noise="enable",
                 noise_seed=spec["seed"], latent_image=latent, start_at_step=0,
                 end_at_step=split, return_with_leftover_noise="enable", **common)
    low = g.add("KSamplerAdvanced", model=[models["low"], 0], add_noise="disable",
                noise_seed=spec["seed"], latent_image=[high, 0], start_at_step=split,
                end_at_step=10000, return_with_leftover_noise="disable", **common)
    _save(g, [low, 0], vae, spec, job_id)
    return g.nodes, [high, low], g.stages


def _blocky_mask(g, mask, width, height, grow=MASK_GROW):
    """Engorda a mascara da pessoa e a quadricula em blocos de ~32 px, como no treino do
    Animate: com o contorno exato, o personagem novo herdaria a silhueta do antigo."""
    grown = g.add("GrowMask", mask=mask, expand=grow, tapered_corners=True)
    as_image = g.add("MaskToImage", mask=[grown, 0])
    coarse = g.add("ImageScale", image=[as_image, 0], upscale_method="area",
                   width=max(1, width // 32), height=max(1, height // 32), crop="disabled")
    coarse_mask = g.add("ImageToMask", image=[coarse, 0], channel="red")
    # Qualquer bloco tocado pela mascara entra inteiro.
    solid = g.add("ThresholdMask", mask=[coarse_mask, 0], value=0.01)
    solid_image = g.add("MaskToImage", mask=[solid, 0])
    full = g.add("ImageScale", image=[solid_image, 0], upscale_method="nearest-exact",
                 width=width, height=height, crop="disabled")
    return [g.add("ImageToMask", image=[full, 0], channel="red"), 0]


def _detector(g, files, onnx):
    """O carregador dos modelos de pose e de pessoa (ViTPose e YOLO)."""
    device = "CUDAExecutionProvider" if onnx == "gpu" else "CPUExecutionProvider"
    return g.add("OnnxDetectionModelLoader", vitpose_model=files["vitpose"],
                 yolo_model=files["yolo"], onnx_device=device)


def _person_mask(g, files, frames, detected, width, height, method, sam_model, cover, box, grow, seal, whole=False):
    """O recorte da pessoa em cada quadro do pedaco, como vai para o Animate. Com `whole`, o do
    SAM2 e completado pelo do modelo de primeiro plano (`SWAP_WHOLE`)."""
    # O SAM2 recebe os pontos do corpo (da deteccao de pose) em alguns quadros do pedaco, mais
    # a caixa da pessoa no primeiro (saida 3), e a acompanha pelos demais: e o metodo do
    # pre-processamento oficial. So com a caixa do primeiro quadro, num teste o recorte pegou
    # apenas a cabeca e o corpo com a roupa original ficou no resultado.
    sam = g.add("DownloadAndLoadSAM2Model", model=SAM2_MODELS[sam_model], segmentor="video",
                device=SAM2["device"], precision=SAM2["precision"])
    person = g.add("WanStudioPersonMask", sam2_model=[sam, 0], images=frames, pose_data=[detected, 0],
                   method=method, key_frames=6, bboxes=[detected, 3], cover=cover, box=box, seal=seal)
    g.stage("recortando a pessoa", sam, person)
    if whole and files.get("foreground"):
        loader = g.add("WanStudioForegroundLoader", model=files["foreground"], device=SAM2_DEVICE)
        person = g.add("WanStudioWholePerson", images=frames, mask=[person, 0], model=[loader, 0],
                       pose_data=[detected, 0])
        g.stage("recortando a pessoa", loader, person)
    # O retangulo ja vem pronto do no; o contorno ainda e engordado e quadriculado aqui.
    return [person, 0] if cover == "caixa" else _blocky_mask(g, [person, 0], width, height, grow)


def _animate_inputs(g, files, frames, width, height, replace, onnx, method=MASK_METHODS[0], sam_model="base",
                    cover="contorno", box="justa", grow=MASK_GROW, seal=True, detector=None, whole=False):
    """Extrai do video o que guia o Animate: pose, rosto e (na troca) fundo e mascara.
    `detector` e um carregador ja no grafo (o da imagem da pessoa), para nao carregar dois."""
    if detector is None:
        detector = _detector(g, files, onnx)
    detected = g.add("PoseAndFaceDetection", model=[detector, 0], images=frames,
                     width=width, height=height)
    pose = g.add("DrawViTPose", pose_data=[detected, 0], width=width, height=height,
                 retarget_padding=16, body_stick_width=-1, hand_stick_width=-1, draw_head=True)
    g.stage("analisando o vídeo", detector, detected, pose)
    driving = {"face_video": [detected, 1], "pose_video": [pose, 0]}
    if not replace:
        return driving

    mask = _person_mask(g, files, frames, detected, width, height, method, sam_model, cover, box, grow, seal, whole)
    black = g.add("EmptyImage", width=width, height=height, batch_size=1, color=0)
    background = g.add("ImageCompositeMasked", destination=frames, source=[black, 0], x=0, y=0,
                       resize_source=False, mask=mask)
    driving.update(background_video=[background, 0], character_mask=mask)
    return driving


def _face_pass(g, spec, files, model, positive, negative, vae, vision, frames, driving, mask, person, motion=None,
               **window):
    """A segunda passada do rosto em detalhe (`SWAP_FACE`) sobre os quadros ja gerados
    (`frames`): a cabeca e ampliada, refeita pelo Animate com o recorte da cabeca da imagem da
    pessoa como referencia e posta de volta. `mask` e o recorte da pessoa no quadro inteiro
    (None: o quadro todo); `motion`, os quadros de que o pedaco continua; `window`, opcoes do
    no da cabeca (region, always, max_size). Devolve (quadros, sampler)."""
    detected = driving["face_video"][0]
    detector = g.nodes[detected]["inputs"]["model"][0]
    head = g.add("WanStudioHeadWindow", images=frames, pose_data=[detected, 0],
                 **({"mask": mask} if mask else {}), **({"motion": motion} if motion else {}), **window)
    side, size = [head, 6], [head, 7]
    found = g.add("PoseAndFaceDetection", model=[detector, 0], images=[person, 0], width=size, height=size)
    face = g.add("WanStudioFitReference", image=[person, 0], pose_data=[found, 0], bboxes=[found, 3],
                 width=size, height=size, region="cabeca")
    reference = g.add("ResizeAndPadImage", image=[face, 0], target_width=size, target_height=size,
                      padding_color="black", interpolation="lanczos")
    seen = g.add("CLIPVisionEncode", clip_vision=[vision, 0], image=[reference, 0], crop="none")
    pose = g.add("DrawViTPose", pose_data=[head, 2], width=size, height=size, retarget_padding=16,
                 body_stick_width=-1, hand_stick_width=-1, draw_head=True)
    black = g.add("EmptyImage", width=size, height=size, batch_size=1, color=0)
    background = g.add("ImageCompositeMasked", destination=[head, 0], source=[black, 0], x=0, y=0,
                       resize_source=False, mask=[head, 1])
    animate = g.add("WanAnimateToVideo", positive=[positive, 0], negative=[negative, 0], vae=[vae, 0],
                    width=size, height=size, length=spec["length"], batch_size=1,
                    continue_motion_max_frames=spec["overlap"], video_frame_offset=0,
                    clip_vision_output=[seen, 0], reference_image=[reference, 0], face_video=driving["face_video"],
                    pose_video=[pose, 0], background_video=[background, 0], character_mask=[head, 1],
                    **({"continue_motion": [head, 3]} if motion else {}))
    sampler = g.add("KSampler", model=[model, 0], seed=spec["seed"], steps=spec["steps"], cfg=1.0,
                    sampler_name="euler", scheduler="simple", positive=[animate, 0],
                    negative=[animate, 1], latent_image=[animate, 2], denoise=1.0)
    trimmed = g.add("TrimVideoLatent", samples=[sampler, 0], trim_amount=[animate, 3])
    decoded = g.add("VAEDecode", samples=[trimmed, 0], vae=[vae, 0])
    small = g.add("ImageScale", image=[decoded, 0], upscale_method="area", width=side, height=side, crop="disabled")
    # Com o rosto ja grande a cabeca nao e refeita: o no de colar nem pede o resultado.
    pasted = g.add("WanStudioFacePaste", images=frames, active=[head, 8], x=[head, 4], y=[head, 5],
                   patch=[small, 0], mask=[head, 9])
    g.stage("refazendo o rosto", head, found, face, reference, seen, pose, black, background, animate)
    g.stage("finalizando", trimmed, decoded, small, pasted)
    return pasted, sampler


def _build_face_image(spec, files, positive, negative, prefix, face_image, body_image):
    """Trocar rosto numa foto (modo "face"; veja FACE_IMAGE_SIZE): o rosto de `face_image` na
    pessoa de `body_image`. Devolve (grafo, samplers, etapas), como `build`."""
    g = _Graph()
    vae = g.add("VAELoader", vae_name=files["vae"])
    pos, neg = _text(g, files, positive, negative)
    model = _load_unet(g, files["unet"])
    model = g.add("LoraLoaderModelOnly", model=[model, 0], lora_name=files["lora"], strength_model=1.0)
    if files.get("relight"):  # a luz do rosto novo acompanha a da foto
        model = g.add("LoraLoaderModelOnly", model=[model, 0], lora_name=files["relight"], strength_model=1.0)
    model = g.add("ModelSamplingSD3", model=[model, 0], shift=8.0)
    vision = g.add("CLIPVisionLoader", clip_name=files["clip_vision"])
    person = g.add("LoadImage", image=face_image)
    body = g.add("LoadImage", image=body_image)
    frames = g.add("RepeatImageBatch", image=[body, 0], amount=spec["length"])
    detector = _detector(g, files, spec["onnx"])
    g.stage_since("carregando o modelo", 0)
    detected = g.add("PoseAndFaceDetection", model=[detector, 0], images=[frames, 0], width=1024, height=1024)
    g.stage("analisando a imagem", detected)
    images, sampler = _face_pass(g, spec, files, model, pos, neg, vae, vision, [frames, 0], {"face_video": [detected, 1]},
                                 None, person, region="cabeca" if spec.get("hair") else "rosto", always=True,
                                 max_size=spec["max_size"])
    single = g.add("ImageFromBatch", image=[images, 0], batch_index=spec["length"] // 2, length=1)
    save = g.add("SaveImage", images=[single, 0], filename_prefix=f"wanstudio/{prefix}")
    g.stage("finalizando", single, save)
    return g.nodes, [sampler], g.stages


def _window_inputs(g, files, frames, width, height, spec, detector=None, motion=None):
    """O mesmo que `_animate_inputs` na troca de pessoa, com a janela (`SWAP_WINDOW`): a pose e o
    recorte sao achados no quadro inteiro, como sempre, e dai em diante tudo e cortado no
    retangulo em que a pessoa esta. O tamanho dele so o grafo sabe: quem precisa de largura e
    altura liga-se as saidas 6 e 7 do no da janela, que esta funcao devolve junto com o que guia
    o Animate. `motion`: os quadros de que o pedaco continua, que sao cortados do mesmo jeito."""
    if detector is None:
        detector = _detector(g, files, spec["onnx"])
    detected = g.add("PoseAndFaceDetection", model=[detector, 0], images=frames, width=width, height=height)
    g.stage("analisando o vídeo", detector, detected)
    mask = _person_mask(g, files, frames, detected, width, height, spec["mask"], spec["sam"],
                        spec.get("cover", MASK_COVERS[0]), spec.get("box", MASK_BOXES[0]),
                        spec.get("grow", MASK_GROW), spec.get("seal", True), spec.get("whole", False))
    cut = g.add("WanStudioWindow", images=frames, mask=mask, pose_data=[detected, 0],
                **({"motion": motion} if motion else {}),
                **({"margin": spec["window_margin"]} if spec.get("window_margin") else {}))
    pose = g.add("DrawViTPose", pose_data=[cut, 2], width=[cut, 6], height=[cut, 7],
                 retarget_padding=16, body_stick_width=-1, hand_stick_width=-1, draw_head=True)
    black = g.add("EmptyImage", width=[cut, 6], height=[cut, 7], batch_size=1, color=0)
    background = g.add("ImageCompositeMasked", destination=[cut, 0], source=[black, 0], x=0, y=0,
                       resize_source=False, mask=[cut, 1])
    g.stage("recortando a pessoa", cut, pose, black, background)
    return {"face_video": [detected, 1], "pose_video": [pose, 0], "background_video": [background, 0],
            "character_mask": [cut, 1]}, cut


def _smooth(g, spec, files, images):
    """Entre dois quadros entram `factor` - 1 calculados (RIFE): N quadros viram
    (N - 1) * factor + 1, e o servidor conta com isso na emenda."""
    if spec.get("factor", 1) <= 1:
        return images
    interpolator = g.add("FrameInterpolationModelLoader", model_name=files["interpolator"])
    smooth = g.add("FrameInterpolate", interp_model=[interpolator, 0], images=[images, 0],
                   multiplier=spec["factor"])
    g.stage("carregando o modelo", interpolator)
    g.stage("suavizando o movimento", smooth)
    return smooth


def build_restore(spec, files, prefix, video):
    """O grafo da alta definicao: restaura um pedaco ja gerado (`video`, os quadros dele sem
    perda) para o tamanho final do job e calcula os quadros intermediarios.

    E um grafo separado do da geracao, rodado depois de todos os pedacos e de o ComfyUI soltar
    os modelos dela. Dentro do mesmo grafo funcionou num pedaco, mas numa troca de 10 s as
    duas GPUs ficaram com os modelos da geracao e os da restauracao na memoria ao mesmo
    tempo, e o sistema matou um dos ComfyUI (a maquina do Kaggle tem 30 GB de RAM)."""
    g = _Graph()
    parts = g.add("GetVideoComponents", video=[g.add("LoadVideo", file=video), 0])
    restored, sampler = _restore(g, files, parts, (spec["width"], spec["height"]), spec["seed"])
    images = _smooth(g, spec, files, restored)
    save = g.add("SaveImage", images=[images, 0], filename_prefix=f"wanstudio/{prefix}")
    g.stage("finalizando", save)
    return g.nodes, [sampler], g.stages


def _restore(g, files, images, size, seed):
    """Restauracao com o SeedVR2: os quadros sao ampliados para `size` e o modelo, em um passo,
    recria o detalhe que a geracao em baixa resolucao nao tem. E o grafo do exemplo oficial do
    ComfyUI; a cor volta a ser a dos quadros de entrada no fim. Devolve (quadros, sampler)."""
    resized = g.add("ImageScale", image=[images, 0], upscale_method="lanczos", width=size[0],
                    height=size[1], crop="disabled")
    padded = g.add("SeedVR2Preprocess", resized_images=[resized, 0])
    vae = g.add("VAELoader", vae_name=files["restorer_vae"])
    tiles = {"tile_size": 512, "overlap": 128, "temporal_size": 64, "temporal_overlap": 8}
    latent = g.add("VAEEncodeTiled", pixels=[padded, 0], vae=[vae, 0], **tiles)
    model = g.add("UNETLoader", unet_name=files["restorer"], weight_dtype="default")
    guide = g.add("SeedVR2Conditioning", model=[model, 0], vae_conditioning=[latent, 0])
    sampled = g.add("KSampler", model=[model, 0], seed=seed, steps=1, cfg=1.0, sampler_name="euler",
                    scheduler="simple", positive=[guide, 0], negative=[guide, 1],
                    latent_image=[latent, 0], denoise=1.0)
    decoded = g.add("VAEDecodeTiled", samples=[sampled, 0], vae=[vae, 0], **tiles)
    restored = g.add("SeedVR2PostProcessing", images=[decoded, 0], original_resized_images=[resized, 0],
                     color_correction_method="lab")
    g.stage("carregando o modelo", vae, model)
    g.stage("restaurando os detalhes", resized, padded, latent, guide, sampled, decoded, restored)
    return restored, sampled


def _mask_preview(g, spec, files, prefix, video):
    """O pedaco com a regiao que seria trocada pintada de vermelho, sem gerar nada: serve para
    conferir, em segundos, se o recorte cobre a pessoa inteira."""
    width, height = spec["width"], spec["height"]
    parts = g.add("GetVideoComponents", video=[g.add("LoadVideo", file=video), 0])
    driving = _animate_inputs(g, files, [parts, 0], width, height, True, spec["onnx"], spec["mask"], spec["sam"],
                              spec.get("cover", MASK_COVERS[0]), spec.get("box", MASK_BOXES[0]), spec.get("grow", MASK_GROW),
                              spec.get("seal", True), whole=spec.get("whole", False))
    red = g.add("EmptyImage", width=width, height=height, batch_size=1, color=0xFF2020)
    painted = g.add("ImageCompositeMasked", destination=[parts, 0], source=[red, 0], x=0, y=0,
                    resize_source=False, mask=driving["character_mask"])
    mixed = g.add("ImageBlend", image1=[parts, 0], image2=[painted, 0], blend_factor=0.6, blend_mode="normal")
    g.stage("finalizando", g.add("SaveImage", images=[mixed, 0], filename_prefix=f"wanstudio/{prefix}"))
    return g.nodes, [], g.stages


def _build_animate(spec, files, positive, negative, prefix, person_image, video, motion=None):
    """Um pedaco da troca de pessoa: Wan 2.2 Animate com a amostragem do template oficial do
    ComfyUI e o pre-processamento automatico (pose/rosto por ViTPose, recorte por SAM2).

    `video` ja chega cortado no tamanho, no fps e na quantidade de quadros do pedaco
    (`spec["length"]`). `motion` sao os quadros ja gerados do vizinho que correspondem ao
    comeco deste pedaco: ele os repete e continua dali, mantendo a aparencia. A saida sao os
    quadros em PNG, inclusive os repetidos: quem monta o video e o servidor (stitch.py).
    """
    g = _Graph()
    width, height, length = spec["width"], spec["height"], spec["length"]
    if spec.get("preview"):
        return _mask_preview(g, spec, files, prefix, video)
    vae = g.add("VAELoader", vae_name=files["vae"])
    pos, neg = _text(g, files, positive, negative)

    model = _load_unet(g, animate_unet(spec, files) or files["unet"])
    model = g.add("LoraLoaderModelOnly", model=[model, 0], lora_name=files["lora"],
                  strength_model=1.0)
    if spec["replace"] and files.get("relight"):  # ajusta a luz da pessoa a do video
        model = g.add("LoraLoaderModelOnly", model=[model, 0], lora_name=files["relight"],
                      strength_model=1.0)
    model = g.add("ModelSamplingSD3", model=[model, 0], shift=8.0)

    # A pessoa entra inteira: bordas pretas em vez de corte quando a proporcao difere. Com
    # `ref=crop`, a imagem e recortada antes na caixa da pessoa, na proporcao do quadro: ela
    # ocupa mais dele, e as faixas so aparecem onde a imagem acaba.
    person = g.add("LoadImage", image=person_image)
    fitted, detector, framed = [person, 0], None, []
    if spec.get("ref") == "crop":
        detector = _detector(g, files, spec["onnx"])
        found = g.add("PoseAndFaceDetection", model=[detector, 0], images=[person, 0], width=width, height=height)
        fitted = [g.add("WanStudioFitReference", image=[person, 0], pose_data=[found, 0], bboxes=[found, 3],
                        width=width, height=height), 0]
        framed.append((fitted[0], "width", "height"))
    reference = g.add("ResizeAndPadImage", image=fitted, target_width=width,
                      target_height=height, padding_color="black", interpolation="lanczos")
    framed.append((reference, "target_width", "target_height"))
    vision = g.add("CLIPVisionLoader", clip_name=files["clip_vision"])
    vision_out = g.add("CLIPVisionEncode", clip_vision=[vision, 0], image=[reference, 0],
                       crop="none")
    # Sem isto a interface seguiria dizendo "analisando o video" enquanto o que roda e a
    # leitura dos modelos, que na primeira vez leva minutos.
    g.stage_since("carregando o modelo", 0)

    source = g.add("LoadVideo", file=video)
    parts = g.add("GetVideoComponents", video=[source, 0])
    cut, size, previous = None, (width, height), None
    if spec.get("window") and spec["replace"]:
        previous = [g.add("GetVideoComponents", video=[g.add("LoadVideo", file=motion), 0]), 0] if motion else None
        driving, cut = _window_inputs(g, files, [parts, 0], width, height, spec, detector, previous)
        size = ([cut, 6], [cut, 7])
        if previous:
            driving["continue_motion"] = [cut, 3]
        # A imagem da pessoa entra no formato da janela, e nao no do quadro.
        for node, wide, tall in framed:
            g.nodes[node]["inputs"].update({wide: size[0], tall: size[1]})
    else:
        driving = _animate_inputs(g, files, [parts, 0], width, height, spec["replace"], spec["onnx"],
                                  spec["mask"], spec["sam"], spec.get("cover", MASK_COVERS[0]),
                                  spec.get("box", MASK_BOXES[0]), spec.get("grow", MASK_GROW), spec.get("seal", True),
                                  detector, whole=spec.get("whole", False))
        if motion:
            previous = [g.add("GetVideoComponents", video=[g.add("LoadVideo", file=motion), 0]), 0]
            driving["continue_motion"] = previous
    animate = g.add("WanAnimateToVideo", positive=[pos, 0], negative=[neg, 0], vae=[vae, 0],
                    width=size[0], height=size[1], length=length, batch_size=1,
                    continue_motion_max_frames=spec["overlap"], video_frame_offset=0,
                    clip_vision_output=[vision_out, 0], reference_image=[reference, 0], **driving)
    g.stage("preparando a geração", animate)
    sampler = g.add("KSampler", model=[model, 0], seed=spec["seed"], steps=spec["steps"], cfg=1.0,
                    sampler_name="euler", scheduler="simple", positive=[animate, 0],
                    negative=[animate, 1], latent_image=[animate, 2], denoise=1.0)
    # A saida 3 diz quanto cortar do comeco: o quadro de referencia.
    trimmed = g.add("TrimVideoLatent", samples=[sampler, 0], trim_amount=[animate, 3])
    decoded = g.add("VAEDecode", samples=[trimmed, 0], vae=[vae, 0])
    images = decoded
    if cut:
        # Os quadros gerados voltam para o lugar deles no video. Quando o pedaco continua de um
        # vizinho no tempo, a cor deles e trazida a dele ali mesmo, dentro do recorte (no comeco
        # de uma segunda cadeia os quadros recebidos sao de outro instante: nao ha o que comparar).
        match = {"previous": [cut, 3], "overlap": spec["overlap"]} if motion and not spec.get("anchored") else {}
        images = g.add("WanStudioPaste", images=[parts, 0], patch=[decoded, 0], mask=[cut, 1],
                       x=[cut, 4], y=[cut, 5], **match)
        g.stage("finalizando", images)
    samplers = [sampler]
    if spec.get("face") and spec["replace"]:
        whole_mask = g.nodes[cut]["inputs"]["mask"] if cut else driving["character_mask"]
        images, second = _face_pass(g, spec, files, model, pos, neg, vae, vision, [images, 0], driving,
                                    whole_mask, person, previous)
        samplers.append(second)
    # A restauracao (SeedVR2) nao entra aqui: e outro grafo, rodado depois (`build_restore`).
    if hd_method(spec, files) == "esrgan":
        upscaler = g.add("UpscaleModelLoader", model_name=files["upscaler"])
        images = g.add("ImageUpscaleWithModel", upscale_model=[upscaler, 0], image=[images, 0])
        # O ComfyUI le os modelos logo no comeco: o rotulo vale para o no que os usa.
        g.stage("carregando o modelo", upscaler)
        g.stage("ampliando", images)
    images = _smooth(g, spec, files, images)
    if spec.get("warm"):  # aquecimento: nada para guardar
        save = g.add("PreviewImage", images=[images, 0])
    else:
        save = g.add("SaveImage", images=[images, 0], filename_prefix=f"wanstudio/{prefix}")
    g.stage("finalizando", decoded, save)
    return g.nodes, samplers, g.stages
