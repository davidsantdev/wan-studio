"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

// O site estatico (hospedado ou aberto do disco) define WAN_SITE. Servida pelo servidor da
// GPU, a pagina fala com a API na mesma origem e nao ha o que conectar.
const SITE = window.WAN_SITE || null;
const NTFY = (SITE && SITE.ntfy) || "https://ntfy.sh";
const FETCH_TIMEOUT = 12000;
const UPLOAD_TIMEOUT = 120000;
const VIDEO_UPLOAD_TIMEOUT = 600000;
const MAX_VIDEO_BYTES = 100 * 1024 * 1024;   // mesmo teto do servidor e do tunel
const MAX_STORED_VIDEO = 25 * 1024 * 1024;   // acima disso o video de origem nao e guardado
const DOWNLOAD_TIMEOUT = 300000;
const STATUS_EVERY = 10000;
const MAX_COLLECT_TRIES = 5;

const ACTIVE = ["preparing", "queued", "running"];
const DEFAULTS = {
  mode: "swap", camera: "none", effect: "none", style: "none", model: "5b", aspect: "16:9",
  resolution: "480p", duration: "5", count: "1", tab: "camera", filter: "all",
  quality: "boa", length: "5",  // troca de pessoa: qualidade e duracao do trecho
};
const SET_NAMES = {
  "5b": "5B", animate: "Animate · trocar pessoa", "14b_i2v": "14B · imagem → vídeo", "14b_t2v": "14B · texto → vídeo",
};
const MODEL_NAMES = { "5b": "5B", "14b": "14B", animate: "Animate" };
// Os modos que usam o Animate: os unicos de uma sessao so de troca de pessoa.
const ANIMATE_MODES = ["swap", "face"];
const ENHANCER_TEXT = {
  ready: "pronto", waiting: "baixando o modelo", loading: "carregando", off: "indisponível", error: "falhou",
  sleeping: "em pausa enquanto a segunda GPU gera vídeo",
};
const QUALITY_NAMES = { rapida: "rápida", boa: "boa", maxima: "máxima", ultra: "ultra" };
// Troca de pessoa, para a estimativa de tempo. Por qualidade: segundos por pedaco numa T4
// (analise do video + passos + gravacao). "boa" e "rapida" foram medidas na GPU do Kaggle em
// 12/10/2026 (10 s em 26,6 e 11,6 min, dois pedacos de 77 quadros); a "maxima" e a medida
// antiga do pedaco de 49 quadros; a "ultra" nao roda na T4, e o numero dela e uma conta.
const SWAP_CHUNK_SECONDS = { rapida: 350, boa: 800, maxima: 900, ultra: 2800 };
const SWAP_FPS = 16;  // todos os quadros sao gerados
// A alta definicao (restauracao de cada pedaco) leva isto a mais por pedaco de 49 quadros:
// medido em 12/10/2026 num video em pe (720x1248), uns 480 s com a outra GPU ocupada; num
// quadrado (720x720), 220 s.
const SWAP_HD_SECONDS = 400;
const SWAP_CHUNK = 77, SWAP_OVERLAP = 5;
const SWAP_CHUNKS = { maxima: 49 };  // quadros por pedaco quando nao sao os 77 de costume
// A "ultra" so aparece em placas com pelo menos isto de memoria (GB); o servidor confere.
const ULTRA_MIN_VRAM = 20;
// Quantas vezes cada placa e mais rapida que a T4 nesta tarefa. Sao estimativas pela ficha
// tecnica, ainda sem medida: o site avisa disso ao mostrar o tempo. A ordem importa ("L40S"
// contem "L4").
const GPU_SPEED = [["B200", 14], ["H200", 10], ["H100", 9], ["RTX PRO 6000", 9], ["A100", 5.5], ["L40S", 5.5],
                   ["A10G", 2.3], ["L4", 1.9]];

// Icone que indica o movimento com a miniatura parada (tracos 24x24, do conjunto Feather).
const ICONS = {
  left: "M19 12H5M12 19l-7-7 7-7",
  right: "M5 12h14M12 5l7 7-7 7",
  up: "M12 19V5M5 12l7-7 7 7",
  down: "M12 5v14M19 12l-7 7-7-7",
  "zoom-in": "M19 11a8 8 0 1 1-16 0 8 8 0 0 1 16 0M21 21l-4.35-4.35M11 8v6M8 11h6",
  "zoom-out": "M19 11a8 8 0 1 1-16 0 8 8 0 0 1 16 0M21 21l-4.35-4.35M8 11h6",
  rotate: "M23 4v6h-6M20.49 15a9 9 0 1 1-2.12-9.36L23 10",
  refresh: "M23 4v6h-6M1 20v-6h6M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15",
  shake: "M22 12h-4l-3 9L9 3l-3 9H2",
  fast: "M13 17l5-5-5-5M6 17l5-5-5-5",
  frame: "M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3",
  focus: "M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0M22 12h-4M6 12H2M12 6V2M12 22v-4",
  clock: "M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0M12 6v6l4 2",
  eye: "M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8zM15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0",
};
const ANIM_ICON = {
  static: "frame", "zoom-in": "zoom-in", "zoom-out": "zoom-out", crash: "fast", "crash-out": "fast",
  "pan-left": "left", "pan-right": "right", "tilt-up": "up", "tilt-down": "down",
  "truck-left": "left", "truck-right": "right", orbit: "rotate", arc: "rotate", "crane-up": "up",
  "crane-down": "down", overhead: "down", "low-angle": "up", shake: "shake", snorricam: "shake",
  fpv: "fast", aerial: "up", through: "zoom-in", vertigo: "focus", bullet: "rotate", whip: "fast",
  roll: "refresh", spin: "refresh", macro: "zoom-in", focus: "focus", fisheye: "eye", slow: "clock",
  timelapse: "clock",
};

function animIcon(anim) {
  if (!ANIM_ICON[anim]) return null;
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("class", anim === "crash-out" ? "thumb-icon flip" : "thumb-icon");
  const path = document.createElementNS(svg.namespaceURI, "path");
  path.setAttribute("d", ICONS[ANIM_ICON[anim]]);
  svg.append(path);
  return svg;
}

function el(tag, props = {}, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children.filter((child) => child !== null && child !== undefined && child !== ""));
  return node;
}

// localStorage pode faltar (janela privada, dados bloqueados): tudo aqui e conveniencia.
const store = {
  get(key, fallback = null) {
    try {
      const raw = localStorage.getItem("wan-" + key);
      return raw === null ? fallback : JSON.parse(raw);
    } catch { return fallback; }
  },
  set(key, value) {
    try {
      if (value === null) localStorage.removeItem("wan-" + key);
      else localStorage.setItem("wan-" + key, JSON.stringify(value));
    } catch { /* sem armazenamento, segue sem lembrar */ }
  },
};

const state = { ...DEFAULTS, ...store.get("settings", {}) };
// As qualidades da troca de pessoa mudaram em 12/10/2026 (a "rapida" de antes saia com
// defeito e era a padrao): quem tinha uma escolha guardada volta, uma vez, para a padrao.
if (store.get("qualities", 1) < 2) {
  state.quality = DEFAULTS.quality;
  store.set("qualities", 2);
}
// O que a sessao da GPU oferece: "all", o estudio inteiro (a sessao de costume), ou "swap", so a
// troca de pessoa (notebook gerado com --so-troca). Fica guardado, para a pagina abrir do mesmo
// jeito com a GPU desligada.
let focus = store.get("focus", "all");
const onlySwap = () => focus === "swap";
const files = { start_image: null, end_image: null, video: null };
let presets = { cameras: [], effects: [], styles: [] };
let conn = store.get("conn");        // { url, password } da sessao atual da GPU
let pairing = store.get("pairing");  // { topic, key }: como achar a sessao sem colar codigo
let status = null;                   // ultima resposta de /api/status; null = sem resposta
let failures = 0;
let submitting = false;

