"""Testes na GPU de verdade (a sessao do Kaggle), pela mesma API que o site usa.

Os outros testes usam um ComfyUI simulado; este e o unico que prova que um video sai. Precisa
da sessao ligada (`python free/ligar.py`) e do ffmpeg no PATH. Gasta a cota de GPU da conta.

  python tests/gpu_real.py estado                 a sessao e os jobs dela
  python tests/gpu_real.py esperar [conjunto]     espera subir (ex.: esperar animate)
  python tests/gpu_real.py bateria [teste ...]    roda os testes em ordem (sem nomes: todos)
  python tests/gpu_real.py juntos teste teste     envia todos de uma vez (um em cada GPU)
  python tests/gpu_real.py reiniciar N [opcao ...]  reinicia o ComfyUI da GPU N com as opcoes
  python tests/gpu_real.py limpar                 remove do servidor os jobs terminados da bateria
  python tests/gpu_real.py melhorar               chama o melhorador de prompt
  python tests/gpu_real.py log [nome] [linhas]    registro da maquina: comfyui, studio ou extras
  python tests/gpu_real.py quadros teste A B      os quadros de A a B do resultado, lado a lado
  python tests/gpu_real.py desligar               desliga a sessao e espera o Kaggle confirmar

`desligar` nao age com job em andamento, e antes guarda em resultados/ os videos prontos que
ninguem buscou. E o passo que falta para subir codigo novo: com a sessao ligada, o Ligar GPU
nao reenvia o notebook.

O endereco e a senha da sessao saem do registro do Kaggle (o quadro que o notebook imprime).
Com WAN_BACKEND=nuvem os testes vao para a sessao do Modal (ligue-a com `python nuvem/ligar.py`):
o endereco vem do atalho de la, e o que e especifico do Kaggle (fila, cota) nao se aplica.
Com WAN_BACKEND=cpu eles vao para a sessao sem GPU do Kaggle (`python free/build.py --cpu` e
`python free/ligar.py --cpu`), que nao gasta cota: os testes "x_" sao os feitos para ela, em
128 px e um passo, e so dizem se o codigo roda no ComfyUI de verdade, nao se o video presta.
Cada resultado fica em tests/.work/gpu com alguns quadros extraidos: abra-os, porque "terminou
sem erro" nao quer dizer que o video esta certo. Os tempos vao para bateria.json.

A bateria da a vez: enquanto houver job que nao e dela (alguem usando o site), ela espera. Os
jobs dos testes ficam na fila que o site nao enxerga (`hold`), para o site aberto num navegador
nao levar o resultado para a galeria dele antes deste script.
"""
import base64
import http.client
import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / ".work" / "gpu"
FIXTURES = OUT / "entradas"
LEDGER = OUT / "bateria.json"
sys.path.insert(0, str(HERE.parent / "free"))
import ligar  # noqa: E402

SAMPLES = "https://raw.githubusercontent.com/Comfy-Org/workflow_templates/main/input/"
BACKEND = os.environ.get("WAN_BACKEND", "kaggle")  # "nuvem": a sessao do Modal; "cpu": a sem GPU do Kaggle
if BACKEND == "cpu":
    ligar.use_cpu_session()
CLOUD = HERE.parent / "nuvem" / "ligar.py"
BUSY = ("preparing", "queued", "running")
CAT = "um gato astronauta flutuando dentro de uma nave espacial, luz suave"

