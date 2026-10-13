"""Presets de camera, efeitos e estilo. Cada preset vira um trecho de prompt para o Wan 2.2.

Para criar um preset novo basta acrescentar uma linha na lista certa: `id` unico, `name` como
aparece na interface e `prompt` em ingles (o Wan entende melhor ingles). Nas cameras, `anim`
escolhe a animacao da miniatura (veja os nomes em app/static/style.css).

Nas cameras, descreva o movimento em vez de usar o jargao: o modelo le o texto ao pe da letra,
e nome de tecnica que tambem e nome de coisa aparece na cena. Num teste real, "Crane up" pos
um guindaste no video. Por isso nada de crane, truck, drone, dolly, whip, bullet ou barrel.
"""

CAMERAS = [
    {"id": "none", "name": "Livre", "anim": "none", "prompt": ""},
    {"id": "static", "name": "Câmera fixa", "anim": "static",
     "prompt": "Static shot, the camera stays completely still"},
    {"id": "dolly_in", "name": "Dolly In", "anim": "zoom-in",
     "prompt": "The camera slowly pushes in toward the subject, a smooth forward move"},
    {"id": "dolly_out", "name": "Dolly Out", "anim": "zoom-out",
     "prompt": "The camera slowly pulls back away from the subject, a smooth backward move "
               "revealing the surroundings"},
    {"id": "crash_zoom_in", "name": "Crash Zoom In", "anim": "crash",
     "prompt": "The camera rushes in extremely fast toward the subject, a sudden dramatic snap "
               "zoom ending in a tight close-up"},
    {"id": "crash_zoom_out", "name": "Crash Zoom Out", "anim": "crash-out",
     "prompt": "The camera pulls back extremely fast from a tight close-up to a wide shot, a "
               "sudden dramatic snap zoom out"},
    {"id": "pan_left", "name": "Pan esquerda", "anim": "pan-left",
     "prompt": "The camera pans left smoothly across the scene"},
    {"id": "pan_right", "name": "Pan direita", "anim": "pan-right",
     "prompt": "The camera pans right smoothly across the scene"},
    {"id": "tilt_up", "name": "Tilt para cima", "anim": "tilt-up",
     "prompt": "The camera tilts up slowly, from the ground toward the sky"},
    {"id": "tilt_down", "name": "Tilt para baixo", "anim": "tilt-down",
     "prompt": "The camera tilts down slowly, from the sky toward the ground"},
    {"id": "truck_left", "name": "Travelling esq.", "anim": "truck-left",
     "prompt": "The camera slides to the left, moving sideways parallel to the scene with "
               "strong parallax between foreground and background"},
    {"id": "truck_right", "name": "Travelling dir.", "anim": "truck-right",
     "prompt": "The camera slides to the right, moving sideways parallel to the scene with "
               "strong parallax between foreground and background"},
    {"id": "orbit", "name": "Órbita 360", "anim": "orbit",
     "prompt": "Arc shot: the camera orbits a full circle around the subject at a constant "
               "distance, the background sweeping past with strong parallax"},
    {"id": "arc", "name": "Arco lento", "anim": "arc",
     "prompt": "Arc shot: the camera glides in a slow quarter circle around the subject, "
               "keeping them centered"},
    {"id": "crane_up", "name": "Grua subindo", "anim": "crane-up",
     "prompt": "The camera rises smoothly straight up, high into the air, revealing the whole "
               "scene from above"},
    {"id": "crane_down", "name": "Grua descendo", "anim": "crane-down",
     "prompt": "The camera descends smoothly from high above down to eye level"},
    {"id": "overhead", "name": "Vista de cima", "anim": "overhead",
     "prompt": "Top-down overhead shot looking straight down at the scene, the camera slowly "
               "rotating"},
    {"id": "low_angle", "name": "Contra-plongée", "anim": "low-angle",
     "prompt": "Low angle shot from near the ground looking up at the subject, heroic and "
               "imposing, the camera slowly pushing in"},
    {"id": "tracking", "name": "Tracking", "anim": "truck-right",
     "prompt": "Tracking shot: the camera follows the moving subject at the same speed, "
               "keeping them steady in frame"},
    {"id": "lead", "name": "De frente", "anim": "zoom-out",
     "prompt": "Leading shot: the camera moves backward in front of the subject, who advances "
               "toward the viewer and stays framed"},
    {"id": "handheld", "name": "Câmera na mão", "anim": "shake",
     "prompt": "Handheld shot with natural shake and small reframings, raw documentary feel"},
    {"id": "pov", "name": "POV", "anim": "shake",
     "prompt": "First-person POV shot seen through the character's eyes, with natural head "
               "movement"},
    {"id": "snorricam", "name": "Snorricam", "anim": "snorricam",
     "prompt": "The subject's face stays locked in the center of the frame, as if the view were "
               "attached to their body, while the background sways wildly around them"},
    {"id": "fpv", "name": "Drone FPV", "anim": "fpv",
     "prompt": "First-person flying shot: the view races fast and low through the scene, "
               "banking and diving dynamically"},
    {"id": "aerial", "name": "Drone aéreo", "anim": "aerial",
     "prompt": "Aerial shot from high above, gliding slowly forward over the landscape"},
    {"id": "through", "name": "Atravessando", "anim": "through",
     "prompt": "The camera flies forward through a narrow opening and emerges into the scene"},
    {"id": "dolly_zoom", "name": "Dolly Zoom", "anim": "vertigo",
     "prompt": "Vertigo effect: the background stretches and warps dramatically while the "
               "subject stays the same size in frame"},
    {"id": "bullet_time", "name": "Bullet Time", "anim": "bullet",
     "prompt": "Frozen moment: time stops and everything hangs motionless in mid-air while the "
               "camera sweeps rapidly around the subject"},
    {"id": "whip_pan", "name": "Whip Pan", "anim": "whip",
     "prompt": "The camera swings sideways extremely fast with heavy motion blur, then settles "
               "on the subject"},
    {"id": "dutch", "name": "Dutch angle", "anim": "roll",
     "prompt": "The camera slowly rolls sideways into a tilted, slanted frame, creating tension"},
    {"id": "spin", "name": "Giro 360", "anim": "spin",
     "prompt": "The whole view rotates a full 360 degrees around its center in one continuous "
               "roll"},
    {"id": "macro", "name": "Super close", "anim": "macro",
     "prompt": "Extreme close-up macro shot with very shallow depth of field, the camera "
               "drifting slowly"},
    {"id": "rack_focus", "name": "Troca de foco", "anim": "focus",
     "prompt": "The focus shifts smoothly from the foreground to the background"},
    {"id": "fisheye", "name": "Olho de peixe", "anim": "fisheye",
     "prompt": "Fisheye view with extreme wide-angle distortion, very close to the subject"},
    {"id": "slow_motion", "name": "Câmera lenta", "anim": "slow",
     "prompt": "Extreme slow motion, high frame rate look, every detail of the movement "
               "visible"},
    {"id": "timelapse", "name": "Timelapse", "anim": "timelapse",
     "prompt": "Time-lapse with a static camera: time passes quickly, clouds race across the "
               "sky, shadows sweep and the light changes"},
    {"id": "hyperlapse", "name": "Hyperlapse", "anim": "fpv",
     "prompt": "Hyperlapse: the camera travels forward quickly while time is accelerated, "
               "clouds and lights streaking"},
]