const reachable = () => Boolean(conn) || !SITE;
const online = () => Boolean(status && status.comfy);

// ---------------------------------------------------------------- conexao

function bytesFromB64(text) {
  const binary = atob(String(text).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(binary, (char) => char.charCodeAt(0));
}

/** Aceita o codigo puro ou um link que o contenha (…#c=CODIGO). */
function parseCode(text) {
  let code = String(text).trim();
  const inLink = code.match(/[#&?]c=([\w-]+)/);
  if (inLink) code = inLink[1];
  try {
    const data = JSON.parse(new TextDecoder().decode(bytesFromB64(code)));
    return hasSession(data) || (data.t && data.k) ? data : null;
  } catch { return null; }
}

const hasSession = (data) => /^https?:\/\//.test(data.u || "");

/** Guarda o que o codigo trouxer: a sessao atual (u, p), o pareamento (t, k) ou os dois.
 *  O atalho "Ligar GPU" manda so o pareamento e a hora em que ligou (s); o da GPU rapida
 *  manda tambem o endereco e a chave com que esta pagina a liga sozinha (w, wk). */
function connect(data) {
  if (data.t && data.k) {
    // Quem pareou pela GPU rapida e depois liga o Kaggle continua podendo ligar a rapida.
    const kept = pairing && pairing.topic === data.t && pairing.wake ? { wake: pairing.wake, wakeKey: pairing.wakeKey } : {};
    pairing = { topic: data.t, key: data.k, ...kept };
    if (/^https:\/\//.test(data.w || "") && data.wk) Object.assign(pairing, { wake: data.w, wakeKey: data.wk });
    store.set("pairing", pairing);
  }
  if (hasSession(data)) {
    conn = { url: data.u.replace(/\/+$/, ""), password: data.p || "" };
    store.set("conn", conn);
    failures = 0;
  }
  if (data.s) store.set("starting", Number(data.s));
}

// Segundos em que "a GPU esta ligando" ainda faz sentido. A instalacao leva uns 5 minutos, mas
// antes dela o Kaggle pode segurar a sessao numa fila: num teste real foram 35.
const STARTING_WINDOW = 60 * 60;

/** A GPU foi ligada ha pouco pelo atalho e ainda nao apareceu? */
function starting() {
  const since = store.get("starting");
  return Boolean(since) && !online() && Date.now() / 1000 - since < STARTING_WINDOW;
}

function api(path, options = {}) {
  const headers = new Headers(options.headers);
  if (conn && conn.password) headers.set("Authorization", "Basic " + btoa("wan:" + conn.password));
  const signal = AbortSignal.timeout(options.timeout || FETCH_TIMEOUT);
  return fetch((conn ? conn.url : "") + path, { ...options, headers, signal });
}

async function decrypt(message, keyText) {
  const raw = bytesFromB64(message);
  const key = await crypto.subtle.importKey("raw", bytesFromB64(keyText), "AES-GCM", false, ["decrypt"]);
  const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv: raw.slice(0, 12) }, key, raw.slice(12));
  return JSON.parse(new TextDecoder().decode(plain));
}

const DISCOVER_EVERY = 20000;
const DISCOVER_STARTING = 4000;  // com a GPU ligando, o anuncio pode chegar a qualquer momento
const WAKE_TIMEOUT = 60000;
let discovering = false;
let lastDiscover = 0;
let waking = false;

/** O notebook anuncia o endereco da sessao, cifrado, num topico que so este navegador conhece. */
async function discover(force = false) {
  if (!pairing || discovering || document.hidden) return;
  if (!force && Date.now() - lastDiscover < (starting() ? DISCOVER_STARTING : DISCOVER_EVERY)) return;
  lastDiscover = Date.now();
  discovering = true;
  try {
    const response = await fetch(`${NTFY}/${pairing.topic}/json?poll=1&since=20m`,
                                 { signal: AbortSignal.timeout(FETCH_TIMEOUT) });
    const messages = (await response.text()).split("\n").filter(Boolean)
      .map((line) => JSON.parse(line)).filter((entry) => entry.event === "message");
    if (!messages.length) return;
    const data = await decrypt(messages[messages.length - 1].message, pairing.key);
    if (data.off || !/^https?:\/\//.test(data.u)) return;
    if (!conn || conn.url !== data.u.replace(/\/+$/, "") || conn.password !== data.p) {
      connect(data);
      await refreshStatus();
      pollJobs();
    }
  } catch { /* sem anuncio legivel: continua desligado */ } finally {
    discovering = false;
  }
}

/** Liga a GPU rapida daqui: chama o endereco fixo que o atalho deixou no pareamento. A sessao
 *  sobe no Modal e se anuncia como qualquer outra; esta pagina a encontra pela descoberta. */
async function wake() {
  if (waking || !pairing || !pairing.wake) return;
  const error = $("#wake-error");
  error.hidden = true;
  waking = true;
  renderGpu();
  try {
    const response = await fetch(pairing.wake, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: pairing.wakeKey }), signal: AbortSignal.timeout(WAKE_TIMEOUT),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(response.status === 403
        ? "Este navegador não está mais autorizado a ligar a GPU. Rode de novo o atalho Ligar GPU rápida."
        : `O Modal não ligou a GPU: ${data.detail || "erro " + response.status}`);
    }
    store.set("starting", Math.floor(Date.now() / 1000));
    lastDiscover = 0;
    renderStatus();
    refreshForm();
    discover(true);
  } catch (failure) {
    error.hidden = false;
    error.textContent = failure.name === "TypeError" || failure.name === "TimeoutError"
      ? "Não consegui falar com o Modal. Confira a internet e tente de novo."
      : failure.message;
  } finally {
    waking = false;
    renderGpu();
  }
}

async function refreshStatus() {
  let next = null;
  if (reachable()) {
    try {
      const response = await api("/api/status");
      if (response.ok) next = await response.json();
    } catch { /* tratado abaixo como falha */ }
  }
  if (next) {
    failures = 0;
    status = next;
    if (next.comfy) store.set("starting", null);  // chegou: nao esta mais "ligando"
    if ((next.focus || "all") !== focus) {
      focus = next.focus || "all";
      store.set("focus", focus);
      render();  // os cartoes mudam: o que leva a outro modo some ou volta
    }
  } else if (++failures >= 2 || !status) {
    status = null;  // uma falha isolada nao derruba a tela; duas seguidas sim
  }
  renderStatus();
  refreshForm();
  if ($("#gpu").open) renderGpu();
  if (!status && SITE) discover();
}

function idleLeft() {
  const idle = status && status.idle;
  if (!idle || !idle.limit || idle.busy) return null;
  return idle.limit - idle.seconds;
}

function renderStatus() {
  const button = $("#status");
  let name = "down";
  let label = SITE ? "GPU desligada" : "Servidor fora do ar";
  let action = SITE ? "Ligar" : "";
  if (online()) {
    name = "ok";
    label = status.gpu || "GPU ligada";
    action = "";
    const left = idleLeft();
    if (left !== null && left < 600) label += ` · desliga em ${Math.max(1, Math.ceil(left / 60))} min`;
  } else if (status) {
    name = "warn";
    label = "GPU iniciando…";
    action = "";
  } else if (SITE && starting()) {
    name = "warn";
    label = "GPU ligando…";
    action = "";
  }
  button.dataset.state = name;
  button.replaceChildren(label, ...(action ? [el("span", { className: "status-action", textContent: action })] : []));
}

function fact(label, value, className = "") {
  return [el("dt", { textContent: label }), el("dd", { textContent: value, className })];
}

function renderGpu() {
  const on = online();
  $("#gpu-title").textContent = on ? "GPU ligada" : starting() ? "GPU ligando…" : "GPU desligada";
  $("#gpu-on").hidden = !on;
  $("#gpu-off").hidden = on;
  if (!on) {
    // Quem pareou pela GPU rapida a liga daqui, sem o atalho (e de qualquer aparelho).
    $("#wake-box").hidden = !(pairing && pairing.wake);
    $("#wake").disabled = waking || starting();
    $("#wake").textContent = waking ? "Pedindo a máquina…" : starting() ? "Ligando… 1 a 2 minutos" : "Ligar a GPU rápida";
    $("#step-paired").hidden = !pairing;
    $("#step-code").hidden = Boolean(pairing);
    // Quem ainda nao pareou precisa do caminho manual a vista; depois ele so atrapalha.
    if (!$("#gpu").open) $("#manual").open = !pairing;
    const notebook = store.get("notebook", "");
    $("#open-notebook").hidden = !notebook;
    $("#open-notebook").href = notebook || "#";
    if (document.activeElement !== $("#notebook-url")) $("#notebook-url").value = notebook;
    return;
  }

  const rows = fact("Placa", status.gpu || "—");
  if (onlySwap()) {  // so o que a troca de pessoa usa
    const swap = status.models.animate || {};
    rows.push(...fact("Troca de pessoa", swap.ok ? "pronta" : status.extras ? "baixando" : "não instalada", swap.ok ? "yes" : "no"));
    rows.push(...fact("Alta definição", swap.hd === "seedvr2" ? "restauração dos detalhes" : swap.hd ? "ampliação" : "indisponível",
                      swap.hd ? "yes" : "no"));
    rows.push(...fact("Movimento fluido", swap.rife ? "pronto" : "indisponível", swap.rife ? "yes" : "no"));
  } else {
    for (const [name, label] of Object.entries(SET_NAMES)) {
      const info = status.models[name];
      const ok = Boolean(info && info.ok);
      rows.push(...fact(`Wan 2.2 ${label}`, ok ? (info.turbo ? "instalado · turbo" : "instalado") : "não instalado",
                        ok ? "yes" : "no"));
    }
    const enhancer = status.enhancer;
    const detail = enhancer.state === "error" || enhancer.state === "off" ? enhancer.detail : "";
    rows.push(...fact("Melhorar prompt", ENHANCER_TEXT[enhancer.state] + (detail ? ` (${detail})` : ""),
                      enhancer.state === "ready" ? "yes" : "no"));
  }
  if (status.extras) rows.push(...fact("Baixando", status.extras));
  $("#gpu-facts").replaceChildren(...rows);

  const idle = status.idle;
  const note = $("#idle-note");
  note.hidden = !idle.limit;
  if (idle.limit) {
    const left = idleLeft();
    note.textContent = `Para poupar sua cota, a GPU desliga sozinha após ${Math.round(idle.limit / 60)} min sem uso.`
      + (left === null ? "" : ` Faltam ${Math.max(1, Math.ceil(left / 60))} min.`);
  }
  $("#keep-alive").hidden = !idle.limit;
  $("#shutdown").hidden = !status.managed;
}

function openGpu() {
  if (!SITE && !online()) return;  // sem GPU e sem site estatico nao ha o que mostrar
  renderGpu();
  if (!$("#gpu").open) $("#gpu").showModal();
}

async function tryConnect() {
  const data = parseCode($("#connect-code").value);
  const error = $("#connect-error");
  error.hidden = Boolean(data);
  if (!data) {
    error.textContent = "Código inválido. Copie de novo a linha inteira que o notebook mostrou.";
    return;
  }
  connect(data);
  $("#connect-code").value = "";
  if (!hasSession(data)) {  // so o pareamento: nada a testar agora, a descoberta cuida do resto
    renderGpu();
    lastDiscover = 0;
    discover();
    return;
  }
  status = null;
  await refreshStatus();
  if (online() || status) {
    $("#gpu").close();
    pollJobs();
  } else {
    error.hidden = false;
    error.textContent = "Código aceito, mas a GPU não respondeu. Confira se o notebook ainda está rodando.";
  }
}

// ---------------------------------------------------------------- galeria no navegador

const library = new Map();  // id -> { id, job, blob, inputs, url }
let dbPromise = null;

function openDb() {
  dbPromise ??= new Promise((resolve, reject) => {
    const request = indexedDB.open("wan-studio", 1);
    request.onupgradeneeded = () => request.result.createObjectStore("items", { keyPath: "id" });
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  return dbPromise;
}

async function db(mode, action) {
  const database = await openDb();
  return new Promise((resolve, reject) => {
    const tx = database.transaction("items", mode);
    const request = action(tx.objectStore("items"));
    tx.oncomplete = () => resolve(request.result);
    tx.onerror = tx.onabort = () => reject(tx.error);
  });
}

function remember(item) {
  library.set(item.id, { ...item, url: URL.createObjectURL(item.blob) });
}

async function loadLibrary() {
  try {
    for (const item of await db("readonly", (items) => items.getAll())) remember(item);
  } catch { /* sem IndexedDB a galeria vale so enquanto a aba estiver aberta */ }
}

let persistenceAsked = false;

function askPersistence() {
  if (persistenceAsked || !navigator.storage || !navigator.storage.persist) return;
  persistenceAsked = true;
  navigator.storage.persist().catch(() => {});
}

function renderGalleryNote() {
  const count = library.size;
  if (!count) {
    $("#gallery-note").textContent = "O que você gerar fica guardado neste navegador, mesmo com a GPU desligada.";
    return;
  }
  let bytes = 0;
  for (const item of library.values()) bytes += item.blob.size;
  const size = bytes > 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : `${Math.max(1, Math.round(bytes / 1e6))} MB`;
  $("#gallery-note").textContent = `${count} ${count === 1 ? "item guardado" : "itens guardados"} neste navegador · ${size}`;
}

// ---------------------------------------------------------------- formulario

function syncChoices(key) {
  for (const group of $$(`[data-key="${key}"]`)) {
    const attr = group.getAttribute("role") === "tablist" ? "aria-selected" : "aria-pressed";
    for (const button of $$("button[data-value]", group)) {
      button.setAttribute(attr, String(button.dataset.value === state[key]));
    }
  }
}

function choose(key, value) {
  state[key] = value;
  store.set("settings", state);
  syncChoices(key);
}

function nameOf(list, id) {
  const item = list.find((entry) => entry.id === id);
  return item ? item.name : id;
}

function neededSet(mode, model) {
  if (ANIMATE_MODES.includes(mode)) return "animate";
  if (model === "5b") return "5b";
  return mode === "t2v" || mode === "img" ? "14b_t2v" : "14b_i2v";
}

function modelOk(mode, model) {
  if (model === "5b" && mode === "flf") return false;
  if (!online()) return true;  // sem GPU nao da para saber: nao bloqueia a escolha
  const info = status.models[neededSet(mode, model)];
  return Boolean(info && info.ok);
}

let undoText = null;       // texto anterior, enquanto "Desfazer melhoria" vale
let enhancedText = null;   // ultimo resultado do melhorador, para nao melhorar duas vezes
let enhancing = false;

function refreshEnhance() {
  const enhancer = online() ? status.enhancer : { state: "off" };
  // Na troca de pessoa o prompt e opcional e o video ja dita a cena.
  const visible = !ANIMATE_MODES.includes(state.mode) && ["ready", "waiting", "loading", "sleeping"].includes(enhancer.state);
  $("#enhance").hidden = !visible;
  $("#auto-row").hidden = !visible;
  if (!visible) return;
  const button = $("#enhance");
  button.disabled = false;
  if (enhancing) {
    button.textContent = "Melhorando…";
    button.disabled = true;
  } else if (undoText !== null) {
    button.textContent = "Desfazer melhoria";
  } else if (enhancer.state !== "ready") {
    button.textContent = `Melhorar com IA (${ENHANCER_TEXT[enhancer.state]})`;
    button.disabled = true;
  } else {
    button.textContent = "Melhorar com IA";
  }
}

/** Quanto a troca de pessoa deve levar, em minutos, com as escolhas atuais. Com duas GPUs o
 * video e gerado do meio para as pontas, dois pedacos por vez (veja app/stitch.py). */
function swapMinutes(quality = state.quality) {
  const frames = Number(state.length) * SWAP_FPS;
  // Quem ja gera em 720p (ultra) nao passa pela alta definicao.
  const hd = $("#hd").checked && Boolean(status && status.models.animate && status.models.animate.hd) && quality !== "ultra";
  // Com a alta definicao, numa placa pequena, os pedacos sao de 49 quadros (o servidor decide).
  const small = !(online() && (status.vram || 0) >= ULTRA_MIN_VRAM);
  const chunk = Math.min(SWAP_CHUNKS[quality] || SWAP_CHUNK, hd && small ? 49 : SWAP_CHUNK);
  const stride = chunk - SWAP_OVERLAP;
  // Os pedacos saem um depois do outro, cada um continuando do anterior: o tempo acompanha
  // os quadros a gerar (um pedaco pequeno ainda custa uns 45% de um inteiro).
  const pieces = frames <= chunk ? Math.max(0.45, frames / chunk) : (frames - SWAP_OVERLAP) / stride;
  // O tempo de um pedaco menor que o de costume cai com os quadros dele, menos a parte fixa.
  const usual = SWAP_CHUNKS[quality] || SWAP_CHUNK;
  let seconds = SWAP_CHUNK_SECONDS[quality] * (0.25 + 0.75 * chunk / usual) * pieces;
  if (hd) {
    // A restauracao vem depois, e nela as GPUs trabalham ao mesmo tempo (o Kaggle tem duas).
    const workers = status ? status.workers || 1 : 2;
    seconds += SWAP_HD_SECONDS * (chunk / 49) * pieces / workers;
  }
  return Math.max(1, Math.round(seconds / gpuSpeed() / 60));
}

/** "~14 min" ou "~1 h 20": o tempo estimado, curto o bastante para caber num botao. */
function shortTime(minutes) {
  return minutes < 60 ? `~${minutes} min` : `~${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, "0")}`;
}

/** Quantas vezes a placa ligada e mais rapida que a T4 (1 para a T4 ou sem conexao). */
function gpuSpeed() {
  const name = (online() && status.gpu || "").toUpperCase();
  const found = GPU_SPEED.find(([model]) => name.includes(model));
  return found ? found[1] : 1;
}

function refreshForm() {
  // Numa sessao so de troca de pessoa so as duas trocas aparecem.
  for (const tab of $$('[data-key="mode"] button')) tab.hidden = onlySwap() && !ANIMATE_MODES.includes(tab.dataset.value);
  if (onlySwap() && !ANIMATE_MODES.includes(state.mode)) choose("mode", "swap");
  const mode = state.mode;
  const image = mode === "img";
  const swap = mode === "swap";
  const face = mode === "face";
  $("#form").classList.toggle("swap", swap);  // a ordem dos campos muda (style.css)

  let ready;
  if (swap || face) {  // modelo fixo: o Animate
    ready = !online() || Boolean(status.models.animate && status.models.animate.ok);
  } else {
    const usable = ["5b", "14b"].filter((model) => modelOk(mode, model));
    if (usable.length && !usable.includes(state.model)) choose("model", usable[0]);
    for (const button of $$('[data-key="model"] button')) button.disabled = !usable.includes(button.dataset.value);
    ready = usable.length > 0;
  }

  const note = $("#mode-note");
  note.hidden = ready;
  if (!ready) {
    note.textContent = status && status.extras
      ? `Os modelos deste modo ainda estão sendo baixados (${status.extras}).`
      : "Os modelos deste modo não estão instalados nesta GPU.";
  }

  const info = online() ? status.models[neededSet(mode, state.model)] : null;
  // Imagem nao tem turbo: o servidor usa sempre os passos completos (veja workflows.resolve).
  // A troca de pessoa tem a propria escolha de qualidade, sempre com a LoRA de poucos passos.
  $("#fast-row").hidden = image || swap || face || (Boolean(info) && !info.turbo);
  $("#fast-label").textContent = state.model === "5b"
    ? "Turbo: 3 passos em vez de 20, bem mais rápido (desmarque se o resultado sair estranho)"
    : "Turbo: 4 passos em vez de 20, bem mais rápido";

  $("#frames").hidden = !(mode === "i2v" || mode === "flf" || swap || face);
  $('[data-frame="end_image"]').hidden = !(mode === "flf" || face);
  $('[data-frame="video"]').hidden = !swap;
  $('[data-frame="start_image"] .drop-title').textContent = swap ? "Pessoa" : face ? "Rosto" : "Frame inicial";
  $('[data-frame="end_image"] .drop-title').textContent = face ? "Pessoa que recebe o rosto" : "Frame final";
  $("#swap-options").hidden = !swap;
  $("#swap-note").hidden = !swap;
  $("#face-options").hidden = !face;
  $("#prompt").placeholder = swap || face
    ? "Opcional: descreva a pessoa ou a cena…"
    : "Descreva a cena: quem aparece, onde, o que acontece, a luz…";

  // Na troca, tamanho e proporcao vem do video, a duracao vai ate 60 s e nao ha presets nem lote.
  $('[data-key="duration"]').hidden = swap;
  $('[data-key="length"]').hidden = !swap;
  $("#resolution-field").hidden = swap;
  // A "ultra" pede uma placa grande; sem ela o botao some e a escolha guardada volta atras.
  const big = online() && (status.vram || 0) >= ULTRA_MIN_VRAM;
  $('[data-key="quality"] [data-value="ultra"]').hidden = !big;
  if (online() && !big && state.quality === "ultra") choose("quality", "maxima");
  // Quem ja gera em 720p nao passa pela alta definicao (o arquivo nao ficaria maior).
  $("#hd-row").hidden = !(swap && info && info.hd) || state.quality === "ultra";
  $("#fluid-row").hidden = !(swap && info && info.rife);
  if (swap) {
    // Cada qualidade mostra quanto levaria com a duracao e a placa de agora.
    for (const button of $$('[data-key="quality"] button')) {
      const note = $("small", button);
      note.textContent = `${note.dataset.note} · ${shortTime(swapMinutes(button.dataset.value))}`;
    }
    const minutes = swapMinutes();
    // Numa maquina paga por tempo (a GPU rapida), o que o video gasta do credito.
    const cost = online() && status.price_hour ? ` Gasta uns US$ ${(minutes / 60 * status.price_hour).toFixed(2)} do crédito.` : "";
    $("#swap-estimate").textContent =
      (gpuSpeed() === 1 ? "Tempo estimado na GPU gratuita" : "Tempo estimado nesta placa (conta ainda não conferida nela)")
      + `: cerca de ${minutes} min.${cost} Pode deixar esta página aberta e voltar depois.`;
  } else {
    $("#swap-estimate").textContent = "";
  }
  $("#duration-field").hidden = image || face;
  $("#start-field").hidden = !swap;
  $("#count-field").hidden = swap;
  $("#aspect-field").hidden = swap || face;  // na troca de rosto o tamanho e o da segunda foto
  $("#resolution-field").hidden = swap || face;
  $("#presets-field").hidden = swap || face;
  $("#swap-model").hidden = !(swap || face);
  $("#swap-model small").textContent = face ? "troca de rosto em foto" : "troca de pessoa · 16 fps";
  $("#model-field").hidden = (swap || face) && onlySwap();  // um modelo so: nao ha o que mostrar
  $('[data-key="model"]').hidden = swap || face;

  // Imagem parada nao tem movimento de camera.
  const tab = image && state.tab === "camera" ? "style" : state.tab;
  $('[data-key="tab"] [data-value="camera"]').hidden = image;
  for (const button of $$('[data-key="tab"] button')) button.setAttribute("aria-selected", String(button.dataset.value === tab));
  $("#cameras").hidden = tab !== "camera";
  $("#effects").hidden = tab !== "effect";
  $("#styles").hidden = tab !== "style";
  for (const [key, list] of [["camera", presets.cameras], ["effect", presets.effects], ["style", presets.styles]]) {
    $(`[data-pick="${key}"]`).textContent = state[key] === "none" ? "" : nameOf(list, state[key]);
  }

  refreshEnhance();

  const submit = $("#submit");
  const count = Number(state.count);
  if (online()) {
    const noun = image ? ["imagem", "imagens"] : ["vídeo", "vídeos"];
    if (swap) submit.textContent = submitting ? "Enviando o vídeo…" : "Trocar pessoa";
    else if (face) submit.textContent = submitting ? "Enviando…" : count > 1 ? `Trocar rosto (${count} versões)` : "Trocar rosto";
    else submit.textContent = submitting ? "Enviando…" : count > 1 ? `Gerar ${count} ${noun[1]}` : `Gerar ${noun[0]}`;
    submit.disabled = submitting || !ready;
  } else if (status || (SITE && starting())) {
    submit.textContent = status ? "A GPU está iniciando…" : "A GPU está ligando…";
    submit.disabled = true;
  } else {
    submit.textContent = SITE ? "Ligar a GPU para gerar" : "GPU fora do ar";
    submit.disabled = !SITE;
  }
}

function renderPresets() {
  $("#cameras").replaceChildren(...presets.cameras.map((camera) => {
    const scene = el("div", { className: "scene" }, el("div", { className: "bg" }), el("div", { className: "subj" }));
    const thumb = el("div", { className: "thumb" }, scene, animIcon(camera.anim));
    thumb.dataset.anim = camera.anim;
    const button = el("button", { type: "button", className: "camera" }, thumb, camera.name);
    button.dataset.value = camera.id;
    return button;
  }));
  for (const [id, list] of [["#effects", presets.effects], ["#styles", presets.styles]]) {
    $(id).replaceChildren(...list.map((item) => {
      const button = el("button", { type: "button", className: "chip", textContent: item.name });
      button.dataset.value = item.id;
      return button;
    }));
  }
}

/** Descarta escolhas salvas que nao existem mais (preset removido, opcao renomeada). */
function sanitize() {
  for (const key of Object.keys(DEFAULTS)) {
    const value = CSS.escape(String(state[key]));
    if (!$(`[data-key="${key}"] button[data-value="${value}"]`)) state[key] = DEFAULTS[key];
    syncChoices(key);
  }
}

// ---------------------------------------------------------------- imagens de entrada

function nearestAspect(width, height) {
  const ratio = width / height;
  const options = { "16:9": 16 / 9, "9:16": 9 / 16, "1:1": 1 };
  return Object.keys(options).reduce((best, key) =>
    Math.abs(Math.log(options[key] / ratio)) < Math.abs(Math.log(options[best] / ratio)) ? key : best);
}

/** Guarda o arquivo de uma das entradas (imagens ou o video) e mostra a previa. */
function setFrame(name, file) {
  const drop = $(`[data-frame="${name}"]`);
  const preview = $("img, video", drop);
  const isVideo = preview.tagName === "VIDEO";
  files[name] = file;
  if (preview.dataset.url) URL.revokeObjectURL(preview.dataset.url);
  preview.hidden = $(".drop-clear", drop).hidden = !file;
  $(".drop-label", drop).hidden = Boolean(file);
  if (!file) {
    delete preview.dataset.url;
    preview.removeAttribute("src");
    if (isVideo) preview.load();
    $("input", drop).value = "";
    return;
  }
  // A proporcao acompanha a imagem inicial (na troca de pessoa, quem manda e o video).
  preview.onload = name === "start_image"
    ? () => { if (!ANIMATE_MODES.includes(state.mode)) choose("aspect", nearestAspect(preview.naturalWidth, preview.naturalHeight)); }
    : null;
  preview.dataset.url = URL.createObjectURL(file);
  preview.src = preview.dataset.url;
  if (isVideo) preview.play().catch(() => {});
  showError("");  // um aviso de arquivo faltando deixa de valer
}

function bindFrames() {
  for (const drop of $$(".drop")) {
    const name = drop.dataset.frame;
    const kind = name === "video" ? "video/" : "image/";
    const input = $("input", drop);
    input.addEventListener("change", () => setFrame(name, input.files[0] || null));
    $(".drop-clear", drop).addEventListener("click", (event) => {
      event.preventDefault();
      setFrame(name, null);
    });
    drop.addEventListener("dragover", (event) => {
      event.preventDefault();
      drop.classList.add("over");
    });
    drop.addEventListener("dragleave", () => drop.classList.remove("over"));
    drop.addEventListener("drop", (event) => {
      event.preventDefault();
      drop.classList.remove("over");
      const file = [...event.dataTransfer.files].find((entry) => entry.type.startsWith(kind));
      if (file) setFrame(name, file);
    });
  }
  document.addEventListener("paste", (event) => {
    if ($("#frames").hidden) return;
    const file = [...event.clipboardData.files].find((entry) => entry.type.startsWith("image/"));
    if (!file) return;
    event.preventDefault();
    setFrame((state.mode === "flf" || state.mode === "face") && files.start_image ? "end_image" : "start_image", file);
  });
}

// ---------------------------------------------------------------- gerar e melhorar

function showError(message) {
  $("#error").textContent = message || "";
  $("#error").hidden = !message;
}

function friendly(error) {
  // TypeError = rede; TimeoutError = sem resposta. Os demais sao mensagens do servidor.
  return error.name === "TypeError" || error.name === "TimeoutError"
    ? "Sem resposta da GPU. Confira se ela ainda está ligada."
    : error.message;
}

async function readError(response) {
  const data = await response.json().catch(() => ({}));
  return new Error(data.error || `erro ${response.status}`);
}

const pendingInputs = new Map();  // id do job -> arquivos enviados, para o "Reusar"

async function submit(event) {
  event.preventDefault();
  if (!online()) {
    openGpu();
    return;
  }
  showError("");
  const swap = state.mode === "swap";
  if (swap && !(files.start_image && files.video)) {
    showError("Envie a imagem da pessoa e o vídeo.");
    return;
  }
  if (state.mode === "face" && !(files.start_image && files.end_image)) {
    showError("Envie a foto do rosto e a foto da pessoa que vai recebê-lo.");
    return;
  }
  if (swap && files.video.size > MAX_VIDEO_BYTES) {
    showError("O vídeo passa de 100 MB. Corte um trecho menor e envie de novo.");
    return;
  }
  const prompt = $("#prompt").value.trim();
  const seed = $("#seed").value.trim();
  const params = {
    mode: state.mode, model: state.model, aspect: state.aspect, resolution: state.resolution,
    duration: Number(swap ? state.length : state.duration), quality: state.quality, count: Number(state.count),
    camera: state.mode === "img" ? "none" : state.camera, effect: state.effect, style: state.style,
    prompt, negative: $("#negative").value, fast: $("#fast").checked,
    seed: seed === "" ? null : Number(seed),
    auto_enhance: !$("#auto-row").hidden && $("#auto-enhance").checked && prompt !== enhancedText,
    replace: $("#replace").checked, start: Number($("#start").value) || 0,
    hd: !$("#hd-row").hidden && $("#hd").checked,
    fluid: !$("#fluid-row").hidden && $("#fluid").checked,
    hair: state.mode === "face" && $("#hair").checked,
  };
  const wanted = { i2v: ["start_image"], flf: ["start_image", "end_image"], swap: ["start_image", "video"],
                   face: ["start_image", "end_image"] }[state.mode] || [];
  const inputs = {};
  const body = new FormData();
  body.append("params", JSON.stringify(params));
  for (const name of wanted) {
    if (!files[name]) continue;
    body.append(name, files[name]);
    // Videos grandes nao sao guardados para o "Reusar": ocupariam a galeria do navegador.
    if (name !== "video" || files[name].size <= MAX_STORED_VIDEO) inputs[name] = files[name];
  }

  submitting = true;
  refreshForm();
  try {
    const response = await api("/api/generate", { method: "POST", body, timeout: swap ? VIDEO_UPLOAD_TIMEOUT : UPLOAD_TIMEOUT });
    if (!response.ok) throw await readError(response);
    for (const job of (await response.json()).jobs) pendingInputs.set(job.id, inputs);
    await pollJobs();
    if (window.matchMedia("(max-width: 860px)").matches) $(".gallery-wrap").scrollIntoView({ behavior: "smooth" });
  } catch (error) {
    showError(friendly(error));
  } finally {
    submitting = false;
    refreshForm();
  }
}

async function enhance() {
  const box = $("#prompt");
  if (undoText !== null) {
    box.value = undoText;
    undoText = enhancedText = null;
    refreshEnhance();
    return;
  }
  const text = box.value.trim();
  if (!text) {
    showError("Escreva um prompt primeiro.");
    return;
  }
  showError("");
  enhancing = true;
  refreshEnhance();
  try {
    const response = await api("/api/enhance", {
      method: "POST", timeout: UPLOAD_TIMEOUT, headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt: text, mode: state.mode, camera: state.mode === "img" ? "none" : state.camera }),
    });
    if (!response.ok) throw await readError(response);
    enhancedText = (await response.json()).prompt;
    undoText = text;
    box.value = enhancedText;
  } catch (error) {
    showError(friendly(error));
  } finally {
    enhancing = false;
    refreshEnhance();
  }
}

// ---------------------------------------------------------------- galeria

let liveJobs = [];
const cards = new Map();       // id -> { node, signature }
const collecting = new Set();
const collectTries = new Map();
const pace = new Map();        // id -> primeira leitura com progresso, para estimar o tempo
const baseTitle = document.title;

/** Baixa o arquivo pronto da GPU para a galeria do navegador e libera o disco de la. */
async function collect(job) {
  if (collecting.has(job.id) || (collectTries.get(job.id) || 0) >= MAX_COLLECT_TRIES) return;
  collecting.add(job.id);
  try {
    const response = await api(job.url, { timeout: DOWNLOAD_TIMEOUT });
    if (!response.ok) throw new Error(`download ${response.status}`);
    const item = { id: job.id, job: { ...job }, blob: await response.blob(), inputs: pendingInputs.get(job.id) || null };
    const saved = await db("readwrite", (items) => items.put(item)).then(() => true, () => false);
    remember(item);
    pendingInputs.delete(job.id);
    if (saved) {
      askPersistence();
      api(`/api/jobs/${job.id}`, { method: "DELETE" }).catch(() => {});
    }
    if (document.hidden) document.title = "✓ Pronto · " + baseTitle;
  } catch {
    collectTries.set(job.id, (collectTries.get(job.id) || 0) + 1);
  } finally {
    collecting.delete(job.id);
  }
  render();
  renderGalleryNote();
}

async function loadJobs() {
  let jobs = [];
  if (status) {
    try {
      const response = await api("/api/jobs");
      jobs = response.ok ? (await response.json()).jobs : liveJobs;
    } catch {
      jobs = liveJobs;  // falha passageira: mantem o que ja estava na tela
    }
  }
  liveJobs = jobs;
  for (const job of jobs) {
    if (job.status === "done" && !library.has(job.id)) collect(job);
  }
  render();
  return jobs.some((job) => ACTIVE.includes(job.status)) || collecting.size > 0;
}

let pollTimer = null;
let pollRun = 0;

/** Atualiza a galeria agora e reagenda: rapido enquanto ha job em andamento. Chamar de novo
 *  (ao enviar um job, ao conectar) antecipa a proxima leitura em vez de esperar a agendada. */
async function pollJobs() {
  const run = ++pollRun;
  clearTimeout(pollTimer);
  const busy = await loadJobs();
  if (run !== pollRun) return;  // uma chamada mais nova assumiu o ritmo
  pollTimer = setTimeout(pollJobs, busy ? 1500 : 5000);
}

function etaText(job) {
  const now = Date.now() / 1000;
  const first = pace.get(job.id);
  if (!first) {
    if (job.progress > 0) pace.set(job.id, { time: now, progress: job.progress });
    return "";
  }
  const gained = job.progress - first.progress;
  if (gained < 0.05) return "";
  const left = (now - first.time) * (1 - job.progress) / gained;
  return left < 60 ? "menos de 1 min" : `~${Math.round(left / 60)} min`;
}

function waitText(item) {
  if (item.status === "preparing") return "Preparando o prompt…";
  if (item.status === "done") {
    return (collectTries.get(item.id) || 0) >= MAX_COLLECT_TRIES
      ? "Não consegui baixar o arquivo da GPU."
      : "Salvando na galeria…";
  }
  if (item.status === "queued") {
    const ahead = liveJobs.filter((job) => ACTIVE.includes(job.status) && job.created < item.created).length;
    return ahead ? `Na fila · ${ahead} na frente` : "Na fila…";
  }
  // Etapas que nao sao amostragem (analisando o video, recortando a pessoa, finalizando).
  if (item.stage) return item.stage.charAt(0).toUpperCase() + item.stage.slice(1) + "…";
  const percent = Math.round(item.progress * 100);
  if (!percent) return "Carregando o modelo…";
  const eta = etaText(item);
  // Troca de pessoa longa: o video sai em pedacos.
  const pieces = item.chunks > 1 ? ` · pedaço ${Math.min(item.chunks, (item.chunks_done || 0) + 1)} de ${item.chunks}` : "";
  return `Gerando… ${percent}%${pieces}${eta ? " · " + eta : ""}`;
}

/** O que mostrar como titulo: o prompt, ou o nome do modo quando ele e opcional. */
function titleOf(job) {
  return job.prompt || (job.mode === "swap" ? "Troca de pessoa" : job.mode === "face" ? "Troca de rosto" : "");
}

function describe(job) {
  const swap = job.mode === "swap";
  const parts = [`Wan 2.2 ${MODEL_NAMES[job.model] || job.model}${job.fast && !swap && job.mode !== "face" ? " turbo" : ""}`];
  if (swap && job.quality) parts.push(`qualidade ${QUALITY_NAMES[job.quality] || job.quality}`);
  parts.push(`${job.width}×${job.height}`);
  if (job.kind !== "image") parts.push(`${job.duration} s`);
  if (job.mode === "swap") parts.push(job.replace === false ? "movimento copiado" : "pessoa trocada");
  if (job.mode === "face") parts.splice(1, 0, job.hair ? "troca de rosto com o cabelo" : "troca de rosto");
  if (job.hd) parts.push(job.hd_method === "seedvr2" ? "alta definição" : "ampliado");
  if (job.fluid) parts.push(`${job.out_fps} fps`);
  if (job.cost) parts.push(`≈ US$ ${job.cost.toFixed(2)}`);  // tempo de maquina, na GPU paga por tempo
  for (const [key, list] of [["camera", presets.cameras], ["effect", presets.effects], ["style", presets.styles]]) {
    if (job[key] && job[key] !== "none") parts.push(nameOf(list, job[key]));
  }
  parts.push(`seed ${job.seed}`);
  return parts.join(" · ");
}

function focusPrompt() {
  $("#viewer").close();
  const box = $("#prompt");
  box.scrollIntoView({ behavior: "smooth", block: "center" });
  box.focus();
  box.select();
}

function reuse(item) {
  $("#prompt").value = item.prompt;
  $("#seed").value = item.seed;
  $("#negative").value = item.negative || "";
  if (item.kind !== "image") $("#fast").checked = Boolean(item.fast);  // imagem nunca e turbo
  undoText = enhancedText = null;
  for (const key of ["mode", "camera", "effect", "style", "model", "aspect", "resolution"]) {
    if (item[key] !== undefined) state[key] = String(item[key]);
  }
  if (item.mode === "face") $("#hair").checked = Boolean(item.hair);
  if (item.mode === "swap") {
    // A duracao gravada e a real do trecho; volta para a menor opcao que a cobre.
    state.length = String([5, 10, 15, 30, 60].find((option) => option >= item.duration - 0.5) || 60);
    if (item.quality) state.quality = item.quality;
    $("#hd").checked = Boolean(item.hd);
    $("#fluid").checked = Boolean(item.fluid);
    $("#replace").checked = item.replace !== false;
    $("#start").value = item.start || "";
  } else if (item.duration) {
    state.duration = String(item.duration);
  }
  sanitize();
  store.set("settings", state);
  const stored = library.get(item.id);
  const inputs = (stored && stored.inputs) || pendingInputs.get(item.id);
  if (inputs) {
    for (const name of Object.keys(files)) setFrame(name, inputs[name] || null);
  }
  refreshForm();
  focusPrompt();
}

function once(target, event, timeout = 15000) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("tempo esgotado")), timeout);
    target.addEventListener(event, () => { clearTimeout(timer); resolve(); }, { once: true });
    target.addEventListener("error", () => { clearTimeout(timer); reject(new Error("não foi possível ler o vídeo")); }, { once: true });
  });
}

