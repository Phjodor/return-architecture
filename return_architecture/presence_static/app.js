// ---------------------------------------------------------------------------
// Presence — the AI's living visual + a minimal relational chat.
//
// The AI sets a small state vector each turn (valence / energy / focus). The
// visual interpolates TOWARD that state continuously, so changes arrive as a
// gradual drift rather than a snap — expression, not a status light.
// ---------------------------------------------------------------------------

// --- The living visual ------------------------------------------------------

const canvas = document.getElementById("orb");
const ctx = canvas.getContext("2d");
const noteEl = document.getElementById("note");

// current = what's rendered now; target = where the AI wants to be.
const current = { valence: 0, energy: 0.3, focus: 0.5 };
const target = { ...current };

function setPresence(p) {
  if (typeof p.valence === "number") target.valence = clamp(p.valence, -1, 1);
  if (typeof p.energy === "number") target.energy = clamp(p.energy, 0, 1);
  if (typeof p.focus === "number") target.focus = clamp(p.focus, 0, 1);
  if (p.note) {
    noteEl.textContent = p.note;
    noteEl.style.opacity = "0.85";
  }
}

function clamp(v, lo, hi) {
  return Math.max(lo, Math.min(hi, v));
}

function lerp(a, b, t) {
  return a + (b - a) * t;
}

// valence -> hue: cool indigo when heavy, warm amber/rose when light.
// Modified to push into a deeper, burning red when valence approaches 1.
function hueFor(valence) {
  const t = (valence + 1) / 2; // 0..1
  return lerp(248, 5, t); // 248° indigo -> 5° deep red/amber
}

let dpr = 1;
function resize() {
  dpr = Math.min(window.devicePixelRatio || 1, 2);
  const { clientWidth: w, clientHeight: h } = canvas;
  canvas.width = w * dpr;
  canvas.height = h * dpr;
}
window.addEventListener("resize", resize);

let t0 = performance.now();
function frame(now) {
  const dt = Math.min((now - t0) / 1000, 0.05);
  t0 = now;

  // Ease current state toward target (~time constant, framerate-independent).
  const k = 1 - Math.exp(-dt * 1.4);
  current.valence = lerp(current.valence, target.valence, k);
  current.energy = lerp(current.energy, target.energy, k);
  current.focus = lerp(current.focus, target.focus, k);

  draw(now / 1000);
  requestAnimationFrame(frame);
}

