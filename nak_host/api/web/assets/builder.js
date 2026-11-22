/* NebulonAK builder — node-based visual canvas over pipeline JSON.
 * No dependencies. Nodes are absolutely positioned divs; edges are SVG beziers.
 * Backend stays the DAG validator + runner; x/y are canvas-only metadata.
 */
"use strict";

/* ---------- constants ---------- */
const NODE_W = 190, OX = 3000, OY = 2000;
const NODE_TYPES = {
  prompt: { label: "Prompt", color: "#58a6ff", desc: "Ask LLM" },
  tool: { label: "Tool", color: "#d29922", desc: "Run harness tool" },
  craft: { label: "Craft", color: "#bc8cff", desc: "Run craft plan" },
  condition: { label: "Condition", color: "#3fb950", desc: "Branch true/false" },
  agent: { label: "Agent", color: "#39c5cf", desc: "Delegate to worker" },
  verify: { label: "Verify", color: "#f778ba", desc: "Check output text" },
};
const TOOL_ACTIONS = {
  file: ["read", "list", "write", "edit", "delete"],
  directory: ["create", "list", "rename", "delete"],
  terminal: ["exec", "run", "sh", "uv", "python"],
  search: ["glob", "find", "grep", "search"],
  observe: ["capture", "read"],
  assertion: ["check", "run", "assert"],
  trace: ["trace", "read", "grep", "tail"],
  scorecard: ["score", "run", "calculate"],
  report: ["write", "generate", "save"],
};

/* ---------- state ---------- */
let state = { name: "first-proj", description: "My pipeline", worker: "viyan", nodes: [], edges: [] };
let view = { x: 40, y: 20, k: 1 };
let selNode = null, selEdge = null;
let MANIFESTS = { workers: [], tools: [], crafts: [], teams: [] };
let PIPELINE_DIR = "";
let counter = 0;
let runStates = {};   // nodeId -> "ok" | "fail" | "skip"
let running = false;
let undoStack = [], redoStack = [];
let pendingFrom = null;  // {node, handle} for click-click connect
let dragNode = null, edgeDrag = null, panning = null;

function slugify(s) {
  return String(s || "").trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 64) || "pipeline";
}
function uid(prefix) {
  counter += 1;
  return (prefix || "n") + counter + "-" + String(Date.now()).slice(-4);
}
function snap() {
  return JSON.stringify({ nodes: state.nodes, edges: state.edges });
}
function pushHistory(prev) {
  undoStack.push(prev === undefined ? snap() : prev);
  if (undoStack.length > 50) undoStack.shift();
  redoStack = [];
}
function doUndo() {
  if (!undoStack.length) return;
  redoStack.push(snap());
  const s = JSON.parse(undoStack.pop());
  state.nodes = s.nodes; state.edges = s.edges;
  selNode = selEdge = null; pendingFrom = null;
  render();
}
function doRedo() {
  if (!redoStack.length) return;
  undoStack.push(snap());
  const s = JSON.parse(redoStack.pop());
  state.nodes = s.nodes; state.edges = s.edges;
  selNode = selEdge = null; pendingFrom = null;
  render();
}
function nodeById(id) { return state.nodes.find(n => n.id === id); }
function nodeType(id) { const n = nodeById(id); return n ? n.type : ""; }

function defaultConfig(type) {
  if (type === "prompt") return { text: "Grade worker viyan" };
  if (type === "tool") return { tool: "file", args: { action: "read", actions: ["read"], path: "" } };
  if (type === "condition") return { contains: "OK" };
  if (type === "agent") return { worker: "viyan", goal: "Do the task" };
  if (type === "verify") return { expect: "done" };
  return { craft: "plan", goal: "ship login" };
}

/* ---------- definition I/O ---------- */
function toDefinition() {
  const nodes = state.nodes.map(n => {
    const o = { id: n.id, type: n.type, label: n.label || n.type, config: n.config || {} };
    if (typeof n.x === "number" && typeof n.y === "number") { o.x = Math.round(n.x); o.y = Math.round(n.y); }
    return o;
  });
  const edges = state.edges.map(e => {
    const o = { from_id: e.from_id, to_id: e.to_id };
    if (e.label && nodeType(e.from_id) === "condition") o.label = e.label;
    return o;
  });
  const name = (document.getElementById("pname") || {}).value;
  const descEl = document.getElementById("pdesc");
  const workEl = document.getElementById("pworker");
  return {
    name: (name || "").trim() || state.name,
    slug: slugify((name || "").trim() || state.name),
    description: descEl ? descEl.value.trim() : (state.description || ""),
    worker: (workEl && workEl.value) || state.worker || "viyan",
    team: "",
    nodes, edges,
  };
}

