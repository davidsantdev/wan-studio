#!/usr/bin/env bash
# Prepara uma maquina Linux com GPU NVIDIA: ComfyUI + modelos Wan 2.2 + Wan Studio.
#
#   MODELS=5b    Wan 2.2 5B + LoRA turbo                      (12 GB de VRAM, ~25 GB de disco)
#   MODELS=14b   Wan 2.2 14B fp8, texto e imagem -> video     (24 GB de VRAM, ~80 GB)
#   MODELS=all   os dois acima + Animate (troca de pessoa)    (24 GB de VRAM, ~125 GB)
#   MODELS=free  5B e, enquanto houver disco: melhorador de prompt, Animate e 14B, em GGUF Q4
#                (GPUs gratuitas: Kaggle, Colab, Lightning AI). Numa placa de 24 GB ou mais o
#                Animate vem em fp8, que e o que a qualidade "ultra" da troca de pessoa usa.
#                Com SO_TROCA=1, so a troca de pessoa: 27 GB em vez de 83, e pronta mais cedo.
#
#   PHASE=all|core|extras|models
#                           core vai ate o 5B ficar pronto; extras baixa o resto (MODELS=free);
#                           models so baixa os modelos (os de core e os de extras), sem instalar
#                           nada: para um disco de modelos preparado a parte (nuvem/)
#   NO_VENV=1               usa o Python do sistema (notebooks)
#   SO_TROCA=1              MODELS=free sem o 5B, o 14B e o melhorador de prompt: a sessao so
#                           troca pessoa em video (e o que o notebook gerado usa; veja free/build.py)
#   SKIP_MODELS=1           core instala tudo e nao baixa modelo nenhum (imagem pronta, sem GPU)
#   KEEP_TORCH=1            nao troca o PyTorch instalado, mesmo sem GPU a vista (idem)
#   TORCH_INDEX=cu130       troca o PyTorch pelo da mesma versao compilado para outro CUDA. Com o
#                           CUDA 13 o ComfyUI liga os nucleos otimizados dele (comfy-kitchen), que
#                           e o que faz um modelo em INT8 render; pede driver NVIDIA 580 ou mais novo
#   ANIMATE_INT8=1          baixa tambem o Animate em INT8 (17 GB), para medir contra o GGUF
#   PESSOA_INTEIRA=1        baixa tambem o BiRefNet (1 GB), que completa o recorte da pessoa
#                           (ajuste de teste `whole` da troca de pessoa)
#   SKIP_APT=1              nao chama o apt-get (a imagem ja traz git, ffmpeg e aria2)
#   INCOMING=/caminho       onde ficam os downloads pela metade (padrao: junto dos modelos)
#   ANIMATE_KIND=fp8|gguf   qual Animate baixar, sem perguntar a placa (padrao: pela memoria dela)
#   NO_ROOM_CHECK=1         nao confere o espaco livre (discos de rede informam valores sem sentido)
#   SKIP_START=1            nao sobe os servicos no final
#   ENHANCER=auto|on|off    melhorador de prompt: auto = so com uma segunda GPU sobrando
#   MODELS_DIR=/caminho     guarda os modelos (e os downloads pela metade) fora da pasta do
#                           projeto; o ComfyUI os enxerga por um atalho. Para quando a pasta do
#                           projeto fica num disco pequeno ou cobrado e ha outro, temporario.
#   DISK_BUDGET_GB=N        teto do que a pasta do projeto pode ocupar
#
# Pode rodar de novo a qualquer momento: nada e baixado duas vezes.
set -euo pipefail

MODELS="${MODELS:-5b}"
PHASE="${PHASE:-all}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMFY="$ROOT/ComfyUI"
VENV="$ROOT/venv"
LOGS="$ROOT/logs"
STORE="${MODELS_DIR:-$ROOT}"     # o disco em que os modelos ficam
INCOMING="${INCOMING:-$STORE/downloads}"  # parciais ficam fora de models/ para o ComfyUI nao os listar
RESERVE_GB="${RESERVE_GB:-6}" # folga para os videos gerados e arquivos temporarios