async function lastFrame(url) {
  const video = el("video", { muted: true, playsInline: true, preload: "auto" });
  video.src = url;
  await once(video, "loadeddata");
  video.currentTime = Math.max(0, video.duration - 0.04);
  await once(video, "seeked");
  const canvas = el("canvas", { width: video.videoWidth, height: video.videoHeight });
  canvas.getContext("2d").drawImage(video, 0, 0);
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
  if (!blob) throw new Error("não foi possível capturar o último frame");
  return blob;
}

/** Leva uma imagem (gerada, ou o ultimo frame de um video) para o modo Imagem → Video. */
async function startFrom(item, getBlob) {
  showError("");
  try {
    const blob = await getBlob();
    $("#prompt").value = item.prompt;
    undoText = enhancedText = null;
    state.mode = "i2v";
    for (const key of ["aspect", "resolution"]) state[key] = String(item[key]);
    sanitize();
    store.set("settings", state);
    setFrame("start_image", new File([blob], `${item.id}.png`, { type: "image/png" }));
    setFrame("end_image", null);
    refreshForm();
    focusPrompt();
  } catch (error) {
    $("#viewer").close();
    showError(error.message);
  }
}

async function remove(item) {
  if ($("#viewer").open && $("#viewer").dataset.id === item.id) $("#viewer").close();
  const stored = library.get(item.id);
  if (stored) {
    URL.revokeObjectURL(stored.url);
    library.delete(item.id);
    await db("readwrite", (items) => items.delete(item.id)).catch(() => {});
  }
  if (liveJobs.some((job) => job.id === item.id)) {
    liveJobs = liveJobs.filter((job) => job.id !== item.id);
    api(`/api/jobs/${item.id}`, { method: "DELETE" }).catch(() => {});
  }
  render();
  renderGalleryNote();
}