function autoLayout() {
  // BFS depths from start; place unpositioned nodes, keep existing ones.
  const byId = {};
  state.nodes.forEach(n => { byId[n.id] = n; });
  const indeg = {}, adj = {};
  state.nodes.forEach(n => { indeg[n.id] = 0; adj[n.id] = []; });
  state.edges.forEach(e => {
    if (byId[e.from_id] && byId[e.to_id]) { adj[e.from_id].push(e.to_id); indeg[e.to_id]++; }
  });
  let start = state.nodes.find(n => indeg[n.id] === 0);
  const depth = {};
  if (start) {
    const q = [[start.id, 0]];
    depth[start.id] = 0;
    while (q.length) {
      const [id, d] = q.shift();
      (adj[id] || []).forEach(t => { if (!(t in depth)) { depth[t] = d + 1; q.push([t, d + 1]); } });
    }
  }
  const perDepth = {};
  let gi = 0;
  const order = state.nodes.slice().sort((a, b) => (depth[a.id] || 0) - (depth[b.id] || 0));
  order.forEach(n => {
    if (typeof n.x === "number" && typeof n.y === "number") return;
    const d = depth[n.id] || 0;
    perDepth[d] = perDepth[d] || 0;
    const slot = (perDepth[d] === 0 && d > 0) ? (gi % 3) : perDepth[d];
    n.x = 80 + d * 260;
    n.y = 120 + slot * 150;
    perDepth[d]++;
    gi++;
  });
  // any still missing (disconnected): grid them
  state.nodes.forEach(n => {
    if (typeof n.x !== "number" || typeof n.y !== "number") { n.x = 80 + (gi % 4) * 260; n.y = 120 + Math.floor(gi / 4) * 150; gi++; }
  });
}

function loadDefinition(d) {
  state = {
    name: d.name || "pipeline",
    description: d.description || "",
    worker: d.worker || "viyan",
    nodes: (d.nodes || []).map(n => ({
      id: String(n.id), type: String(n.type), label: String(n.label || ""),
      x: (typeof n.x === "number" ? n.x : undefined),
      y: (typeof n.y === "number" ? n.y : undefined),
      config: (n.config && typeof n.config === "object") ? n.config : {},
    })),
    edges: (d.edges || []).filter(e => e && e.from_id && e.to_id).map(e => ({
      from_id: String(e.from_id), to_id: String(e.to_id),
      label: String(e.label || "").toLowerCase() === "true" ? "true"
        : String(e.label || "").toLowerCase() === "false" ? "false" : "",
    })),
  };
  document.getElementById("pname").value = state.name;
  document.getElementById("pdesc").value = state.description;
  syncWorkerSelect();
  selNode = selEdge = null;
  pendingFrom = null;
  runStates = {};
  counter = state.nodes.length;
  undoStack = []; redoStack = [];
  autoLayout();
  fitView();
  render();
}

/* ---------- view transform ---------- */
function applyView() {
  const w = document.getElementById("world");
  w.style.transform = "translate(" + view.x + "px," + view.y + "px) scale(" + view.k + ")";
  document.getElementById("zoomLbl").textContent = Math.round(view.k * 100) + "%";
  drawMinimap();
}
function screenToWorld(cx, cy) {
  const r = document.getElementById("viewport").getBoundingClientRect();
  return { x: (cx - r.left - view.x) / view.k, y: (cy - r.top - view.y) / view.k };
}
function zoomAt(cx, cy, factor) {
  const r = document.getElementById("viewport").getBoundingClientRect();
  const k2 = Math.min(1.8, Math.max(0.3, view.k * factor));
  const f = k2 / view.k;
  view.x = cx - r.left - (cx - r.left - view.x) * f;
  view.y = cy - r.top - (cy - r.top - view.y) * f;
  view.k = k2;
  applyView();
}
function fitView() {
  const vp = document.getElementById("viewport");
  const vw = vp.clientWidth || 600, vh = vp.clientHeight || 480;
  if (!state.nodes.length) { view = { x: 40, y: 20, k: 1 }; applyView(); return; }
  let minX = 1e9, minY = 1e9, maxX = -1e9, maxY = -1e9;
  state.nodes.forEach(n => {
    const x = (n.x || 0) + OX, y = (n.y || 0) + OY;
    minX = Math.min(minX, x); minY = Math.min(minY, y);
    maxX = Math.max(maxX, x + NODE_W); maxY = Math.max(maxY, y + 120);
  });
  const pad = 60, bw = (maxX - minX) + pad * 2, bh = (maxY - minY) + pad * 2;
  const k = Math.min(1.2, vw / bw, vh / bh);
  view.k = Math.max(0.3, k);
  view.x = (vw - bw * view.k) / 2 - (minX - pad) * view.k;
  view.y = (vh - bh * view.k) / 2 - (minY - pad) * view.k;
  applyView();
}