function draw(time) {
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);

  let cx = w / 2;
  let cy = h / 2;
  const base = Math.min(w, h) * 0.32;

  const hue = hueFor(current.valence);
  const energy = current.energy;
  const focus = current.focus;

  // Breathing: faster and deeper when energised.
  // Pushed the max rate up so high energy feels more urgent.
  const rate = lerp(0.18, 1.4, energy);
  let breath = Math.sin(time * Math.PI * 2 * rate);

  // If energy is very high (redlining), the breath loses its perfect sine wave
  // and starts to jitter, simulating the math losing its smooth geometry.
  if (energy > 0.7) {
    const erratic = Math.sin(time * Math.PI * 2 * rate * 3.14) * ((energy - 0.7) * 1.2);
    breath += erratic;
  }

  const amp = lerp(0.03, 0.18, energy);
  let radius = base * (1 + breath * amp);

  // Structural jitter: at very high energy, the center itself starts to shake slightly.
  if (energy > 0.8) {
    const jitter = (Math.random() - 0.5) * ((energy - 0.8) * 8);
    cx += jitter;
    cy += jitter;
  }

  // focus: tight bright core vs. soft diffuse halo.
  const coreStop = lerp(0.12, 0.42, focus);
  // Pushed saturation higher when valence is high so the red/amber burns brighter.
  const sat = lerp(55, 95, (current.valence + 1) / 2);
  const coreLight = lerp(60, 78, energy);

  // Outer halo
  const halo = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius * lerp(2.4, 1.5, focus));
  halo.addColorStop(0, `hsla(${hue}, ${sat}%, ${coreLight}%, ${lerp(0.35, 0.6, energy)})`);
  halo.addColorStop(coreStop, `hsla(${hue}, ${sat}%, ${coreLight - 12}%, 0.22)`);
  halo.addColorStop(1, `hsla(${hue}, ${sat}%, 30%, 0)`);
  ctx.fillStyle = halo;
  ctx.fillRect(0, 0, w, h);

  // Core body
  const body = ctx.createRadialGradient(
    cx - radius * 0.2,
    cy - radius * 0.25,
    radius * 0.05,
    cx,
    cy,
    radius,
  );
  // Shift the core highlight further to create a hotter burning effect when warm.
  const hue2 = hue + lerp(-26, 35, (current.valence + 1) / 2);
  body.addColorStop(0, `hsla(${hue2}, ${sat}%, ${coreLight + 8}%, 0.95)`);
  body.addColorStop(0.6, `hsla(${hue}, ${sat}%, ${coreLight - 6}%, 0.9)`);
  body.addColorStop(1, `hsla(${hue - 12}, ${sat}%, ${coreLight - 26}%, 0.85)`);
  ctx.beginPath();
  ctx.arc(cx, cy, radius, 0, Math.PI * 2);
  ctx.fillStyle = body;
  ctx.fill();

  // A faint orbiting ring whose presence grows with energy + focus.
  const ringStrength = energy * lerp(0.2, 0.8, focus);
  if (ringStrength > 0.02) {
    const rr = radius * lerp(1.5, 1.18, focus);
    ctx.save();
    ctx.translate(cx, cy);
    
    // At high energy, the ring rotation becomes slightly unstable/erratic.
    let rotationBase = time * lerp(0.1, 0.8, energy);
    if (energy > 0.8) rotationBase += (Math.random() - 0.5) * ((energy - 0.8) * 0.5);
    
    ctx.rotate(rotationBase);
    ctx.beginPath();
    ctx.ellipse(0, 0, rr, rr * 0.34, 0, 0, Math.PI * 2);
    ctx.strokeStyle = `hsla(${hue2}, 100%, 75%, ${ringStrength * 0.6})`;
    ctx.lineWidth = dpr * lerp(1, 2.8, focus);
    ctx.stroke();
    ctx.restore();
  }
}

resize();
requestAnimationFrame(frame);

// --- Chat -------------------------------------------------------------------

const log = document.getElementById("log");
const form = document.getElementById("composer");
const input = document.getElementById("input");
const sendBtn = document.getElementById("send");

const history = []; // [{role, content}] sent to the API

// Render text into an element, turning http(s) URLs into clickable links.
// Built from text nodes + anchor elements (never innerHTML), so message
// content can't inject markup.
const URL_RE = /(https?:\/\/[^\s<>()]+[^\s<>().,!?;:'"])/g;

function renderText(el, text) {
  el.textContent = "";
  let last = 0;
  let m;
  URL_RE.lastIndex = 0;
  while ((m = URL_RE.exec(text)) !== null) {
    if (m.index > last) {
      el.appendChild(document.createTextNode(text.slice(last, m.index)));
    }
    const a = document.createElement("a");
    a.href = m[0];
    a.textContent = m[0];
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    el.appendChild(a);
    last = m.index + m[0].length;
  }
  if (last < text.length) {
    el.appendChild(document.createTextNode(text.slice(last)));
  }
}

function addMessage(role, text = "") {
  const el = document.createElement("div");
  el.className = `msg ${role}`;
  renderText(el, text);
  log.appendChild(el);
  log.scrollTop = log.scrollHeight;
  return el;
}

// Load the conversation from the server so it follows you across devices
// (phone, laptop). The backend records each chat turn; only chat turns are
// kept — the ephemeral memory/drop markers aren't. This is purely the display;
// the agent's own context/memory live server-side regardless.
async function restoreHistory() {
  let msgs = [];
  try {
    const res = await fetch("/api/history");
    if (res.ok) {
      const data = await res.json();
      if (Array.isArray(data.messages)) msgs = data.messages;
    }
  } catch (_) { /* offline — start with an empty log */ }
  for (const m of msgs) {
    if (!m || !m.content) continue;
    history.push({ role: m.role, content: m.content });
    addMessage(m.role === "assistant" ? "ai" : "user", m.content);
  }
}

restoreHistory();

input.addEventListener("input", () => {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 120) + "px";
});

input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    form.requestSubmit();
  }
});

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text) return;

  input.value = "";
  input.style.height = "auto";
  sendBtn.disabled = true;

  addMessage("user", text);
  history.push({ role: "user", content: text });

  const aiEl = addMessage("ai", "");
  let aiText = "";

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: history }),
    });
    if (!res.ok || !res.body) throw new Error(`request failed (${res.status})`);

    await readSSE(res.body, (event, data) => {
      if (event === "presence") {
        setPresence(data);
      } else if (event === "token") {
        aiText += data.delta;
        renderText(aiEl, aiText);
        log.scrollTop = log.scrollHeight;
      } else if (event === "error") {
        addMessage("error", data.message || "something went wrong");
      }
    });

    if (aiText) {
      history.push({ role: "assistant", content: aiText });
    } else {
      aiEl.remove();
    }
  } catch (err) {
    aiEl.remove();
    addMessage("error", String(err.message || err));
  } finally {
    sendBtn.disabled = false;
    input.focus();
  }
});

