// Easel · Automation Control Center
// Site 100% estático (GitHub Pages). Toda a "inteligência" vem de:
//   1) API pública do GitHub (api.github.com) — leitura de runs/jobs/releases
//   2) Arquivos versionados do próprio repo via raw.githubusercontent.com
//      (data/db.json, data/stats.json, data/automation-status.json)
//   3) Opcionalmente, um token pessoal guardado só no localStorage do usuário,
//      usado para disparar workflow_dispatch de verdade sem sair da página.
"use strict";

const OWNER = "camillanapoles";
const REPO = "ai-agent-social-media-Easel";
const BRANCH = "main";
const API = `https://api.github.com/repos/${OWNER}/${REPO}`;
const RAW = `https://raw.githubusercontent.com/${OWNER}/${REPO}/${BRANCH}`;

const WORKFLOWS = [
  { id: "ci.yml", title: "CI — testes + skills + frontend", desc: "pytest, validação de skills, lint/build do frontend, lint de instaladores." },
  { id: "crud-data.yml", title: "Backend · CRUD Data", desc: "Create/Read/Update/Delete sobre data/db.json, com cache e object storage." },
  { id: "functions.yml", title: "Backend · Functions", desc: "stats_indexer (cache), backup_object (object storage), report." },
  { id: "pages.yml", title: "Deploy do Painel (Pages)", desc: "Publica este próprio dashboard no GitHub Pages." },
];

const $ = (sel) => document.querySelector(sel);

function tokenKey() { return "easel_automation_pat"; }
function getToken() { return localStorage.getItem(tokenKey()) || ""; }
function setToken(v) { localStorage.setItem(tokenKey(), v); }
function clearToken() { localStorage.removeItem(tokenKey()); }

async function ghFetch(path, opts = {}) {
  const headers = Object.assign({ Accept: "application/vnd.github+json" }, opts.headers || {});
  const token = getToken();
  if (token) headers.Authorization = `token ${token}`;
  const res = await fetch(`${API}${path}`, Object.assign({}, opts, { headers }));
  return res;
}

function badgeFor(run) {
  if (!run) return { cls: "unknown", label: "sem execuções" };
  if (run.status !== "completed") return { cls: "pending", label: run.status };
  if (run.conclusion === "success") return { cls: "success", label: "sucesso" };
  if (run.conclusion === "failure") return { cls: "failure", label: "falhou" };
  return { cls: "unknown", label: run.conclusion || "desconhecido" };
}

function fmtDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("pt-BR");
}

// ---------- Pipelines ----------
async function loadPipelines() {
  const grid = $("#pipeline-grid");
  grid.innerHTML = "";
  for (const wf of WORKFLOWS) {
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `
      <div class="card-title">${wf.title}</div>
      <div class="card-desc">${wf.desc}</div>
      <span class="badge unknown" data-badge>consultando…</span>
      <div class="card-actions">
        <button data-run="${wf.id}">Executar</button>
        <a class="btn secondary" target="_blank" rel="noopener"
           href="https://github.com/${OWNER}/${REPO}/actions/workflows/${wf.id}">Ver no GitHub</a>
      </div>`;
    grid.appendChild(card);

    const badgeEl = card.querySelector("[data-badge]");
    try {
      const res = await ghFetch(`/actions/workflows/${wf.id}/runs?per_page=1`);
      if (res.ok) {
        const data = await res.json();
        const run = data.workflow_runs && data.workflow_runs[0];
        const b = badgeFor(run);
        badgeEl.className = `badge ${b.cls}`;
        badgeEl.textContent = run ? `${b.label} · ${fmtDate(run.created_at)}` : b.label;
        if (run) badgeEl.title = run.html_url;
      } else {
        badgeEl.className = "badge unknown";
        badgeEl.textContent = res.status === 403 ? "limite da API atingido" : "indisponível";
      }
    } catch {
      badgeEl.className = "badge unknown";
      badgeEl.textContent = "offline";
    }

    card.querySelector("[data-run]").addEventListener("click", () => dispatchOrOpen(wf.id));
  }
}

async function dispatchOrOpen(workflowId) {
  const token = getToken();
  const url = `https://github.com/${OWNER}/${REPO}/actions/workflows/${workflowId}`;
  if (!token) {
    termPrint(`sem token salvo — abrindo a tela oficial de execução: ${url}`, "ok");
    window.open(url, "_blank", "noopener");
    return;
  }
  termPrint(`$ dispatch ${workflowId} (via token salvo)`, "cmd");
  try {
    const res = await ghFetch(`/actions/workflows/${workflowId}/dispatches`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ref: BRANCH }),
    });
    if (res.status === 204) {
      termPrint(`✔ gatilho enviado! acompanhe em ${url}`, "ok");
    } else {
      const body = await res.text();
      termPrint(`✖ falhou (HTTP ${res.status}): ${body}`, "err");
    }
  } catch (e) {
    termPrint(`✖ erro de rede: ${e}`, "err");
  }
  setTimeout(loadPipelines, 2500);
}