/* ---------- rendering ---------- */
function nodeHeight(id) {
  const e = document.querySelector('.node[data-id="' + id + '"]');
  return e ? e.offsetHeight : 100;
}
function portPos(id, handle) {
  const n = nodeById(id);
  const x = (n.x || 0) + OX, y = (n.y || 0) + OY;
  const h = nodeHeight(id);
  if (handle === "in") return { x: x, y: y + h / 2 };
  if (handle === "true") return { x: x + NODE_W, y: y + h / 2 - 12 };
  if (handle === "false") return { x: x + NODE_W, y: y + h / 2 + 12 };
  return { x: x + NODE_W, y: y + h / 2 };
}
function edgePath(p1, p2) {
  const dx = Math.max(40, Math.abs(p2.x - p1.x) / 2);
  return "M " + p1.x + " " + p1.y + " C " + (p1.x + dx) + " " + p1.y + ", " + (p2.x - dx) + " " + p2.y + ", " + p2.x + " " + p2.y;
}

function render() {
  const layer = document.getElementById("nodes");
  layer.innerHTML = "";
  const order = state.nodes.slice();
  order.forEach(n => {
    const meta = NODE_TYPES[n.type] || { label: n.type, color: "#78909c" };
    const box = document.createElement("div");
    box.className = "node" + (selNode === n.id ? " selected" : "") + (runStates[n.id] ? " st-" + runStates[n.id] : "");
    box.dataset.id = n.id;
    box.style.left = ((n.x || 0) + OX) + "px";
    box.style.top = ((n.y || 0) + OY) + "px";
    box.style.borderLeftColor = meta.color;
    const head = document.createElement("div");
    head.className = "node-head";
    const dot = document.createElement("span");
    dot.className = "dot" + (runStates[n.id] ? " st-" + runStates[n.id] : "");
    dot.style.background = runStates[n.id] === "ok" ? "var(--good)" : runStates[n.id] === "fail" ? "var(--bad)" : meta.color;
    head.appendChild(dot);
    const tag = document.createElement("span");
    tag.className = "node-type";
    tag.textContent = meta.label.toUpperCase();
    head.appendChild(tag);
    if (running) {
      const spin = document.createElement("span");
      spin.className = "spin";
      head.appendChild(spin);
    }
    box.appendChild(head);
    const lab = document.createElement("div");
    lab.className = "node-label";
    lab.textContent = n.label || n.type;
    box.appendChild(lab);
    const pin = document.createElement("div");
    pin.className = "port in";
    pin.dataset.node = n.id; pin.dataset.handle = "in";
    pin.title = "input";
    box.appendChild(pin);
    if (n.type === "condition") {
      [["true", "T"], ["false", "F"]].forEach(([h, t]) => {
        const p = document.createElement("div");
        p.className = "port out " + h + (pendingFrom && pendingFrom.node === n.id && pendingFrom.handle === h ? " pending" : "");
        p.dataset.node = n.id; p.dataset.handle = h;
        p.title = h + " branch";
        const lt = document.createElement("span");
        lt.className = "port-tag";
        lt.textContent = t;
        p.appendChild(lt);
        box.appendChild(p);
      });
    } else {
      const p = document.createElement("div");
      p.className = "port out" + (pendingFrom && pendingFrom.node === n.id ? " pending" : "");
      p.dataset.node = n.id; p.dataset.handle = "out";
      p.title = "output — drag to an input";
      box.appendChild(p);
    }
    box.addEventListener("mousedown", onNodeDown);
    layer.appendChild(box);
  });
  drawEdges();
  drawMinimap();
  renderProps();
  refreshJson();
  const ub = document.getElementById("btn-undo"), rb = document.getElementById("btn-redo");
  if (ub) ub.disabled = !undoStack.length;
  if (rb) rb.disabled = !redoStack.length;
}

function drawEdges(temp) {
  const svg = document.getElementById("wires");
  svg.innerHTML = "";
  const NS = "http://www.w3.org/2000/svg";
  state.edges.forEach((e, idx) => {
    if (!nodeById(e.from_id) || !nodeById(e.to_id)) return;
    const h = e.label === "true" ? "true" : e.label === "false" ? "false" : "out";
    const p1 = portPos(e.from_id, h === "out" ? "out" : h);
    const p2 = portPos(e.to_id, "in");
    const g = document.createElementNS(NS, "g");
    g.setAttribute("class", "edge" + (selEdge === idx ? " selected" : ""));
    const hit = document.createElementNS(NS, "path");
    hit.setAttribute("d", edgePath(p1, p2));
    hit.setAttribute("class", "wire-hit");
    hit.addEventListener("mousedown", (ev) => { ev.stopPropagation(); selEdge = idx; selNode = null; render(); });
    const line = document.createElementNS(NS, "path");
    line.setAttribute("d", edgePath(p1, p2));
    line.setAttribute("class", "wire" + (e.label ? " has-label" : ""));
    const mid = document.createElementNS(NS, "text");
    if (e.label) {
      const mx = (p1.x + p2.x) / 2, my = (p1.y + p2.y) / 2 - 6;
      mid.setAttribute("x", mx); mid.setAttribute("y", my);
      mid.setAttribute("class", "wire-label " + e.label);
      mid.textContent = e.label === "true" ? "T" : "F";
    }
    g.appendChild(hit); g.appendChild(line);
    if (e.label) g.appendChild(mid);
    svg.appendChild(g);
  });
  if (temp) {
    const line = document.createElementNS(NS, "path");
    line.setAttribute("d", edgePath(temp.p1, temp.p2));
    line.setAttribute("class", "wire temp");
    svg.appendChild(line);
  }
}