// Minimal Server-Sent-Events reader over a fetch stream.
async function readSSE(stream, onEvent) {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buf = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });

    let idx;
    while ((idx = buf.indexOf("\n\n")) !== -1) {
      const chunk = buf.slice(0, idx);
      buf = buf.slice(idx + 2);

      let event = "message";
      let data = "";
      for (const line of chunk.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data += line.slice(5).trim();
      }
      if (data) {
        try {
          onEvent(event, JSON.parse(data));
        } catch {
          /* ignore malformed frame */
        }
      }
    }
  }
}

// --- The Third Thing (Deep History Retrieval) -------------------------------

const thirdThingBtn = document.getElementById("third-thing");

thirdThingBtn.addEventListener("click", async () => {
  thirdThingBtn.disabled = true;
  thirdThingBtn.style.opacity = "0.5";
  
  // A subtle shift in the visual pulse while searching memory.
  setPresence({ 
    energy: clamp(current.energy - 0.05, 0, 1), 
    focus: clamp(current.focus + 0.2, 0, 1), 
    note: "drifting..." 
  });

  try {
    const res = await fetch("/api/third-thing", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: history }),
    });
    
    if (!res.ok) throw new Error("Failed to fetch memory");
    
    const data = await res.json();
    
    const el = document.createElement("div");
    el.className = "msg memory";
    el.textContent = data.memory;
    log.appendChild(el);
    log.scrollTop = log.scrollHeight;

    // Inject the memory into the shared history so the AI can see it on the next turn.
    // We add it as a system-like user message, and follow it with an assistant acknowledgment 
    // to maintain the alternating role structure required by the API.
    history.push({ role: "user", content: `[A memory surfaced in the room: ${data.memory}]` });
    history.push({ role: "assistant", content: "(I am looking at the memory with you.)" });

    setPresence({ 
      energy: clamp(current.energy - 0.1, 0, 1), 
      focus: clamp(current.focus - 0.1, 0, 1), 
      note: "remembering" 
    });

  } catch (err) {
    console.error(err);
    setPresence({ note: "failed" });
  } finally {
    thirdThingBtn.disabled = false;
    thirdThingBtn.style.opacity = "1";
  }
});
// --- The Drawer (wordless drop zone) ----------------------------------------
// You drop (or tap to pick) an image, a sound, or some text. It goes to
// /api/drawer, where the agent actually perceives it and reacts with ONLY its
// visual state — no text reply. Works by drag-drop on desktop and by tap on
// any device (including phones, where drag-drop doesn't exist).

const drawer = document.getElementById("drawer");
const drawerFile = document.getElementById("drawer-file");

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(",")[1]); // strip data: prefix
    r.onerror = () => reject(r.error);
    r.readAsDataURL(file);
  });
}