function actionButton(label, onClick, className = "") {
  const node = el("button", { type: "button", textContent: label, className });
  node.addEventListener("click", onClick);
  return node;
}

/** Excluir pede um segundo clique; cancelar um job em andamento nao. */
function deleteButton(item) {
  const cancel = !item.local && ACTIVE.includes(item.status);
  const label = cancel ? "Cancelar" : "Excluir";
  let armed = null;
  const node = actionButton(label, () => {
    if (cancel || armed) return remove(item);
    node.textContent = "Confirmar?";
    armed = setTimeout(() => {
      armed = null;
      node.textContent = label;
    }, 3000);
  }, "danger");
  return node;
}

function actionsFor(item) {
  const nodes = [];
  if (item.local) {
    const extension = item.kind === "image" ? "png" : "mp4";
    nodes.push(el("a", { href: item.url, download: `wan-${item.id}.${extension}`, textContent: "Baixar" }));
  }
  // Numa sessao so de troca de pessoa, o que levaria a outro modo nao aparece.
  if (!onlySwap() || ANIMATE_MODES.includes(item.mode)) nodes.push(actionButton("Reusar", () => reuse(item)));
  if (!onlySwap() && item.local && item.kind === "image") {
    nodes.push(actionButton("Animar", () => startFrom(item, async () => library.get(item.id).blob)));
  }
  if (!onlySwap() && item.local && item.kind === "video") {
    nodes.push(actionButton("Continuar", () => startFrom(item, () => lastFrame(item.url))));
  }
  nodes.push(deleteButton(item));
  return nodes;
}