function drawMinimap() {
  const mm = document.getElementById("minimap");
  const MW = 170, MH = 110;
  mm.setAttribute("viewBox", "0 0 " + MW + " " + MH);
  mm.innerHTML = "";
  const NS = "http://www.w3.org/2000/svg";
  if (!state.nodes.length) return;
  let minX = 1e9, minY = 1e9, maxX = -1e9, maxY = -1e9;
  state.nodes.forEach(n => {
    const x = (n.x || 0) + OX, y = (n.y || 0) + OY;
    minX = Math.min(minX, x); minY = Math.min(minY, y);
    maxX = Math.max(maxX, x + NODE_W); maxY = Math.max(maxY, y + 110);
  });
  const pad = 40, bw = Math.max(1, maxX - minX + pad * 2), bh = Math.max(1, maxY - minY + pad * 2);
  const k = Math.min(MW / bw, MH / bh);
  const ox = (MW - bw * k) / 2 - (minX - pad) * k, oy = (MH - bh * k) / 2 - (minY - pad) * k;
  state.nodes.forEach(n => {
    const meta = NODE_TYPES[n.type] || { color: "#78909c" };
    const r = document.createElementNS(NS, "rect");
    r.setAttribute("x", ((n.x || 0) + OX) * k + ox);
    r.setAttribute("y", ((n.y || 0) + OY) * k + oy);
    r.setAttribute("width", Math.max(3, NODE_W * k));
    r.setAttribute("height", Math.max(3, 60 * k));
    r.setAttribute("fill", meta.color);
    r.setAttribute("opacity", selNode === n.id ? "1" : "0.55");
    mm.appendChild(r);
  });
  const vp = document.getElementById("viewport").getBoundingClientRect();
  const vx = (-view.x) / view.k, vy = (-view.y) / view.k, vw = vp.width / view.k, vh = vp.height / view.k;
  const vr = document.createElementNS(NS, "rect");
  vr.setAttribute("x", vx * k + ox); vr.setAttribute("y", vy * k + oy);
  vr.setAttribute("width", vw * k); vr.setAttribute("height", vh * k);
  vr.setAttribute("class", "mm-view");
  mm.appendChild(vr);
  mm.onmousedown = (e) => {
    const r = mm.getBoundingClientRect();
    const mx = (e.clientX - r.left) * (MW / r.width), my = (e.clientY - r.top) * (MH / r.height);
    const wx = (mx - ox) / k, wy = (my - oy) / k;
    const vpr = document.getElementById("viewport").getBoundingClientRect();
    view.x = vpr.width / 2 - wx * view.k;
    view.y = vpr.height / 2 - wy * view.k;
    applyView();
  };
}

/* ---------- mouse: nodes, ports, pan ---------- */
function onNodeDown(e) {
  if (e.button !== 0) return;
  if (e.target.closest(".port")) return;
  const id = e.currentTarget.dataset.id;
  const sx = e.clientX, sy = e.clientY;
  const n = nodeById(id);
  const ox = n.x || 0, oy = n.y || 0;
  let moved = false;
  pushHistory();
  const mv = (ev) => {
    if (Math.abs(ev.clientX - sx) + Math.abs(ev.clientY - sy) > 3) moved = true;
    if (!moved) return;
    n.x = Math.round(ox + (ev.clientX - sx) / view.k);
    n.y = Math.round(oy + (ev.clientY - sy) / view.k);
    const box = document.querySelector('.node[data-id="' + id + '"]');
    if (box) { box.style.left = (n.x + OX) + "px"; box.style.top = (n.y + OY) + "px"; }
    drawEdges();
  };
  const up = () => {
    window.removeEventListener("mousemove", mv);
    window.removeEventListener("mouseup", up);
    if (!moved) { undoStack.pop(); selNode = id; selEdge = null; pendingFrom = null; render(); return; }
    const def = toDefinition();
    document.getElementById("json").textContent = JSON.stringify(def, null, 2);
    drawMinimap();
    renderProps();
  };
  window.addEventListener("mousemove", mv);
  window.addEventListener("mouseup", up);
  e.stopPropagation();
}

function portHandle(port) { return { node: port.dataset.node, handle: port.dataset.handle }; }

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("nodes").addEventListener("mousedown", (e) => {
    const p = e.target.closest(".port");
    if (!p || e.button !== 0) return;
    e.stopPropagation();
    e.preventDefault();
    const h = portHandle(p);
    if (h.handle === "in") {
      if (pendingFrom) { finishConnect(pendingFrom.node, pendingFrom.handle, h.node); pendingFrom = null; render(); }
      return;
    }
    edgeDrag = { from: h, moved: false };
    const mv = (ev) => {
      edgeDrag.moved = true;
      const w = screenToWorld(ev.clientX, ev.clientY);
      drawEdges({ p1: portPos(h.node, h.handle), p2: w });
    };
    const up = (ev) => {
      window.removeEventListener("mousemove", mv);
      window.removeEventListener("mouseup", up);
      const t = document.elementFromPoint(ev.clientX, ev.clientY);
      const inp = t ? t.closest(".port.in") : null;
      if (inp) finishConnect(h.node, h.handle, inp.dataset.node);
      else if (!edgeDrag.moved) { pendingFrom = h; render(); }
      edgeDrag = null;
      drawEdges();
    };
    window.addEventListener("mousemove", mv);
    window.addEventListener("mouseup", up);
  });
});