case "$MODELS" in 5b|14b|all|free) ;; *) echo "MODELS deve ser 5b, 14b, all ou free" >&2; exit 1 ;; esac
case "$PHASE" in all|core|extras|models) ;; *) echo "PHASE deve ser all, core, extras ou models" >&2; exit 1 ;; esac
mkdir -p "$LOGS" "$INCOMING"

if [ -n "${NO_VENV:-}" ]; then PY="${PYTHON:-python3}"; else PY="$VENV/bin/python"; fi

W22="https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files"
W21="https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files"
GGUF="https://huggingface.co/QuantStack"
KIJAI="https://huggingface.co/Kijai/WanVideo_comfy/resolve/main"
FASTWAN="$KIJAI/FastWan"
LLM_REPO="Qwen/Qwen3-4B-Instruct-2507"
# Commits com que os workflows foram validados. Para testar outra versao, defina um commit,
# uma tag ou "master".
COMFY_REF="${COMFY_REF:-7f7fd918ed0815c5d5ef2ccfe44bfc8e72cfd217}"  # ComfyUI 0.39
GGUF_REF="${GGUF_REF:-6ea2651e7df66d7585f6ffee804b20e92fb38b8a}"
# Pre-processadores do Animate (troca de pessoa): pose/rosto e recorte por SAM2.
PREPROCESS_REF="${PREPROCESS_REF:-0e0b6a2a555625acf4d4aefb780e27d06937132f}"
SAM2_REF="${SAM2_REF:-0c35fff5f382803e2310103357b5e985f5437f32}"
FOREGROUND_REF="${FOREGROUND_REF:-b6561965a70070d9143fd9e558f6ca3c481510db}"

free_gb() { df -BG --output=avail "$STORE" | tail -1 | tr -dc 0-9; }

gpu_count() { nvidia-smi -L 2>/dev/null | grep -c '^GPU ' || true; }

# O Animate em fp8 (18 GB) cabe inteiro numa placa de 24 GB; abaixo disso, GGUF Q4 (11 GB).
animate_kind() {
  local vram
  if [ -n "${ANIMATE_KIND:-}" ]; then echo "$ANIMATE_KIND"; return 0; fi
  vram="$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -dc 0-9)"
  if [ "${vram:-0}" -ge 20000 ]; then echo fp8; else echo gguf; fi
}

# Com MODELS_DIR, a pasta de modelos do ComfyUI vira um atalho para la. O que ja houver nela
# (os arquivos de marcacao do ComfyUI, ou modelos de uma sessao anterior) vai junto.
link_models() {
  if [ -z "${MODELS_DIR:-}" ]; then
    # Sobrou o atalho de uma sessao que usou um disco temporario: volta a ser uma pasta.
    if [ -L "$COMFY/models" ]; then rm -f "$COMFY/models"; mkdir -p "$COMFY/models"; fi
    return 0
  fi
  mkdir -p "$MODELS_DIR/models"
  if [ -d "$COMFY/models" ] && [ ! -L "$COMFY/models" ]; then
    cp -a "$COMFY/models/." "$MODELS_DIR/models/" || return 1
    rm -rf "$COMFY/models"
  fi
  ln -sfn "$MODELS_DIR/models" "$COMFY/models"
}

# So na fase extras: a interface mostra esta linha enquanto os downloads acontecem.
note() {
  echo "$*"
  if [ "$PHASE" = extras ]; then echo "$*" >"$LOGS/extras.status"; fi
}

checkout() {  # checkout <url> <pasta> <commit, tag ou branch>
  local url="$1" dir="$2" ref="$3"
  if [ "$(git -C "$dir" rev-parse HEAD 2>/dev/null)" = "$ref" ]; then
    return 0
  fi
  mkdir -p "$dir"
  git -C "$dir" init -q
  git -C "$dir" remote add origin "$url" 2>/dev/null || git -C "$dir" remote set-url origin "$url"
  git -C "$dir" fetch -q --depth 1 origin "$ref"
  git -C "$dir" checkout -q FETCH_HEAD
}

