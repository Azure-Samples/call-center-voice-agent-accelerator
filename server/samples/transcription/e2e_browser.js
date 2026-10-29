// Drives the real /genesys simulator page in a headless Edge browser (Stereo mode):
// customer.wav is uploaded as the customer (external) and agent.wav is played through
// Chromium's fake microphone as the agent (internal). Prints the AudioHook protocol log and
// the speaker-labelled lines shown on /transcript, and saves screenshots next to this script.
//
// Usage (server running with CALL_MODE=transcription and GENESYS_API_KEY=test):
//   $env:PW_PATH = "$(npm root -g)\playwright"      # or: npm i playwright (then PW_PATH can be omitted)
//   node samples\transcription\e2e_browser.js [http://localhost:8000] [genesys-api-key]
const { chromium } = require(process.env.PW_PATH || "playwright");
const path = require("path");

const BASE = (process.argv[2] || "http://localhost:8000").replace(/\/$/, "");
const API_KEY = process.argv[3] || "test";
const DIR = __dirname;
const CALL_SECONDS = 27;

(async () => {
  const browser = await chromium.launch({
    channel: "msedge",
    headless: true,
    args: [
      "--use-fake-ui-for-media-stream",
      "--use-fake-device-for-media-stream",
      `--use-file-for-fake-audio-capture=${path.join(DIR, "agent.wav")}`,
      "--autoplay-policy=no-user-gesture-required",
    ],
  });
  const ctx = await browser.newContext({ permissions: ["microphone"], viewport: { width: 1200, height: 800 } });

  const sim = await ctx.newPage();
  sim.on("pageerror", (e) => console.log("simulator page error:", e.message));
  await sim.goto(`${BASE}/genesys`);
  await sim.fill("#apiKey", API_KEY);
  await sim.selectOption("#channelMode", "stereo");
  await sim.setInputFiles("#customerFile", path.join(DIR, "customer.wav"));
  await sim.click("#connectBtn");

  const cidRe = /conversationId=([0-9a-f-]{36})/;
  await sim.waitForFunction((re) => new RegExp(re).test(document.getElementById("log").textContent), cidRe.source, { timeout: 15000 });
  const cid = (await sim.textContent("#log")).match(cidRe)[1];

  const view = await ctx.newPage();
  view.on("pageerror", (e) => console.log("transcript page error:", e.message));
  await view.goto(`${BASE}/transcript?conversationId=${cid}`);

  await sim.waitForTimeout(CALL_SECONDS * 1000);
  await sim.click("#disconnectBtn");
  await sim.waitForTimeout(5000);

  console.log("--- AudioHook protocol log ---");
  console.log((await sim.$$eval("#log > div", (els) => els.map((e) => e.textContent))).join("\n"));
  console.log("\n--- /transcript ---");
  const lines = await view.$$eval(".msg", (els) =>
    els.map((e) => ({ speaker: e.className.replace("msg", "").trim(), text: e.querySelector(".bubble").textContent })));
  lines.forEach((l) => console.log(`[${l.speaker.padEnd(8)}] ${l.text}`));
  const status = await view.textContent("#status");
  console.log("status:", status);

  await view.screenshot({ path: path.join(DIR, "transcript.png") });
  await sim.screenshot({ path: path.join(DIR, "simulator.png"), fullPage: true });
  await browser.close();

  const speakers = new Set(lines.map((l) => l.speaker));
  const ok = speakers.has("customer") && speakers.has("agent") && status === "Call ended";
  console.log(ok ? "PASS" : "FAIL (expected customer + agent lines and 'Call ended')");
  process.exit(ok ? 0 : 1);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
