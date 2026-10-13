// Teste da interface servida pelo proprio servidor (mesma origem), com o melhorador de
// prompt simulado: melhorar/desfazer, melhoria automatica, erro, cancelamento e fila.
const { chromium } = require("playwright-core");
const fs = require("fs");

const BASE = "http://127.0.0.1:8092";
const path = require("path");
const TESTS = path.join(__dirname, ".work").split(path.sep).join("/");
const SHOTS = `${TESTS}/shots`;
const failures = [];
let page;

function check(label, condition, detail = "") {
  console.log((condition ? "ok    " : "FALHA ") + label + (detail ? `  [${detail}]` : ""));
  if (!condition) failures.push(label);
}
const text = (selector) => page.textContent(selector);
const count = (selector) => page.locator(selector).count();
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const serverJobs = async () => (await (await fetch(`${BASE}/api/jobs`)).json()).jobs;

(async () => {
  fs.rmSync(`${TESTS}/profile2`, { recursive: true, force: true });
  fs.mkdirSync(SHOTS, { recursive: true });
  const context = await chromium.launchPersistentContext(`${TESTS}/profile2`, {
    channel: "chrome", headless: true, viewport: { width: 1440, height: 1000 },
  });
  page = context.pages()[0] || await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push("pageerror: " + error.message));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push("console: " + message.text());
  });

  await page.goto(BASE + "/");
  await page.waitForFunction(() => document.querySelector("#status").dataset.state === "ok", null, { timeout: 20000 });
  check("S1 mesma origem: conecta sem codigo", (await text("#status")).includes("NVIDIA L40S · 45 GB") && await page.evaluate(() => window.WAN_SITE === undefined));
  check("S2 miniaturas trazem icone de movimento", (await count("#cameras .thumb-icon")) >= 30, String(await count("#cameras .thumb-icon")));
  const box = await page.evaluate(() => {
    const rect = document.querySelector("#submit").getBoundingClientRect();
    return { bottom: Math.round(rect.bottom), viewport: window.innerHeight };
  });
  check("S3 botao de gerar visivel sem rolar", box.bottom <= box.viewport, JSON.stringify(box));

  // ---- melhorar / desfazer
  check("S4 melhorador visivel e ativo", await page.isVisible("#enhance") && await page.isVisible("#auto-row") && !(await page.isDisabled("#enhance")));
  await page.fill("#prompt", "um cachorro correndo na praia");
  await page.click("#enhance");
  await page.waitForFunction(() => document.querySelector("#prompt").value.startsWith("[t2v]"), null, { timeout: 15000 });
  check("S5 Melhorar troca o texto e oferece desfazer", (await page.inputValue("#prompt")) === "[t2v] A cinematic shot of: um cachorro correndo na praia"
    && (await text("#enhance")) === "Desfazer melhoria", await page.inputValue("#prompt"));
  await page.screenshot({ path: `${SHOTS}/12-melhorador.png` });
  await page.click("#enhance");
  check("S6 Desfazer restaura", (await page.inputValue("#prompt")) === "um cachorro correndo na praia" && (await text("#enhance")) === "Melhorar com IA");
  await page.click('#cameras [data-value="dolly_in"]');
  await page.click("#enhance");
  await page.waitForFunction(() => document.querySelector("#prompt").value.startsWith("[t2v cam]"), null, { timeout: 15000 });
  check("S7 com camera escolhida o melhorador nao inventa camera", true);

  // ---- texto ja melhorado nao e melhorado de novo ao gerar
  await page.click("#submit");
  await page.waitForFunction(() => document.querySelectorAll(".card video").length === 1, null, { timeout: 90000 });
  await page.locator(".card .card-media.open").first().click();
  await page.waitForSelector("#viewer[open]");
  let final = await text("#viewer-final");
  check("S8 prompt melhorado a mao vai como esta", (final.match(/A cinematic shot of/g) || []).length === 1 && final.includes("pushes in toward the subject"), final.slice(0, 150));
  await page.click("#viewer [data-close]");

  // ---- melhoria automatica ao gerar
  await page.click('#cameras [data-value="none"]');
  await page.fill("#prompt", "uma cidade futurista à noite");
  await page.click("#submit");
  await page.waitForFunction(() => document.querySelectorAll(".card video").length === 2, null, { timeout: 90000 });
  await page.locator(".card .card-media.open").first().click();
  await page.waitForSelector("#viewer[open]");
  final = await text("#viewer-final");
  check("S9 melhoria automatica aplicada, prompt original preservado", final.includes("[t2v] A cinematic shot of: uma cidade futurista à noite")
    && (await text("#viewer-prompt")) === "uma cidade futurista à noite", final.slice(0, 120));
  await page.click("#viewer [data-close]");
  await sleep(1200);
  check("S10 mesma origem tambem guarda no navegador e libera o servidor", (await serverJobs()).length === 0
    && await page.evaluate(() => [...document.querySelectorAll(".card video")].every((v) => v.src.startsWith("blob:"))));

  // ---- erro da GPU vira mensagem util no card
  await page.uncheck("#auto-enhance");
  await page.fill("#prompt", "FAIL de proposito");
  await page.click("#submit");
  await page.waitForSelector(".card .card-wait.failed", { timeout: 60000 });
  check("S11 falta de memoria vira orientacao", (await text(".card .card-wait.failed")).includes("faltou memória na GPU"), await text(".card .card-wait.failed"));
  await page.screenshot({ path: `${SHOTS}/13-erro.png` });
  await page.locator(".card:has(.failed) button.danger").click();
  await page.locator(".card:has(.failed) button.danger").click();
  await page.waitForFunction(() => !document.querySelector(".card .failed"));
  check("S12 card de erro pode ser excluido", (await serverJobs()).length === 0);

  // ---- fila e cancelamento (14B sem turbo = 20 passos lentos)
  await page.click('[data-key="model"] [data-value="14b"]');
  await page.uncheck("#fast");
  await page.click('[data-key="count"] [data-value="2"]');
  await page.fill("#prompt", "ondas quebrando");
  await page.click("#submit");
  await page.waitForFunction(() => /Na fila · 1 na frente/.test(document.querySelector("#gallery").textContent), null, { timeout: 20000 });
  check("S13 segundo job mostra posicao na fila", true);
  await page.waitForFunction(() => /Gerando… \d+% · /.test(document.querySelector("#gallery").textContent), null, { timeout: 40000 });
  check("S14 progresso com estimativa de tempo", true, (await text("#gallery")).match(/Gerando… \d+% · [^W]+/)[0].slice(0, 40));
  await page.screenshot({ path: `${SHOTS}/14-fila.png` });
  const cancel = page.locator('.card button.danger:has-text("Cancelar")');
  check("S15 jobs ativos oferecem Cancelar", (await cancel.count()) === 2);
  await cancel.first().click();
  await page.locator('.card button.danger:has-text("Cancelar")').first().click();
  await page.waitForFunction(() => document.querySelectorAll(".card .card-wait").length === 0, null, { timeout: 15000 });
  await sleep(2500);
  const queue = await (await fetch("http://127.0.0.1:8189/queue")).json();
  check("S16 cancelar esvazia a fila no servidor e no ComfyUI", (await serverJobs()).length === 0
    && queue.queue_running.length === 0 && queue.queue_pending.length === 0, JSON.stringify(queue));

  // ---- dialogo da GPU sem launcher: nao oferece desligar
  await page.click("#status");
  await page.waitForSelector("#gpu[open] #gpu-facts dd");
  const facts = await text("#gpu-facts");
  check("S17 dialogo mostra melhorador pronto e sem botao de desligar", facts.includes("Melhorar promptpronto") && !(await page.isVisible("#shutdown")) && !(await page.isVisible("#idle-note")), facts.slice(-60));
  await page.click("#gpu [data-close]");

  // ---- registro da maquina, para diagnostico
  const log = await fetch(`${BASE}/api/log`);
  const logText = await log.text();
  check("S18 registro do ComfyUI: so o fim do arquivo, e so os nomes conhecidos", log.status === 200
    && logText.endsWith("Prompt executed in 12.34 seconds\n") && !logText.includes("inicio") && logText.length === 200 * 1024
    && (await fetch(`${BASE}/api/log?name=tunnel`)).status === 404 && (await (await fetch(`${BASE}/api/log?name=extras`)).text()) === "",
    `${log.status}, ${logText.length} bytes`);

  // ---- placa grande (aqui, uma L40S simulada): a troca de pessoa oferece a qualidade ultra
  await page.click('[data-key="mode"] [data-value="swap"]');
  await page.waitForFunction(() => !document.querySelector('[data-key="quality"] [data-value="ultra"]').hidden, null, { timeout: 20000 });
  await page.click('[data-key="length"] [data-value="10"]');
  const minutes = {};
  for (const quality of ["maxima", "ultra"]) {
    await page.click(`[data-key="quality"] [data-value="${quality}"]`);
    minutes[quality] = Number((await text("#swap-estimate")).match(/cerca de (\d+) min/)[1]);
  }
  check("S19 placa de 45 GB: a ultra aparece, sem a alta definicao, e a estimativa avisa que e uma conta",
    minutes.ultra > minutes.maxima && !(await page.isVisible("#hd-row")) && (await text("#swap-estimate")).includes("conta ainda não conferida nela")
    && (await text('[data-key="quality"] [data-value="ultra"]')).includes("720p direto"), JSON.stringify(minutes));
  await page.setInputFiles('[data-frame="start_image"] input', `${TESTS}/start.png`);
  await page.setInputFiles('[data-frame="video"] input', `${TESTS}/sample.mp4`);
  await page.click('[data-key="length"] [data-value="5"]');
  await page.click("#submit");
  // O card da troca, ja com o video (ha cards prontos de antes: nao basta existir um video).
  const ultraCard = () => [...document.querySelectorAll("#gallery .card")]
    .find((card) => /qualidade ultra/.test(card.textContent) && card.querySelector("video"));
  await page.waitForFunction(ultraCard, null, { timeout: 120000 });
  const made = await page.evaluate(`(${ultraCard})().querySelector(".card-meta").textContent`);
  for (let tries = 0; tries < 20 && (await serverJobs()).length; tries++) await sleep(500);
  check("S20 troca na ultra pelo site: gerada em 1280×720, guardada no navegador", made.includes("qualidade ultra")
    && made.includes("1280×720") && made.includes("pessoa trocada") && !made.includes("alta definição")
    && (await serverJobs()).length === 0, made);

  const unexpected = errors.filter((entry) => !/Failed to load resource/.test(entry));
  check("Z sem erros de JavaScript", unexpected.length === 0, unexpected.slice(0, 3).join(" | "));
  await context.close();
  console.log(`\n${failures.length} falha(s)` + (failures.length ? ": " + failures.join("; ") : ""));
  process.exit(failures.length ? 1 : 0);
})().catch((error) => {
  console.error("ERRO NO TESTE:", error.message.split("\n").slice(0, 6).join("\n"));
  if (page) page.screenshot({ path: `${SHOTS}/zz-erro-servidor.png` }).catch(() => {}).finally(() => process.exit(2));
  else process.exit(2);
});
