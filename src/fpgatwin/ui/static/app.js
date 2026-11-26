"use strict";

// Renders the board purely from /api/board and follows /api/events (Server-Sent Events).
// Inputs are sent with POST /api/pin; everything is keyed by physical pin.
// The "design files" card uploads RTL + UCF to POST /api/load, which builds and swaps the engine.

const LED_COLORS = { green: "var(--led-green)", red: "var(--led-red)", yellow: "var(--led-yellow)" };
const pinEls = new Map(); // pin -> { kind, el }
let engineLabel = "";
let engineId; // id of the engine behind the event stream; changes when a design is loaded
let clockHz = 0;

const $ = (id) => document.getElementById(id);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "style") node.style.cssText = v;
    else node.setAttribute(k, v);
  }
  for (const c of children) node.append(c);
  return node;
}

function markUnconnected(connected) {
  for (const [pin, ref] of pinEls) {
    const box = ref.kind === "button" ? ref.el.parentElement : ref.el;
    const nc = !connected.has(pin);
    box.classList.toggle("nc", nc);
    if (nc) box.title = "此腳位未連接到設計";
    else box.removeAttribute("title");
  }
}

function items(board, type) {
  return (board.peripherals || []).filter((p) => p.type === type).flatMap((p) => p.items || []);
}

function splitLabel(label) {
  // "DS11 (GPIO_LED_0)" -> ["DS11", "GPIO_LED_0"]
  const m = /^(.*?)\s*\((.*)\)\s*$/.exec(label);
  return m ? [m[1], m[2]] : [label, ""];
}

function labelBlock(cls, label, pin) {
  const [ref, net] = splitLabel(label);
  return el("div", { class: cls }, ref, el("br"), net, el("br"), el("span", { class: "pin" }, pin));
}

const TOKEN = document.querySelector('meta[name="fpgatwin-token"]')?.content || "";

function post(path, body) {
  return fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-FPGATwin-Token": TOKEN },
    body: JSON.stringify(body),
  });
}

async function setPin(pin, value) {
  try {
    await post("/api/pin", { pin, value });
  } catch (e) {
    console.warn("setPin failed", e);
  }
}

// ---------------------------------------------------------------- rendering

function renderLeds(board) {
  const box = $("leds");
  for (const it of items(board, "led_array")) {
    const node = el("div", { class: "led", style: `--c:${LED_COLORS[it.color] || LED_COLORS.green}` },
      el("div", { class: "led-dot", role: "img", "aria-label": `${it.label} off` }),
      labelBlock("led-label", it.label, it.pin));
    box.append(node);
    pinEls.set(it.pin, { kind: "led", el: node, label: it.label });
  }
}

function renderSwitches(board) {
  const box = $("switches");
  for (const it of items(board, "dip_switch")) {
    const btn = el("button", { class: "switch", type: "button", role: "switch", "aria-checked": "false", "aria-label": it.label },
      el("div", { class: "switch-track" }, el("div", { class: "switch-knob" })),
      el("span", { class: "switch-state" }, "OFF"),
      labelBlock("ctl-label", it.label, it.pin));
    btn.addEventListener("click", () => {
      const next = btn.getAttribute("aria-checked") === "true" ? 0 : 1;
      showSwitch(btn, next); // optimistic; the event stream confirms
      setPin(it.pin, next);
    });
    box.append(btn);
    pinEls.set(it.pin, { kind: "switch", el: btn });
  }
}

function showSwitch(btn, v) {
  btn.setAttribute("aria-checked", v ? "true" : "false");
  btn.querySelector(".switch-state").textContent = v ? "ON" : "OFF";
}

function renderButtons(board) {
  const box = $("buttons");
  for (const it of items(board, "pushbutton_array")) {
    const isReset = /RESET/i.test(it.label);
    const b = el("button", { type: "button", "aria-label": it.label, "aria-pressed": "false" });
    const wrap = el("div", { class: "pushbtn" + (isReset ? " reset" : "") }, b, labelBlock("ctl-label", it.label, it.pin));

    let down = false;
    const press = (e) => {
      if (e && e.pointerId !== undefined) b.setPointerCapture(e.pointerId);
      if (down) return;
      down = true;
      b.classList.add("down");
      b.setAttribute("aria-pressed", "true");
      setPin(it.pin, 1);
    };
    const release = () => {
      if (!down) return;
      down = false;
      b.classList.remove("down");
      b.setAttribute("aria-pressed", "false");
      setPin(it.pin, 0);
    };
    b.addEventListener("pointerdown", press);
    b.addEventListener("pointerup", release);
    b.addEventListener("pointercancel", release);
    b.addEventListener("lostpointercapture", release);
    b.addEventListener("keydown", (e) => { if ((e.key === " " || e.key === "Enter") && !e.repeat) { e.preventDefault(); press(); } });
    b.addEventListener("keyup", (e) => { if (e.key === " " || e.key === "Enter") release(); });
    b.addEventListener("blur", release);
    window.addEventListener("blur", release);

    box.append(wrap);
    pinEls.set(it.pin, { kind: "button", el: b });
  }
}