# nome -> parametros do /api/generate e os arquivos que acompanham (campo -> entrada).
TESTS = {
    "t2v_5b": ({"mode": "t2v", "model": "5b", "prompt": CAT, "camera": "dolly_in", "style": "cinematic",
                "resolution": "480p", "aspect": "16:9", "duration": 3, "fast": True, "seed": 7}, {}),
    "i2v_5b": ({"mode": "i2v", "model": "5b", "prompt": "o personagem dança e acena para a câmera",
                "resolution": "480p", "aspect": "1:1", "duration": 3, "fast": True, "seed": 21},
               {"start_image": "pessoa.png"}),
    "img_5b": ({"mode": "img", "model": "5b", "prompt": "retrato de uma raposa vermelha na neve, luz dourada",
                "style": "cinematic", "resolution": "720p", "aspect": "16:9", "seed": 5}, {}),
    "t2v_5b_720p": ({"mode": "t2v", "model": "5b", "camera": "dolly_in", "resolution": "720p", "aspect": "16:9",
                     "prompt": "uma cidade futurista à noite, chuva, letreiros de neon refletidos no asfalto",
                     "duration": 3, "fast": True, "seed": 41}, {}),
    "t2v_5b_20passos": ({"mode": "t2v", "model": "5b", "prompt": CAT, "camera": "dolly_in", "style": "cinematic",
                         "resolution": "480p", "aspect": "16:9", "duration": 3, "fast": False, "seed": 7}, {}),
    "t2v_14b": ({"mode": "t2v", "model": "14b", "prompt": "uma onda enorme quebrando ao pôr do sol, gotas no ar",
                 "camera": "crane_up", "style": "cinematic", "resolution": "480p", "aspect": "16:9",
                 "duration": 3, "fast": True, "seed": 33}, {}),
    "i2v_14b": ({"mode": "i2v", "model": "14b", "prompt": "o personagem corre e dança, vento leve",
                 "camera": "dolly_in", "resolution": "480p", "aspect": "1:1", "duration": 3, "fast": True,
                 "seed": 31}, {"start_image": "pessoa.png"}),
    "flf_14b": ({"mode": "flf", "model": "14b", "prompt": "a mulher gira o corpo e leva a mão à cabeça",
                 "resolution": "480p", "aspect": "1:1", "duration": 3, "fast": True, "seed": 32},
                {"start_image": "video_inicio.png", "end_image": "video_fim.png"}),
    "img_14b": ({"mode": "img", "model": "14b", "prompt": "retrato de uma raposa vermelha na neve, luz dourada",
                 "style": "cinematic", "resolution": "720p", "aspect": "16:9", "seed": 5}, {}),
    # Troca de pessoa. O exemplo oficial tem 9,3 s (149 quadros a 16 fps), sem audio.
    "troca": ({"mode": "swap", "duration": 5, "quality": "boa", "seed": 11},
              {"start_image": "pessoa.png", "video": "video.mp4"}),
    # O video inteiro, com audio: pedacos dos dois lados do meio, nas duas GPUs.
    "troca_longa": ({"mode": "swap", "duration": 10, "quality": "boa", "seed": 12},
                    {"start_image": "pessoa.png", "video": "video_audio.mp4"}),
    "troca_rapida": ({"mode": "swap", "duration": 10, "quality": "rapida", "seed": 12},
                     {"start_image": "pessoa.png", "video": "video_audio.mp4"}),
    # Trinta segundos: 11 pedacos, cinco para cada lado do meio.
    "troca_30s": ({"mode": "swap", "duration": 30, "quality": "rapida", "seed": 12},
                  {"start_image": "pessoa.png", "video": "video_30s.mp4"}),
    # Os 10 s na qualidade boa (12 quadros gerados por segundo, 24 no arquivo).
    "troca_boa": ({"mode": "swap", "duration": 10, "quality": "boa", "seed": 12},
                  {"start_image": "pessoa.png", "video": "video_audio.mp4"}),
    # Video em pe, 10 s, na qualidade rapida.
    "troca_vertical_10s": ({"mode": "swap", "duration": 10, "quality": "rapida", "seed": 14},
                           {"start_image": "pessoa.png", "video": "video_vertical.mp4"}),
    "troca_movimento": ({"mode": "swap", "duration": 3, "quality": "boa", "replace": False, "seed": 13},
                        {"start_image": "pessoa.png", "video": "video.mp4"}),
    # Video em pe com imagem quadrada: a pessoa entra com faixas pretas em cima e embaixo.
    "troca_vertical": ({"mode": "swap", "duration": 3, "quality": "boa", "seed": 14},
                       {"start_image": "pessoa.png", "video": "video_vertical.mp4"}),
    # O mesmo com a imagem recortada na caixa da pessoa (ref=crop): ela ocupa a altura do quadro
    # em vez de ficar pequena entre as faixas. Compare com a "troca_vertical" (o gorro do boneco).
    "troca_vertical_crop": ({"mode": "swap", "duration": 3, "quality": "boa", "seed": 14,
                             "expert": {"ref": "crop"}},
                            {"start_image": "pessoa.png", "video": "video_vertical.mp4"}),
    # A referencia da "r_larga_rapida", com os ajustes de hoje: so muda a caixa e o engorde.
    "r_base_rapida": ({"mode": "swap", "duration": 10, "quality": "rapida", "seed": 12},
                      {"start_image": "pessoa.png", "video": "video_audio.mp4"}),
    # ---- Para a sessao sem GPU (WAN_BACKEND=cpu): o menor pedaco que exercita tudo. Um passo
    # so, 128 px e 17 quadros: mesmo assim a CPU leva minutos em cada um. O que conferir: que
    # termina sem erro e que os quadros tem a cena e uma pessoa no lugar da outra, por mais
    # borrada que saia. A referencia; o recorte da imagem da pessoa; o modelo em INT8 (sem os
    # nucleos de GPU ele e desfeito camada a camada: prova o arquivo e as LoRAs, nao a velocidade);
    # e as duas cadeias num trecho de 4 s em pedacos de 21 quadros (o menor plano em que a
    # segunda cadeia, com ancora, compensa: 3 pedacos numa e 2 na outra).
    "x_base": ({"mode": "swap", "duration": 1.0625, "quality": "rapida", "seed": 11,
                "expert": {"size": [128, 128], "steps": 1, "chunk": 17, "onnx": "cpu"}},
               {"start_image": "pessoa.png", "video": "video.mp4"}),
    "x_crop": ({"mode": "swap", "duration": 1.0625, "quality": "rapida", "seed": 11,
                "expert": {"size": [128, 224], "steps": 1, "chunk": 17, "onnx": "cpu", "ref": "crop"}},
               {"start_image": "pessoa.png", "video": "video_vertical.mp4"}),
    "x_int8": ({"mode": "swap", "duration": 1.0625, "quality": "rapida", "seed": 11,
                "expert": {"size": [128, 128], "steps": 1, "chunk": 17, "onnx": "cpu", "unet": "int8"}},
               {"start_image": "pessoa.png", "video": "video.mp4"}),
    # A janela na pessoa, num quadro deitado (o exemplo e quadrado: a pessoa fica com parte da
    # largura). O registro diz o tamanho da janela ("[wan_studio] janela: ...").
    "x_janela": ({"mode": "swap", "duration": 1.0625, "quality": "rapida", "seed": 11,
                  "expert": {"size": [384, 224], "steps": 1, "chunk": 17, "onnx": "cpu", "window": True}},
                 {"start_image": "pessoa.png", "video": "video_largo.mp4"}),
    # A pessoa inteira, so o recorte (pintado de vermelho, sem gerar), nos 2 s do exemplo em que
    # as trancas da pessoa original pendem soltas e voltavam no video trocado: sem e com o BiRefNet.
    "x_recorte": ({"mode": "swap", "duration": 2, "start": 6.5, "quality": "rapida", "seed": 1,
                   "expert": {"preview": True, "onnx": "cpu"}},
                  {"start_image": "pessoa.png", "video": "video.mp4"}),
    "x_inteira": ({"mode": "swap", "duration": 2, "start": 6.5, "quality": "rapida", "seed": 1,
                   "expert": {"preview": True, "onnx": "cpu", "whole": True}},
                  {"start_image": "pessoa.png", "video": "video.mp4"}),
    # A janela de verdade menor que o quadro: o corte, a pose deslocada e a colagem. (Em 384 x 224 a
    # pessoa do exemplo ocupa quase toda a largura e a janela vira o quadro inteiro, mesmo com pouca
    # folga: 14/10/2026. Num quadro mais largo sobra cenario dos lados.)
    "x_janela2": ({"mode": "swap", "duration": 1.0625, "quality": "rapida", "seed": 11,
                   "expert": {"size": [576, 224], "steps": 1, "chunk": 17, "onnx": "cpu", "window": True}},
                  {"start_image": "pessoa.png", "video": "video_largo.mp4"}),
    # O rosto em detalhe num quadro deitado: a segunda passada na cabeca ampliada (256 px na CPU).
    "x_rosto": ({"mode": "swap", "duration": 1.0625, "quality": "rapida", "seed": 11,
                 "expert": {"size": [384, 224], "steps": 1, "chunk": 17, "onnx": "cpu", "face": True}},
                {"start_image": "pessoa.png", "video": "video_largo.mp4"}),
    # Trocar rosto numa foto: o rosto da imagem de referencia na mulher do exemplo, de frente
    # (o Klein a 512 px e um passo na CPU: so diz se o grafo roda e se a cabeca volta para o lugar).
    "x_foto": ({"mode": "face", "seed": 11, "expert": {"steps": 1, "onnx": "cpu", "size": 512}},
               {"start_image": "pessoa.png", "end_image": "video_rosto.png"}),
    "x_cadeias": ({"mode": "swap", "duration": 4.0625, "quality": "rapida", "seed": 11,
                   "expert": {"size": [128, 128], "steps": 1, "chunk": 21, "onnx": "cpu", "chains": 2}},
                  {"start_image": "pessoa.png", "video": "video.mp4"}),
    # ---- O modelo em INT8 contra o GGUF Q4 de hoje (13/10/2026). O mesmo pedaco de 49 quadros
    # da "m_4passos", um em cada GPU ao mesmo tempo (`juntos i_q4 i_int8`). So faz sentido num
    # notebook gerado com `--env TORCH_INDEX=cu130 --env ANIMATE_INT8=1`: sem o
    # PyTorch de CUDA 13 o ComfyUI desliga os nucleos de INT8 e o modelo fica mais lento que o
    # GGUF. O que olhar: os segundos por passo no registro (`log comfyui` e `log comfyui1`), se
    # o modelo coube ("loaded completely" ou quanto ficou de fora) e os quadros lado a lado.
    "i_q4": ({"mode": "swap", "duration": 3.0625, "quality": "boa", "seed": 11, "expert": {"worker": 0}},
             {"start_image": "pessoa.png", "video": "video.mp4"}),
    "i_int8": ({"mode": "swap", "duration": 3.0625, "quality": "boa", "seed": 11,
                "expert": {"worker": 1, "unet": "int8"}},
               {"start_image": "pessoa.png", "video": "video.mp4"}),
    # O caso que mais pesa: o tamanho da "maxima" (576x1024, 49 quadros), em que o GGUF ja nao
    # cabia inteiro na T4 (154 s por passo no video de 13/10).
    "i_q4_max": ({"mode": "swap", "duration": 3.0625, "quality": "maxima", "seed": 21, "expert": {"worker": 0}},
                 {"start_image": "retrato_vertical.png", "video": "vertical_audio.mp4"}),
    "i_int8_max": ({"mode": "swap", "duration": 3.0625, "quality": "maxima", "seed": 21,
                    "expert": {"worker": 1, "unet": "int8"}},
                   {"start_image": "retrato_vertical.png", "video": "vertical_audio.mp4"}),
    # ---- Tres passos em vez dos quatro para que a LoRA foi destilada: um quarto do tempo a menos,
    # se a pessoa nao perder detalhe. O mesmo pedaco do "i_q4", na outra GPU (`juntos i_q4 p_3passos`).
    "p_3passos": ({"mode": "swap", "duration": 3.0625, "quality": "boa", "seed": 11, "expert": {"worker": 1, "steps": 3}},
                  {"start_image": "pessoa.png", "video": "video.mp4"}),
    # ---- Duas cadeias que avancam, uma em cada GPU (13/10/2026; app/stitch.py `chains`). Os
    # mesmos 10 s em pedacos de 49 quadros (4 pedacos numa cadeia so): a referencia; com a
    # segunda cadeia partindo de quadros do primeiro pedaco (a ancora); e com ela partindo so
    # da imagem. O que olhar: o tempo, e os quadros em volta da emenda entre as cadeias (nos 149
    # quadros do exemplo a segunda entra no quadro 97 com ancora e no 68 sem, e a mistura leva
    # 6), que e onde a pessoa pode mudar de um quadro para o outro:
    #   python tests/gpu_real.py quadros c_duas 92 106     python tests/gpu_real.py quadros c_livre 63 77
    "c_base": ({"mode": "swap", "duration": 10, "quality": "rapida", "seed": 12, "expert": {"chunk": 49, "chains": 1}},
               {"start_image": "pessoa.png", "video": "video_audio.mp4"}),
    "c_duas": ({"mode": "swap", "duration": 10, "quality": "rapida", "seed": 12, "expert": {"chunk": 49, "chains": 2}},
               {"start_image": "pessoa.png", "video": "video_audio.mp4"}),
    "c_livre": ({"mode": "swap", "duration": 10, "quality": "rapida", "seed": 12,
                 "expert": {"chunk": 49, "chains": 2, "anchor": False}},
                {"start_image": "pessoa.png", "video": "video_audio.mp4"}),
    # ---- A janela na pessoa (13/10/2026; `SWAP_WINDOW` em app/workflows.py): o Animate gera so
    # o retangulo em que a pessoa esta e os quadros voltam para o video. Num quadro deitado com a
    # pessoa no meio (`juntos j_base j_janela`, um em cada GPU): 6 s, dois pedacos. O que olhar:
    # no registro, o tamanho da janela e os segundos por passo de cada um; nos quadros, a borda
    # da colagem em volta da pessoa (algo "recortado"?), se a pessoa saiu igual nos dois e a
    # emenda entre os pedacos (`quadros j_janela 70 84`). O "j_vertical" e o caso de uso (video
    # em pe): diz se a janela chega a valer quando a pessoa enche o quadro.
    "j_base": ({"mode": "swap", "duration": 6, "quality": "boa", "seed": 12, "expert": {"worker": 0}},
               {"start_image": "pessoa.png", "video": "video_largo.mp4"}),
    "j_janela": ({"mode": "swap", "duration": 6, "quality": "boa", "seed": 12, "expert": {"worker": 1, "window": True}},
                 {"start_image": "pessoa.png", "video": "video_largo.mp4"}),
    "j_vertical": ({"mode": "swap", "duration": 6, "quality": "boa", "seed": 14, "expert": {"window": True}},
                   {"start_image": "pessoa.png", "video": "vertical_audio.mp4"}),
    # ---- A pessoa inteira (13/10/2026; `SWAP_WHOLE`): o recorte do SAM2 completado pelo do BiRefNet
    # (notebook com `--env PESSOA_INTEIRA=1`). O exemplo inteiro, em que as trancas da pessoa original
    # voltavam do meio para o fim (`juntos w_base w_inteira`, um em cada GPU). O que olhar: os quadros
    # do meio para o fim de cada um (`quadros w_inteira 90 148`), e no registro quanto o recorte cresceu.
    "w_base": ({"mode": "swap", "duration": 9.3, "quality": "boa", "seed": 12, "expert": {"worker": 0}},
               {"start_image": "pessoa.png", "video": "video.mp4"}),
    "w_inteira": ({"mode": "swap", "duration": 9.3, "quality": "boa", "seed": 12, "expert": {"worker": 1, "whole": True}},
                  {"start_image": "pessoa.png", "video": "video.mp4"}),
    # ---- O rosto em detalhe (13/10/2026; `SWAP_FACE`): uma segunda passada so na cabeca, ampliada.
    # No quadro deitado o rosto tem uns 90 px em 832 x 480 (`juntos f_base f_rosto`). O que olhar: se
    # o rosto do f_rosto se parece mais com a foto, se a cabeca casa com o pescoco e o cabelo em
    # volta (borda da colagem), e no registro o tamanho da janela ("[wan_studio] rosto: ...") e o tempo.
    "f_base": ({"mode": "swap", "duration": 3.0625, "quality": "boa", "seed": 12, "expert": {"worker": 0}},
               {"start_image": "pessoa.png", "video": "video_largo.mp4"}),
    "f_rosto": ({"mode": "swap", "duration": 3.0625, "quality": "boa", "seed": 12, "expert": {"worker": 1, "face": True}},
                {"start_image": "pessoa.png", "video": "video_largo.mp4"}),
    # ---- Trocar rosto numa foto (modo "face", FLUX.2 Klein desde 16/10/2026). O rosto da foto
    # (gerada por IA) em exemplos/ na mulher do exemplo oficial, num quadro em que ela esta de
    # frente: a cabeca inteira (o padrao) e so o rosto; e a versao antiga, com o Animate.
    # O que olhar: se o rosto se parece com o da foto, se a expressao e a pose sao as da mulher, se
    # a borda da colagem aparece e se o resto da imagem ficou igual (`juntos r_foto r_foto_rosto`).
    "r_foto": ({"mode": "face", "seed": 21, "expert": {"worker": 0}},
               {"start_image": "retrato_quadrado.png", "end_image": "video_rosto.png"}),
    "r_foto_rosto": ({"mode": "face", "seed": 21, "hair": False, "expert": {"worker": 1}},
                     {"start_image": "retrato_quadrado.png", "end_image": "video_rosto.png"}),
    "r_foto_animate": ({"mode": "face", "seed": 21, "hair": True, "expert": {"worker": 1, "engine": "animate"}},
                       {"start_image": "retrato_quadrado.png", "end_image": "video_rosto.png"}),
    # O caso de uso: a foto de uma pessoa num video em pe de 23 s, na qualidade boa, em duas cadeias.
    "c_humano": ({"mode": "swap", "duration": 23.5, "quality": "boa", "seed": 21, "expert": {"chains": 2}},
                 {"start_image": "retrato_vertical.png", "video": "vertical_30s.mp4"}),

    # So o recorte (a pessoa pintada de vermelho, sem gerar), em cada estrategia: segundos por pedaco.
    **{f"v_{method}": ({"mode": "swap", "duration": 10, "quality": "rapida", "seed": 1,
                        "expert": {"preview": True, "mask": method}},
                       {"start_image": "pessoa.png", "video": "video.mp4"})
       for method in ("caixas", "pontos", "partes", "caixa")},
    "v_caixas_grande": ({"mode": "swap", "duration": 10, "quality": "rapida", "seed": 1,
                         "expert": {"preview": True, "mask": "caixas", "sam": "grande"}},
                        {"start_image": "pessoa.png", "video": "video.mp4"}),
    "v_vertical": ({"mode": "swap", "duration": 10, "quality": "rapida", "seed": 1, "expert": {"preview": True}},
                   {"start_image": "pessoa.png", "video": "video_vertical.mp4"}),
    # O recorte exatamente como a "troca_vertical" o recebe (3 s, tamanho da boa): o que o SAM2
    # acompanha depende do tamanho do pedaco e dos quadros em que recebe os pontos da pose.
    "v_vertical_boa": ({"mode": "swap", "duration": 3, "quality": "boa", "seed": 1, "expert": {"preview": True}},
                       {"start_image": "pessoa.png", "video": "video_vertical.mp4"}),
    # Resultado (12/10/2026, com o metodo "caixas" de entao): no primeiro quadro, com a pessoa de
    # costas, o miolo e a barra da jaqueta ficaram fora do recorte, e a jaqueta da pessoa original
    # ficou no video inteiro ("troca_vertical", "p_humano"). Com "pontos" o miolo entrou. Dai o
    # padrao de hoje: os dois metodos juntos e o recorte fechado. Estes mostram cada parte.
    "v_novo": ({"mode": "swap", "duration": 10, "quality": "rapida", "seed": 1, "expert": {"preview": True}},
               {"start_image": "pessoa.png", "video": "video.mp4"}),
    "v_vertical_aberto": ({"mode": "swap", "duration": 3, "quality": "boa", "seed": 1,
                           "expert": {"preview": True, "seal": False}},
                          {"start_image": "pessoa.png", "video": "video_vertical.mp4"}),
    "v_vertical_caixas": ({"mode": "swap", "duration": 3, "quality": "boa", "seed": 1,
                           "expert": {"preview": True, "mask": "caixas"}},
                          {"start_image": "pessoa.png", "video": "video_vertical.mp4"}),
    # O mesmo com a caixa mais larga em volta da pose, e com o retangulo em volta do recorte.
    "v_vertical_larga": ({"mode": "swap", "duration": 3, "quality": "boa", "seed": 1,
                          "expert": {"preview": True, "box": "larga", "grow": 16}},
                         {"start_image": "pessoa.png", "video": "video_vertical.mp4"}),
    "v_vertical_pontos": ({"mode": "swap", "duration": 3, "quality": "boa", "seed": 1,
                           "expert": {"preview": True, "mask": "pontos"}},
                          {"start_image": "pessoa.png", "video": "video_vertical.mp4"}),
    "v_vertical_49": ({"mode": "swap", "duration": 5, "quality": "boa", "seed": 1,
                       "expert": {"preview": True, "chunk": 49}},
                      {"start_image": "pessoa.png", "video": "video_vertical.mp4"}),

    # Medidas: um pedaco so de 49 quadros (os mesmos do primeiro teste real, que usou 6 passos e
    # levou 80 s por passo), cada uma mudando um ajuste. "worker" fixa a GPU.
    "m_4passos": ({"mode": "swap", "duration": 3.0625, "quality": "boa", "seed": 11, "expert": {"worker": 0}},
                  {"start_image": "pessoa.png", "video": "video.mp4"}),
    "m_6passos": ({"mode": "swap", "duration": 3.0625, "quality": "maxima", "seed": 11, "expert": {"worker": 0}},
                  {"start_image": "pessoa.png", "video": "video.mp4"}),
    "m_3passos": ({"mode": "swap", "duration": 3.0625, "quality": "boa", "seed": 11, "expert": {"worker": 0, "steps": 3}},
                  {"start_image": "pessoa.png", "video": "video.mp4"}),
    "m_menor": ({"mode": "swap", "duration": 3.0625, "quality": "rapida", "seed": 11, "expert": {"worker": 0}},
                {"start_image": "pessoa.png", "video": "video.mp4"}),
    # A deteccao de pose na CPU, para comparar com o padrao (GPU).
    "m_onnx_cpu": ({"mode": "swap", "duration": 3.0625, "quality": "boa", "seed": 11,
                    "expert": {"worker": 0, "onnx": "cpu"}}, {"start_image": "pessoa.png", "video": "video.mp4"}),
    # Com a ampliacao dos quadros: o arquivo sai em 720p.
    "m_hd": ({"mode": "swap", "duration": 3.0625, "quality": "boa", "seed": 11, "hd": True, "expert": {"worker": 1}},
             {"start_image": "pessoa.png", "video": "video.mp4"}),
    # A segunda GPU, para comparar depois de reinicia-la com outras opcoes (comando "reiniciar").
    "m_gpu1": ({"mode": "swap", "duration": 3.0625, "quality": "boa", "seed": 11, "expert": {"worker": 1}},
               {"start_image": "pessoa.png", "video": "video.mp4"}),
    "m_gpu1_menor": ({"mode": "swap", "duration": 3.0625, "quality": "rapida", "seed": 11, "expert": {"worker": 1}},
                     {"start_image": "pessoa.png", "video": "video.mp4"}),
    # Metade dos quadros por segundo (o dobro de video por pedaco), com os intermediarios
    # estimados na emenda.
    "m_8fps": ({"mode": "swap", "duration": 6.125, "quality": "boa", "seed": 11,
                "expert": {"worker": 1, "fps": 8, "out_fps": 16}}, {"start_image": "pessoa.png", "video": "video.mp4"}),
    "m_12fps": ({"mode": "swap", "duration": 4.0834, "quality": "boa", "seed": 11,
                 "expert": {"worker": 1, "fps": 12, "out_fps": 24}}, {"start_image": "pessoa.png", "video": "video.mp4"}),
    # Os 10 s da troca_rapida com os intermediarios feitos pelo ffmpeg, para comparar com o RIFE.
    "m_10s_ffmpeg": ({"mode": "swap", "duration": 10, "quality": "rapida", "seed": 12, "expert": {"smooth": "ffmpeg"}},
                     {"start_image": "pessoa.png", "video": "video_audio.mp4"}),
    # Os 10 s da troca_rapida gerados a 8 fps, nas duas GPUs: no tamanho pequeno e no de 480p.
    "m_10s_8fps_menor": ({"mode": "swap", "duration": 10, "quality": "rapida", "seed": 12,
                          "expert": {"fps": 8, "out_fps": 16}},
                         {"start_image": "pessoa.png", "video": "video_audio.mp4"}),
    "m_10s_8fps": ({"mode": "swap", "duration": 10, "quality": "boa", "seed": 12,
                    "expert": {"fps": 8, "out_fps": 16}}, {"start_image": "pessoa.png", "video": "video_audio.mp4"}),
    # Qualidade: o mesmo pedaco de 49 quadros (boa, 16 fps), mudando uma coisa de cada vez.
    "q_base": ({"mode": "swap", "duration": 3.0625, "quality": "boa", "seed": 11,
                "expert": {"worker": 0, "fps": 16}}, {"start_image": "pessoa.png", "video": "video.mp4"}),
    "q_seedvr": ({"mode": "swap", "duration": 3.0625, "quality": "boa", "seed": 11, "hd": True,
                  "expert": {"worker": 1, "fps": 16}}, {"start_image": "pessoa.png", "video": "video.mp4"}),
    "q_q6": ({"mode": "swap", "duration": 3.0625, "quality": "boa", "seed": 11,
              "expert": {"worker": 0, "fps": 16, "unet": "Q6_K"}}, {"start_image": "pessoa.png", "video": "video.mp4"}),
    "q_768": ({"mode": "swap", "duration": 3.0625, "quality": "boa", "seed": 11,
               "expert": {"worker": 1, "fps": 16, "size": [768, 768]}}, {"start_image": "pessoa.png", "video": "video.mp4"}),
    "q_8passos": ({"mode": "swap", "duration": 3.0625, "quality": "boa", "seed": 11,
                   "expert": {"worker": 0, "fps": 16, "steps": 8}}, {"start_image": "pessoa.png", "video": "video.mp4"}),
    # A alta definicao no menor video que tem tres pedacos (5 s): para reproduzir rapido um defeito.
    "q_hd_curto": ({"mode": "swap", "duration": 5, "quality": "boa", "seed": 12, "hd": True},
                   {"start_image": "pessoa.png", "video": "video_audio.mp4"}),
    # Uma pessoa realista (gerada aqui mesmo) no lugar do boneco do exemplo oficial.
    "q_retrato": ({"mode": "img", "model": "5b", "resolution": "720p", "aspect": "1:1", "seed": 7,
                   "prompt": "foto de meio corpo de uma mulher de 30 anos, cabelo castanho curto, jaqueta jeans azul "
                             "sobre camiseta branca, fundo cinza liso de estudio, luz suave, olhando para a camera"}, {}),
    "q_humano": ({"mode": "swap", "duration": 10, "quality": "boa", "seed": 12},
                 {"start_image": "retrato.png", "video": "video_audio.mp4"}),
    "q_humano_hd": ({"mode": "swap", "duration": 10, "quality": "boa", "seed": 12, "hd": True},
                    {"start_image": "retrato.png", "video": "video_audio.mp4"}),
    # ---- Por que a troca piorou desde a primeira versao (12/10/2026). A primeira gerava todos os
    # quadros (16 fps), com 6 passos, em pedacos de 77 que so avancavam. Cada teste abaixo muda
    # uma coisa, nos mesmos 10 s do exemplo oficial.
    # Como a primeira versao, numa GPU: a referencia.
    "r_primeira": ({"mode": "swap", "duration": 10, "quality": "boa", "seed": 12,
                    "expert": {"worker": 0, "middle": 0, "fps": 16, "steps": 6, "chunk": 77}},
                   {"start_image": "pessoa.png", "video": "video_audio.mp4"}),
    # A qualidade boa de hoje (12 quadros por segundo, 4 passos, pedacos de 49), mas so avancando:
    # contra a "troca_boa", mostra o que o comeco pelo meio e os pedacos ao contrario custam.
    "r_boa_frente": ({"mode": "swap", "duration": 10, "quality": "boa", "seed": 12, "expert": {"worker": 1, "middle": 0}},
                     {"start_image": "pessoa.png", "video": "video_audio.mp4"}),
    # Como a primeira, com 4 passos: o que os 2 passos a mais rendem.
    "r_4passos": ({"mode": "swap", "duration": 10, "quality": "boa", "seed": 12,
                   "expert": {"worker": 1, "middle": 0, "fps": 16, "chunk": 77}},
                  {"start_image": "pessoa.png", "video": "video_audio.mp4"}),
    # Resultado: "r_primeira" e "r_4passos" sairam fieis e iguais entre si (os 2 passos a mais nao
    # rendem nada e dobram o tempo; pedacos de 77 custam 3,1 s por quadro e passo, contra 1,7 dos
    # de 49). A "r_boa_frente" saiu ruim desde o primeiro quadro: cabelo da pessoa original no
    # lugar do gorro. O que sobra testar e o recorte em retangulo, nas duas GPUs, gerando todos
    # os quadros ("r_nova") e gerando 12 por segundo ("r_nova12").
    "r_nova": ({"mode": "swap", "duration": 10, "quality": "boa", "seed": 12, "expert": {"fps": 16, "cover": "caixa"}},
               {"start_image": "pessoa.png", "video": "video_audio.mp4"}),
    "r_nova12": ({"mode": "swap", "duration": 10, "quality": "boa", "seed": 12, "expert": {"cover": "caixa"}},
                 {"start_image": "pessoa.png", "video": "video_audio.mp4"}),
    # A "r_nova" inventou um objeto na cabeca da pessoa (com o retangulo o modelo fica livre
    # demais) a partir do pedaco do meio. Falta saber se o comeco pelo meio, sozinho, atrapalha:
    # todos os quadros, recorte de contorno, nas duas GPUs.
    "r_meio16": ({"mode": "swap", "duration": 10, "quality": "boa", "seed": 12, "expert": {"fps": 16}},
                 {"start_image": "pessoa.png", "video": "video_audio.mp4"}),
    # Resultado: tambem inventou um objeto na cabeca. O defeito vem de comecar por um pedaco curto
    # (21 quadros) no meio da acao. Ultima duvida, para os videos longos: e se o pedaco do meio
    # for inteiro (77 quadros)?
    "r_meio77": ({"mode": "swap", "duration": 10, "quality": "boa", "seed": 12,
                  "expert": {"fps": 16, "chunk": 77, "middle": 77}},
                 {"start_image": "pessoa.png", "video": "video_audio.mp4"}),
    # So o recorte, com a caixa mais larga em volta da pose e o contorno mais engordado: a
    # tentativa de cobrir a tranca que volta no fim (a "v_caixas" usa o padrao).
    "v_larga": ({"mode": "swap", "duration": 10, "quality": "rapida", "seed": 1,
                 "expert": {"preview": True, "box": "larga", "grow": 16}},
                {"start_image": "pessoa.png", "video": "video.mp4"}),
    # E os 10 s gerados com ela, para ver se a tranca some sem o modelo inventar nada.
    "r_larga": ({"mode": "swap", "duration": 10, "quality": "boa", "seed": 12, "expert": {"box": "larga", "grow": 16}},
                {"start_image": "pessoa.png", "video": "video_audio.mp4"}),
    # A mesma tentativa no rascunho, que leva metade do tempo: compara com a "r_rascunho".
    "r_larga_rapida": ({"mode": "swap", "duration": 10, "quality": "rapida", "seed": 12,
                        "expert": {"box": "larga", "grow": 16}},
                       {"start_image": "pessoa.png", "video": "video_audio.mp4"}),
    # Para o Instagram (30 quadros por segundo): gerar 15 por segundo em vez de 16 e calcular os
    # intermediarios da exatos 30, sem quadro repetido nem descartado. Compara com a "r_4passos".
    # Resultado (12/10/2026): pior. O gorro do personagem sumiu desde o primeiro quadro e a manga
    # da pessoa original ficou, como no teste a 12 por segundo. O modelo quer os 16 dele.
    "r_15": ({"mode": "swap", "duration": 10, "quality": "boa", "seed": 12, "expert": {"fps": 15, "out_fps": 30}},
             {"start_image": "pessoa.png", "video": "video_audio.mp4"}),
    # O caso do Instagram: a imagem de uma pessoa, em pe e de meio corpo como o video, num video
    # em pe. Sem e com a alta definicao, com o movimento fluido.
    "p_humano": ({"mode": "swap", "duration": 5, "quality": "boa", "seed": 21, "fluid": True},
                 {"start_image": "retrato_vertical.png", "video": "vertical_audio.mp4"}),
    "p_humano_hd": ({"mode": "swap", "duration": 5, "quality": "boa", "seed": 21, "hd": True, "fluid": True},
                    {"start_image": "retrato_vertical.png", "video": "vertical_audio.mp4"}),
    # O mesmo trecho a partir de 1,5 s, quando a pessoa ja esta de frente e ha cenario a vista: no
    # teste acima, que comeca com ela de costas enchendo o quadro (99% dele dentro do recorte), o
    # modelo inventou uma mesa na borda de baixo.
    "p_humano_frente": ({"mode": "swap", "duration": 3.5, "start": 1.5, "quality": "boa", "seed": 21, "fluid": True},
                        {"start_image": "retrato_vertical.png", "video": "vertical_audio.mp4"}),
    # A mesma pessoa com a imagem quadrada, que entra com faixas pretas em cima e embaixo: o que
    # se perde quando a imagem nao tem o formato do video.
    "p_humano_faixas": ({"mode": "swap", "duration": 5, "quality": "boa", "seed": 21, "fluid": True},
                        {"start_image": "retrato_quadrado.png", "video": "vertical_audio.mp4"}),
    # O rascunho: como a "r_4passos" (so avancando, todos os quadros), gerado menor.
    "r_rascunho": ({"mode": "swap", "duration": 10, "quality": "rapida", "seed": 12,
                    "expert": {"fps": 16, "chunk": 77, "middle": 0}},
                   {"start_image": "pessoa.png", "video": "video_audio.mp4"}),
    # So o recorte, com a mascara em retangulo.
    "v_retangulo": ({"mode": "swap", "duration": 10, "quality": "boa", "seed": 1,
                     "expert": {"preview": True, "cover": "caixa"}},
                    {"start_image": "pessoa.png", "video": "video.mp4"}),
    # Os mesmos 10 s so avancando, numa GPU: a referencia para julgar os pedacos gerados ao contrario.
    "m_so_frente": ({"mode": "swap", "duration": 10, "quality": "rapida", "seed": 12, "expert": {"middle": 0, "worker": 0}},
                    {"start_image": "pessoa.png", "video": "video_audio.mp4"}),
}