let compareUrl = null;  // o video original aberto na comparacao, para soltar ao fechar

/** O original e o resultado lado a lado, andando juntos: o resultado manda (tem os controles)
 *  e o original o acompanha, a partir do ponto do video em que a troca comecou. */
function compareMedia(item, original) {
  const offset = Number(item.start) || 0;
  compareUrl = URL.createObjectURL(original);
  const before = el("video", { src: compareUrl, muted: true, playsInline: true, preload: "auto" });
  const after = el("video", { src: item.url, controls: true, autoplay: true, loop: true, playsInline: true });
  const follow = () => {
    const wanted = offset + after.currentTime;
    if (Math.abs(before.currentTime - wanted) > 0.12) before.currentTime = wanted;
  };
  after.addEventListener("timeupdate", follow);
  after.addEventListener("seeking", follow);
  after.addEventListener("play", () => { follow(); before.play().catch(() => {}); });
  after.addEventListener("pause", () => before.pause());
  const pane = (label, video) => el("figure", { className: "compare-pane" }, video, el("figcaption", { textContent: label }));
  const box = el("div", { className: "compare" }, pane("Original", before), pane("Resultado", after));
  box.style.setProperty("--ratio", `${item.width} / ${item.height}`);
  return box;
}

function releaseCompare() {
  if (compareUrl) URL.revokeObjectURL(compareUrl);
  compareUrl = null;
}