function renderClocks(board) {
  const ul = $("clocks");
  for (const c of board.clocks || []) {
    const freq = c.freq_hz ? `${(c.freq_hz / 1e6).toLocaleString()} MHz` : "外部輸入";
    const li = el("li", { "data-clock": c.name },
      el("span", { class: "clk-name" }, c.name), el("span", { class: "freq" }, freq));
    li.title = Object.entries(c.pins).map(([k, v]) => `${k}: ${v}`).join(", ");
    ul.append(li);
  }
}

// ---------------------------------------------------------------- live state

function fmtTime(ps) {
  const s = ps / 1e12;
  if (s >= 1) return `${s.toFixed(3)} s`;
  if (s >= 1e-3) return `${(s * 1e3).toFixed(3)} ms`;
  return `${(s * 1e6).toFixed(3)} µs`;
}

function fmtRate(hz) {
  if (hz >= 1e6) return `${(hz / 1e6).toFixed(2)} MHz`;
  if (hz >= 1e3) return `${(hz / 1e3).toFixed(1)} kHz`;
  return `${hz.toFixed(0)} Hz`;
}

function applySnapshot(s) {
  if (s.engine_id !== undefined && s.engine_id !== engineId) {
    const first = engineId === undefined;
    engineId = s.engine_id;
    uartReset(); // the server streams a new design's UART output from its start
    if (!first) refreshEngine(); // the design was swapped (here or in another tab)
  }
  uartUpdate(s);
  waveUpdate(s);
  for (const [pin, v] of Object.entries(s.pins)) {
    const ref = pinEls.get(pin);
    if (!ref) continue;
    if (ref.kind === "led") {
      ref.el.classList.toggle("on", !!v);
      ref.el.firstChild.setAttribute("aria-label", `${ref.label} ${v ? "on" : "off"}`);
    } else if (ref.kind === "switch") {
      showSwitch(ref.el, v);
    }
  }
  $("st-engine").textContent = engineLabel;
  if (s.status && s.status !== "running") {
    setConn("bad", "模擬已停止");
    const b = $("status-banner");
    b.textContent = `模擬已停止：${s.message || s.status}`;
    b.hidden = false;
  } else if (!$("status-banner").hidden) {
    $("status-banner").hidden = true;
    setConn("ok", "已連線");
  }
  $("st-time").textContent = fmtTime(s.sim_time_ps);
  $("st-cycles").textContent = s.cycles.toLocaleString();
  const pct = clockHz ? ` (${((s.rate_hz / clockHz) * 100).toFixed(0)}%)` : "";
  $("st-rate").textContent = fmtRate(s.rate_hz) + pct;
}

function setConn(state, text) {
  const c = $("conn");
  c.className = `conn conn-${state}`;
  c.textContent = text;
}

function connect() {
  const es = new EventSource("/api/events");
  es.onopen = () => {
    setConn("ok", "已連線");
    uartReset(); // a (re)connected stream replays the buffered UART output
  };
  es.onmessage = (ev) => applySnapshot(JSON.parse(ev.data));
  es.onerror = () => setConn("bad", "連線中斷，重試中…");
}

function applyEngine(engine) {
  $("demo-banner").hidden = engine.name !== "demo";
  engineLabel = engine.top ? `${engine.name} (${engine.top})` : engine.name;
  $("st-engine").textContent = engineLabel;
  clockHz = engine.clock_hz;
  for (const li of $("clocks").children) li.classList.toggle("active", li.dataset.clock === engine.clock);
  markUnconnected(new Set(engine.connected || []));
  uartConfigure(engine);
  waveConfigure(engine);
}

// ---------------------------------------------------------------- UART terminal

const UART_KEEP = 128 * 1024; // bytes kept for re-rendering (mode switch)
const EOL = { "": "", lf: "\n", crlf: "\r\n", cr: "\r" };
const uart = { id: null, bytes: new Uint8Array(0), mode: "text", decoder: null, lastCR: false, col: 0 };