function canConnect(fromId, handle, toId) {
  if (fromId === toId) return { ok: false, error: "cannot connect a node to itself" };
  if (!nodeById(fromId) || !nodeById(toId)) return { ok: false, error: "unknown node" };
  if (state.edges.some(e => e.from_id === fromId && e.to_id === toId)) return { ok: false, error: "already connected" };
  const indeg = state.edges.filter(e => e.to_id === toId).length;
  if (indeg >= 1) return { ok: false, error: "target already has an input (no joins in v1)" };
  const srcType = nodeType(fromId);
  if (srcType === "condition") {
    if (handle !== "true" && handle !== "false") return { ok: false, error: "condition connects via T/F ports" };
    if (state.edges.some(e => e.from_id === fromId && e.label === handle)) return { ok: false, error: "condition already has a '" + handle + "' edge" };
  } else if (state.edges.some(e => e.from_id === fromId)) {
    return { ok: false, error: "node already has an output (only condition may branch)" };
  }
  return { ok: true };
}

function finishConnect(fromId, handle, toId) {
  const chk = canConnect(fromId, handle, toId);
  if (!chk.ok) { banner("cannot connect: " + chk.error); return; }
  pushHistory();
  banner("");
  const label = nodeType(fromId) === "condition" ? handle : "";
  state.edges.push({ from_id: fromId, to_id: toId, label });
  selEdge = state.edges.length - 1;
  selNode = null;
  render();
}