# No Kaggle o disco diz ter 1 TB livre, mas a sessao tem um teto proprio para o que grava:
# em 12/10/2026, com algo entre 91 e 100 GB gravados, o sistema de arquivos passou a somente
# leitura, os dois ComfyUI travaram e o Kaggle encerrou a sessao com erro. Com 83 GB rodou
# varias vezes. Por isso o instalador tem o seu teto, e os conjuntos somam 83 GB.
if [ -z "${DISK_BUDGET_GB:-}" ] && [ -d /kaggle ]; then DISK_BUDGET_GB=88; fi
used_gb() { du -sk "$ROOT" 2>/dev/null | awk '{printf "%d", ($1 + 1048575) / 1048576}'; }

have_room() {  # have_room <GB> <descricao>
  local free used
  if [ -n "${NO_ROOM_CHECK:-}" ]; then return 0; fi
  free="$(free_gb)"
  if [ "$free" -lt $(( $1 + RESERVE_GB )) ]; then
    echo "pulando $2: precisa de ~$1 GB e ha $free GB livres"
    return 1
  fi
  if [ -n "${DISK_BUDGET_GB:-}" ]; then
    used="$(used_gb)"
    if [ $(( used + $1 )) -gt "$DISK_BUDGET_GB" ]; then
      echo "pulando $2: precisa de ~$1 GB, ja ha $used GB gravados e o teto desta sessao e de $DISK_BUDGET_GB GB"
      return 1
    fi
  fi
}

# As funcoes de download devolvem erro com "|| return 1" em vez de contar com o "set -e":
# o bash ignora o "set -e" dentro de funcoes chamadas em "if" ou "||".
fetch() {  # fetch <url> <subpasta de models>
  local url="$1" dir="$COMFY/models/$2" name
  name="$(basename "$url")"
  if [ -f "$dir/$name" ]; then
    echo "ok  $2/$name"
    return 0
  fi
  mkdir -p "$dir"
  echo "baixando $2/$name"
  if command -v aria2c >/dev/null; then
    aria2c -c -x 16 -s 16 -k 1M --max-tries=8 --retry-wait=5 --file-allocation=none \
      --console-log-level=warn --summary-interval=60 -d "$INCOMING" -o "$name" "$url" || return 1
  else
    wget -c -q --show-progress --tries=8 -O "$INCOMING/$name" "$url" || return 1
  fi
  mv "$INCOMING/$name" "$dir/$name" || return 1
}

set_5b() {
  fetch "$W22/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors" diffusion_models || return 1
  fetch "$W22/vae/wan2.2_vae.safetensors" vae || return 1
  fetch "$FASTWAN/Wan2_2_5B_FastWanFullAttn_lora_rank_128_bf16.safetensors" loras || return 1
}

loras_14b() {  # loras_14b <t2v|i2v>
  local version=v1 noise
  if [ "$1" = t2v ]; then version=v1.1; fi
  fetch "$W22/vae/wan_2.1_vae.safetensors" vae || return 1
  for noise in high low; do
    fetch "$W22/loras/wan2.2_$1_lightx2v_4steps_lora_${version}_${noise}_noise.safetensors" loras || return 1
  done
}

set_14b_fp8() {  # set_14b_fp8 <t2v|i2v>
  local noise
  for noise in high low; do
    fetch "$W22/diffusion_models/wan2.2_$1_${noise}_noise_14B_fp8_scaled.safetensors" diffusion_models || return 1
  done
  loras_14b "$1" || return 1
}

set_14b_gguf() {  # set_14b_gguf <t2v|i2v>
  local kind noise
  kind="$(echo "$1" | tr a-z A-Z)"
  for noise in HighNoise LowNoise; do
    # Q4_K_S (8,2 GB) em vez de Q4_K_M (9,0): os 3,4 GB poupados nos dois conjuntos pagam a
    # restauracao de video da troca de pessoa sem passar do teto de disco.
    fetch "$GGUF/Wan2.2-$kind-A14B-GGUF/resolve/main/$noise/Wan2.2-$kind-A14B-$noise-Q4_K_S.gguf" diffusion_models || return 1
  done
  loras_14b "$1" || return 1
}

