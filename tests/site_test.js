// Teste de ponta a ponta do site estatico num Chrome real (headless), contra a pilha do
// notebook rodando localmente com o ComfyUI simulado.
const { chromium } = require("playwright-core");
const fs = require("fs");

const path = require("path");
const { pathToFileURL } = require("url");

const SITE = pathToFileURL(path.join(__dirname, "..", "site", "index.html")).href;
const TESTS = path.join(__dirname, ".work").split(path.sep).join("/");
const SHOTS = `${TESTS}/shots`;
const failures = [];
let page;

function check(label, condition, detail = "") {
  console.log((condition ? "ok    " : "FALHA ") + label + (detail ? `  [${detail}]` : ""));
  if (!condition) failures.push(label);
}
const shot = (name, options = {}) => page.screenshot({ path: `${SHOTS}/${name}.png`, ...options });
const text = (selector) => page.textContent(selector);
const count = (selector) => page.locator(selector).count();
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

(async () => {
  fs.rmSync(`${TESTS}/profile`, { recursive: true, force: true });
  fs.mkdirSync(SHOTS, { recursive: true });
  const code = fs.readFileSync(`${TESTS}/code.txt`, "utf8").trim();
  const session = JSON.parse(Buffer.from(code, "base64url").toString());
  const auth = { Authorization: "Basic " + Buffer.from(`wan:${session.p}`).toString("base64") };
  const serverJobs = async () => (await (await fetch(`${session.u}/api/jobs`, { headers: auth })).json()).jobs;

  const context = await chromium.launchPersistentContext(`${TESTS}/profile`, {
    channel: "chrome", headless: true, viewport: { width: 1440, height: 900 },
  });
  page = context.pages()[0] || await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push("pageerror: " + error.message));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push("console: " + message.text());
  });

  // ---- P. o atalho "Ligar GPU" abre o site com um link que so pareia (sem endereco de sessao)
  fs.rmSync(`${TESTS}/profile3`, { recursive: true, force: true });
  const second = await chromium.launchPersistentContext(`${TESTS}/profile3`, {
    channel: "chrome", headless: true, viewport: { width: 1280, height: 800 },
  });
  const paired = second.pages()[0] || await second.newPage();
  await second.route("https://ntfy.sh/**", (route) => route.abort());  // segura a descoberta para ver o "ligando"
  const pairOnly = { t: session.t, k: session.k, s: Math.floor(Date.now() / 1000) };
  await paired.goto(`${SITE}#c=${Buffer.from(JSON.stringify(pairOnly)).toString("base64url")}`);
  await paired.waitForSelector("#cameras .camera");
  await paired.waitForFunction(() => /ligando/.test(document.querySelector("#status").textContent), null, { timeout: 10000 }).catch(() => {});
  const waiting = await paired.evaluate(() => ({
    status: document.querySelector("#status").textContent, botao: document.querySelector("#submit").textContent,
    travado: document.querySelector("#submit").disabled, hash: location.hash,
    pareado: Boolean(localStorage.getItem("wan-pairing")), sessao: localStorage.getItem("wan-conn"),
  }));
  check("P1 link do atalho pareia e mostra a GPU ligando", waiting.pareado && waiting.sessao === null && waiting.hash === ""
    && /GPU ligando/.test(waiting.status) && /ligando/.test(waiting.botao) && waiting.travado, JSON.stringify(waiting));
  await second.unroute("https://ntfy.sh/**");
  await paired.evaluate(() => { lastDiscover = 0; refreshStatus(); });
  const found = await paired.waitForFunction(() => document.querySelector("#status").dataset.state === "ok", null, { timeout: 60000 }).then(() => true, () => false);
  check("P2 conecta sozinho quando a GPU aparece, sem colar codigo", found, await paired.textContent("#status"));
  check("P3 sai do estado ligando", await paired.evaluate(() => localStorage.getItem("wan-starting") === null && !document.querySelector("#submit").disabled));
  await second.close();

  // ---- A. primeira visita, GPU desligada
  await page.goto(SITE);
  await page.waitForSelector("#cameras .camera");
  check("A1 status inicial desligado", (await text("#status")).includes("GPU desligada"), await text("#status"));
  check("A2 botao principal pede para ligar", (await text("#submit")).includes("Ligar a GPU"));
  check("A3 37 movimentos de camera", (await count("#cameras .camera")) === 37);
  check("A4 contexto seguro com WebCrypto em file://", await page.evaluate(() => window.isSecureContext && Boolean(crypto.subtle)));
  // O aviso so aparece depois que a galeria do navegador (IndexedDB) e lida.
  const emptyShown = await page.waitForSelector("#empty", { state: "visible", timeout: 5000 }).then(() => true, () => false);
  check("A5 galeria vazia explica", emptyShown);
  await page.hover('#cameras [data-value="orbit"]');
  await shot("01-desligado");

  // ---- B. conectar colando o codigo
  await page.click("#submit");  // sem GPU, o botao abre o dialogo
  await page.waitForSelector("#gpu[open]");
  check("B1 dialogo mostra passo de colar codigo", await page.isVisible("#step-code") && !(await page.isVisible("#step-paired")));
  await shot("02-dialogo-desligado");
  await page.fill("#connect-code", "codigo-errado");
  await page.click("#connect-go");
  check("B2 codigo invalido avisa", await page.isVisible("#connect-error"));
  await page.fill("#connect-code", code);
  await page.click("#connect-go");
  await page.waitForFunction(() => document.querySelector("#status").dataset.state === "ok", null, { timeout: 20000 });
  check("B3 conectou e mostra a placa", (await text("#status")).includes("Tesla T4"), await text("#status"));
  check("B4 dialogo fechou", !(await page.evaluate(() => document.querySelector("#gpu").open)));
  check("B5 pareamento guardado", await page.evaluate(() => Boolean(localStorage.getItem("wan-pairing"))));
  check("B6 melhorador escondido sem 2a GPU", !(await page.isVisible("#enhance")));

  // ---- C. texto -> video, 2 de uma vez, com presets
  await page.fill("#prompt", "um gato astronauta flutuando");
  await page.click('#cameras [data-value="orbit"]');
  await page.click('[data-key="tab"] [data-value="effect"]');
  await page.click('#effects [data-value="glow"]');
  await page.click('[data-key="tab"] [data-value="style"]');
  await page.click('#styles [data-value="cinematic"]');
  await page.click('[data-key="tab"] [data-value="camera"]');
  await page.click('[data-key="count"] [data-value="2"]');
  check("C1 botao diz Gerar 2 videos", (await text("#submit")).trim() === "Gerar 2 vídeos", await text("#submit"));
  check("C2 abas mostram a escolha", (await text('[data-pick="camera"]')) === "Órbita 360" && (await text('[data-pick="effect"]')) === "Brilho");
  await shot("03-formulario");
  await page.click("#submit");
  await page.waitForSelector(".card .card-wait");
  check("C3 cards de espera aparecem na hora", (await count(".card .card-wait")) === 2);
  await page.evaluate(() => window.scrollTo(0, 0));
  await shot("04-progresso");
  await page.waitForFunction(() => document.querySelectorAll(".card video").length === 2, null, { timeout: 90000 });
  const videos = await page.evaluate(async () => {
    const list = [...document.querySelectorAll(".card video")];
    await Promise.all(list.map((v) => v.readyState >= 1 || new Promise((r) => v.addEventListener("loadedmetadata", r, { once: true }))));
    return list.map((v) => ({ blob: v.src.startsWith("blob:"), width: v.videoWidth, duration: Math.round(v.duration) }));
  });
  check("C4 dois videos tocaveis vindos do navegador", videos.length === 2 && videos.every((v) => v.blob && v.width === 832 && v.duration === 3), JSON.stringify(videos));
  await sleep(1500);
  check("C5 servidor liberou os arquivos apos salvar", (await serverJobs()).length === 0);
  check("C6 nota da galeria conta os itens", (await text("#gallery-note")).includes("2 itens guardados"), await text("#gallery-note"));
  check("C7 card descreve o job", /Wan 2\.2 5B turbo · 832×480 · 5 s · Órbita 360 · Brilho · Cinemático · seed \d+/.test(await text(".card-meta")), await text(".card-meta"));
  await shot("05-galeria");

  // ---- D. imagem e "Animar"
  await page.click('[data-key="mode"] [data-value="img"]');
  await page.click('[data-key="count"] [data-value="1"]');
  check("D1 modo imagem esconde camera e duracao", !(await page.isVisible('[data-key="tab"] [data-value="camera"]')) && !(await page.isVisible("#duration-field")));
  check("D2 botao diz Gerar imagem", (await text("#submit")).trim() === "Gerar imagem");
  await page.fill("#prompt", "retrato de uma raposa");
  await page.click("#submit");
  await page.waitForFunction(() => document.querySelectorAll(".card img").length === 1, null, { timeout: 60000 });
  check("D3 imagem na galeria", (await count(".card img")) === 1);
  const imageGraph = Object.values(await (await fetch("http://127.0.0.1:8188/_graphs")).json()).pop();
  const imageNodes = Object.values(imageGraph).map((node) => node.class_type);
  const imageSampler = Object.values(imageGraph).find((node) => node.class_type === "KSampler");
  check("D3b imagem vai sem turbo (passos completos) e a opcao some do formulario", !(await page.isVisible("#fast-row"))
    && await page.isChecked("#fast") && !imageNodes.includes("SamplerCustom") && !imageNodes.includes("LoraLoaderModelOnly")
    && imageSampler && imageSampler.inputs.steps === 20 && !(await text(".card:has(.card-media img) .card-meta")).includes("turbo"),
    await text(".card:has(.card-media img) .card-meta"));
  await page.click('.card:has(.card-media img) .card-actions button:has-text("Animar")');
  await page.waitForFunction(() => !document.querySelector('[data-frame="start_image"] img').hidden);
  check("D4 Animar leva para Imagem → Video com o frame", (await page.getAttribute('[data-key="mode"] [data-value="i2v"]', "aria-selected")) === "true"
    && await page.evaluate(() => files.start_image instanceof File && files.start_image.size > 1000));
  await page.fill("#prompt", "a raposa vira a cabeça devagar");
  await page.click("#submit");
  await page.waitForFunction(() => document.querySelectorAll(".card video").length === 3, null, { timeout: 90000 });
  check("D5 imagem → video gerou", true);

  // ---- E. "Continuar" a partir do ultimo frame
  await page.evaluate(() => { setFrame("start_image", null); });
  await page.locator('.card:has(.card-media video)').first().locator('button:has-text("Continuar")').click();
  await page.waitForFunction(() => !document.querySelector('[data-frame="start_image"] img').hidden, null, { timeout: 20000 });
  const frame = await page.evaluate(() => ({ type: files.start_image.type, size: files.start_image.size, error: document.querySelector("#error").textContent }));
  check("E1 ultimo frame capturado como PNG", frame.type === "image/png" && frame.size > 5000 && !frame.error, JSON.stringify(frame));

  // ---- F. modo Inicio + Fim so libera quando o 14B termina de baixar
  await page.click('[data-key="mode"] [data-value="flf"]');
  check("F1 sem 14B: aviso e botao travado", await page.isVisible("#mode-note") && await page.isDisabled("#submit"), await text("#mode-note"));
  await fetch("http://127.0.0.1:8188/_install", { method: "POST", body: JSON.stringify({ set: "14b_i2v" }) });
  await page.waitForFunction(() => document.querySelector("#mode-note").hidden, null, { timeout: 40000 });
  check("F2 14B instalado: liberou sozinho e escolheu o 14B", (await page.getAttribute('[data-key="model"] [data-value="14b"]', "aria-pressed")) === "true"
    && await page.isDisabled('[data-key="model"] [data-value="5b"]'));
  await page.setInputFiles('[data-frame="start_image"] input', `${TESTS}/start.png`);
  await page.setInputFiles('[data-frame="end_image"] input', `${TESTS}/sample.png`);
  await page.fill("#prompt", "transição suave entre as duas cenas");
  await shot("06-inicio-fim");
  await page.click("#submit");
  await page.waitForFunction(() => document.querySelectorAll(".card video").length === 4, null, { timeout: 90000 });
  check("F3 inicio + fim gerou no 14B turbo", (await text(".card-meta")).includes("Wan 2.2 14B turbo"), await text(".card-meta"));
  const graphs = Object.values(await (await fetch("http://127.0.0.1:8188/_graphs")).json());
  const last = Object.values(graphs[graphs.length - 1]).map((node) => node.class_type);
  check("F4 grafo usou GGUF + primeiro/ultimo frame", last.includes("UnetLoaderGGUF") && last.includes("WanFirstLastFrameToVideo"));

  // ---- G. visualizador e Reusar
  await page.locator(".card .card-media.open").first().click();
  await page.waitForSelector("#viewer[open] video");
  check("G1 visualizador abre com video e prompt final", (await text("#viewer-final")).includes("Enviado ao modelo: transição suave"), (await text("#viewer-final")).slice(0, 80));
  await sleep(600);
  await shot("07-visualizador");
  await page.evaluate(() => { setFrame("start_image", null); setFrame("end_image", null); });
  await page.click('#viewer-actions button:has-text("Reusar")');
  check("G2 Reusar restaura prompt, modo e imagens", (await page.inputValue("#prompt")) === "transição suave entre as duas cenas"
    && await page.evaluate(() => !document.querySelector("#viewer").open && state.mode === "flf" && files.start_image instanceof File && files.end_image instanceof File));

  // ---- H. dialogo com a GPU ligada
  await page.click("#status");
  await page.waitForSelector("#gpu[open] #gpu-facts dd");
  const facts = await text("#gpu-facts");
  check("H1 dialogo lista placa e modelos", facts.includes("Tesla T4 · 15 GB") && facts.includes("instalado · turbo") && facts.includes("não instalado"), facts.slice(0, 160));
  check("H2 mostra ociosidade e botao de desligar", await page.isVisible("#idle-note") && await page.isVisible("#shutdown") && await page.isVisible("#keep-alive"));
  await shot("08-dialogo-ligado");
  await page.click("#gpu [data-close]");

  // ---- I. recarregar: galeria vem do navegador e a conexao volta sozinha
  await page.reload();
  await page.waitForFunction(() => document.querySelectorAll(".card").length === 5, null, { timeout: 20000 });
  check("I1 galeria persiste apos recarregar (4 videos + 1 imagem)", (await count(".card video")) === 4 && (await count(".card img")) === 1);
  await page.waitForFunction(() => document.querySelector("#status").dataset.state === "ok", null, { timeout: 20000 });
  check("I2 reconectou sozinho", true);
  check("I3 lembra as escolhas do formulario", await page.evaluate(() => state.mode === "flf" && state.camera === "orbit"));
  await page.click('[data-key="filter"] [data-value="image"]');
  check("I4 filtro de imagens", (await count(".card")) === 1);
  await page.click('[data-key="filter"] [data-value="all"]');

  // ---- T. trocar pessoa (Wan 2.2 Animate): imagem + video, com o video preparado pelo ffmpeg
  await page.click('[data-key="mode"] [data-value="swap"]');
  check("T1 sem o Animate: aviso e botao travado", await page.isVisible("#mode-note") && await page.isDisabled("#submit"), await text("#mode-note"));
  await fetch("http://127.0.0.1:8188/_install", { method: "POST", body: JSON.stringify({ set: "animate" }) });
  await page.waitForFunction(() => document.querySelector("#mode-note").hidden, null, { timeout: 40000 });
  const swapUi = await page.evaluate(() => {
    const hidden = (selector) => document.querySelector(selector).hidden;
    return {
      semPresets: hidden("#presets-field"), semProporcao: hidden("#aspect-field"), semLote: hidden("#count-field"),
      semModelos: hidden('[data-key="model"]'), modeloFixo: !hidden("#swap-model"),
      ateUmMinuto: !hidden('[data-key="length"]') && hidden('[data-key="duration"]') && Boolean(document.querySelector('[data-key="length"] [data-value="60"]')),
      qualidade: document.querySelectorAll('[data-key="quality"] button:not([hidden])').length === 3 && state.quality === "boa",
      semUltraNaT4: document.querySelector('[data-key="quality"] [data-value="ultra"]').hidden,
      semTurboNemResolucao: hidden("#fast-row") && hidden("#resolution-field"), ampliar: !hidden("#hd-row"),
      estimativa: /cerca de \d+ min/.test(document.querySelector("#swap-estimate").textContent),
      inicio: !hidden("#start-field"), video: !hidden('[data-frame="video"]'), semFrameFinal: hidden('[data-frame="end_image"]'),
      titulo: document.querySelector('[data-frame="start_image"] .drop-title').textContent,
      botao: document.querySelector("#submit").textContent.trim(),
    };
  });
  check("T2 formulario do modo de troca", Object.entries(swapUi).every(([key, value]) => (key === "titulo" ? value === "Pessoa" : key === "botao" ? value === "Trocar pessoa" : value === true)), JSON.stringify(swapUi));
  await page.evaluate(() => { for (const name of Object.keys(files)) setFrame(name, null); });
  await page.click("#submit");
  check("T3 sem arquivos avisa", (await text("#error")).includes("Envie a imagem da pessoa e o vídeo"));
  await page.setInputFiles('[data-frame="start_image"] input', `${TESTS}/start.png`);
  await page.setInputFiles('[data-frame="video"] input', `${TESTS}/sample.mp4`);
  await page.waitForFunction(() => {
    const preview = document.querySelector('[data-frame="video"] video');
    return !preview.hidden && preview.readyState >= 1;
  });
  const estimates = [];
  for (const [length, quality] of [["5", "rapida"], ["60", "rapida"], ["60", "maxima"]]) {
    await page.click(`[data-key="length"] [data-value="${length}"]`);
    await page.click(`[data-key="quality"] [data-value="${quality}"]`);
    estimates.push(Number((await text("#swap-estimate")).match(/cerca de (\d+) min/)[1]));
  }
  check("T2b a estimativa de tempo cresce com a duracao e com a qualidade", estimates[0] < estimates[1] && estimates[1] < estimates[2], estimates.join(" < "));
  // Uma escolha guardada numa sessao com placa grande nao pode sobrar numa T4.
  const fallback = await page.evaluate(() => { choose("quality", "ultra"); refreshForm(); return state.quality; });
  check("T2c qualidade ultra guardada volta para a maxima numa placa de 16 GB", fallback === "maxima"
    && (await text("#swap-estimate")).startsWith("Tempo estimado na GPU gratuita"), fallback);
  await page.click('[data-key="length"] [data-value="10"]');
  await page.click('[data-key="quality"] [data-value="boa"]');
  await page.fill("#prompt", "");
  await shot("06b-trocar-pessoa");
  await page.click("#submit");
  await page.waitForFunction(() => /Analisando o vídeo…|Recortando a pessoa…/.test(document.querySelector("#gallery").textContent), null, { timeout: 40000 });
  check("T4 mostra as etapas do pre-processamento", true);
  await page.evaluate(() => window.scrollTo(0, 0));
  await shot("06c-etapas");
  await page.waitForFunction(() => document.querySelectorAll(".card video").length === 5, null, { timeout: 120000 });
  const swapMeta = await text(".card-meta");
  check("T5 troca gerada com o tamanho e a duracao do video", /Wan 2\.2 Animate · qualidade boa · 832×480 · 2\.8 s · pessoa trocada/.test(swapMeta)
    && (await text(".card-prompt")) === "Troca de pessoa", swapMeta);
  // O ComfyUI lista no historico tambem a previa do video enviado (um padrao de teste
  // colorido). O que tem de chegar a galeria sao os quadros gerados (aqui, verdes), emendados.
  const pixelOf = (selector) => page.evaluate(async (selector) => {
    const video = document.querySelector(selector);
    if (video.readyState < 1) await new Promise((resolve) => video.addEventListener("loadedmetadata", resolve, { once: true }));
    video.currentTime = Math.min(1, video.duration / 2);
    await new Promise((resolve) => video.addEventListener("seeked", resolve, { once: true }));
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 8;
    const context = canvas.getContext("2d");
    context.drawImage(video, 0, 0, 8, 8);
    const [red, green, blue] = context.getImageData(4, 4, 1, 1).data;
    return { red, green, blue, duracao: Number(video.duration.toFixed(2)) };
  }, selector);
  const swapResult = await pixelOf(".card video");
  check("T5b o resultado sao os quadros gerados (45, a 16 fps), nao o video enviado",
    swapResult.green > swapResult.red + 40 && swapResult.green > swapResult.blue + 40 && Math.abs(swapResult.duracao - 45 / 16) < 0.1, JSON.stringify(swapResult));
  const graphsOf = async (port) => Object.values(await (await fetch(`http://127.0.0.1:${port}/_graphs`)).json()).map((graph) => Object.values(graph));
  const lastGraph = async () => (await graphsOf(8188)).pop();
  let swapNodes = await lastGraph();
  let swapTypes = swapNodes.map((node) => node.class_type);
  const animate = swapNodes.find((node) => node.class_type === "WanAnimateToVideo").inputs;
  check("T6 grafo: um pedaco de 45 quadros (todos os 16 por segundo) com deteccao e SAM2, sem interpolacao, 4 passos, quadros em PNG", ["LoadVideo", "PoseAndFaceDetection", "WanStudioPersonMask", "ImageCompositeMasked", "SaveImage"].every((type) => swapTypes.includes(type))
    && !swapTypes.includes("SaveVideo") && !swapTypes.includes("FrameInterpolate") && animate.length === 45 && animate.width === 832 && animate.height === 480 && !("continue_motion" in animate)
    && swapNodes.find((node) => node.class_type === "KSampler").inputs.steps === 4, `length=${animate.length} ${animate.width}x${animate.height}`);
  await page.uncheck("#replace");
  await page.click("#submit");
  await page.waitForFunction(() => document.querySelectorAll(".card video").length === 6, null, { timeout: 120000 });
  swapTypes = (await lastGraph()).map((node) => node.class_type);
  check("T7 sem manter o cenario: sem SAM2 e a descricao muda", !swapTypes.includes("WanStudioPersonMask") && swapTypes.includes("PoseAndFaceDetection")
    && (await text(".card-meta")).includes("movimento copiado"), await text(".card-meta"));
  await page.evaluate(() => { for (const name of Object.keys(files)) setFrame(name, null); document.querySelector("#replace").checked = true; });
  await page.click('[data-key="mode"] [data-value="t2v"]');
  await page.locator(".card").first().locator('button:has-text("Reusar")').click();
  check("T8 Reusar restaura modo, pessoa, video e opcoes", await page.evaluate(() => state.mode === "swap" && files.start_image instanceof File
    && files.video instanceof File && !document.querySelector("#replace").checked && state.length === "5" && state.quality === "boa"));

  // ---- T9. video longo: pedacos encadeados do comeco para o fim, um de cada vez
  const graphsBefore = [(await graphsOf(8188)).length, (await graphsOf(8189)).length];
  await page.check("#replace");
  await page.setInputFiles('[data-frame="video"] input', `${TESTS}/long.mp4`);
  await page.waitForFunction(() => {
    const preview = document.querySelector('[data-frame="video"] video');
    return !preview.hidden && preview.readyState >= 1 && preview.duration > 9;
  });
  await page.click('[data-key="length"] [data-value="10"]');
  await page.click('[data-key="quality"] [data-value="rapida"]');
  await page.click("#submit");
  await page.waitForFunction(() => /pedaço \d de 3/.test(document.querySelector("#gallery").textContent), null, { timeout: 60000 });
  const longWait = (await text("#gallery")).match(/Gerando… \d+% · pedaço \d de 3[^W]*/)[0].slice(0, 60);
  await page.evaluate(() => window.scrollTo(0, 0));
  await shot("06d-video-longo");
  await page.waitForFunction(() => document.querySelectorAll(".card video").length === 7, null, { timeout: 180000 });
  const longMeta = await text(".card-meta");
  const longResult = await pixelOf(".card video");
  const graphsAfter = [await graphsOf(8188), await graphsOf(8189)];
  const longPieces = [...graphsAfter[0].slice(graphsBefore[0]), ...graphsAfter[1].slice(graphsBefore[1])]
    .map((graph) => graph.find((node) => node.class_type === "WanAnimateToVideo").inputs);
  check("T9 troca de 10 s: 3 pedacos em fila (77, 77 e 17 quadros), um de partida e 2 continuando, emendados em 10 s",
    /qualidade rápida · 832×480 · 10 s · pessoa trocada/.test(longMeta) && Math.abs(longResult.duracao - 10) < 0.1 && longPieces.length === 3
    && longPieces.map((piece) => piece.length).sort((a, b) => a - b).join() === "17,77,77"
    && longPieces.filter((piece) => "continue_motion" in piece).length === 2 && longPieces.every((piece) => piece.width === 640 && piece.height === 368),
    `${longMeta} | ${longWait} | ${graphsAfter[0].length - graphsBefore[0]} + ${graphsAfter[1].length - graphsBefore[1]} pedacos`);

  // ---- J. descoberta automatica: sem a conexao salva, acha a sessao pelo anuncio cifrado
  await page.evaluate(() => localStorage.removeItem("wan-conn"));
  await page.reload();
  // No modo "Trocar pessoa" (que ficou selecionado) os presets existem, mas ficam ocultos.
  await page.waitForSelector("#cameras .camera", { state: "attached" });
  let discovered = true;
  try {
    await page.waitForFunction(() => document.querySelector("#status").dataset.state === "ok", null, { timeout: 45000 });
  } catch { discovered = false; }
  check("J1 conectou sozinho pelo anuncio (ntfy.sh real)", discovered, await text("#status"));

  // ---- K. excluir pede confirmacao
  const before = await count(".card");
  const del = page.locator(".card").last().locator("button.danger");
  await del.click();
  check("K1 primeiro clique so arma", (await del.textContent()) === "Confirmar?" && (await count(".card")) === before);
  await del.click();
  await page.waitForFunction((n) => document.querySelectorAll(".card").length === n - 1, before);
  check("K2 segundo clique exclui", true);

  // ---- L. desligar pelo site
  if (discovered) {
    await page.click("#status");
    await page.waitForSelector("#gpu[open] #shutdown");
    await page.click("#shutdown");
    check("L1 desligar pede confirmacao", (await text("#shutdown")).includes("Confirmar"));
    await page.click("#shutdown");
    await page.waitForFunction(() => document.querySelector("#status").textContent.includes("GPU desligada"), null, { timeout: 60000 });
    check("L2 site percebe a GPU desligada", true);
    const still = await page.evaluate(async () => {
      const video = document.querySelector(".card video");
      await video.play();
      return { cards: document.querySelectorAll(".card").length, playing: !video.paused, submit: document.querySelector("#submit").textContent };
    });
    check("L3 galeria continua e o video toca com a GPU desligada", still.cards === before - 1 && still.playing && still.submit.includes("Ligar a GPU"), JSON.stringify(still));
    await page.click("#status");
    await page.waitForSelector("#gpu[open]");
    const offDialog = await page.evaluate(() => ({
      atalho: document.querySelector("#gpu-off .steps").textContent.includes("Ligar GPU"),
      manualFechado: !document.querySelector("#manual").open,
      semPassoDeCodigo: document.querySelector("#step-code").hidden && !document.querySelector("#step-paired").hidden,
    }));
    check("L4 pareado: o dialogo aponta o atalho e recolhe o caminho manual", Object.values(offDialog).every(Boolean), JSON.stringify(offDialog));
    await shot("09-dialogo-pareado");
    await page.click("#gpu [data-close]");
    await shot("10-desligado-com-galeria");
  }

  // ---- M. celular
  await page.setViewportSize({ width: 390, height: 844 });
  await sleep(400);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  check("M1 sem rolagem horizontal no celular", overflow <= 0, `sobra ${overflow}px`);
  await shot("11-celular", { fullPage: true });

  const unexpected = errors.filter((entry) => !/ERR_CONNECTION_REFUSED|Failed to load resource|net::ERR_/.test(entry));
  check("Z sem erros de JavaScript", unexpected.length === 0, unexpected.slice(0, 3).join(" | "));

  await context.close();
  console.log(`\n${failures.length} falha(s)` + (failures.length ? ": " + failures.join("; ") : ""));
  process.exit(failures.length ? 1 : 0);
})().catch((error) => {
  console.error("ERRO NO TESTE:", error.message.split("\n").slice(0, 6).join("\n"));
  if (page) page.screenshot({ path: `${SHOTS}/zz-erro.png` }).catch(() => {}).finally(() => process.exit(2));
  else process.exit(2);
});
