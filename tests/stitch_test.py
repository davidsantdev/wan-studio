"""Testa app/stitch.py: o plano de pedacos e a emenda, com o ffmpeg de verdade.

  python tests/stitch_test.py

Os "pedacos gerados" sao feitos aqui: quadros de uma cor que muda a cada quadro do video, de
modo que da para conferir, no arquivo emendado, que cada quadro veio do lugar certo.
"""
import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORK = HERE / ".work" / "stitch"
sys.path.insert(0, str(HERE.parent / "app"))
import stitch  # noqa: E402

failures = []


def check(label, condition, detail=""):
    print(("ok    " if condition else "FALHA ") + label + (f"  [{detail}]" if detail else ""))
    if not condition:
        failures.append(label)


def plan_ok(total, chunk, overlap, seed):
    """Confere as regras de um plano; devolve "" ou o que esta errado."""
    pieces = stitch.plan(total, chunk, overlap, seed)
    if total < 17:
        return "" if pieces == [] else "deveria ser vazio"
    if not pieces:
        return "vazio"
    if sum(piece["after"] is None for piece in pieces) != 1:
        return "precisa de exatamente uma semente"
    for index, piece in enumerate(pieces):
        if piece["length"] % 4 != 1 or not 17 <= piece["length"] <= chunk + stitch.STRETCH or piece["start"] < 0:
            return f"pedaco {index} com tamanho invalido"
        if piece["after"] is not None:
            if abs(piece["after"] - index) != 1 or piece["reverse"] != (piece["after"] > index):
                return f"pedaco {index} continua do vizinho errado"
            source, first = stitch.copied(pieces, index, overlap)
            if not 0 <= first <= pieces[source]["length"] - overlap:
                return f"pedaco {index} copia quadros que o vizinho nao tem"
    end = pieces[-1]["start"] + pieces[-1]["length"]
    if pieces[0]["start"] != 0 or (end != total if len(pieces) > 1 else total - end > 3):
        return "nao cobre o video"
    spans = stitch.bounds(pieces, overlap)
    if spans[0][0] != 0 or spans[-1][1] != end or any(a[1] != b[0] for a, b in zip(spans, spans[1:])):
        return "trechos nao encostam"
    return ""


def chains_ok(total, chunk, overlap, anchor):
    """Confere as regras de um plano em duas cadeias; devolve "" ou o que esta errado."""
    single = stitch.plan(total, chunk, overlap)
    pieces = stitch.chains(total, chunk, overlap, anchor=anchor)
    if pieces == single:  # video curto, ou sem ganho que compense: vale o plano de uma cadeia
        return ""
    seeds = [index for index, piece in enumerate(pieces) if piece["after"] is None]
    if len(seeds) != 2 or seeds[0] != 0:
        return "precisa de duas cadeias, a primeira no comeco"
    second = pieces[seeds[1]]
    lead = overlap + stitch.SETTLE if anchor else 0
    if second.get("blend") != stitch.BLEND or second.get("skip", -1) < lead or second.get("skip") > lead + 3:
        return "comeco da segunda cadeia sem a mistura ou sem os quadros de acomodacao"
    if second.get("anchor") != (0 if anchor else None):
        return "ancora errada"
    if anchor:
        source, first = stitch.anchored(pieces, seeds[1], overlap)
        if source != 0 or not 0 <= first <= pieces[0]["length"] - overlap:
            return "a ancora copia quadros que o primeiro pedaco nao tem"
    for index, piece in enumerate(pieces):
        if piece["length"] % 4 != 1 or not 17 <= piece["length"] <= chunk + stitch.STRETCH or piece["start"] < 0:
            return f"pedaco {index} com tamanho invalido"
        if piece["reverse"] or (piece["after"] is not None and piece["after"] != index - 1):
            return f"pedaco {index} nao avanca a partir do anterior"
    if pieces[-1]["start"] + pieces[-1]["length"] != total:
        return "nao cobre o video ate o fim"
    spans = stitch.bounds(pieces, overlap)
    for index, (a, b) in enumerate(zip(spans, spans[1:])):
        shared = a[1] - b[0]
        if shared != (stitch.BLEND if index + 1 == seeds[1] else 0):
            return f"emenda {index} com {shared} quadros em comum"
    if spans[0][0] != 0 or spans[-1][1] != total:
        return "trechos nao cobrem o video"
    if chain_time(pieces, anchor) > stitch.CHAIN_GAIN * stitch._cost(single):
        return "duas cadeias sem ganho de tempo sobre uma"
    return ""