set_animate() {  # set_animate <gguf|fp8>
  # Os opcionais vem primeiro (sao pequenos): a troca de pessoa e liberada quando o ultimo
  # arquivo obrigatorio chega, e ai eles ja precisam estar no lugar.
  #  - amplia os quadros gerados (saida em 720p sem gerar em 720p, que na T4 leva horas);
  #  - calcula quadros intermediarios (RIFE), para gerar so parte dos quadros do video. Sem
  #    ele o ffmpeg faz isso na emenda, com resultado pior em movimento rapido.
  fetch "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth" upscale_models \
    || echo "aviso: o modelo de ampliacao nao baixou (a troca de pessoa funciona sem ele)" >&2
  fetch "https://huggingface.co/Comfy-Org/frame_interpolation/resolve/main/frame_interpolation/rife_v4.25.safetensors" frame_interpolation \
    || echo "aviso: o modelo de interpolacao nao baixou (a troca de pessoa funciona sem ele)" >&2
  # Recorte (SAM2): o no so baixa sozinho se o arquivo faltar; deixa-lo pronto evita depender disso.
  fetch "https://huggingface.co/Kijai/sam2-safetensors/resolve/main/sam2.1_hiera_base_plus-fp16.safetensors" sam2 || return 1
  if [ "$1" = gguf ]; then
    fetch "$GGUF/Wan2.2-Animate-14B-GGUF/resolve/main/Wan2.2-Animate-14B-Q4_K_S.gguf" diffusion_models || return 1
  else
    fetch "https://huggingface.co/Kijai/WanVideo_comfy_fp8_scaled/resolve/main/Wan22Animate/Wan2_2-Animate-14B_fp8_e4m3fn_scaled_KJ.safetensors" diffusion_models || return 1
  fi
  fetch "$W22/vae/wan_2.1_vae.safetensors" vae || return 1
  fetch "$W21/clip_vision/clip_vision_h.safetensors" clip_vision || return 1
  fetch "$KIJAI/Lightx2v/lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors" loras || return 1
  fetch "$KIJAI/LoRAs/Wan22_relight/WanAnimate_relight_lora_fp16.safetensors" loras || return 1
  # Deteccao da pessoa (YOLO) e pose e rosto (ViTPose).
  fetch "https://huggingface.co/Wan-AI/Wan2.2-Animate-14B/resolve/main/process_checkpoint/det/yolov10m.onnx" detection || return 1
  fetch "https://huggingface.co/JunkyByte/easy_ViTPose/resolve/main/onnx/wholebody/vitpose-l-wholebody.onnx" detection || return 1
  # Opcional: restauracao de video (SeedVR2, um passo). Recria o detalhe que a geracao em 480p
  # nao tem e entrega em 720p; e o que a opcao de alta definicao usa quando esta instalado.
  { fetch "https://huggingface.co/Comfy-Org/SeedVR2/resolve/main/vae/seedvr2_ema_vae_fp16.safetensors" vae \
      && fetch "https://huggingface.co/Comfy-Org/SeedVR2/resolve/main/diffusion_models/seedvr2_3b_fp8_e4m3fn.safetensors" diffusion_models; } \
    || echo "aviso: o modelo de restauracao nao baixou (a troca de pessoa funciona sem ele)" >&2
  # So para medir: o recorte da pessoa inteira (BiRefNet, licenca MIT), na versao conferida.
  if [ -n "${PESSOA_INTEIRA:-}" ] && have_room 2 "o modelo do recorte da pessoa inteira"; then
    set_foreground || echo "aviso: o modelo do recorte da pessoa inteira nao baixou" >&2
  fi
  # So para medir: o mesmo modelo em INT8 (o formato em que a T4 mais rende, com os nucleos do
  # comfy-kitchen: veja TORCH_INDEX). Nao cabe inteiro na T4: o ComfyUI carrega o que couber.
  if [ -n "${ANIMATE_INT8:-}" ] && have_room 18 "o Animate em INT8"; then
    fetch "$W22/diffusion_models/wan2.2_animate_14B_int8_convrot.safetensors" diffusion_models \
      || echo "aviso: o Animate em INT8 nao baixou" >&2
  fi
  # So para medir: outra compressao do mesmo modelo (ex.: ANIMATE_EXTRA=Q6_K), escolhida por pedido.
  if [ -n "${ANIMATE_EXTRA:-}" ] && have_room 14 "o Animate ${ANIMATE_EXTRA}"; then
    fetch "$GGUF/Wan2.2-Animate-14B-GGUF/resolve/main/Wan2.2-Animate-14B-${ANIMATE_EXTRA}.gguf" diffusion_models \
      || echo "aviso: o Animate ${ANIMATE_EXTRA} nao baixou" >&2
  fi
}