function uartConfigure(engine) {
  const u = (engine.uarts || [])[0];
  uart.id = u ? u.id : null;
  $("uart-on").hidden = !u;
  $("uart-off").hidden = !!u;
  $("uart-label").textContent = u ? u.label : "";
  if (!u) {
    $("uart-off").textContent = engine.name === "demo"
      ? "Demo 引擎沒有 UART。載入有接 UART 腳位的設計即可使用。"
      : "這個設計沒有接到板上 UART 的腳位。";
    return;
  }
  setBaudSelect(u.baud);
  $("uart-form").hidden = !u.rx;
  const note = !u.tx ? "設計只接了 RX（K14）：可以送資料給 FPGA，但不會收到回應。"
    : !u.rx ? "設計只接了 TX（L12）：只能接收 FPGA 送出的資料。" : "";
  $("uart-note").textContent = note;
  $("uart-note").hidden = !note;
}

function setBaudSelect(baud) {
  const sel = $("uart-baud");
  const v = String(baud);
  if (![...sel.options].some((o) => o.value === v)) sel.append(el("option", { value: v }, v));
  sel.value = v;
}

function uartReset() {
  uart.bytes = new Uint8Array(0);
  uartRender();
  $("uart-err").hidden = true;
}

function uartRender() {
  uart.decoder = new TextDecoder("utf-8");
  uart.lastCR = false;
  uart.col = 0;
  $("uart-out").replaceChildren();
  uartAppendView(uart.bytes);
}

// Text view: CR LF / lone CR become a line break, other control bytes become visible symbols.
function textNodes(chunk) {
  const text = uart.decoder.decode(chunk, { stream: true });
  const out = [];
  let plain = "";
  for (const ch of text) {
    const c = ch.codePointAt(0);
    if (ch === "\n" && uart.lastCR) { uart.lastCR = false; continue; }
    uart.lastCR = ch === "\r";
    if (ch === "\r" || ch === "\n") plain += "\n";
    else if ((c < 0x20 && ch !== "\t") || c === 0x7f) {
      if (plain) out.push(plain);
      plain = "";
      out.push(el("span", { class: "ctl", title: `0x${c.toString(16).padStart(2, "0")}` },
        String.fromCodePoint(c === 0x7f ? 0x2421 : 0x2400 + c)));
    } else plain += ch;
  }
  if (plain) out.push(plain);
  return out;
}

function hexText(chunk) {
  let s = "";
  for (const b of chunk) {
    s += b.toString(16).padStart(2, "0");
    uart.col = (uart.col + 1) % 16;
    s += uart.col === 0 ? "\n" : " ";
  }
  return s;
}

function uartAppendView(chunk) {
  if (!chunk.length) return;
  const out = $("uart-out");
  const stick = out.scrollHeight - out.scrollTop - out.clientHeight < 24;
  if (uart.mode === "hex") out.append(hexText(chunk));
  else out.append(...textNodes(chunk));
  if (stick) out.scrollTop = out.scrollHeight;
}

function uartUpdate(s) {
  if (!uart.id) return;
  const hex = s.uart_rx && s.uart_rx[uart.id];
  if (hex) {
    const chunk = new Uint8Array(hex.length / 2);
    for (let i = 0; i < chunk.length; i++) chunk[i] = parseInt(hex.substr(i * 2, 2), 16);
    const all = new Uint8Array(uart.bytes.length + chunk.length);
    all.set(uart.bytes);
    all.set(chunk, uart.bytes.length);
    uart.bytes = all.length > UART_KEEP ? all.slice(all.length - UART_KEEP) : all;
    if (all.length > UART_KEEP) uartRender();
    else uartAppendView(chunk);
  }
  const st = s.uart && s.uart[uart.id];
  if (st) {
    const err = $("uart-err");
    err.hidden = !st.errors;
    if (st.errors) err.textContent = `框架錯誤 ${st.errors} 次：鮑率可能與設計不符`;
    if (document.activeElement !== $("uart-baud") && $("uart-baud").value !== String(st.baud)) {
      setBaudSelect(st.baud); // changed elsewhere (another tab)
    }
  }
}

async function uartPost(body) {
  const res = await post("/api/uart", { id: uart.id, ...body });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
}

function setupUart() {
  const setMode = (mode) => {
    uart.mode = mode;
    $("uart-mode-text").setAttribute("aria-pressed", String(mode === "text"));
    $("uart-mode-hex").setAttribute("aria-pressed", String(mode === "hex"));
    uartRender();
  };
  $("uart-mode-text").addEventListener("click", () => setMode("text"));
  $("uart-mode-hex").addEventListener("click", () => setMode("hex"));
  $("uart-clear").addEventListener("click", () => { uart.bytes = new Uint8Array(0); uartRender(); });
  $("uart-baud").addEventListener("change", (e) => {
    uartPost({ baud: Number(e.target.value) }).catch((err) => alert(`無法設定鮑率：${err.message}`));
  });
  $("uart-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const input = $("uart-input");
    const data = input.value + EOL[$("uart-eol").value];
    if (!data) return;
    uartPost({ data }).then(() => { input.value = ""; })
      .catch((err) => alert(`送出失敗：${err.message}`));
  });
}