KAUTH = USER = None


def stamp():
    return time.strftime("%H:%M:%S")


# ---------------------------------------------------------------- entradas

def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *map(str, args)], check=True)


def fixture(name):
    """Caminho de uma entrada de teste, baixada ou derivada na primeira vez."""
    FIXTURES.mkdir(parents=True, exist_ok=True)
    path = FIXTURES / name
    if path.exists() and path.stat().st_size:
        return path
    video = "video_wan2_2_14B_animate_original_video.mp4"
    if name == "video.mp4":
        urllib.request.urlretrieve(SAMPLES + video, path)
    elif name == "pessoa.png":
        urllib.request.urlretrieve(SAMPLES + "video_wan2_2_14B_animate_reference_image.png", path)
    elif name == "video_audio.mp4":  # o exemplo oficial nao tem som: um tom serve para conferir
        ffmpeg("-i", fixture("video.mp4"), "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=44100",
               "-c:v", "copy", "-c:a", "aac", "-b:a", "96k", "-shortest", path)
    elif name == "video_30s.mp4":  # o exemplo indo e voltando, sem saltos, ate dar 30 s
        ffmpeg("-i", fixture("video_audio.mp4"), "-filter_complex",
               "[0:v]split=4[a][b][c][d];[b]reverse[rb];[d]reverse[rd];[a][rb][c][rd]concat=n=4:v=1[v]",
               "-map", "[v]", "-t", "30", "-c:v", "libx264", "-crf", "14", path)
    elif name == "video_vertical.mp4":  # o miolo 9:16 do exemplo, que e quadrado
        ffmpeg("-i", fixture("video.mp4"), "-vf", "crop=360:640", "-c:v", "libx264", "-crf", "14", path)
    elif name == "vertical_audio.mp4":  # o mesmo miolo, com o som
        ffmpeg("-i", fixture("video_audio.mp4"), "-vf", "crop=360:640", "-c:v", "libx264", "-crf", "14",
               "-c:a", "copy", path)
    elif name == "vertical_30s.mp4":  # o miolo 9:16 do video de 30 s (sem som)
        ffmpeg("-i", fixture("video_30s.mp4"), "-vf", "crop=360:640", "-an", "-c:v", "libx264", "-crf", "14", path)
    elif name == "video_largo.mp4":
        # O exemplo (quadrado) no meio de um quadro 16:9, com as laterais preenchidas por ele mesmo
        # borrado: a pessoa fica com um terco da largura, que e o caso em que a janela mais rende.
        ffmpeg("-i", fixture("video_audio.mp4"), "-filter_complex",
               "[0:v]split[a][b];[a]scale=1136:640,boxblur=24:2[bg];[bg][b]overlay=(W-w)/2:0[v]",
               "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-crf", "14", "-c:a", "copy", path)
    elif name == "retrato_vertical.png":  # a pessoa de exemplos/ (gerada por IA), cortada em 9:16
        ffmpeg("-i", HERE.parent / "exemplos" / "troca-de-pessoa - imagem usada (pessoa gerada por IA).png",
               "-vf", "crop=ih*9/16:ih", path)
    elif name == "retrato_quadrado.png":
        path.write_bytes((HERE.parent / "exemplos"
                          / "troca-de-pessoa - imagem usada (pessoa gerada por IA).png").read_bytes())
    elif name == "retrato.png":  # a imagem que o teste q_retrato gerou nesta bateria
        made = OUT / "q_retrato.png"
        if not made.exists():
            raise SystemExit("rode antes o teste q_retrato (ele gera a pessoa)")
        path.write_bytes(made.read_bytes())
    elif name in ("video_inicio.png", "video_fim.png", "video_rosto.png"):
        # (no quadro 0 a mulher do exemplo esta de costas; no 24, de frente, olhando para cima)
        index = {"video_inicio.png": 0, "video_fim.png": 48, "video_rosto.png": 24}[name]
        ffmpeg("-i", fixture("video.mp4"), "-vf", f"select=eq(n\\,{index})", "-frames:v", "1", path)
    else:
        raise SystemExit(f"entrada desconhecida: {name}")
    return path