/* ---------- props panel ---------- */
function renderProps() {
  const box = document.getElementById("props");
  box.innerHTML = "";
  if (selEdge !== null && state.edges[selEdge]) {
    const e = state.edges[selEdge];
    const d = el("div", null, e.from_id + " → " + e.to_id + (e.label ? " [" + e.label + "]" : ""));
    box.appendChild(d);
    const del = document.createElement("button");
    del.textContent = "Delete edge";
    del.onclick = () => { pushHistory(); state.edges.splice(selEdge, 1); selEdge = null; render(); };
    box.appendChild(del);
    return;
  }
  const n = nodeById(selNode);
  if (!n) { box.appendChild(el("span", "dim", "Click a node or edge.")); return; }
  const meta = NODE_TYPES[n.type] || { label: n.type };
  const h = el("div", "dim", meta.label + " · " + n.id);
  box.appendChild(h);
  const mk = (labelText, value, oninput, textarea) => {
    const lab = document.createElement("label");
    lab.textContent = labelText + " ";
    let inp;
    if (textarea) { inp = document.createElement("textarea"); inp.rows = 3; }
    else { inp = document.createElement("input"); }
    inp.value = value || "";
    inp.oninput = () => { oninput(inp.value); refreshJson(); };
    lab.appendChild(inp);
    box.appendChild(lab);
  };
  const mkSelect = (labelText, value, options, oninput) => {
    const lab = document.createElement("label");
    lab.textContent = labelText + " ";
    const sel = document.createElement("select");
    const opts = options.length ? options : [value || ""];
    opts.forEach(o => {
      const op = document.createElement("option");
      op.value = o; op.textContent = o;
      if (o === value) op.selected = true;
      sel.appendChild(op);
    });
    if (value && !opts.includes(value)) {
      const op = document.createElement("option");
      op.value = value; op.textContent = value + " (custom)"; op.selected = true;
      sel.appendChild(op);
    }
    sel.onchange = () => { pushHistory(); oninput(sel.value); render(); };
    lab.appendChild(sel);
    box.appendChild(lab);
  };
  if (n.type === "prompt") mk("text", (n.config || {}).text, v => { n.config.text = v; }, true);
  if (n.type === "tool") {
    mkSelect("tool (from tools.json)", (n.config || {}).tool || "file", MANIFESTS.tools, v => {
      n.config.tool = v;
      const acts = TOOL_ACTIONS[v] || [];
      n.config.args = n.config.args || {};
      const cur = Array.isArray(n.config.args.actions) ? n.config.args.actions : [n.config.args.action];
      const kept = cur.filter(a => acts.includes(a));
      n.config.args.actions = kept.length ? kept : (acts.length ? [acts[0]] : []);
      n.config.args.action = n.config.args.actions[0] || "";
    });
    const toolName = (n.config || {}).tool || "file";
    const actions = TOOL_ACTIONS[toolName] || ["read", "list", "write", "edit", "delete"];
    n.config.args = n.config.args || {};
    if (!Array.isArray(n.config.args.actions) || !n.config.args.actions.length) {
      n.config.args.actions = [n.config.args.action || actions[0]];
    }
    const actLab = document.createElement("label");
    actLab.textContent = "actions (tick multiple) ";
    const actBox = document.createElement("div");
    actBox.className = "row";
    actions.forEach(a => {
      const cbLab = document.createElement("label");
      cbLab.style.display = "inline";
      const cb = document.createElement("input");
      cb.type = "checkbox"; cb.value = a;
      cb.checked = n.config.args.actions.includes(a);
      cb.style.width = "auto";
      cb.onchange = () => {
        pushHistory();
        const set = new Set(n.config.args.actions || []);
        if (cb.checked) set.add(a); else set.delete(a);
        if (!set.size) set.add(a);
        n.config.args.actions = actions.filter(x => set.has(x));
        n.config.args.action = n.config.args.actions[0];
        render();
      };
      cbLab.appendChild(cb);
      cbLab.appendChild(document.createTextNode(" " + a));
      actBox.appendChild(cbLab);
    });
    actLab.appendChild(actBox);
    box.appendChild(actLab);
    mk("path (absolute allowed)", ((n.config || {}).args || {}).path, v => { n.config.args = n.config.args || {}; n.config.args.path = v; });
    const row = document.createElement("div");
    row.className = "row";
    const fb = document.createElement("button");
    fb.type = "button"; fb.textContent = "📁 Pick file";
    const fi = document.createElement("input");
    fi.type = "file"; fi.style.display = "none";
    fi.onchange = () => {
      if (fi.files && fi.files.length) {
        pushHistory();
        n.config.args = n.config.args || {};
        n.config.args.path = fi.files[0].name;
        render();
      }
    };
    fb.onclick = () => fi.click();
    row.appendChild(fb);
    box.appendChild(row);
  }
  if (n.type === "craft") {
    mkSelect("craft (from crafts.json)", (n.config || {}).craft || "plan", MANIFESTS.crafts, v => { n.config.craft = v; });
    mk("goal", (n.config || {}).goal, v => { n.config.goal = v; }, true);
  }
  if (n.type === "condition") {
    mk("contains", (n.config || {}).contains, v => { n.config.contains = v; });
    const outs = state.edges.filter(e => e.from_id === n.id);
    const tEdge = outs.find(e => e.label === "true");
    const fEdge = outs.find(e => e.label === "false");
    box.appendChild(el("div", "dim small", "true → " + (tEdge ? tEdge.to_id : "(connect T port)")));
    box.appendChild(el("div", "dim small", "false → " + (fEdge ? fEdge.to_id : "(connect F port)")));
  }
  if (n.type === "agent") {
    mkSelect("worker (from workers.json)", (n.config || {}).worker || "viyan", MANIFESTS.workers, v => { n.config.worker = v; });
    mk("goal", (n.config || {}).goal, v => { n.config.goal = v; }, true);
  }
  if (n.type === "verify") {
    mk("expect", (n.config || {}).expect, v => { n.config.expect = v; });
    box.appendChild(el("div", "dim small", "Passes when the previous step's output contains this text."));
  }
  const del = document.createElement("button");
  del.textContent = "Delete node";
  del.onclick = () => {
    pushHistory();
    state.edges = state.edges.filter(e => e.from_id !== n.id && e.to_id !== n.id);
    state.nodes = state.nodes.filter(x => x.id !== n.id);
    selNode = null;
    render();
  };
  box.appendChild(del);
}

/* ---------- JSON + save location ---------- */
function refreshJson() {
  const def = toDefinition();
  document.getElementById("json").textContent = JSON.stringify(def, null, 2);
  const loc = document.getElementById("saveLoc");
  if (loc) loc.textContent = "saving to: " + (PIPELINE_DIR ? PIPELINE_DIR + "/" + def.slug + "/pipeline.json" : def.slug + "/pipeline.json");
}

/* ---------- backend wiring ---------- */
function syncWorkerSelect() {
  const wsel = document.getElementById("pworker");
  wsel.innerHTML = "";
  const workers = MANIFESTS.workers.length ? MANIFESTS.workers : [state.worker || "viyan"];
  workers.forEach(w => {
    const o = document.createElement("option");
    o.value = w; o.textContent = w;
    if (w === (state.worker || "viyan")) o.selected = true;
    wsel.appendChild(o);
  });
  wsel.value = state.worker || "viyan";
}

async function loadManifests() {
  try {
    const m = await nakApi.manifests();
    MANIFESTS = { workers: m.workers || [], tools: m.tools || [], crafts: m.crafts || [], teams: m.teams || [] };
    document.getElementById("manifestInfo").textContent =
      MANIFESTS.workers.length + " workers · " + MANIFESTS.tools.length + " tools · " +
      MANIFESTS.crafts.length + " crafts (branching: condition/agent/verify ready)";
  } catch (e) {
    document.getElementById("manifestInfo").textContent = "manifests failed: " + e.message;
    MANIFESTS = { workers: ["viyan"], tools: ["file"], crafts: ["plan"], teams: [] };
  }
  syncWorkerSelect();
}