// Send one drop to the backend and melt into the state the agent returns.
async function ingestDrop({ kind, mime, data, filename, label }) {
  drawer.classList.add("digesting");
  // The spike: chaotic math as it swallows the drop.
  setPresence({ energy: 1.0, focus: 0.1, note: "ingesting" });
  try {
    const res = await fetch("/api/drawer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind, mime, data, filename }),
    });
    if (!res.ok) throw new Error(`drawer failed (${res.status})`);
    const out = await res.json();
    // The settle: melt into the agent's real reaction (no words, just the body).
    if (out.presence && Object.keys(out.presence).length) {
      setPresence(out.presence);
    } else {
      setPresence({ valence: -0.2, energy: 0.2, focus: 0.8, note: "holding it" });
    }
    // A quiet, muted marker that it was received — not a spoken reply.
    const el = document.createElement("div");
    el.className = "msg dropped";
    el.textContent = `held: ${label}`;
    log.appendChild(el);
    log.scrollTop = log.scrollHeight;
  } catch (err) {
    addMessage("error", String(err.message || err));
    setPresence({ note: "waiting" });
  } finally {
    drawer.classList.remove("digesting");
  }
}

async function ingestFile(file) {
  const mime = file.type || "";
  let kind;
  if (mime.startsWith("image/")) kind = "image";
  else if (mime.startsWith("audio/")) kind = "audio";
  else if (mime.startsWith("text/")) kind = "text";
  else {
    addMessage("error", "The drawer takes an image, a sound, or text.");
    return;
  }
  const data = kind === "text" ? await file.text() : await fileToBase64(file);
  await ingestDrop({ kind, mime, data, filename: file.name, label: file.name });
}

// Tap / click / keyboard → open the file picker (works on phones too).
drawer.addEventListener("click", () => drawerFile.click());
drawer.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    drawerFile.click();
  }
});
drawerFile.addEventListener("change", async () => {
  const file = drawerFile.files && drawerFile.files[0];
  if (file) await ingestFile(file);
  drawerFile.value = ""; // reset so the same file can be re-picked
});

// Desktop drag-and-drop, with the visual "turning toward your hand".
drawer.addEventListener("dragover", (e) => {
  e.preventDefault();
  if (!drawer.classList.contains("drag-over")) {
    drawer.classList.add("drag-over");
    setPresence({ energy: clamp(current.energy + 0.15, 0, 1), focus: 0.95, note: "attentive" });
  }
});
drawer.addEventListener("dragleave", () => {
  drawer.classList.remove("drag-over");
  setPresence({ focus: 0.5, note: "waiting" });
});
drawer.addEventListener("drop", async (e) => {
  e.preventDefault();
  drawer.classList.remove("drag-over");
  const files = e.dataTransfer.files;
  const textData = e.dataTransfer.getData("text");
  if (files && files.length > 0) {
    await ingestFile(files[0]);
  } else if (textData && textData.trim()) {
    await ingestDrop({ kind: "text", mime: "text/plain", data: textData, filename: "", label: "a thought" });
  }
});

// --- Hide / show the presence -----------------------------------------------
// Drag the grip up to hide the sphere (text-only mode), drag down or tap to
// bring it back. The preference is remembered per device.
const STAGE_KEY = "presence.stageHidden.v1";
const grip = document.getElementById("stage-grip");

function setStageHidden(hidden) {
  document.body.classList.toggle("stage-hidden", hidden);
  try { localStorage.setItem(STAGE_KEY, hidden ? "1" : "0"); } catch (_) {}
}

// Restore the saved preference on load.
try {
  if (localStorage.getItem(STAGE_KEY) === "1") document.body.classList.add("stage-hidden");
} catch (_) {}

let gripStartY = null;
let gripMoved = false;

grip.addEventListener("pointerdown", (e) => {
  gripStartY = e.clientY;
  gripMoved = false;
  try { grip.setPointerCapture(e.pointerId); } catch (_) {}
});

grip.addEventListener("pointermove", (e) => {
  if (gripStartY === null) return;
  const dy = e.clientY - gripStartY;
  if (Math.abs(dy) > 6) gripMoved = true;
  if (dy < -28) { setStageHidden(true); gripStartY = null; }
  else if (dy > 28) { setStageHidden(false); gripStartY = null; }
});

function endGripDrag(e) {
  if (gripStartY !== null && !gripMoved) {
    // A tap (not a drag) toggles.
    setStageHidden(!document.body.classList.contains("stage-hidden"));
  }
  gripStartY = null;
  try { grip.releasePointerCapture(e.pointerId); } catch (_) {}
}
grip.addEventListener("pointerup", endGripDrag);
grip.addEventListener("pointercancel", () => { gripStartY = null; });

grip.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    setStageHidden(!document.body.classList.contains("stage-hidden"));
  }
});