def chain_time(pieces, anchor):
    """Quanto as duas cadeias levam, na mesma conta do plano: a segunda, com ancora, espera o
    primeiro pedaco da primeira."""
    split = next((index for index, piece in enumerate(pieces) if piece.get("blend")), len(pieces))
    wait = stitch._cost(pieces[:1]) if anchor and split < len(pieces) else 0
    return max(stitch._cost(pieces[:split]), wait + stitch._cost(pieces[split:]))


def read_blues(path):
    """O azul medio de cada quadro: em `make_fine` ele diz de que pedaco o quadro veio."""
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(path), "-vf", "scale=1:1:flags=area",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return [raw[at + 2] for at in range(0, len(raw) - 2, 3)]


def frame_color(number):
    """Uma cor por quadro do video, facil de reconhecer depois (so o vermelho varia)."""
    return (20 + number * 2 % 200, 90, 160)


def make_chunk(folder, piece, shift=0, size="64x48"):
    """Os quadros de um pedaco, ja na ordem do video. `shift` clareia o pedaco inteiro, como a
    diferenca de cor entre pedacos gerados."""
    folder.mkdir(parents=True)
    for local in range(piece["length"]):
        red, green, blue = (min(255, value + shift) for value in frame_color(piece["start"] + local))
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        f"color=c=0x{red:02x}{green:02x}{blue:02x}:s={size}:d=1", "-frames:v", "1",
                        str(folder / f"{local:05d}.png")], check=True)


def make_fine(folder, piece, factor, marker, shift=0):
    """Os quadros de um pedaco que ja traz intermediarios: `factor` por quadro gerado. A
    posicao de cada um no video (em quadros finos) vai no vermelho e no verde, em degraus de
    16, e `marker`, o numero do pedaco, no azul: da para ler de onde cada quadro veio. Com
    `shift`, em vez disso, o vermelho cresce 1 por quadro fino e o pedaco inteiro e clareado."""
    folder.mkdir(parents=True)
    first = factor * piece["start"]
    if shift is None:
        colors = f"r='16+16*mod(N+{first},15)':g='16+16*floor((N+{first})/15)':b={40 + 40 * marker}"
    else:
        colors = f"r='min(255,20+N+{first}+{shift})':g={90 + shift}:b={160 + shift}"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"nullsrc=s=64x48:r=16,format=rgb24,geq={colors}", "-frames:v",
                    str((piece["length"] - 1) * factor + 1), "-start_number", "0", str(folder / "%05d.png")], check=True)


def read_marks(path):
    """De cada quadro do video: (posicao em quadros finos, numero do pedaco), como `make_fine` gravou."""
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(path), "-vf", "scale=1:1:flags=area",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return [(round((raw[at] - 16) / 16) + 15 * round((raw[at + 1] - 16) / 16), round((raw[at + 2] - 40) / 40))
            for at in range(0, len(raw) - 2, 3)]