// ---------------------------------------------------------------- waveform capture

const wave = { id: null };

function fmtSize(bytes) {
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(1)} MB`;
  if (bytes >= 1e3) return `${(bytes / 1e3).toFixed(0)} KB`;
  return `${bytes} B`;
}

function waveConfigure(engine) {
  $("wave-on").hidden = !engine.wave;
  $("wave-off").hidden = !!engine.wave;
  $("wave-off").textContent = engine.name === "demo"
    ? "Demo 引擎無法擷取波形。載入自己的設計即可使用。"
    : "目前的引擎不支援波形擷取。";
  wave.id = null;
  waveShow({ state: "idle" }, 0);
}

function waveStatus(kind, text) {
  const st = $("wave-status");
  st.className = `wave-status${kind ? ` ${kind}` : ""}`;
  st.textContent = text;
}

function waveShow(w, simPs) {
  const rec = w.state === "recording";
  $("wave-start").hidden = rec;
  $("wave-stop").hidden = !rec;
  $("wave-ms").disabled = rec;
  $("wave-progress").hidden = !rec;
  $("wave-download").hidden = w.state !== "done";
  if (rec) {
    const done = Math.max(0, Math.min(simPs - w.start_ps, w.length_ps));
    $("wave-progress").value = done / w.length_ps;
    waveStatus("", `擷取中… ${fmtTime(done)} / ${fmtTime(w.length_ps)}`);
  } else if (w.state === "done") {
    wave.id = w.id;
    waveStatus("ok", `已完成：${fmtTime(w.start_ps)} – ${fmtTime(w.end_ps)}，${fmtSize(w.size)}`);
  } else if (w.state === "error") {
    waveStatus("err", `無法擷取：${w.message || "未知錯誤"}`);
  } else {
    waveStatus("", "");
  }
}

function waveUpdate(s) {
  if (s.wave && !$("wave-on").hidden) waveShow(s.wave, s.sim_time_ps);
}

async function waveDownload() {
  const btn = $("wave-download");
  btn.disabled = true;
  try {
    const res = await fetch(`/api/wave/${wave.id}.vcd`, { headers: { "X-FPGATwin-Token": TOKEN } });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.error || `HTTP ${res.status}`);
    }
    const name = /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") || "")?.[1]
      || "wave.vcd";
    const url = URL.createObjectURL(await res.blob());
    const a = el("a", { href: url, download: name });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  } catch (err) {
    waveStatus("err", `下載失敗：${err.message}`);
  } finally {
    btn.disabled = false;
  }
}

function setupWave() {
  const send = async (body) => {
    const res = await post("/api/wave", body);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data.wave;
  };
  $("wave-start").addEventListener("click", () => {
    send({ action: "start", ms: Number($("wave-ms").value) })
      .then((w) => waveShow(w, w.start_ps))
      .catch((err) => waveStatus("err", `無法擷取：${err.message}`));
  });
  $("wave-stop").addEventListener("click", () => {
    send({ action: "stop" }).then((w) => waveShow(w, 0))
      .catch((err) => waveStatus("err", `無法停止：${err.message}`));
  });
  $("wave-download").addEventListener("click", waveDownload);
}

async function refreshEngine() {
  try {
    const { engine } = await (await fetch("/api/board")).json();
    applyEngine(engine);
  } catch (e) {
    console.warn("refresh failed", e);
  }
}

// ---------------------------------------------------------------- design files

const isUcf = (name) => /\.(ucf|xdc)$/i.test(name); // any pin-constraint file
const isHdl = (name) => /\.(v|sv)$/i.test(name);
const ACCEPT = /\.(v|sv|vh|svh|mem|hex|ucf|xdc)$/i;
const selected = new Map(); // lower-case name -> File
let building = false;

function addFiles(list) {
  const replaced = [];
  for (const f of list) {
    if (!ACCEPT.test(f.name)) continue; // folder picks bring everything
    const key = f.name.toLowerCase();
    if (selected.has(key)) replaced.push(f.webkitRelativePath || f.name);
    selected.set(key, f);
  }
  renderFiles();
  setBuildStatus("", replaced.length ? `已取代同名檔案：${replaced.join(", ")}` : "");
}

function renderFiles() {
  const ul = $("file-list");
  ul.replaceChildren();
  const names = [...selected.values()].map((f) => f.name).sort((a, b) => a.localeCompare(b));
  for (const name of names) {
    const rm = el("button", { type: "button", "aria-label": `移除 ${name}`, title: "移除" }, "×");
    rm.addEventListener("click", () => { selected.delete(name.toLowerCase()); renderFiles(); });
    ul.append(el("li", { class: isUcf(name) ? "ucf" : "" }, name, rm));
  }
  const sel = $("ucf-select");
  const prev = sel.value;
  const ucfs = names.filter(isUcf);
  sel.replaceChildren(...(ucfs.length ? ucfs : [""]).map((n) => el("option", { value: n }, n || "（未選擇）")));
  if (ucfs.includes(prev)) sel.value = prev;
  sel.disabled = ucfs.length < 2;
  $("clear-files").hidden = selected.size === 0;
  updateBuildButton();
}

function updateBuildButton() {
  const hasHdl = [...selected.keys()].some(isHdl);
  $("build-btn").disabled = building || !hasHdl || !$("ucf-select").value;
  $("demo-btn").disabled = building;
}

function setBuildStatus(kind, text) {
  const s = $("build-status");
  s.className = `build-status ${kind}`;
  s.textContent = text;
}

function showReport({ notes = [], warnings = [], error = "", log = "" }) {
  const box = $("build-report");
  box.replaceChildren();
  if (error) box.append(el("div", { class: "warn" }, error));
  if (notes.length) box.append(el("div", {}, "腳位對應"), el("pre", {}, notes.join("\n")));
  if (warnings.length) {
    box.append(el("div", { class: "warn", style: "margin-top:8px" }, `警告（${warnings.length}）`),
      el("pre", {}, warnings.join("\n")));
  }
  if (log) box.append(el("details", { open: "" }, el("summary", {}, "編譯記錄"), el("pre", {}, log)));
  box.hidden = !box.childElementCount;
}

async function readAll() {
  const ucf = $("ucf-select").value;
  const files = [...selected.values()].filter((f) => !isUcf(f.name) || f.name === ucf);
  return Promise.all(files.map(async (f) => ({ name: f.name, content: await f.text() })));
}

async function busy(fn) {
  building = true;
  updateBuildButton();
  try {
    await fn();
  } catch (e) {
    setBuildStatus("err", e.message || String(e));
  } finally {
    building = false;
    updateBuildButton();
  }
}

function runBuild() {
  return busy(async () => {
    setBuildStatus("", "編譯中…（第一次約需 10–30 秒）");
    showReport({});
    let files;
    try {
      files = await readAll(); // re-read every time, so "編譯並執行" picks up edits on disk
    } catch (e) {
      throw new Error("無法讀取檔案（可能已移動或刪除），請重新選擇。");
    }
    const res = await post("/api/load", { files, top: $("top-input").value.trim() });
    const data = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
    if (!res.ok) {
      setBuildStatus("err", "編譯失敗");
      showReport(data);
      return;
    }
    setBuildStatus("ok", `執行中：${data.engine.top}${data.cached ? "（使用快取）" : ""}`);
    showReport(data);
    applyEngine(data.engine);
  });
}

function useDemo() {
  return busy(async () => {
    const res = await post("/api/demo", {});
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    applyEngine(data.engine);
    setBuildStatus("", "已切換為 Demo 引擎");
    showReport({});
  });
}

function setupDesignPanel(canLoad) {
  if (!canLoad) {
    $("design-card").hidden = true;
    return;
  }
  for (const id of ["pick-files", "pick-dir"]) {
    $(id).addEventListener("change", (e) => {
      addFiles(e.target.files);
      e.target.value = ""; // allow picking the same files again
    });
  }
  $("clear-files").addEventListener("click", () => { selected.clear(); renderFiles(); setBuildStatus("", ""); });
  $("ucf-select").addEventListener("change", updateBuildButton);
  $("build-btn").addEventListener("click", runBuild);
  $("demo-btn").addEventListener("click", useDemo);
  renderFiles();
}

async function main() {
  const res = await fetch("/api/board");
  const { board, engine, can_load: canLoad } = await res.json();
  const info = board.board;
  document.title = `${info.name} · FPGATwin`;
  $("board-name").textContent = info.name;
  $("board-part").textContent = `${info.fpga.part}`;

  renderLeds(board);
  renderSwitches(board);
  renderButtons(board);
  renderClocks(board);
  applyEngine(engine);
  setupDesignPanel(canLoad);
  setupUart();
  setupWave();
  connect();
}

main().catch((e) => {
  console.error(e);
  setConn("bad", "載入失敗");
});
