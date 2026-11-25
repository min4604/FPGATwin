"use strict";

// Renders the board purely from /api/board and follows /api/events (Server-Sent Events).
// Inputs are sent with POST /api/pin; everything is keyed by physical pin.

const LED_COLORS = { green: "var(--led-green)", red: "var(--led-red)", yellow: "var(--led-yellow)" };
const pinEls = new Map(); // pin -> { kind, el }

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

async function setPin(pin, value) {
  try {
    await fetch("/api/pin", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pin, value }),
    });
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

function renderClocks(board, engine) {
  const ul = $("clocks");
  for (const c of board.clocks || []) {
    const freq = c.freq_hz ? `${(c.freq_hz / 1e6).toLocaleString()} MHz` : "外部輸入";
    const li = el("li", { class: c.name === engine.clock ? "active" : "" },
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

function applySnapshot(s, clockHz) {
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
  $("st-engine").textContent = s.engine;
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

function connect(clockHz) {
  const es = new EventSource("/api/events");
  es.onopen = () => setConn("ok", "已連線");
  es.onmessage = (ev) => applySnapshot(JSON.parse(ev.data), clockHz);
  es.onerror = () => setConn("bad", "連線中斷，重試中…");
}

async function main() {
  const res = await fetch("/api/board");
  const { board, engine } = await res.json();
  const info = board.board;
  document.title = `${info.name} · FPGATwin`;
  $("board-name").textContent = info.name;
  $("board-part").textContent = `${info.fpga.part}`;
  $("demo-banner").hidden = engine.name !== "demo";

  renderLeds(board);
  renderSwitches(board);
  renderButtons(board);
  renderClocks(board, engine);
  connect(engine.clock_hz);
}

main().catch((e) => {
  console.error(e);
  setConn("bad", "載入失敗");
});