EFFECTS = [
    {"id": "none", "name": "Nenhum", "prompt": ""},
    {"id": "explosion", "name": "Explosão",
     "prompt": "A massive explosion erupts behind the subject: a fireball and shockwave with "
               "flying debris, smoke and sparks"},
    {"id": "fire", "name": "Em chamas",
     "prompt": "The subject bursts into flames, fire spreading over them while glowing embers "
               "and smoke rise"},
    {"id": "disintegrate", "name": "Desintegrar",
     "prompt": "The subject slowly disintegrates into fine dust and particles carried away by "
               "the wind"},
    {"id": "levitate", "name": "Levitação",
     "prompt": "The subject slowly rises and levitates above the ground, hair and clothes "
               "floating weightlessly"},
    {"id": "freeze", "name": "Congelar",
     "prompt": "Ice and frost spread rapidly over the subject and the scene until everything "
               "is frozen solid, cold mist hanging in the air"},
    {"id": "lightning", "name": "Raios",
     "prompt": "Bolts of lightning strike around the subject, electricity crackling over "
               "their body, eyes glowing"},
    {"id": "flood", "name": "Inundação",
     "prompt": "Water floods into the scene, rising quickly with waves and splashes"},
    {"id": "melt", "name": "Derreter",
     "prompt": "The subject melts slowly like wax, dripping and flowing down into a puddle"},
    {"id": "glow", "name": "Brilho",
     "prompt": "The subject starts to glow from within with radiant light, luminous particles "
               "floating around them"},
    {"id": "wings", "name": "Asas",
     "prompt": "Large feathered wings unfold from the subject's back and spread wide"},
    {"id": "bloom", "name": "Florescer",
     "prompt": "Flowers and plants sprout and bloom rapidly all around, covering the scene in "
               "vivid petals"},
    {"id": "smoke", "name": "Fumaça",
     "prompt": "Thick colorful smoke swirls around the subject and engulfs the scene"},
    {"id": "rain", "name": "Chuva",
     "prompt": "Heavy rain starts pouring, drops splashing on every surface, wet reflections "
               "everywhere"},
    {"id": "snow", "name": "Neve",
     "prompt": "Snow falls gently and gradually covers the scene"},
    {"id": "wind", "name": "Vento forte",
     "prompt": "A powerful gust of wind hits, hair and clothes whipping violently while "
               "leaves and dust fly past"},
    {"id": "metal", "name": "Virar metal",
     "prompt": "The subject's surface transforms into polished liquid chrome, reflecting the "
               "environment"},
    {"id": "hologram", "name": "Holograma",
     "prompt": "The subject turns into a flickering translucent blue hologram with scan lines"},
    {"id": "glitch", "name": "Glitch",
     "prompt": "Digital glitch effect, datamoshing: the image tears with RGB split and pixel "
               "sorting"},
    {"id": "clones", "name": "Clones",
     "prompt": "The subject multiplies into several identical copies that step out to both "
               "sides"},
    {"id": "portal", "name": "Portal",
     "prompt": "A glowing portal tears open in the air behind the subject, swirling with "
               "energy"},
    {"id": "confetti", "name": "Confete",
     "prompt": "Colorful confetti bursts into the air and rains down over the scene"},
]