set_foreground() {
  # O codigo do modelo vem junto dos pesos (o ComfyUI o carrega da pasta): por isso a versao fixa.
  local dir="$COMFY/models/BiRefNet/BiRefNet-portrait"
  if [ -f "$dir/.complete" ]; then
    echo "ok  BiRefNet/BiRefNet-portrait"
    return 0
  fi
  "$PY" -c "import sys; from huggingface_hub import snapshot_download; snapshot_download(sys.argv[1], revision=sys.argv[2], local_dir=sys.argv[3])" \
    "ZhengPeng7/BiRefNet-portrait" "$FOREGROUND_REF" "$dir" || return 1
  touch "$dir/.complete"
}

set_llm() {
  local dir="$ROOT/models-llm/enhancer"
  if [ -f "$dir/.complete" ]; then
    echo "ok  melhorador de prompt"
    return 0
  fi
  "$PY" -c "import sys; from huggingface_hub import snapshot_download; snapshot_download(sys.argv[1], local_dir=sys.argv[2])" \
    "$LLM_REPO" "$dir" || return 1
  touch "$dir/.complete"
}

wants_enhancer() {
  if [ -n "${SO_TROCA:-}" ]; then return 1; fi  # a troca de pessoa nao usa o prompt melhorado
  case "${ENHANCER:-auto}" in
    on) return 0 ;;
    off) return 1 ;;
    *) [ "$(gpu_count)" -ge 2 ] ;;
  esac
}