def attachments(files):
    types = {".png": "image/png", ".mp4": "video/mp4"}
    return {field: (name, fixture(name).read_bytes(), types[Path(name).suffix]) for field, name in files.items()}


# ---------------------------------------------------------------- sessao

def headers_for(password):
    token = base64.b64encode(f"wan:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}", "User-Agent": "wan-studio"}


def cloud_session():
    """O mesmo que `session`, para a sessao do Modal: quem sabe o endereco e o atalho de la."""
    out = subprocess.run([sys.executable, str(CLOUD), "sessao"], capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    try:
        data = json.loads(out.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None, None, (out.stdout + out.stderr).strip()
    return data["url"], headers_for(data["password"]), ""


def session():
    """(url, cabecalhos, registro) da sessao ligada; url None enquanto ela nao anunciou o endereco."""
    if BACKEND == "nuvem":
        return cloud_session()
    text = "\n".join(ligar.log_lines(KAUTH, USER, 12))
    found = re.findall(r"(https://[a-z0-9-]+\.trycloudflare\.com)\s+usu.rio: wan\s+senha: (\S+)", text)
    if not found:
        return None, None, text
    url, password = found[-1]
    return url, headers_for(password), text


def call(url, auth, path, data=None, content_type=None, method=None, timeout=120):
    headers = dict(auth)
    if content_type:
        headers["Content-Type"] = content_type
    request = urllib.request.Request(url + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def jobs_of(url, auth, held=True):
    """Os jobs dos testes (`held`: a fila que o site nao enxerga, onde eles sao postos) ou os do site."""
    return json.loads(call(url, auth, "/api/jobs" + ("?hold=1" if held else "")))


def multipart(fields, files):
    boundary = "----wan" + secrets.token_hex(8)
    body = bytearray()
    for name, value in fields.items():
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
    for name, (filename, data, mime) in files.items():
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                 f"Content-Type: {mime}\r\n\r\n").encode() + data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def status_line(url, auth):
    status = json.loads(call(url, auth, "/api/status"))
    models = {name: (("ok+turbo" if s["turbo"] else "ok") + (f"+hd:{s['hd']}" if s.get("hd") else "")
                     + ("+rife" if s.get("rife") else "")) if s["ok"] else "-"
              for name, s in status["models"].items()}
    ram = status.get("ram") or {}
    return status, (f"gpu={status['gpu']} comfy={status['comfy']} modelos={models} "
                    f"melhorador={status['enhancer']['state']} aquecendo={status.get('warming', '?')} "
                    f"baixando={status['extras']} "
                    f"ram_livre={ram.get('free', '?')}/{ram.get('total', '?')} GB")


def wait_up(need=None, minutes=30):
    """Espera o estudio responder e, se pedido, um conjunto de modelos (5b, animate, 14b_i2v...)."""
    deadline, last = time.time() + minutes * 60, ""
    while time.time() < deadline:
        url, auth, text = session()
        if url:
            try:
                status, line = status_line(url, auth)
            except (OSError, ValueError) as error:
                status, line = None, f"sessão encontrada, a API ainda não responde ({error})"
            if status and status["comfy"] and (need is None or status["models"].get(need, {}).get("ok")):
                print(stamp(), line, flush=True)
                return url, auth
        elif BACKEND == "nuvem":
            line = "a sessão do Modal ainda não está no ar (python nuvem/ligar.py)"
        else:
            marks = [row for row in text.splitlines()
                     if row.startswith(("baixando ", "Instala", "Iniciando")) or "SystemExit" in row]
            state, failure = ligar.patient_status(KAUTH, USER)
            if state not in (*ligar.ON, ligar.OFFLINE):
                raise SystemExit(f"a sessão parou: {state} {failure}")
            line = "na fila do Kaggle" if state == "QUEUED" else \
                "instalando: " + (marks[-1][:100] if marks else "(início)")
        if line != last:
            print(stamp(), line, flush=True)
            last = line
        time.sleep(20)
    raise SystemExit("tempo esgotado esperando a sessão")


# ---------------------------------------------------------------- jobs

def submit(url, auth, label, params, files):
    body, content_type = multipart({"params": json.dumps({**params, "hold": True})}, attachments(files))
    try:
        reply = json.loads(call(url, auth, "/api/generate", data=body, content_type=content_type,
                                method="POST", timeout=300))
    except urllib.error.HTTPError as error:
        raise SystemExit(f"{label}: recusado ({error.code}) {error.read().decode()[:300]}")
    job = reply["jobs"][0]
    print(stamp(), f"{label}: aceito, job {job['id']}",
          {key: job[key] for key in ("mode", "model", "quality", "steps", "width", "height", "length", "duration",
                                     "chunks") if key in job}, flush=True)
    return job


def follow(url, auth, label, job_id, minutes=90):
    """Acompanha o job ate o fim e baixa o resultado: (arquivo, job). Uma queda de conexao
    passageira (o tunel as vezes derruba uma) nao interrompe: o job segue na GPU."""
    began, shown, deadline, misses = time.time(), None, time.time() + minutes * 60, 0
    while time.time() < deadline:
        try:
            listed = jobs_of(url, auth)["jobs"]
        except (OSError, ValueError, http.client.HTTPException) as error:
            misses += 1
            if misses > 30:
                raise SystemExit(f"{label}: sem resposta da sessao ({error})") from None
            print(stamp(), f"{label}: sem resposta ({error.__class__.__name__}), tentando de novo", flush=True)
            time.sleep(20)
            continue
        misses = 0
        current = {job["id"]: job for job in listed}.get(job_id)
        if current is None:
            raise SystemExit(f"{label}: o job sumiu do servidor (o site aberto baixou o resultado antes?)")
        mark = (current["status"], current.get("stage"), round(current["progress"], 2))
        if mark != shown:
            shown = mark
            print(stamp(), f"{label}: {current['status']} etapa={current.get('stage')!r} "
                           f"{current['progress']:.0%} ({(time.time() - began) / 60:.1f} min)", flush=True)
        if current["status"] == "done":
            target = OUT / f"{label}{Path(current['url']).suffix}"
            target.write_bytes(call(url, auth, current["url"], timeout=600))
            print(stamp(), f"{label}: PRONTO -> {target.name} ({target.stat().st_size} bytes)", flush=True)
            print("   prompt enviado ao modelo:", current.get("final_prompt"), flush=True)
            return target, current
        if current["status"] == "error":
            raise SystemExit(f"{label}: ERRO: {current.get('error')}")
        time.sleep(1 if current["progress"] > 0.75 else 4)  # perto do fim, para baixar antes do site
    raise SystemExit(f"{label}: tempo esgotado")


def probe(path):
    keys = ("codec_type", "codec_name", "width", "height", "r_frame_rate", "nb_frames", "duration")
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=" + ",".join(keys), "-of", "json",
                          str(path)], capture_output=True, text=True).stdout
    return [{key: stream[key] for key in keys if stream.get(key) is not None}
            for stream in json.loads(out or "{}").get("streams", [])]


