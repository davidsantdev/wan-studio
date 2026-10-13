"""Corta o video em pedacos e emenda os pedacos gerados (troca de pessoa).

A troca de pessoa e gerada em pedacos curtos, que custam menos por quadro e nao limitam a
duracao. Um pedaco gerado sozinho inventa detalhes proprios (num teste, o cabelo mudou de um
pedaco para o outro), entao cada pedaco continua de um vizinho ja pronto: recebe 5 quadros dele
e segue dali. Para duas GPUs trabalharem ao mesmo tempo, a geracao comeca por um pedaco curto
no meio do video (a semente) e cresce para os dois lados: uma GPU avanca, a outra recua. Os
pedacos que recuam sao gerados de tras para a frente e desvirados depois.

Na emenda nao ha mistura: cada pedaco entra a partir do ponto em que deixou de copiar o
vizinho. So a cor e ajustada, porque a saturacao costuma subir de um pedaco para o seguinte.

Tudo e feito com o ffmpeg; as contas sao por numero de quadro, nunca por tempo.
"""

import asyncio

TIMEOUT = 900
SAMPLE = 48          # lado, em pixels, das miniaturas usadas para medir cor
WINDOW = 4           # quadros de cada lado da emenda que entram na medida
# Quanto um pedaco pode passar do tamanho pedido para terminar o video. Sem isso, 120 quadros
# (10 s a 12 fps, 15 s a 8) viravam 5 pedacos, os dois das pontas com o minimo de 17 quadros e
# quase todos repetidos do vizinho: medido na T4, 17 min em vez de 12.
STRETCH = 8
MAX_GAIN = 1.25      # limites da correcao: alem disso a diferenca nao e de cor, e de conteudo
MAX_OFFSET = 24.0
SCENE_GAIN = 0.06    # se o proprio video muda isto na emenda (corte de cena), nao se corrige
SCENE_OFFSET = 4.0
NEUTRAL = (1.0, 0.0)


class StitchError(RuntimeError):
    pass


