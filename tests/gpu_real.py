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
  python tests/gpu_real.py desligar               desliga a sessao e espera o Kaggle confirmar

`desligar` nao age com job em andamento, e antes guarda em resultados/ os videos prontos que
ninguem buscou. E o passo que falta para subir codigo novo: com a sessao ligada, o Ligar GPU
nao reenvia o notebook.

O endereco e a senha da sessao saem do registro do Kaggle (o quadro que o notebook imprime).
Cada resultado fica em tests/.work/gpu com alguns quadros extraidos: abra-os, porque "terminou
sem erro" nao quer dizer que o video esta certo. Os tempos vao para bateria.json.

A bateria da a vez: enquanto houver job que nao e dela (alguem usando o site), ela espera. Os
jobs dos testes ficam na fila que o site nao enxerga (`hold`), para o site aberto num navegador
nao levar o resultado para a galeria dele antes deste script.
"""
import base64
import json
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
    elif name in ("video_inicio.png", "video_fim.png"):
        index = 0 if name == "video_inicio.png" else 48
        ffmpeg("-i", fixture("video.mp4"), "-vf", f"select=eq(n\\,{index})", "-frames:v", "1", path)
    else:
        raise SystemExit(f"entrada desconhecida: {name}")
    return path


def attachments(files):
    types = {".png": "image/png", ".mp4": "video/mp4"}
    return {field: (name, fixture(name).read_bytes(), types[Path(name).suffix]) for field, name in files.items()}


# ---------------------------------------------------------------- sessao

def session():
    """(url, cabecalhos, registro) da sessao ligada; url None enquanto ela nao anunciou o endereco."""
    text = "\n".join(ligar.log_lines(KAUTH, USER, 12))
    found = re.findall(r"(https://[a-z0-9-]+\.trycloudflare\.com)\s+usu.rio: wan\s+senha: (\S+)", text)
    if not found:
        return None, None, text
    url, password = found[-1]
    token = base64.b64encode(f"wan:{password}".encode()).decode()
    return url, {"Authorization": f"Basic {token}", "User-Agent": "wan-studio"}, text


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
    """Acompanha o job ate o fim e baixa o resultado: (arquivo, job)."""
    began, shown, deadline = time.time(), None, time.time() + minutes * 60
    while time.time() < deadline:
        current = {job["id"]: job for job in jobs_of(url, auth)["jobs"]}.get(job_id)
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