def read_reds(path, count):
    """O vermelho medio de cada quadro do video emendado."""
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(path), "-vf", "scale=1:1:flags=area",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return [raw[index * 3] for index in range(min(count, len(raw) // 3))], len(raw) // 3


async def main():
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)

    # ---- plano
    problems = []
    for seed in (0, 17, 21):
        for total in list(range(1, 400)) + [481, 961, 1500]:
            problem = plan_ok(total, 49, 5, seed)
            if problem:
                problems.append(f"total={total} seed={seed}: {problem}")
    check("P1 plano valido para todo tamanho de video, com e sem semente no meio", not problems,
          "; ".join(problems[:3]))
    middle = stitch.plan(160, 49, 5, 21)
    check("P2 com semente: pedaco curto no meio, metade recua e metade avanca",
          [p["length"] for p in middle if p["after"] is None] == [21]
          and sum(p["reverse"] for p in middle) == sum(not p["reverse"] and p["after"] is not None for p in middle),
          str([(p["start"], p["length"], p["after"], p["reverse"]) for p in middle]))
    forward = stitch.plan(160, 49, 5)
    check("P3 sem semente: comeca no primeiro quadro e todos avancam",
          forward[0]["after"] is None and forward[0]["start"] == 0 and not any(p["reverse"] for p in forward)
          and [p["after"] for p in forward[1:]] == list(range(len(forward) - 1)))
    check("P4 video menor que um pedaco: um pedaco so", stitch.plan(48, 49, 5, 21) == [
        {"start": 0, "length": 45, "after": None, "reverse": False}])
    stretched = [piece["length"] for piece in stitch.plan(120, 49, 5, 21)]
    wasted = max((sum(piece["length"] for piece in stitch.plan(total, 49, 5, seed)) - total) / total
                 for seed in (0, 21) for total in range(100, 1000))
    check("P5 nao sobra pedaco minimo no fim: 120 quadros sao 3 pedacos, e nunca se gera mais que 30% alem do video",
          stretched == [57, 21, 57] and wasted <= 0.30, f"{stretched}; pior sobra {wasted:.0%}")

    # ---- emenda: cada quadro vem do pedaco certo, e a cor de cada pedaco e trazida a do vizinho
    total = 120
    pieces = stitch.plan(total, 49, 5, 21)
    shifts = [12 * abs(index - next(i for i, p in enumerate(pieces) if p["after"] is None))
              for index in range(len(pieces))]
    chunks = []
    for index, (piece, shift) in enumerate(zip(pieces, shifts)):
        make_chunk(WORK / f"{index:03d}", piece, shift)
        chunks.append({**piece, "pattern": str(WORK / f"{index:03d}" / "%05d.png")})
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
                    "-f", "lavfi", "-i", "color=c=black:s=64x48:r=16:d=10", "-shortest", "-c:v", "libx264",
                    "-c:a", "aac", str(WORK / "audio.mp4")], check=True)
    # O video de entrada: os mesmos quadros, sem a diferenca de cor.
    make_chunk(WORK / "entrada", {"start": 0, "length": total})
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-framerate", "16", "-i", str(WORK / "entrada" / "%05d.png"),
                    "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv444p", str(WORK / "entrada.mp4")], check=True)
    target = WORK / "emendado.mp4"
    fixes = await stitch.stitch(chunks, target, 16, 5, (128, 96), audio=WORK / "audio.mp4",
                                original=WORK / "entrada.mp4")
    reds, count = read_reds(target, total)
    expected = [frame_color(number)[0] for number in range(total)]
    worst = max(abs(a - b) for a, b in zip(reds, expected))
    check("E1 emenda tem todos os quadros, na ordem", count == total, f"{count} quadros")
    check("E2 a diferenca de cor entre pedacos e corrigida a partir da semente", worst <= 6,
          f"maior erro {worst}; deslocamentos aplicados {[round(fix[0][1], 1) for fix in fixes]}")
    blind = await stitch.stitch(chunks, WORK / "cego.mp4", 16, 5, (128, 96))
    reds_blind, _ = read_reds(WORK / "cego.mp4", total)
    check("E2b sem o video de entrada a correcao e so aproximada (o movimento conta como cor)",
          worst < max(abs(a - b) for a, b in zip(reds_blind, expected)) <= 30,
          f"maior erro {max(abs(a - b) for a, b in zip(reds_blind, expected))}; {len(blind)} pedacos")
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,width,height,r_frame_rate",
                            "-of", "csv=p=0", str(target)], capture_output=True, text=True).stdout.split()
    check("E3 saida H.264 no tamanho pedido, 16 fps, com o audio", "h264,128,96,16/1" in probe
          and any(line.startswith("aac") for line in probe), " | ".join(probe))

    # ---- corte de cena no video original: a diferenca ali e conteudo, nao se corrige
    scene = [{"start": 0, "length": 49, "after": None, "reverse": False},
             {"start": 44, "length": 49, "after": 0, "reverse": False}]
    for index, shift in enumerate((0, 20)):
        make_chunk(WORK / f"cena{index}", scene[index], shift)
        scene[index]["pattern"] = str(WORK / f"cena{index}" / "%05d.png")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=0x303030:s=64x48:r=16:d=3.0625",
                    "-f", "lavfi", "-i", "color=c=0xd0d0d0:s=64x48:r=16:d=3", "-filter_complex", "[0][1]concat=n=2",
                    "-c:v", "libx264", "-qp", "0", str(WORK / "original.mp4")], check=True)
    fixed = await stitch.stitch(scene, WORK / "cena_sem.mp4", 16, 5, (64, 48))
    kept = await stitch.stitch(scene, WORK / "cena_com.mp4", 16, 5, (64, 48), original=WORK / "original.mp4")
    check("E4 sem corte de cena a cor e corrigida; com corte no original, fica como veio",
          fixed[1] != [stitch.NEUTRAL] * 3 and kept[1] == [stitch.NEUTRAL] * 3,
          f"sem: {[round(o, 1) for _, o in fixed[1]]} com: {[round(o, 1) for _, o in kept[1]]}")

    # ---- pedaco de tras para a frente e os quadros que o vizinho recebe para continuar
    await stitch.cut(WORK / "original.mp4", WORK / "volta.mp4", 44, 9, reverse=True)
    values, count = read_reds(WORK / "volta.mp4", 9)
    check("C1 corte ao contrario: 9 quadros, do ultimo para o primeiro", count == 9 and values[0] > 150 > values[-1],
          f"{count} quadros, de {values[0]} a {values[-1]}")
    source, first = stitch.copied(pieces, 0, 5)
    await stitch.clip(chunks[source]["pattern"], first, 5, WORK / "continua.mp4", 16, reverse=True)
    values, count = read_reds(WORK / "continua.mp4", 5)
    last_shared = pieces[0]["start"] + pieces[0]["length"] - 1
    want = min(255, frame_color(last_shared)[0] + shifts[source])
    check("C2 o pedaco que recua recebe os 5 quadros do vizinho, ja invertidos",
          count == 5 and abs(values[0] - want) <= 6, f"{count} quadros, primeiro {values[0]} (esperado {want})")

    # ---- pedacos que ja trazem quadros intermediarios (interpolacao em cada pedaco)
    factor, total = 2, 100
    pieces = stitch.plan(total, 49, 5, 21)
    fine = []
    for index, piece in enumerate(pieces):
        make_fine(WORK / f"fino{index}", piece, factor, index, None)
        fine.append({**piece, "pattern": str(WORK / f"fino{index}" / "%05d.png")})
    real_fixes = stitch.color_fixes

    async def no_fixes(chunks, *_args):  # a cor aqui e a marca de cada quadro: nao se corrige
        return [[stitch.NEUTRAL] * 3] * len(chunks)
    stitch.color_fixes = no_fixes
    try:
        await stitch.stitch(fine, WORK / "fino.mp4", 8, 5, (64, 48), factor=factor)
    finally:
        stitch.color_fixes = real_fixes
    marks = read_marks(WORK / "fino.mp4")
    wrong = [number for number, (position, _) in enumerate(marks) if position != number]
    check("F1 com intermediarios: todos os quadros finos, cada um na sua posicao",
          len(marks) == factor * (total - 1) + 1 and not wrong, f"{len(marks)} quadros; fora do lugar: {wrong[:5]}")
    orphans = [number for number, (_, owner) in enumerate(marks)
               if not 0 <= owner < len(pieces) or not pieces[owner]["start"] <= number // factor
               or -(-number // factor) > pieces[owner]["start"] + pieces[owner]["length"] - 1]
    seams = sum(a[1] != b[1] for a, b in zip(marks, marks[1:]))
    check("F2 o intermediario de cada emenda vem de um pedaco que gerou os dois quadros vizinhos",
          not orphans and seams == len(pieces) - 1, f"{seams} emendas; sem os dois vizinhos: {orphans[:5]}")
    rate = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=r_frame_rate",
                           "-of", "csv=p=0", str(WORK / "fino.mp4")], capture_output=True, text=True).stdout.strip()
    check("F3 o video sai no fps dos quadros finos (8 gerados por segundo, 16 no arquivo)", rate == "16/1", rate)
    source, first = stitch.copied(pieces, 0, 5)
    await stitch.clip(fine[source]["pattern"], first, 5, WORK / "continua_fino.mp4", 8, reverse=True, factor=factor)
    got = [position for position, _ in read_marks(WORK / "continua_fino.mp4")]
    last_shared = pieces[0]["start"] + pieces[0]["length"] - 1
    check("F4 quem continua recebe so quadros gerados do vizinho, sem os intermediarios",
          got == [factor * (last_shared - back) for back in range(5)], str(got))
    shifted = []
    seed = next(index for index, piece in enumerate(pieces) if piece["after"] is None)
    for index, piece in enumerate(pieces):
        make_fine(WORK / f"cor{index}", piece, factor, index, 12 * abs(index - seed))
        shifted.append({**piece, "pattern": str(WORK / f"cor{index}" / "%05d.png")})
    make_fine(WORK / "cor_entrada", {"start": 0, "length": total}, factor, 0, 0)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-framerate", "16", "-i", str(WORK / "cor_entrada" / "%05d.png"),
                    "-vf", "select='not(mod(n\\,2))',setpts=N/(8*TB)", "-r", "8", "-c:v", "libx264", "-qp", "0",
                    "-pix_fmt", "yuv444p", str(WORK / "cor_entrada.mp4")], check=True)
    await stitch.stitch(shifted, WORK / "cor.mp4", 8, 5, (64, 48), original=WORK / "cor_entrada.mp4", factor=factor)
    reds, count = read_reds(WORK / "cor.mp4", factor * total)
    worst = max(abs(red - (20 + number)) for number, red in enumerate(reds))
    check("F5 com intermediarios a cor entre pedacos tambem e corrigida", count == factor * (total - 1) + 1 and worst <= 6,
          f"{count} quadros, maior erro {worst}")

    # ---- duas cadeias que avancam: o plano
    problems = []
    for anchor in (True, False):
        for chunk in (49, 77):
            for total in list(range(1, 500)) + [961, 1500]:
                problem = chains_ok(total, chunk, 5, anchor)
                if problem:
                    problems.append(f"total={total} chunk={chunk} ancora={anchor}: {problem}")
    check("D1 plano em duas cadeias valido para todo tamanho de video, com e sem ancora", not problems,
          "; ".join(problems[:3]))
    two = stitch.chains(376, 49, 5)
    one = stitch.plan(376, 49, 5)
    start_two = next(index for index, piece in enumerate(two) if piece.get("blend"))
    ratio = chain_time(two, True) / stitch._cost(one)
    check("D2 o video de 23,5 s (9 pedacos de 49): 5 numa cadeia e 4 na outra, que parte do primeiro pedaco, "
          "em 60% do tempo",
          len(one) == 9 and start_two == 5 and len(two) - start_two == 4 and two[start_two]["anchor"] == 0
          and 5 + stitch.SETTLE <= two[start_two]["skip"] <= 8 + stitch.SETTLE and ratio < 0.62,
          f"{ratio:.0%}; {[(p['start'], p['length']) for p in two[start_two - 1:start_two + 1]]}")
    whole = stitch._cost(stitch.plan(160, 77, 5))
    tied, free = chain_time(stitch.chains(160, 77, 5), True) / whole, chain_time(stitch.chains(160, 77, 5, anchor=False), False) / whole
    check("D3 10 s em pedacos de 77: com ancora a segunda cadeia espera o primeiro pedaco e o tempo cai a uns 3/4; "
          "sem ela, as duas GPUs comecam juntas e ele cai a perto da metade",
          0.7 < tied < 0.8 and 0.5 < free < 0.6, f"{tied:.0%} e {free:.0%}")
    check("D3b video que cabe num pedaco: com ancora nao ha o que dividir; sem ela, meio video para cada GPU",
          stitch.chains(80, 77, 5) == stitch.plan(80, 77, 5)
          and [piece["after"] for piece in stitch.chains(80, 77, 5, anchor=False)] == [None, None])

    # ---- duas cadeias: a emenda. O azul diz o pedaco; vermelho e verde, a posicao do quadro
    # (ate 224: por isso o video com intermediarios e mais curto).
    for factor, label in ((1, "D4"), (2, "D5")):
        total = 150 if factor == 1 else 110
        pieces = stitch.chains(total, 49, 5)
        seam = next(index for index, piece in enumerate(pieces) if piece.get("blend"))
        marked = []
        for index, piece in enumerate(pieces):
            folder = WORK / f"cadeia{factor}_{index}"
            make_fine(folder, piece, factor, index, None)
            marked.append({**piece, "pattern": str(folder / "%05d.png")})
        stitch.color_fixes = no_fixes
        try:
            await stitch.stitch(marked, WORK / f"cadeias{factor}.mp4", 8, 5, (64, 48), factor=factor)
        finally:
            stitch.color_fixes = real_fixes
        marks = read_marks(WORK / f"cadeias{factor}.mp4")
        blues = read_blues(WORK / f"cadeias{factor}.mp4")
        wrong = [number for number, (position, _) in enumerate(marks) if position != number]
        first = factor * (pieces[seam]["start"] + pieces[seam]["skip"])   # onde a mistura comeca
        mixed = factor * (stitch.BLEND - 1) + 1
        before, after = 40 + 40 * (seam - 1), 40 + 40 * seam
        ramp = blues[first:first + mixed]
        check(f"{label} duas cadeias{' com intermediarios' if factor > 1 else ''}: todos os quadros no lugar, e na "
              "emenda entre elas uma vira a outra aos poucos",
              len(marks) == factor * (total - 1) + 1 and not wrong
              and abs(blues[first - 1] - before) <= 3 and abs(blues[first + mixed] - after) <= 3
              and abs(ramp[0] - before) <= 3 and all(a <= b + 1 for a, b in zip(ramp, ramp[1:]))
              and before + 8 < ramp[len(ramp) // 2] < after - 8,
              f"{len(marks)} quadros; fora do lugar {wrong[:4]}; azul na mistura {ramp}")

    # A cor: a segunda cadeia sai mais clara que a primeira; nos quadros que as duas geraram
    # da para medir a diferenca direto.
    pieces = stitch.chains(150, 49, 5)
    seam = next(index for index, piece in enumerate(pieces) if piece.get("blend"))
    tinted = []
    for index, piece in enumerate(pieces):
        folder = WORK / f"tom{index}"
        make_fine(folder, piece, 1, index, 0 if index < seam else 14)
        tinted.append({**piece, "pattern": str(folder / "%05d.png")})
    make_fine(WORK / "tom_entrada", {"start": 0, "length": 150}, 1, 0, 0)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-framerate", "16", "-i", str(WORK / "tom_entrada" / "%05d.png"),
                    "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv444p", str(WORK / "tom_entrada.mp4")], check=True)
    fixes = await stitch.stitch(tinted, WORK / "tom.mp4", 16, 5, (64, 48), original=WORK / "tom_entrada.mp4")
    reds, count = read_reds(WORK / "tom.mp4", 150)
    worst = max(abs(red - (20 + number)) for number, red in enumerate(reds))
    check("D6 a cor da segunda cadeia e trazida a da primeira pelos quadros que as duas tem",
          count == 150 and worst <= 6 and fixes[seam][0][1] < -8 and fixes[seam - 1] == [stitch.NEUTRAL] * 3,
          f"{count} quadros, maior erro {worst}; deslocamento {round(fixes[seam][0][1], 1)}")

    print(f"\n{len(failures)} falha(s)" + (": " + "; ".join(failures) if failures else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