core() {
  if [ -z "${SKIP_GPU_CHECK:-}" ]; then
    if ! command -v nvidia-smi >/dev/null; then
      echo "nvidia-smi nao encontrado: esta maquina nao tem GPU NVIDIA ativa." >&2
      echo "No Kaggle: Session options > Accelerator > GPU T4 x2." >&2
      exit 1
    fi
    nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
  fi

  local need_gb=25 free
  if [ "$MODELS" = 14b ]; then need_gb=80; fi
  if [ "$MODELS" = all ]; then need_gb=125; fi
  if [ -n "${SKIP_MODELS:-}" ] || [ -n "${NO_ROOM_CHECK:-}" ]; then need_gb=0; fi
  free="$(free_gb)"
  if [ "$free" -lt "$need_gb" ]; then
    echo "Disco insuficiente: $free GB livres, MODELS=$MODELS precisa de ~$need_gb GB." >&2
    exit 1
  fi

  local sudo=""
  if [ "$(id -u)" -ne 0 ]; then sudo="sudo"; fi
  if command -v apt-get >/dev/null && [ -z "${SKIP_APT:-}" ]; then
    # Sem terminal (notebook em segundo plano) um pacote que pergunte algo travaria tudo.
    export DEBIAN_FRONTEND=noninteractive
    $sudo apt-get update -y || true
    $sudo apt-get install -y git python3 python3-venv python3-pip aria2 ffmpeg \
      || echo "aviso: apt-get falhou, seguindo com o que ja esta instalado" >&2
  fi

  checkout https://github.com/comfyanonymous/ComfyUI.git "$COMFY" "$COMFY_REF"
  link_models
  # Carregador de modelos GGUF (14B quantizado para GPUs de 16 GB).
  checkout https://github.com/city96/ComfyUI-GGUF "$COMFY/custom_nodes/ComfyUI-GGUF" "$GGUF_REF"
  checkout https://github.com/kijai/ComfyUI-WanAnimatePreprocess \
    "$COMFY/custom_nodes/ComfyUI-WanAnimatePreprocess" "$PREPROCESS_REF"
  checkout https://github.com/kijai/ComfyUI-segment-anything-2 \
    "$COMFY/custom_nodes/ComfyUI-segment-anything-2" "$SAM2_REF"
  # Nos do proprio Wan Studio (o recorte da pessoa por pontos do corpo).
  # (Numa imagem montada antes do codigo chegar, quem copia e quem a liga: nuvem/sessao.py.)
  if [ -d "$ROOT/vm/nodes/wan_studio" ]; then
    mkdir -p "$COMFY/custom_nodes/wan_studio"
    cp -r "$ROOT/vm/nodes/wan_studio/." "$COMFY/custom_nodes/wan_studio/"
  fi

  if [ -z "${NO_VENV:-}" ]; then
    if [ ! -d "$VENV" ]; then python3 -m venv --system-site-packages "$VENV"; fi
    "$PY" -m pip install --upgrade pip
  fi
  # O CUDA 13 nao fala com driver anterior ao 580: nesse caso nem vale baixar o PyTorch dele.
  if [ "${TORCH_INDEX:-}" = cu130 ] && command -v nvidia-smi >/dev/null; then
    local driver
    driver="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1 | cut -d. -f1 | tr -dc 0-9 || true)"
    if [ "${driver:-0}" -lt 580 ]; then
      echo "aviso: o driver NVIDIA daqui e o ${driver:-desconhecido} e o CUDA 13 pede o 580 ou mais novo; fica o PyTorch que ja havia" >&2
      TORCH_INDEX=""
    fi
  fi
  # Outra variante de CUDA do mesmo PyTorch, se pedida e ainda nao for a instalada.
  if [ -n "${TORCH_INDEX:-}" ] && ! "$PY" -c "import sys, torch; sys.exit(0 if (torch.version.cuda or '').replace('.', '').startswith('${TORCH_INDEX#cu}') else 1)" 2>/dev/null; then
    local same
    same="$("$PY" -c "import torch; print('torch==' + torch.__version__.split('+')[0])" 2>/dev/null || echo torch)"
    echo "trocando o PyTorch pelo de $TORCH_INDEX ($same)"
    "$PY" -m pip install "$same" torchvision torchaudio --index-url "https://download.pytorch.org/whl/$TORCH_INDEX" \
      || echo "aviso: nao consegui instalar o PyTorch de $TORCH_INDEX; segue o que ja havia" >&2
  fi
  # Reaproveita o PyTorch da imagem quando ele ja enxerga a GPU (ou quando quem chama garante
  # que ele e o certo: KEEP_TORCH, para montar a imagem numa maquina sem GPU).
  if [ -z "${KEEP_TORCH:-}" ] && ! "$PY" -c "import torch, sys; sys.exit(0 if torch.cuda.is_available() else 1)" 2>/dev/null; then
    "$PY" -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
  fi
  "$PY" -m pip install -r "$COMFY/requirements.txt" \
    -r "$COMFY/custom_nodes/ComfyUI-GGUF/requirements.txt" -r "$ROOT/app/requirements.txt"
  # Dependencias dos pre-processadores: so o que faltar, para nao trocar uma versao que a
  # imagem ja traga.
  local missing="" pair
  for pair in cv2:opencv-python-headless onnxruntime:onnxruntime matplotlib:matplotlib timm:timm; do
    if ! "$PY" -c "import ${pair%%:*}" 2>/dev/null; then missing="$missing ${pair##*:}"; fi
  done
  if [ -n "$missing" ]; then
    # shellcheck disable=SC2086
    "$PY" -m pip install $missing
  fi
  # Opcional de velocidade: com o onnxruntime-gpu a deteccao de pose roda na GPU (na CPU o
  # modelo de pose leva 1,3 s por quadro: mais de um minuto por pedaco de video). A versao e
  # fixa porque as mais novas exigem CUDA 13 e o PyTorch daqui traz o 12; se a GPU dele nao
  # carregar, ele mesmo usa a CPU. Ocupa o lugar do onnxruntime comum.
  # (Medido na T4 e deixado de fora: --use-sage-attention nao compila e cai no padrao;
  # --fast fp16_accumulation quebra o Animate em GGUF.)
  if [ -z "${SKIP_SPEEDUPS:-}" ]; then
    # Onde o PyTorch ja vem com CUDA 13 (a imagem do Lightning AI em 12/10/2026), vale a versao
    # atual do onnxruntime-gpu, que e a feita para ele.
    local ort="onnxruntime-gpu==1.22.0"
    if "$PY" -c "import sys, torch; sys.exit(0 if (torch.version.cuda or '').startswith('13') else 1)" 2>/dev/null; then
      ort="onnxruntime-gpu"
    fi
    if [ -n "${ORT_GPU:-}" ]; then ort="onnxruntime-gpu==$ORT_GPU"; fi
    { "$PY" -m pip uninstall -y onnxruntime onnxruntime-gpu \
        && "$PY" -m pip install "$ort"; } \
      || "$PY" -m pip install onnxruntime \
      || echo "aviso: nao consegui instalar o onnxruntime" >&2
  fi

  if [ -z "${SKIP_MODELS:-}" ]; then core_models; fi
}