def contact_sheet(path, label, count):
    """Cinco quadros lado a lado (inicio, 1/4, meio, 3/4, fim), para conferir de relance."""
    last = count - 1
    picks = "+".join(f"eq(n\\,{index})" for index in sorted({0, last // 4, last // 2, 3 * last // 4, last}))
    ffmpeg("-i", path, "-vf", f"select='{picks}',scale=-2:320,tile=5x1", "-frames:v", "1", OUT / f"{label}_quadros.png")


def frame_strip(label, first, last):
    """Os quadros de `first` a `last` de um resultado ja baixado, lado a lado: para olhar uma
    emenda quadro a quadro."""
    path = OUT / f"{label}.mp4"
    if not path.exists():
        raise SystemExit(f"não achei {path}: rode o teste antes")
    target = OUT / f"{label}_quadros_{first}_{last}.png"
    ffmpeg("-i", path, "-vf", f"select='between(n\\,{first}\\,{last})',scale=-2:480,tile={last - first + 1}x1",
           "-frames:v", "1", target)
    print(target)


def ledger():
    return json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else {}


def record(label, **entry):
    data = ledger()
    data[label] = {**data.get(label, {}), **entry}
    LEDGER.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def wait_turn(url, auth, mine):
    told = False
    while True:
        foreign = [job for held in (True, False) for job in jobs_of(url, auth, held)["jobs"]
                   if job["id"] not in mine and job["status"] in BUSY]
        if not foreign:
            return
        if not told:
            print(stamp(), f"há {len(foreign)} job(s) de outra origem em andamento; espero terminarem", flush=True)
            told = True
        time.sleep(15)


def battery(url, auth, names, together=False):
    """Um teste por vez, ou todos enviados de uma vez (`together`: com tarefas fixadas em GPUs
    diferentes, rodam ao mesmo tempo). Guarda o id de cada job, para retomar se este script
    for interrompido."""
    unknown = [name for name in names if name not in TESTS]
    if unknown:
        raise SystemExit(f"teste desconhecido: {unknown}. Existem: {', '.join(TESTS)}")
    mine = {entry["id"] for entry in ledger().values() if entry.get("id")}
    failed, sent = [], {}

    def send(label):
        known = {job["id"] for job in jobs_of(url, auth)["jobs"]}
        job_id = ledger().get(label, {}).get("id")
        if job_id not in known:  # ainda nao enviado (ou de uma sessao que ja acabou)
            job_id = submit(url, auth, label, *TESTS[label])["id"]
            mine.add(job_id)
            record(label, id=job_id, status="enviado")
        return job_id

    if together:
        wait_turn(url, auth, mine)
        for label in names:
            try:
                sent[label] = send(label)
            except SystemExit as stop:
                print(stamp(), stop, flush=True)
    for label in names:
        try:
            if not together:
                wait_turn(url, auth, mine)
                sent[label] = send(label)
            if label not in sent:
                raise SystemExit(f"{label}: não foi aceito")
            target, job = follow(url, auth, label, sent[label])
        except SystemExit as stop:
            print(stamp(), stop, flush=True)
            record(label, status="erro", error=str(stop))
            failed.append(label)
            continue
        streams = probe(target)
        print("   arquivo:", streams, flush=True)
        if job["kind"] == "video":
            contact_sheet(target, label, int(streams[0]["nb_frames"]))
        record(label, status="pronto", size=[job["width"], job["height"], job["length"]], streams=streams,
               minutes=round((job["finished"] - job.get("started", job["created"])) / 60, 1))
    print(f"\nFIM DA BATERIA: {len(names) - len(failed)} de {len(names)} geraram arquivo"
          + (f"; falharam: {', '.join(failed)}" if failed else "") + f". Confira os quadros em {OUT}", flush=True)
    return 1 if failed else 0


def enhance(url, auth):
    for mode, camera, text in (("t2v", "none", "um cachorro correndo na praia"),
                               ("t2v", "dolly_in", "mulher andando numa rua de tóquio à noite"),
                               ("i2v", "none", "ela sorri e olha para a câmera")):
        began = time.time()
        body = json.dumps({"prompt": text, "mode": mode, "camera": camera}).encode()
        try:
            reply = json.loads(call(url, auth, "/api/enhance", data=body, content_type="application/json",
                                    method="POST"))
        except urllib.error.HTTPError as error:
            reply = {"erro": error.code, "corpo": error.read().decode()[:300]}
        print(f"[{mode}, câmera={camera}] {text!r} ({time.time() - began:.1f} s)\n   -> {reply}", flush=True)


def restart(url, auth, worker, args):
    """Reinicia o ComfyUI de uma GPU com outras opcoes e espera ele voltar."""
    body = json.dumps({"args": args}).encode()
    try:
        call(url, auth, f"/api/worker/{worker}/restart", data=body, content_type="application/json", method="POST")
    except urllib.error.HTTPError as error:
        raise SystemExit(f"recusado ({error.code}): {error.read().decode()[:200]}")
    print(stamp(), f"pedido o reinício da GPU {worker} com {args or '(sem opções)'}", flush=True)
    deadline = time.time() + 600
    while time.time() < deadline:
        time.sleep(10)
        if json.loads(call(url, auth, "/api/status")).get("restart") is None:
            print(stamp(), "reiniciado", flush=True)
            return
    raise SystemExit("o reinício não foi confirmado em 10 minutos")


def shut_down(url, auth):
    jobs = [job for held in (True, False) for job in jobs_of(url, auth, held)["jobs"]]
    busy = [job for job in jobs if job["status"] in BUSY]
    if busy:
        raise SystemExit(f"há {len(busy)} job(s) em andamento; não desligo")
    saved = HERE.parent / "resultados"
    for job in jobs:
        if job["status"] == "done" and job.get("url"):
            saved.mkdir(exist_ok=True)
            target = saved / f"{job['mode']}-{job['width']}x{job['height']}-{job['id']}{Path(job['url']).suffix}"
            target.write_bytes(call(url, auth, job["url"], timeout=900))
            print("guardado:", target, target.stat().st_size, "bytes", flush=True)
    call(url, auth, "/api/shutdown", data=b"{}", content_type="application/json", method="POST")
    print(stamp(), "desligamento pedido", flush=True)
    if BACKEND == "nuvem":
        for _ in range(30):
            time.sleep(5)
            if cloud_session()[0] is None:
                print(stamp(), "sessão encerrada", flush=True)
                return
        raise SystemExit("a sessão não encerrou em 2 minutos e meio")
    for _ in range(60):
        time.sleep(10)
        state, _failure = ligar.patient_status(KAUTH, USER)
        if state not in (*ligar.ON, ligar.OFFLINE):
            print(stamp(), "sessão encerrada:", state, flush=True)
            return
    raise SystemExit("a sessão não encerrou em 10 minutos")


def show_log(url, auth, name, lines):
    text = call(url, auth, f"/api/log?name={name}").decode("utf-8", errors="replace")
    (OUT / f"registro_{name}.txt").write_text(text, encoding="utf-8")
    # As barras de progresso reescrevem a linha com \r: fica o estado final de cada uma.
    rows = [row.split("\r")[-1] for row in text.splitlines() if row.strip()]
    print("\n".join(rows[-lines:]))


def main():
    global KAUTH, USER
    command = sys.argv[1] if len(sys.argv) > 1 else "estado"
    OUT.mkdir(parents=True, exist_ok=True)
    if command == "quadros":  # so le o que ja foi baixado: nao precisa da sessao
        frame_strip(sys.argv[2], int(sys.argv[3]), int(sys.argv[4]))
        return 0
    if BACKEND != "nuvem":
        KAUTH, USER = ligar.credentials(browser=False)
    if command == "esperar":
        wait_up(sys.argv[2] if len(sys.argv) > 2 else None)
        return 0
    url, auth, _ = session()
    if not url:
        raise SystemExit("a sessão não está no ar (python free/ligar.py status)")
    if command == "estado":
        print(status_line(url, auth)[1])
        for held in (True, False):
            data = jobs_of(url, auth, held)
            for job in data["jobs"]:
                elapsed = (job.get("finished") or data["now"]) - (job.get("started") or job["created"])
                print("  ", "teste" if held else "site ", job["id"], job["mode"], job["status"], f"{job['progress']:.0%}",
                      repr(job.get("stage")), f"{elapsed / 60:.1f} min", job.get("error") or "")
    elif command == "bateria":
        return battery(url, auth, sys.argv[2:] or list(TESTS))
    elif command == "juntos":
        return battery(url, auth, sys.argv[2:], together=True)
    elif command == "reiniciar":
        restart(url, auth, int(sys.argv[2]), sys.argv[3:])
    elif command == "limpar":  # tira do servidor os jobs ja terminados desta bateria, para repeti-los
        mine = {entry.get("id") for entry in ledger().values()}
        for job in jobs_of(url, auth)["jobs"]:
            if job["id"] in mine and job["status"] not in BUSY:
                call(url, auth, f"/api/jobs/{job['id']}", method="DELETE")
                print("removido:", job["id"], job["mode"], job["status"])
    elif command == "melhorar":
        enhance(url, auth)
    elif command == "desligar":
        shut_down(url, auth)
    elif command == "log":
        show_log(url, auth, sys.argv[2] if len(sys.argv) > 2 else "comfyui", int(sys.argv[3]) if len(sys.argv) > 3 else 80)
    else:
        raise SystemExit(__doc__)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ligar.KaggleError as error:
        sys.exit(f"Kaggle: {error}")
    except urllib.error.URLError as error:  # o endereco do registro e de uma sessao ja encerrada
        sys.exit(f"a sessão não respondeu ({error}); veja python free/ligar.py status")
