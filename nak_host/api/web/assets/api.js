/* NebulonAK console client — thin fetch() wrapper, no dependencies. */
"use strict";

const BASE = "/api/NebulonAK";

async function api(path, query) {
  const qs = query
    ? "?" + new URLSearchParams(query).toString()
    : "";
  const resp = await fetch(BASE + path + qs, {headers: {"Accept": "application/json"}});
  if (!resp.ok) {
    throw new Error("HTTP " + resp.status + " for " + path);
  }
  return resp.json();
}

const nakApi = {
  status: () => api("/status"),
  tasks: (limit) => api("/pipeline/tasks", {limit: limit || 20}),
  task: (id) => api("/pipeline/tasks/" + encodeURIComponent(id)),
  vetting: (limit) => api("/pipeline/vetting", {limit: limit || 10}),
  sessions: (limit) => api("/pipeline/sessions", {limit: limit || 20}),
  settings: () => api("/settings"),
  manifests: () => api("/settings/manifests"),
};

function banner(msg) {
  const el = document.getElementById("error");
  if (el) {
    el.textContent = msg;
    el.style.display = msg ? "block" : "none";
  }
}

/* Small DOM helpers shared by the pages. */
function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function pill(ok) {
  const s = el("span", "pill " + (ok ? "ok" : "bad"), ok ? "OK" : "DOWN");
  return s;
}