# Os modelos sem os quais o estudio nao abre.
core_models() {
  fetch "$W21/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors" text_encoders
  case "$MODELS" in
    5b) set_5b ;;
    free) if [ -z "${SO_TROCA:-}" ]; then set_5b; fi ;;  # so a troca: o Animate vem em `extras`
    14b) set_14b_fp8 t2v; set_14b_fp8 i2v ;;
    all) set_5b; set_14b_fp8 t2v; set_14b_fp8 i2v; set_animate fp8 ;;
  esac
}

# Cada extra e independente: se um download falhar, os outros continuam.
extras() {
  if [ "$MODELS" != free ]; then return 0; fi
  local kind need=21
  kind="$(animate_kind)"
  if [ "$kind" = fp8 ]; then need=29; fi
  if have_room "$need" "o Wan 2.2 Animate (troca de pessoa)"; then
    note "Wan 2.2 Animate, troca de pessoa ($(( need - 1 )) GB)"
    set_animate "$kind" || echo "aviso: o download do Animate falhou" >&2
  fi
  if wants_enhancer && have_room 9 "o melhorador de prompt"; then
    note "melhorador de prompt (8 GB)"
    set_llm || echo "aviso: o download do melhorador de prompt falhou" >&2
  fi
  if [ -n "${SKIP_14B:-}${SO_TROCA:-}" ]; then  # a sessao so de troca de pessoa para por aqui
    rm -f "$LOGS/extras.status"
    echo "downloads extras concluidos (sem o 14B)"
    return 0
  fi
  if have_room 19 "o Wan 2.2 14B imagem -> video"; then
    note "Wan 2.2 14B imagem → vídeo (18 GB)"
    set_14b_gguf i2v || echo "aviso: o download do 14B imagem -> video falhou" >&2
  fi
  if have_room 19 "o Wan 2.2 14B texto -> video"; then
    note "Wan 2.2 14B texto → vídeo (18 GB)"
    set_14b_gguf t2v || echo "aviso: o download do 14B texto -> video falhou" >&2
  fi
  rm -f "$LOGS/extras.status"
  echo "downloads extras concluidos"
}

if [ "$PHASE" = models ]; then
  # So os arquivos: a troca de pessoa primeiro (TROCA_PRIMEIRO, o que mais se usa numa placa
  # grande), depois o resto. O ComfyUI ja esta instalado; os modelos vao para MODELS_DIR.
  link_models
  if [ -n "${TROCA_PRIMEIRO:-}" ] && [ "$MODELS" = free ]; then
    fetch "$W21/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors" text_encoders
    set_animate "$(animate_kind)" || echo "aviso: o download do Animate falhou" >&2
    if [ -n "${SO_TROCA:-}" ]; then echo "modelos da troca de pessoa prontos"; exit 0; fi
  fi
  core_models
  extras
  exit 0
fi
if [ "$PHASE" != extras ]; then core; fi
if [ "$PHASE" != core ]; then extras; fi
if [ "$PHASE" = all ] && [ -z "${SKIP_START:-}" ]; then
  bash "$ROOT/vm/start.sh"
fi