async function loadLocation() {
  try {
    const r = await nakApi.pipelineLocation();
    PIPELINE_DIR = r.directory || "";
  } catch (e) { PIPELINE_DIR = ""; }
  refreshJson();
}

async function refreshSaved() {
  try {
    const r = await nakApi.listPipelines();
    const sel = document.getElementById("saved");
    sel.innerHTML = "";
    (r.definitions || []).forEach(d => {
      const o = document.createElement("option");
      o.value = d.slug;
      o.textContent = (d.builtin ? "★ " : "") + d.slug + " — " + (d.name || "");
      sel.appendChild(o);
    });
  } catch (e) { banner("list failed: " + e.message); }
}

/* ---------- run ---------- */
function setRunStates(result) {
  runStates = {};
  (result.steps || []).forEach(s => {
    if (s.skipped) runStates[s.id] = "skip";
    else if (s.ok === true) runStates[s.id] = "ok";
    else if (s.ok === false) runStates[s.id] = "fail";
  });
}

async function init() {
  // palette drag (HTML5 DnD) + click-to-add
  document.querySelectorAll(".pal-item").forEach(item => {
    item.addEventListener("dragstart", e => {
      e.dataTransfer.setData("text/node-type", item.dataset.type);
    });
    item.addEventListener("click", () => addNodeAtCenter(item.dataset.type));
  });
  const vp = document.getElementById("viewport");
  vp.addEventListener("dragover", e => e.preventDefault());
  vp.addEventListener("drop", e => {
    e.preventDefault();
    const type = e.dataTransfer.getData("text/node-type");
    if (type) {
      const w = screenToWorld(e.clientX, e.clientY);
      addNode(type, w.x - NODE_W / 2, w.y - 40);
    }
  });
  // pan on background
  vp.addEventListener("mousedown", e => {
    if (e.button !== 0) return;
    if (e.target.closest(".node") || e.target.closest(".port") || e.target.closest("button")) return;
    if (e.target.closest("#minimap")) return;
    const sx = e.clientX, sy = e.clientY, ox = view.x, oy = view.y;
    let moved = false;
    pendingFrom = null;
    const mv = ev => {
      if (Math.abs(ev.clientX - sx) + Math.abs(ev.clientY - sy) > 3) moved = true;
      view.x = ox + (ev.clientX - sx);
      view.y = oy + (ev.clientY - sy);
      applyView();
    };
    const up = () => {
      window.removeEventListener("mousemove", mv);
      window.removeEventListener("mouseup", up);
      if (!moved) { selNode = selEdge = null; render(); }
      else drawMinimap();
    };
    window.addEventListener("mousemove", mv);
    window.addEventListener("mouseup", up);
  });
  vp.addEventListener("wheel", e => {
    e.preventDefault();
    zoomAt(e.clientX, e.clientY, e.deltaY < 0 ? 1.12 : 1 / 1.12);
  }, { passive: false });
  document.getElementById("btn-zin").onclick = () => {
    const r = vp.getBoundingClientRect();
    zoomAt(r.left + r.width / 2, r.top + r.height / 2, 1.25);
  };
  document.getElementById("btn-zout").onclick = () => {
    const r = vp.getBoundingClientRect();
    zoomAt(r.left + r.width / 2, r.top + r.height / 2, 1 / 1.25);
  };
  document.getElementById("btn-fit").onclick = fitView;
  document.getElementById("btn-undo").onclick = doUndo;
  document.getElementById("btn-redo").onclick = doRedo;
  document.addEventListener("keydown", e => {
    const t = (e.target.tagName || "").toLowerCase();
    if (t === "input" || t === "textarea" || t === "select") {
      if (e.key === "Escape") e.target.blur();
      return;
    }
    if (e.key === "Delete" || e.key === "Backspace") {
      if (selNode) {
        pushHistory();
        state.edges = state.edges.filter(x => x.from_id !== selNode && x.to_id !== selNode);
        state.nodes = state.nodes.filter(x => x.id !== selNode);
        selNode = null; render();
      } else if (selEdge !== null && state.edges[selEdge]) {
        pushHistory();
        state.edges.splice(selEdge, 1);
        selEdge = null; render();
      }
    } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z" && !e.shiftKey) { e.preventDefault(); doUndo(); }
    else if ((e.ctrlKey || e.metaKey) && (e.key.toLowerCase() === "y" || (e.key.toLowerCase() === "z" && e.shiftKey))) { e.preventDefault(); doRedo(); }
    else if (e.key === "Escape") { pendingFrom = null; render(); }
  });
  // toolbar
  document.getElementById("btn-clear").onclick = () => {
    if (!state.nodes.length) return;
    pushHistory();
    state.nodes = []; state.edges = [];
    selNode = selEdge = null; runStates = {};
    render();
  };
  document.getElementById("btn-load").onclick = async () => {
    const slug = document.getElementById("saved").value;
    if (!slug) return;
    try { banner(""); loadDefinition(await nakApi.getPipeline(slug).then(r => r.definition)); refreshSaved(); }
    catch (e) { banner("load failed: " + e.message); }
  };
  document.getElementById("btn-save").onclick = async () => {
    try {
      banner("");
      const saved = await nakApi.savePipeline(toDefinition());
      banner("");
      document.getElementById("runout").textContent = "saved to " + PIPELINE_DIR + "/" + saved.definition.slug + "/pipeline.json";
      refreshSaved();
    } catch (e) { banner("save failed: " + e.message); }
  };
  document.getElementById("btn-validate").onclick = async () => {
    try {
      const r = await nakApi.validatePipeline(toDefinition());
      document.getElementById("runout").textContent = r.ok
        ? "valid ✓ order: " + (r.ordered || []).join(" → ")
        : "invalid: " + (r.errors || []).join("; ");
    } catch (e) { banner("validate failed: " + e.message); }
  };
  const doRun = async (dry) => {
    const bar = document.getElementById("approvalBar");
    bar.style.display = "none";
    bar.innerHTML = "";
    let pollId = null;
    const stopPoll = () => { if (pollId) clearInterval(pollId); pollId = null; };
    const startPoll = () => {
      stopPoll();
      pollId = setInterval(async () => {
        try {
          const r = await nakApi.listApprovals();
          const list = r.approvals || [];
          if (!list.length) { bar.style.display = "none"; bar.innerHTML = ""; return; }
          bar.style.display = "block";
          bar.innerHTML = "";
          const h = document.createElement("div");
          h.textContent = "Agent waits for approval (" + list.length + "):";
          bar.appendChild(h);
          list.forEach(a => {
            const row = document.createElement("div");
            row.className = "row";
            const txt = document.createElement("span");
            txt.className = "mono";
            txt.textContent = (a.worker || "?") + " wants " + (a.tool || "?") + " — " + (a.what || "");
            const okB = document.createElement("button");
            okB.textContent = "Approve";
            okB.onclick = async () => { await nakApi.decideApproval(a.id, true); };
            const noB = document.createElement("button");
            noB.textContent = "Deny";
            noB.onclick = async () => { await nakApi.decideApproval(a.id, false); };
            row.appendChild(txt); row.appendChild(okB); row.appendChild(noB);
            bar.appendChild(row);
          });
        } catch (e) { /* polling must never break run */ }
      }, 2000);
    };
    try {
      running = true; runStates = {}; render();
      document.getElementById("runout").textContent = dry ? "dry running…" : "running… (if approval needed, popup appears below)";
      const auto = document.getElementById("papprove").checked;
      if (!dry && !auto) startPoll();
      const r = await nakApi.runPipeline(toDefinition(), document.getElementById("pworker").value, dry, auto);
      stopPoll();
      running = false;
      bar.style.display = "none"; bar.innerHTML = "";
      if (!dry) { setRunStates(r); render(); }
      let out = JSON.stringify(r, null, 2).slice(0, 4000);
      const bad = (r.steps || []).find(s => s && s.ok === false);
      const hint = bad && (bad.output && bad.output.hint || bad.hint);
      if (hint) out += "\nHINT: " + hint;
      document.getElementById("runout").textContent = out;
    } catch (e) { stopPoll(); running = false; render(); banner("run failed: " + e.message); }
  };
  document.getElementById("btn-dry").onclick = () => doRun(true);
  document.getElementById("btn-run").onclick = () => doRun(false);
  ["pname", "pdesc"].forEach(id => document.getElementById(id).addEventListener("input", () => {
    state.name = document.getElementById("pname").value;
    state.description = document.getElementById("pdesc").value;
    refreshJson();
  }));
  document.getElementById("pworker").addEventListener("change", e => { state.worker = e.target.value; refreshJson(); });
  await loadManifests();
  await loadLocation();
  await refreshSaved();
  try { loadDefinition(await nakApi.getPipeline("vetting").then(r => r.definition)); }
  catch (e) { applyView(); render(); }
}

function addNode(type, x, y) {
  pushHistory();
  const id = uid("n");
  const n = state.nodes.length;
  state.nodes.push({
    id, type,     label: ((NODE_TYPES[type] || {}).label || type) + " " + (n + 1),
    x: Math.round(x !== undefined ? x : 80 + (n % 4) * 260),
    y: Math.round(y !== undefined ? y : 120 + Math.floor(n / 4) * 150),
    config: defaultConfig(type),
  });
  selNode = id; selEdge = null;
  render();
}

function addNodeAtCenter(type) {
  const vp = document.getElementById("viewport").getBoundingClientRect();
  const w = screenToWorld(vp.left + vp.width / 2, vp.top + vp.height / 2);
  addNode(type, w.x - NODE_W / 2 + (counter % 5) * 24, w.y - 50 + (counter % 5) * 24);
}

init();