function openViewer(id, compare = false) {
  const stored = library.get(id);
  if (!stored) return;
  const item = { ...stored.job, status: "done", url: stored.url, local: true };
  // A troca de pessoa guarda o video de origem (ate 25 MB): da para ver os dois lado a lado.
  const original = item.mode === "swap" && stored.inputs && stored.inputs.video;
  releaseCompare();
  const media = item.kind === "image"
    ? el("img", { src: item.url, alt: item.prompt })
    : compare && original
      ? compareMedia(item, original)
      : el("video", { src: item.url, controls: true, autoplay: true, loop: true, playsInline: true });
  $("#viewer-media").replaceChildren(media);
  $("#viewer-prompt").textContent = titleOf(item);
  const final = $("#viewer-final");
  final.hidden = !item.final_prompt;
  final.textContent = item.final_prompt ? `Enviado ao modelo: ${item.final_prompt}` : "";
  $("#viewer-meta").textContent = describe(item);
  const actions = actionsFor(item);
  if (original) {
    actions.splice(1, 0, actionButton(compare ? "Ver só o resultado" : "Comparar com o original",
                                      () => openViewer(id, !compare), "accent"));
  }
  $("#viewer-actions").replaceChildren(...actions);
  $("#viewer").dataset.id = id;
  if (!$("#viewer").open) $("#viewer").showModal();
}

