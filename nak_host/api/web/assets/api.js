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

async function apiPost(path, body) {
  const resp = await fetch(BASE + path, {
    method: "POST",
    headers: {"Accept": "application/json", "Content-Type": "application/json"},
    body: JSON.stringify(body || {}),
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new Error("HTTP " + resp.status + " for " + path + ": " + (data.detail || JSON.stringify(data).slice(0, 200)));
  }
  return data;
}

async function apiDelete(path) {
  const resp = await fetch(BASE + path, {method: "DELETE", headers: {"Accept": "application/json"}});
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new Error("HTTP " + resp.status + " for " + path);
  }
  return data;
}

const nakApi = {
  status: () => api("/status"),
  tasks: (limit) => api("/pipeline/tasks", {limit: limit || 20}),
  task: (id) => api("/pipeline/tasks/" + encodeURIComponent(id)),
  sessions: (limit) => api("/pipeline/sessions", {limit: limit || 20}),
  settings: () => api("/settings"),
  manifests: () => api("/settings/manifests"),
  saveSection: (section, values) => apiPost("/settings/section/" + encodeURIComponent(section), {values}),
  listConfigFiles: () => api("/settings/files"),
  getConfigFile: (id) => api("/settings/files/" + encodeURIComponent(id)),
  validateConfigFile: (id, content) => apiPost("/settings/files/" + encodeURIComponent(id) + "/validate", {content}),
  saveConfigFile: (id, content) => apiPost("/settings/files/" + encodeURIComponent(id), {content}),
  listPipelines: () => api("/pipeline_engine/definitions"),
  getPipeline: (slug) => api("/pipeline_engine/definitions/" + encodeURIComponent(slug)),
  pipelineRuns: (slug) => api("/pipeline_engine/definitions/" + encodeURIComponent(slug) + "/runs"),
  savePipeline: (def) => apiPost("/pipeline_engine/definitions", def),
  deletePipeline: (slug) => apiDelete("/pipeline_engine/definitions/" + encodeURIComponent(slug)),
  validatePipeline: (def) => apiPost("/pipeline_engine/validate", {definition: def}),
  runPipeline: (def, worker, dry, autoApprove) => apiPost("/pipeline_engine/run",
    {definition: def, worker: worker || "viyan", dry: !!dry, auto_approve: !!autoApprove}),
  pipelineLocation: () => api("/pipeline_engine/location"),
  workspaceFiles: (prefix) => api("/pipeline_engine/files", {prefix: prefix || ""}),
  listApprovals: () => api("/pipeline_engine/approvals"),
  decideApproval: (id, approved) => apiPost("/pipeline_engine/approvals/" + encodeURIComponent(id), {approved: !!approved}),
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