// ---------- CRUD DB table ----------
async function loadDb() {
  const tbody = document.querySelector("#db-table tbody");
  try {
    const res = await fetch(`${RAW}/data/db.json?cachebust=${Date.now()}`);
    const db = await res.json();
    tbody.innerHTML = "";
    if (!db.records || !db.records.length) {
      tbody.innerHTML = `<tr><td colspan="5">sem registros</td></tr>`;
      return;
    }
    for (const r of db.records) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${r.id}</td><td>${r.title}</td><td>${r.status}</td><td>${r.channel || "—"}</td><td>${fmtDate(r.updated_at)}</td>`;
      tbody.appendChild(tr);
    }
  } catch {
    tbody.innerHTML = `<tr><td colspan="5">falha ao carregar data/db.json</td></tr>`;
  }
}

// ---------- Object storage ----------
async function loadStorage() {
  const el = $("#storage-list");
  el.innerHTML = "";
  try {
    const res = await fetch(`https://api.github.com/repos/${OWNER}/${REPO}/releases/tags/data-store`);
    if (res.ok) {
      const rel = await res.json();
      const assets = rel.assets || [];
      if (!assets.length) {
        el.innerHTML = `<div class="status-row"><span>release <code>data-store</code> existe, sem assets ainda</span></div>`;
      }
      for (const a of assets) {
        const row = document.createElement("div");
        row.className = "status-row";
        row.innerHTML = `<span>${a.name}</span><a href="${a.browser_download_url}" target="_blank" rel="noopener">${(a.size / 1024).toFixed(1)} KB ↓</a>`;
        el.appendChild(row);
      }
    } else {
      el.innerHTML = `<div class="status-row"><span>release <code>data-store</code> ainda não foi criada — rode o pipeline CRUD ou Functions</span></div>`;
    }
  } catch {
    el.innerHTML = `<div class="status-row"><span>falha ao consultar releases</span></div>`;
  }
}

// ---------- Cache + status ----------
async function loadStatus() {
  const cacheEl = $("#cache-status");
  try {
    const res = await fetch(`${RAW}/data/automation-status.json?cachebust=${Date.now()}`);
    if (!res.ok) throw new Error("sem status ainda");
    const status = await res.json();
    cacheEl.innerHTML = "";
    const stats = status.stats_indexer;
    if (stats) {
      cacheEl.innerHTML += `<div class="status-row"><span>stats_indexer</span><span>${stats.cache_hit ? "cache HIT ✅" : "cache MISS 🧮"}</span></div>`;
      cacheEl.innerHTML += `<div class="status-row"><span>digest</span><span>${stats.digest}</span></div>`;
    }
    if (status.crud) {
      cacheEl.innerHTML += `<div class="status-row"><span>última operação CRUD</span><span>${status.crud.last_operation} (${status.crud.record_count} registros)</span></div>`;
    }
    if (!stats && !status.crud) cacheEl.innerHTML = `<div class="status-row"><span>ainda sem execuções registradas</span></div>`;
    return status;
  } catch {
    cacheEl.innerHTML = `<div class="status-row"><span>data/automation-status.json ainda não existe — rode alguma pipeline</span></div>`;
    return null;
  }
}

// ---------- Recent runs across all workflows ----------
async function loadRuns() {
  const el = $("#runs-list");
  el.innerHTML = "";
  try {
    const res = await ghFetch(`/actions/runs?per_page=6`);
    if (!res.ok) throw new Error(String(res.status));
    const data = await res.json();
    for (const run of data.workflow_runs || []) {
      const b = badgeFor(run);
      const row = document.createElement("div");
      row.className = "status-row";
      row.innerHTML = `<a href="${run.html_url}" target="_blank" rel="noopener">${run.name} #${run.run_number}</a><span class="badge ${b.cls}">${b.label}</span>`;
      el.appendChild(row);
    }
    if (!data.workflow_runs || !data.workflow_runs.length) el.innerHTML = `<div class="status-row"><span>sem execuções ainda</span></div>`;
  } catch {
    el.innerHTML = `<div class="status-row"><span>API indisponível (limite de taxa anônimo é 60/h)</span></div>`;
  }
}

// ---------- Terminal ----------
function termPrint(text, cls = "") {
  const out = $("#terminal-output");
  const div = document.createElement("div");
  div.className = `line ${cls}`;
  div.textContent = text;
  out.appendChild(div);
  out.scrollTop = out.scrollHeight;
}

const HELP = `comandos disponíveis:
  help                 mostra esta ajuda
  clear                limpa a tela
  status               mostra data/automation-status.json
  db list              lista registros de data/db.json
  db read <id>         mostra um registro
  runs [workflow.yml]  últimas execuções (todas ou de um workflow)
  open <workflow.yml>  abre a tela de execução no GitHub
  whoami               informações do repositório
  token set <pat>      salva um token (fine-grained, Actions:write) localmente
  token clear          remove o token salvo`;