function renderCard(item) {
  const image = item.kind === "image";
  let media;
  if (item.local) {
    media = el("button", { type: "button", className: "card-media open", title: "Ampliar" });
    if (image) {
      media.append(el("img", { src: item.url, alt: item.prompt, loading: "lazy" }));
    } else {
      const video = el("video", { src: item.url, muted: true, loop: true, playsInline: true, preload: "metadata" });
      media.append(video);
      media.addEventListener("mouseenter", () => video.play().catch(() => {}));
      media.addEventListener("mouseleave", () => video.pause());
    }
    media.append(el("span", { className: "badge", textContent: image ? "Imagem" : `${item.duration} s` }));
    media.addEventListener("click", () => openViewer(item.id));
  } else if (item.status === "error") {
    media = el("div", { className: "card-media" },
               el("div", { className: "card-wait failed", textContent: item.error || "Falhou" }));
  } else {
    const fill = el("i");
    const bar = el("div", { className: "bar" }, fill);
    if (item.status === "running" && item.progress > 0) fill.style.width = `${Math.round(item.progress * 100)}%`;
    else bar.classList.add("busy");
    media = el("div", { className: "card-media" }, el("div", { className: "card-wait" }, waitText(item), bar));
  }
  media.style.aspectRatio = `${item.width} / ${item.height}`;

  return el("article", { className: "card" }, media,
    el("div", { className: "card-body" },
      el("p", { className: "card-prompt", textContent: titleOf(item), title: titleOf(item) }),
      el("p", { className: "card-meta", textContent: describe(item) }),
      el("div", { className: "card-actions" }, ...actionsFor(item))));
}