async def run(*command, timeout=TIMEOUT):
    """Roda o ffmpeg e devolve a saida em bytes."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *map(str, command), stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
    except FileNotFoundError:
        raise StitchError(f"{command[0]} não está instalado nesta máquina") from None
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise StitchError("o ffmpeg demorou demais") from None
    if proc.returncode != 0:
        detail = err.decode(errors="replace").strip().splitlines()[-1:] or ["erro desconhecido"]
        raise StitchError(f"o ffmpeg falhou ({detail[0][:200]})")
    return out


# ---------------------------------------------------------------- plano

def _fit(count):
    """O maior 4n+1 que cabe em `count` (o Wan so gera 4n+1 quadros)."""
    return (count - 1) // 4 * 4 + 1


def _cover(count, chunk, minimum):
    """O tamanho (4n+1) do proximo pedaco quando faltam `count` quadros: o menor que cobre
    todos, a partir de `minimum`, ou `chunk` se nao der. Da quando passa de `chunk` por ate
    STRETCH quadros: o pedaco estica em vez de deixar para tras um pedaco minimo."""
    whole = max(minimum, -(-(count - 1) // 4) * 4 + 1)
    return whole if whole <= chunk + STRETCH else chunk


def plan(total, chunk, overlap, seed=0, minimum=17):
    """Divide `total` quadros em pedacos de ate `chunk` quadros (sempre 4n+1).

    O ultimo pedaco de cada lado pode ter ate `chunk` + STRETCH quadros.
    Devolve, em ordem de tempo, [{"start", "length", "after", "reverse"}, ...]. `after` e o
    indice do pedaco de que este continua (None na semente): os dois tem ao menos `overlap`
    quadros em comum, e o pedaco copia `overlap` deles. `reverse` marca os que ficam antes da
    semente e por isso sao gerados de tras para a frente.

    Com `seed` (quadros), a semente e um pedaco curto no meio e ha pedacos dos dois lados;
    sem ele, a semente e o primeiro pedaco e todos avancam. Um video menor que um pedaco vira
    um pedaco so, sem os ate 3 quadros finais que nao fecham 4n+1.
    """
    if total < minimum:
        return []
    if total <= chunk:
        return [{"start": 0, "length": _fit(total), "after": None, "reverse": False}]
    first = (total - seed) // 2 if seed else 0
    spans = [(first, seed or chunk)]
    while spans[-1][0] + spans[-1][1] < total:  # para a frente, ate o ultimo quadro
        begin = spans[-1][0] + spans[-1][1] - overlap
        length = _cover(total - begin, chunk, minimum)
        spans.append((min(begin, total - length), length))
    center = 0
    while spans[0][0] > 0:  # para tras, ate o primeiro quadro
        end = spans[0][0] + overlap
        length = _cover(end, chunk, minimum)
        spans.insert(0, (max(0, end - length), length))
        center += 1
    pieces = []
    for index, (start, length) in enumerate(spans):
        after = None if index == center else index + 1 if index < center else index - 1
        pieces.append({"start": start, "length": length, "after": after, "reverse": index < center})
    return pieces


def copied(pieces, index, overlap):
    """Os quadros que o pedaco `index` copia do vizinho de que continua, como (indice do
    vizinho, primeiro quadro dentro do vizinho): sao sempre `overlap` quadros, os primeiros
    do pedaco na ordem em que ele e gerado."""
    piece = pieces[index]
    source = pieces[piece["after"]]
    first = piece["start"] + piece["length"] - overlap if piece["reverse"] else piece["start"]
    return piece["after"], first - source["start"]


def bounds(pieces, overlap):
    """O trecho do video que cada pedaco fornece, como (primeiro, fim) em quadros do video:
    do ponto em que deixou de copiar o vizinho ate o ponto em que o proximo assume."""
    cuts = [0]
    for earlier, later in zip(pieces, pieces[1:]):
        if later["reverse"] or earlier["reverse"]:  # o de antes continua do de depois
            cuts.append(earlier["start"] + earlier["length"] - overlap)
        else:
            cuts.append(later["start"] + overlap)
    cuts.append(pieces[-1]["start"] + pieces[-1]["length"])
    spans = list(zip(cuts, cuts[1:]))
    for piece, (first, end) in zip(pieces, spans):
        if not piece["start"] <= first < end <= piece["start"] + piece["length"]:
            raise StitchError("plano de pedaços inconsistente")
    return spans


def fine(pieces, spans, factor):
    """Os trechos de `bounds` quando a sequencia de cada pedaco tem, entre dois quadros gerados,
    `factor` - 1 intermediarios (o quadro k do pedaco fica no indice `factor` * k), em indices
    dessa sequencia mais fina. Os intermediarios de uma emenda vem do pedaco que tem os dois
    quadros vizinhos a ela: o que continuou do outro."""
    cuts = [0]
    for earlier, later, (_, edge) in zip(pieces, pieces[1:], spans):
        continued = earlier if later["reverse"] or earlier["reverse"] else later
        cuts.append(factor * edge - (0 if continued is earlier else factor - 1))
    cuts.append(factor * (pieces[-1]["start"] + pieces[-1]["length"] - 1) + 1)
    return list(zip(cuts, cuts[1:]))


async def cut(source, target, start, length, reverse=False, ffmpeg="ffmpeg"):
    """Grava os quadros [start, start+length) de `source` em `target`, sem audio; com
    `reverse`, de tras para a frente."""
    chain = f"trim=start_frame={start}:end_frame={start + length},setpts=PTS-STARTPTS"
    await run(ffmpeg, "-nostdin", "-y", "-v", "error", "-i", source, "-an",
              "-vf", chain + (",reverse" if reverse else ""),
              "-c:v", "libx264", "-preset", "veryfast", "-crf", "12", "-pix_fmt", "yuv420p", target)


async def clip(pattern, first, count, target, fps, reverse=False, factor=1, lossless=False,
               ffmpeg="ffmpeg"):
    """Um video curto com `count` quadros gerados da sequencia de imagens, a partir do quadro
    `first`: e o que o pedaco seguinte recebe para continuar deste. Com `factor`, a sequencia
    tem intermediarios (veja `fine`), que ficam de fora. `lossless`: sem perda nenhuma, para
    quadros que ainda vao ser restaurados."""
    quality = ["-qp", "0", "-pix_fmt", "yuv444p"] if lossless else ["-crf", "6", "-pix_fmt", "yuv420p"]
    # O corte vem antes da inversao: o filtro "reverse" le a entrada inteira.
    chain = (f"select='not(mod(n\\,{factor}))'," if factor > 1 else "") + f"trim=end_frame={count}"
    chain += (",reverse" if reverse else "") + f",setpts=N/({fps}*TB)"
    await run(ffmpeg, "-nostdin", "-y", "-v", "error", "-framerate", fps * factor,
              "-start_number", first * factor, "-i", pattern, "-vf", chain, "-r", fps,
              "-c:v", "libx264", "-preset", "veryfast", *quality, target)


# ---------------------------------------------------------------- cor

def _measure(raw):
    plane = SAMPLE * SAMPLE
    if len(raw) < 3 * plane:
        raise StitchError("não consegui ler os quadros gerados")
    stats = []
    for channel in range(3):  # yuv444p: um plano inteiro de cada canal por quadro
        values = b"".join(raw[frame + channel * plane:frame + (channel + 1) * plane]
                          for frame in range(0, len(raw) - 3 * plane + 1, 3 * plane))
        mean = sum(values) / len(values)
        spread = (sum(value * value for value in values) / len(values) - mean * mean) ** 0.5
        stats.append((mean, spread))
    return stats


async def color_stats(source, first, count, ffmpeg="ffmpeg"):
    """Media e desvio de Y, U e V em `count` quadros a partir do quadro `first`. `source` e
    uma sequencia de imagens ("pasta/%05d.png") ou um video."""
    shrink = f"scale={SAMPLE}:{SAMPLE}:flags=area,format=yuv444p"
    if "%" in str(source):
        command = [ffmpeg, "-nostdin", "-v", "error", "-start_number", first, "-i", source,
                   "-frames:v", count, "-vf", shrink]
    else:
        command = [ffmpeg, "-nostdin", "-v", "error", "-i", source, "-an", "-vf",
                   f"trim=start_frame={first}:end_frame={first + count},{shrink}"]
    return _measure(await run(*command, "-f", "rawvideo", "-"))


def match(reference, current):
    """Ganho e deslocamento, por canal, que levam as estatisticas `current` ate `reference`.
    A cor (U, V) e medida em torno de 128, o cinza: o ganho nela e a saturacao."""
    fixes = []
    for channel, ((ref_mean, ref_spread), (mean, spread)) in enumerate(zip(reference, current)):
        center = 128.0 if channel else 0.0
        gain = ref_spread / spread if spread > 1.0 and ref_spread > 1.0 else 1.0
        gain = min(MAX_GAIN, max(1 / MAX_GAIN, gain))
        offset = (ref_mean - center) - gain * (mean - center)
        offset = min(MAX_OFFSET, max(-MAX_OFFSET, offset))
        fixes.append(NEUTRAL if abs(gain - 1) < 0.01 and abs(offset) < 0.75 else (gain, offset))
    return fixes


def applied(stats, fixes):
    """As estatisticas depois da correcao (o pedaco seguinte se ajusta a este ja corrigido)."""
    out = []
    for channel, ((mean, spread), (gain, offset)) in enumerate(zip(stats, fixes)):
        center = 128.0 if channel else 0.0
        out.append((gain * (mean - center) + center + offset, gain * spread))
    return out


def lut(fixes):
    """O filtro do ffmpeg que aplica a correcao, ou "" se ela nao muda nada."""
    if all(fix == NEUTRAL for fix in fixes):
        return ""
    parts = []
    for name, center, (gain, offset) in zip("yuv", (0, 128, 128), fixes):
        parts.append(f"{name}='clip((val-{center})*{gain:.4f}+{center + offset:.3f},0,255)'")
    return "lutyuv=" + ":".join(parts)


async def color_fixes(chunks, spans, original=None, factor=1, ffmpeg="ffmpeg"):
    """A correcao de cor de cada pedaco, da semente para as pontas: cada um e comparado com
    aquele de que continua, nos quadros vizinhos a emenda (os do pedaco logo depois dela, os
    do outro logo antes). Como sao instantes diferentes, parte da diferenca e do proprio
    video; o `original`, medido nos mesmos quadros, diz quanto, e essa parte nao e corrigida.
    Se ele muda demais ali, e corte de cena e nao da para comparar. `factor`: veja `fine`."""
    fixes = [None] * len(chunks)
    order = sorted(range(len(chunks)), key=lambda index: (chunks[index]["after"] is not None,
                                                          abs(index - _seed(chunks))))
    for index in order:
        chunk = chunks[index]
        if chunk["after"] is None:
            fixes[index] = [NEUTRAL] * 3
            continue
        other = chunks[chunk["after"]]
        first, end = spans[index]
        edge = end if chunk["reverse"] else first  # o quadro do video onde os dois se encontram
        count = max(1, min(WINDOW, end - first, spans[chunk["after"]][1] - spans[chunk["after"]][0]))
        mine, theirs = (edge - count, edge) if chunk["reverse"] else (edge, edge - count)
        # Do primeiro ao ultimo quadro gerado da janela, com os intermediarios que ha entre eles.
        span = factor * (count - 1) + 1
        ours = await color_stats(chunk["pattern"], factor * (mine - chunk["start"]), span, ffmpeg)
        base = await color_stats(other["pattern"], factor * (theirs - other["start"]), span, ffmpeg)
        expected = applied(base, fixes[chunk["after"]])
        if original:
            # O que muda no video original entre os quadros do vizinho e os deste pedaco.
            change = match(await color_stats(original, mine, count, ffmpeg),
                           await color_stats(original, theirs, count, ffmpeg))
            if any(abs(gain - 1) > SCENE_GAIN or abs(offset) > SCENE_OFFSET for gain, offset in change):
                fixes[index] = [NEUTRAL] * 3
                continue
            expected = applied(expected, change)
        fixes[index] = match(expected, ours)
    return fixes


def _seed(chunks):
    return next(index for index, chunk in enumerate(chunks) if chunk["after"] is None)


# ---------------------------------------------------------------- emenda

def filtergraph(chunks, spans, fixes, fps, size, out_fps=None, factor=1):
    """O grafo de filtros que emenda os pedacos (entradas 0..n-1) na saida [v]. `fps` e o das
    sequencias de entrada; com `factor`, elas e os trechos `spans` sao os de `fine`."""
    parts, order = [], []
    for index, (chunk, (first, end), fix) in enumerate(zip(chunks, spans, fixes)):
        chain = [f"fps={fps}", "format=yuv444p"]
        color = lut(fix)
        if color:
            chain.append(color)
        begin = factor * chunk["start"]
        chain.append(f"trim=start_frame={first - begin}:end_frame={end - begin}")
        chain.append("setpts=PTS-STARTPTS")
        parts.append(f"[{index}:v]{','.join(chain)}[p{index}]")
        order.append(f"[p{index}]")
    joined = order[0]
    if len(order) > 1:
        joined = "[cat]"
        parts.append(f"{''.join(order)}concat=n={len(order)}:v=1:a=0{joined}")
    final = f"{joined}setpts=N/({fps}*TB)"
    if out_fps and out_fps != fps:  # quadros intermediarios por estimativa de movimento
        final += f",minterpolate=fps={out_fps}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1"
    width, height = size
    parts.append(f"{final},scale={width}:{height}:flags=lanczos,format=yuv420p[v]")
    return ";".join(parts)


async def stitch(chunks, target, fps, overlap, size, audio=None, out_fps=None, original=None,
                 factor=1, ffmpeg="ffmpeg"):
    """Emenda os pedacos em `target` (MP4 H.264).

    `chunks`: os pedacos do plano, em ordem de tempo, cada um com mais um campo `pattern`: a
    sequencia de imagens dele ("pasta/%05d.png"), ja na ordem do video. `audio`: um arquivo
    cuja primeira trilha de audio acompanha o resultado. `original`: o video de entrada, quadro
    a quadro igual ao plano, para reconhecer cortes de cena. `factor`: as sequencias ja trazem
    `factor` - 1 quadros intermediarios entre dois gerados (veja `fine`) e o video sai com
    `fps` * `factor`; se `out_fps` for outro, o ffmpeg estima os quadros pelo movimento.
    Devolve as correcoes de cor.
    """
    spans = bounds(chunks, overlap)
    fixes = await color_fixes(chunks, spans, original, factor, ffmpeg)
    rate = fps * factor
    command = [ffmpeg, "-nostdin", "-y", "-v", "error"]
    for chunk in chunks:
        command += ["-framerate", rate, "-start_number", 0, "-i", chunk["pattern"]]
    if audio:
        command += ["-i", audio]
    graph = filtergraph(chunks, fine(chunks, spans, factor), fixes, rate, size, out_fps, factor)
    command += ["-filter_complex", graph, "-map", "[v]"]
    if audio:
        command += ["-map", f"{len(chunks)}:a:0?", "-c:a", "aac", "-b:a", "160k", "-shortest"]
    command += ["-c:v", "libx264", "-preset", "medium", "-crf", "17", "-movflags", "+faststart", target]
    await run(*command)
    return fixes