async function runCommand(raw) {
  const line = raw.trim();
  if (!line) return;
  termPrint(`easel@automation:~$ ${line}`, "cmd");
  const [cmd, ...rest] = line.split(/\s+/);

  switch (cmd) {
    case "help":
      termPrint(HELP);
      break;
    case "clear":
      $("#terminal-output").innerHTML = "";
      break;
    case "whoami":
      termPrint(`repo: ${OWNER}/${REPO}\nbranch: ${BRANCH}\npainel: GitHub Pages (estático, sem backend próprio)`);
      break;
    case "status": {
      const status = await loadStatus();
      termPrint(status ? JSON.stringify(status, null, 2) : "sem status disponível ainda", status ? "ok" : "err");
      break;
    }
    case "db": {
      const res = await fetch(`${RAW}/data/db.json?cachebust=${Date.now()}`);
      const db = await res.json();
      if (rest[0] === "read" && rest[1]) {
        const rec = (db.records || []).find((r) => String(r.id) === rest[1]);
        termPrint(rec ? JSON.stringify(rec, null, 2) : `registro ${rest[1]} não encontrado`, rec ? "ok" : "err");
      } else {
        termPrint(JSON.stringify(db.records || [], null, 2), "ok");
      }
      break;
    }
    case "runs": {
      const wf = rest[0];
      const path = wf ? `/actions/workflows/${wf}/runs?per_page=5` : `/actions/runs?per_page=5`;
      try {
        const res = await ghFetch(path);
        if (!res.ok) throw new Error(String(res.status));
        const data = await res.json();
        const runs = data.workflow_runs || [];
        if (!runs.length) termPrint("sem execuções.", "err");
        for (const r of runs) {
          const b = badgeFor(r);
          termPrint(`#${r.run_number}  ${r.name}  ${b.label}  ${fmtDate(r.created_at)}  ${r.html_url}`, b.cls === "success" ? "ok" : b.cls === "failure" ? "err" : "");
        }
      } catch (e) {
        termPrint(`falha ao consultar: ${e}`, "err");
      }
      break;
    }
    case "open": {
      const wf = rest[0];
      if (!wf) { termPrint("uso: open <workflow.yml>", "err"); break; }
      const url = `https://github.com/${OWNER}/${REPO}/actions/workflows/${wf}`;
      window.open(url, "_blank", "noopener");
      termPrint(`abrindo ${url}`, "ok");
      break;
    }
    case "token": {
      if (rest[0] === "set" && rest[1]) {
        setToken(rest[1]);
        termPrint("token salvo no localStorage deste navegador.", "ok");
        refreshTokenUi();
      } else if (rest[0] === "clear") {
        clearToken();
        termPrint("token removido.", "ok");
        refreshTokenUi();
      } else {
        termPrint("uso: token set <pat> | token clear", "err");
      }
      break;
    }
    default:
      termPrint(`comando desconhecido: ${cmd} (digite "help")`, "err");
  }
}

function refreshTokenUi() {
  const has = !!getToken();
  $("#token-state").textContent = has ? "token salvo ✓" : "nenhum token salvo";
}

function wireTerminal() {
  const input = $("#terminal-input");
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      const v = input.value;
      input.value = "";
      runCommand(v);
    }
  });
  termPrint("Easel Automation Terminal — digite 'help' para começar.", "ok");
}

function wireAdvancedPanel() {
  $("#token-save").addEventListener("click", () => {
    const v = $("#token-input").value.trim();
    if (v) { setToken(v); $("#token-input").value = ""; refreshTokenUi(); termPrint("token salvo.", "ok"); }
  });
  $("#token-clear").addEventListener("click", () => { clearToken(); refreshTokenUi(); termPrint("token removido.", "ok"); });
  refreshTokenUi();
}

async function loadRepoMeta() {
  $("#repo-link").href = `https://github.com/${OWNER}/${REPO}`;
  $("#actions-link").href = `https://github.com/${OWNER}/${REPO}/actions`;
  try {
    const res = await fetch(`https://api.github.com/repos/${OWNER}/${REPO}/commits/${BRANCH}`);
    if (res.ok) {
      const commit = await res.json();
      $("#repo-meta").textContent = `${OWNER}/${REPO} @ ${commit.sha.slice(0, 7)} · ${fmtDate(commit.commit.committer.date)}`;
    } else {
      $("#repo-meta").textContent = `${OWNER}/${REPO}`;
    }
  } catch {
    $("#repo-meta").textContent = `${OWNER}/${REPO}`;
  }
}

function boot() {
  wireTerminal();
  wireAdvancedPanel();
  loadRepoMeta();
  loadPipelines();
  loadDb();
  loadStorage();
  loadStatus();
  loadRuns();
  setInterval(loadPipelines, 30000);
  setInterval(loadRuns, 30000);
}

document.addEventListener("DOMContentLoaded", boot);