function galleryItems() {
  const items = liveJobs.filter((job) => !library.has(job.id)).map((job) => ({ ...job, local: false }));
  for (const stored of library.values()) items.push({ ...stored.job, status: "done", url: stored.url, local: true });
  return items
    .filter((item) => state.filter === "all" || item.kind === state.filter)
    .sort((a, b) => b.created - a.created);
}

function render() {
  // Uma sessao so de troca de pessoa nao gera imagem: o filtro so aparece se a galeria ja as tem.
  const images = [...library.values()].some((stored) => stored.job.kind === "image");
  $('[data-key="filter"]').hidden = onlySwap() && !images;
  if (onlySwap() && !images && state.filter !== "all") choose("filter", "all");
  const items = galleryItems();
  const gallery = $("#gallery");
  const seen = new Set();
  items.forEach((item, index) => {
    seen.add(item.id);
    // So redesenha o card quando algo mudou, para nao interromper um video tocando.
    const signature = item.local ? `local|${focus}` : `${item.status}|${waitText(item)}|${item.error || ""}`;
    let entry = cards.get(item.id);
    if (!entry || entry.signature !== signature) {
      const node = renderCard(item);
      if (entry) entry.node.replaceWith(node);
      entry = { node, signature };
      cards.set(item.id, entry);
    }
    if (gallery.children[index] !== entry.node) gallery.insertBefore(entry.node, gallery.children[index] || null);
  });
  for (const [id, entry] of cards) {
    if (!seen.has(id)) {
      entry.node.remove();
      cards.delete(id);
    }
  }
  const empty = $("#empty");
  empty.hidden = items.length > 0;
  const nothing = state.filter === "all" || !library.size;
  empty.textContent = !nothing ? "Nada neste filtro."
    : onlySwap() ? "Nada por aqui ainda. Envie a foto da pessoa e o vídeo para começar."
      : "Nada por aqui ainda. O que você quer criar?";
  // Na pagina so de troca de pessoa, os tres passos; na do estudio inteiro, um cartao por modo.
  $("#starter").hidden = items.length > 0 || !nothing || onlySwap();
  $("#howto").hidden = items.length > 0 || !nothing || !onlySwap();
}

// ---------------------------------------------------------------- inicio

function bindUi() {
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-key] button[data-value]");
    if (button && !button.disabled) {
      const key = button.closest("[data-key]").dataset.key;
      choose(key, button.dataset.value);
      refreshForm();
      if (key === "filter") render();
    }
    const close = event.target.closest("[data-close]");
    if (close) close.closest("dialog").close();
    if (event.target instanceof HTMLDialogElement) event.target.close();  // clique no fundo
  });

  $("#form").addEventListener("submit", submit);
  $("#enhance").addEventListener("click", enhance);
  $("#hd").addEventListener("change", refreshForm);  // a estimativa de tempo muda com a alta definicao
  $("#prompt").addEventListener("input", () => {
    if (undoText === null) return;
    undoText = null;  // o texto foi editado a mao: nao ha mais o que desfazer
    refreshEnhance();
  });
  $("#prompt").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) $("#form").requestSubmit();
  });

  $("#status").addEventListener("click", openGpu);
  $("#wake").addEventListener("click", wake);
  $("#connect-go").addEventListener("click", tryConnect);
  $("#connect-code").addEventListener("keydown", (event) => {
    if (event.key === "Enter") tryConnect();
  });
  $("#notebook-url").addEventListener("change", (event) => {
    const url = event.target.value.trim();
    store.set("notebook", /^https:\/\//.test(url) ? url : null);
    renderGpu();
  });
  $("#keep-alive").addEventListener("click", async () => {
    await api("/api/touch", { method: "POST" }).catch(() => {});
    refreshStatus();
  });
  let armed = null;
  $("#shutdown").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    if (!armed) {
      button.textContent = "Confirmar desligamento?";
      armed = setTimeout(() => {
        armed = null;
        button.textContent = "Desligar GPU";
      }, 4000);
      return;
    }
    clearTimeout(armed);
    armed = null;
    button.textContent = "Desligar GPU";
    await api("/api/shutdown", { method: "POST" }).catch(() => {});
    $("#gpu").close();
    $("#status").dataset.state = "warn";
    $("#status").textContent = "Desligando a GPU…";
  });

  $("#viewer").addEventListener("close", () => {
    $("#viewer-media").replaceChildren();
    releaseCompare();
  });
  // Os cartoes da galeria vazia: cada um leva ao modo dele.
  $("#starter").addEventListener("click", (event) => {
    const card = event.target.closest("[data-start]");
    if (!card) return;
    choose("mode", card.dataset.start);
    refreshForm();
    $("#form").scrollIntoView({ behavior: "smooth", block: "start" });
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) return;
    document.title = baseTitle;
    lastDiscover = 0;  // voltou para a aba: procura a GPU na hora
    refreshStatus();
  });
}

async function init() {
  // Link de conexao aberto direto: guarda e tira o codigo da barra de endereco.
  const fromLink = /[#&]c=/.test(location.hash) ? parseCode(location.hash) : null;
  if (fromLink) {
    connect(fromLink);
    try { history.replaceState(null, "", location.pathname + location.search); } catch { /* file:// */ }
  }

  bindUi();
  bindFrames();
  presets = window.WAN_PRESETS || await (await fetch("/api/presets")).json();
  renderPresets();
  sanitize();
  refreshForm();
  renderStatus();

  await loadLibrary();
  render();
  renderGalleryNote();

  await refreshStatus();
  pollJobs();
  setInterval(refreshStatus, STATUS_EVERY);
  // Com a GPU ligando, a procura pelo anuncio nao espera a proxima leitura de estado.
  setInterval(() => { if (SITE && !status && starting()) discover(); }, DISCOVER_STARTING);
}

init();