STYLES = [
    {"id": "none", "name": "Nenhum", "prompt": ""},
    {"id": "cinematic", "name": "Cinemático",
     "prompt": "Cinematic film look, anamorphic lens, soft film grain, dramatic lighting, "
               "shallow depth of field"},
    {"id": "commercial", "name": "Comercial",
     "prompt": "High-end commercial look, clean studio lighting, glossy reflections, crisp "
               "detail"},
    {"id": "documentary", "name": "Documentário",
     "prompt": "Documentary style, natural light, realistic colors, authentic atmosphere"},
    {"id": "golden_hour", "name": "Golden hour",
     "prompt": "Golden hour sunset light, warm tones, long soft shadows, gentle lens flare"},
    {"id": "noir", "name": "Noir",
     "prompt": "Black and white film noir, hard shadows, high contrast, smoke in the air"},
    {"id": "cyberpunk", "name": "Cyberpunk",
     "prompt": "Cyberpunk aesthetic, neon lights, rain-soaked streets, teal and magenta color "
               "grade"},
    {"id": "fantasy", "name": "Fantasia",
     "prompt": "Epic fantasy look, volumetric god rays, rich painterly colors, magical "
               "atmosphere"},
    {"id": "horror", "name": "Terror",
     "prompt": "Horror film look, dim cold light, deep shadows, desaturated colors, unsettling "
               "atmosphere"},
    {"id": "vintage", "name": "Filme antigo",
     "prompt": "Vintage 8mm home movie look, faded colors, heavy grain, light leaks, slight "
               "flicker"},
    {"id": "vhs", "name": "VHS",
     "prompt": "1990s VHS home video look, scan lines, chromatic aberration, washed-out colors"},
    {"id": "anime", "name": "Anime",
     "prompt": "Anime style, cel shading, vibrant colors, detailed hand-drawn backgrounds"},
    {"id": "3d", "name": "Animação 3D",
     "prompt": "Stylized 3D animated film look, soft global illumination, expressive characters"},
    {"id": "claymation", "name": "Massinha",
     "prompt": "Claymation stop-motion style, handmade clay figures, visible fingerprints, "
               "miniature sets"},
    {"id": "pixel", "name": "Pixel art",
     "prompt": "Pixel art style, limited color palette, crisp blocky pixels, retro video game "
               "look"},
    {"id": "watercolor", "name": "Aquarela",
     "prompt": "Watercolor painting style, soft washes of color bleeding on textured paper"},
    {"id": "oil", "name": "Pintura a óleo",
     "prompt": "Oil painting style, thick visible brush strokes, rich textured colors"},
]

NEGATIVE = (
    "色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，最差质量，低质量，"
    "JPEG压缩残留，丑陋的，残缺的，多余的手指，画得不好的手部，画得不好的脸部，畸形的，毁容的，"
    "形态畸形的肢体，手指融合，静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走"
)


def _by_id(items, item_id):
    for item in items:
        if item["id"] == item_id:
            return item
    raise ValueError(f"preset desconhecido: {item_id}")


def camera_prompt(camera_id):
    return _by_id(CAMERAS, camera_id)["prompt"]


def compose_prompt(prompt, camera_id="none", effect_id="none", style_id="none"):
    """Texto do usuario + o que acontece (efeito) + como a camera se move + o visual."""
    parts = [prompt.strip().rstrip(".")]
    fragments = (_by_id(EFFECTS, effect_id), _by_id(CAMERAS, camera_id), _by_id(STYLES, style_id))
    parts += [item["prompt"] for item in fragments if item["prompt"]]
    return ". ".join(parts) + "."


def compose_negative(extra=""):
    extra = extra.strip().rstrip(",")
    return f"{extra}, {NEGATIVE}" if extra else NEGATIVE


def public():
    """O que a interface precisa conhecer (sem os textos de prompt)."""
    return {
        "cameras": [{k: c[k] for k in ("id", "name", "anim")} for c in CAMERAS],
        "effects": [{k: e[k] for k in ("id", "name")} for e in EFFECTS],
        "styles": [{k: s[k] for k in ("id", "name")} for s in STYLES],
    }
