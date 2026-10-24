const $ = (id) => document.getElementById(id);
// Theme: "system" follows the OS; "light"/"dark" force one. Stored per browser.
function setTheme(value) {
  if (value === "light" || value === "dark") document.documentElement.dataset.theme = value;
  else delete document.documentElement.dataset.theme;
  try { localStorage.setItem("theme", value); } catch (e) {}
  const select = $("theme-select"); if (select) select.value = value;
}
(function () {
  let saved = "system"; try { saved = localStorage.getItem("theme") || "system"; } catch (e) {}
  const fromUrl = new URLSearchParams(location.search).get("theme");  // ?theme=dark|light|system
  setTheme(["light", "dark", "system"].includes(fromUrl) ? fromUrl : saved);
})();
const state = { settings: null, currentRun: null, eventsOffset: 0, timer: null, texts: {},
  models: new Set(["gpt-6-astra", "gpt-6.1-sol", "gpt-5.6-sol", "claude-opus-5-5", "claude-fable-5-1", "claude-sonnet-5-5", "claude-haiku-4-5-20251001"]),
  backendModels: { codex: "gpt-6-astra", claude: "claude-opus-5-5" } };

const VERIFY_FIELDS = [
  ["model", "Model", "text"], ["reasoning_effort", "Reasoning effort", "effort"], ["web_search", "Web search", "web"],
  ["timeout_seconds", "Timeout per call (s)", "number"], ["workers", "Concurrent verifier calls", "number"],
  ["max_issues", "Max issues (0 = no cap)", "number"], ["max_attempts", "Attempts per stage", "number"],
  ["decompose", "Decomposed review", "bool"], ["early_stop", "Early stop on the first major mistake", "bool"],
];
const PROVE_FIELDS = [
  ["generator_backend", "Generator backend", "backend"], ["verifier_backend", "Verifier backend", "backend"],
  ["generator_model", "Generator model", "text"], ["generator_reasoning_effort", "Generator reasoning effort", "effort"],
  ["generator_revision_effort", "Generator effort after REVISE / REJECT", "revision"],
  ["verifier_model", "Verifier model", "text"], ["verifier_reasoning_effort", "Verifier reasoning effort", "effort"],
  ["max_rounds", "Max rounds per branch", "number"], ["generation_branches", "Parallel branches", "number"],
  ["search_policy", "Generator search policy", "policy"], ["verifier_web_search", "Verifier web search", "web"],
  ["generator_timeout", "Generation turn deadline (s, 0 = none)", "number"], ["verifier_timeout", "Verifier timeout per call (s)", "number"],
  ["agent_threads", "Generator subagent threads", "number"], ["agent_depth", "Generator subagent depth", "number"],
  ["workers", "Concurrent verifier calls", "number"], ["max_issues", "Max issues (0 = no cap)", "number"],
  ["max_attempts", "Attempts per stage", "number"], ["decompose", "Decomposed review", "bool"], ["early_stop", "Early stop on the first major mistake", "bool"],
];
const CHOICES = { effort: ["low", "medium", "high", "xhigh", "max"], web: ["live", "cached", "disabled"], policy: ["alternating", "live", "disabled"], backend: ["codex", "claude"],
  revision: ["", "low", "medium", "high", "xhigh", "max"] };
const CHOICE_LABELS = { "": "unchanged" };

async function api(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const body = await response.json().catch(() => ({ error: response.statusText }));
  if (!response.ok) throw new Error(body.error || response.statusText);
  return body;
}

const loadedNames = {};
// Older LaTeX sources are often Latin-1; decoding them as UTF-8 would silently replace
// every accented character. The text reaches the run folder as UTF-8 either way.
function decodeText(buffer) {
  try { return new TextDecoder("utf-8", { fatal: true }).decode(buffer); }
  catch (e) { return new TextDecoder("windows-1252").decode(buffer); }
}
function pickFile(inputId, targetId) {
  const input = $(inputId);
  input.onchange = () => {
    const file = input.files[0];
    if (!file) return;
    file.arrayBuffer().then((buffer) => { $(targetId).value = decodeText(buffer); loadedNames[targetId] = file.name; });
    input.value = "";
  };
  input.click();
}
document.querySelectorAll("input[name=v-mode]").forEach((r) => r.addEventListener("change", () => {
  $("v-tags-field").classList.toggle("hidden", document.querySelector("input[name=v-mode]:checked").value !== "manual");
}));

function showTab(name, keepHash) {
  document.querySelectorAll("nav button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab").forEach((s) => s.classList.toggle("hidden", s.id !== "tab-" + name));
  if (!keepHash) history.replaceState(null, "", "#" + name);
  if (name === "runs") loadRuns();
  if (name !== "runs") stopPolling();
}
document.querySelectorAll("nav button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));

// ---- settings -----------------------------------------------------------------
function fieldInput(section, key, type) {
  const id = `s-${section}-${key}`;
  if (type === "bool") return `<label class="check"><input type="checkbox" id="${id}"> yes</label>`;
  const follow = type === "backend" ? ` data-change="follow-backend" data-model="s-${section}-${key.replace("_backend", "_model")}"` : "";
  if (CHOICES[type]) {
    const options = CHOICES[type].map((c) => `<option value="${c}">${CHOICE_LABELS[c] ?? c}</option>`).join("");
    return `<select id="${id}"${follow}>${options}</select>`;
  }
  if (type === "number") return `<input type="number" id="${id}" step="any">`;
  return `<input type="text" id="${id}" list="models">`;
}
function renderSettingsForm() {
  for (const [section, fields] of [["verify", VERIFY_FIELDS], ["prove", PROVE_FIELDS]]) {
    $(`s-${section}`).innerHTML = fields.map(([key, label, type]) => `<div><label>${label}</label>${fieldInput(section, key, type)}</div>`).join("");
  }
}
// A model still at the other backend's default follows a backend switch.
function followBackend(backendId, modelId) {
  const backend = $(backendId).value, model = $(modelId);
  if (Object.entries(state.backendModels).some(([b, m]) => b !== backend && m === model.value)) model.value = state.backendModels[backend];
}
// Model fields accept any ID; suggestions come from the Codex account plus saved settings.
function renderModels() {
  $("models").innerHTML = [...state.models].map((m) => `<option value="${esc(m)}">`).join("");
}
function fillSettings(settings) {
  state.settings = settings;
  $("s-codex-bin").value = settings.codex_bin;
  $("s-codex-home").value = settings.codex_home || "";
  $("s-claude-bin").value = settings.claude_bin;
  $("s-claude-config-dir").value = settings.claude_config_dir || "";
  for (const [section, fields] of [["verify", VERIFY_FIELDS], ["prove", PROVE_FIELDS]]) {
    for (const [key, , type] of fields) {
      const el = $(`s-${section}-${key}`);
      const value = settings[section][key];
      if (type === "bool") el.checked = !!value; else el.value = value === null ? 0 : value;
      if (type === "text") state.models.add(value);
    }
  }
  renderModels();
  // quick settings mirror the defaults
  $("v-model").value = settings.verify.model; $("v-effort").value = settings.verify.reasoning_effort;
  $("v-web").value = settings.verify.web_search; $("v-workers").value = settings.verify.workers;
  $("v-decompose").checked = settings.verify.decompose; $("v-early").checked = settings.verify.early_stop;
  $("p-gbackend").value = settings.prove.generator_backend; $("p-vbackend").value = settings.prove.verifier_backend;
  $("p-gmodel").value = settings.prove.generator_model; $("p-geffort").value = settings.prove.generator_reasoning_effort;
  $("p-vmodel").value = settings.prove.verifier_model; $("p-veffort").value = settings.prove.verifier_reasoning_effort;
  $("p-rounds").value = settings.prove.max_rounds; $("p-branches").value = settings.prove.generation_branches;
}
function readSettingsForm() {
  const out = { codex_bin: $("s-codex-bin").value, codex_home: $("s-codex-home").value,
    claude_bin: $("s-claude-bin").value, claude_config_dir: $("s-claude-config-dir").value, verify: {}, prove: {} };
  for (const [section, fields] of [["verify", VERIFY_FIELDS], ["prove", PROVE_FIELDS]]) {
    for (const [key, , type] of fields) {
      const el = $(`s-${section}-${key}`);
      out[section][key] = type === "bool" ? el.checked : (type === "number" ? Number(el.value) : el.value);
    }
  }
  return out;
}
async function saveSettings() {
  $("s-error").textContent = ""; $("s-saved").textContent = "";
  try {
    fillSettings(await api("/api/settings", { method: "PUT", body: JSON.stringify(readSettingsForm()) }));
    $("s-saved").textContent = "Saved."; checkCodex(false);
  } catch (e) { $("s-error").textContent = e.message; }
}

// ---- codex --------------------------------------------------------------------
async function checkCodex(verbose) {
  const pill = $("codex-pill");
  try {
    const s = await api("/api/codex/status");
    for (const m of s.models || []) state.models.add(m);
    renderModels();
    if (s.error) { pill.textContent = "Codex: not found"; pill.className = "badge bad"; }
    else if (s.logged_in) { pill.textContent = `Codex ${s.version.replace(/^codex-cli\s*/, "")}: signed in`; pill.className = "badge ok"; }
    else { pill.textContent = "Codex: not signed in"; pill.className = "badge warn"; }
    $("codex-detail").textContent = s.error || `${s.path} · ${s.version} · ${s.login_output || ""}`;
    $("about-codex").textContent = s.error ? "Codex CLI not found" : `${s.version}${s.logged_in ? ", signed in" : ", not signed in"}`;
    $("about-codex-line").textContent = s.error || (s.logged_in ? "Signed in; you can start a run."
      : "Not signed in: sign in with your own Codex subscription first.");
    const c = s.claude || {};
    const claudeLogin = c.logged_in ? "signed in" : "not signed in: run claude, then /login, in a terminal";
    $("claude-detail").textContent = c.error || `${c.path} · ${c.version} · ${claudeLogin}`;
    const claudePill = $("claude-pill");
    if (c.error) { claudePill.textContent = "Claude Code: not found"; claudePill.className = "badge"; }
    else if (c.logged_in) { claudePill.textContent = "Claude Code: signed in"; claudePill.className = "badge ok"; }
    else { claudePill.textContent = "Claude Code: not signed in"; claudePill.className = "badge warn"; }
  } catch (e) { pill.textContent = "Codex: check failed"; pill.className = "badge bad"; if (verbose) $("codex-detail").textContent = e.message; }
}
let loginPoll = null;
// The sign-in box (status, page link, cancel) follows the button that started it.
function startLoginHere(slotId) { $(slotId).appendChild($("login-box")); startLogin(); }
async function startLogin() {
  $("login-box").classList.remove("hidden"); $("login-open").classList.add("hidden");
  $("login-status").textContent = "Starting Codex sign-in…"; $("login-output").textContent = "";
  try { await api("/api/codex/login", { method: "POST" }); pollLogin(); }
  catch (e) { $("login-status").textContent = e.message; }
}
async function pollLogin() {
  clearTimeout(loginPoll);
  let st;
  try { st = await api("/api/codex/login"); } catch (e) { $("login-status").textContent = e.message; return; }
  $("login-output").textContent = st.output || "";
  if (st.url) { $("login-open").dataset.url = st.url; $("login-open").classList.remove("hidden"); }
  if (st.running) {
    $("login-status").textContent = st.url
      ? "Codex is trying to open your browser. If no window appeared, use the button below, then finish signing in there."
      : "Starting Codex sign-in…";
    loginPoll = setTimeout(pollLogin, 1000);
  } else {
    $("login-status").textContent = st.exit_code === 0 ? "Sign-in finished." : "The sign-in process ended" + (st.exit_code === null ? "." : ` (exit ${st.exit_code}).`);
    $("login-open").classList.add("hidden");
    checkCodex(true);
  }
}
async function cancelLogin() {
  clearTimeout(loginPoll);
  try { await api("/api/codex/login/cancel", { method: "POST" }); } catch (e) { $("login-status").textContent = e.message; }
  pollLogin();
}

// ---- starting runs ------------------------------------------------------------
async function startVerify() {
  $("v-error").textContent = ""; $("v-start").disabled = true;
  try {
    const own = $("v-own").open && $("v-question").value.trim() && $("v-solution").value.trim();
    const mode = document.querySelector("input[name=v-mode]:checked").value;
    const inputs = own
      ? { question: $("v-question").value, solution: $("v-solution").value }
      : { paper: $("v-paper").value, paper_name: loadedNames["v-paper"] || "", mode, tags: $("v-tags").value };
    const run = await api("/api/runs", { method: "POST", body: JSON.stringify({
      kind: "verify", name: $("v-name").value, ...inputs,
      settings: { verify: { model: $("v-model").value, reasoning_effort: $("v-effort").value, web_search: $("v-web").value,
        workers: Number($("v-workers").value), decompose: $("v-decompose").checked, early_stop: $("v-early").checked } } }) });
    showTab("runs"); openRun(run.name);
  } catch (e) { $("v-error").textContent = e.message; }
  finally { $("v-start").disabled = false; }
}
async function startProve() {
  $("p-error").textContent = ""; $("p-start").disabled = true;
  try {
    const run = await api("/api/runs", { method: "POST", body: JSON.stringify({
      kind: "prove", name: $("p-name").value, problem: $("p-problem").value,
      settings: { prove: {
        generator_backend: $("p-gbackend").value, verifier_backend: $("p-vbackend").value,
        generator_model: $("p-gmodel").value, generator_reasoning_effort: $("p-geffort").value,
        verifier_model: $("p-vmodel").value, verifier_reasoning_effort: $("p-veffort").value,
        max_rounds: Number($("p-rounds").value), generation_branches: Number($("p-branches").value),
      } } }) });
    showTab("runs"); openRun(run.name);
  } catch (e) { $("p-error").textContent = e.message; }
  finally { $("p-start").disabled = false; }
}

// ---- runs list ----------------------------------------------------------------
const STATUS_LABEL = { running: ["Running", "running"], completed: ["Completed", "ok"], ready_for_human: ["Ready for human", "ok"],
  budget_exhausted: ["Round budget used", "warn"], quota_exhausted: ["Quota exhausted", "warn"], incomplete: ["Incomplete", "warn"],
  interrupted: ["Interrupted", "warn"], failed: ["Failed", "bad"], not_started: ["Not started", ""] };
function badge(status) {
  const [label, cls] = STATUS_LABEL[status] || [status, ""];
  return `<span class="badge ${cls}">${esc(label)}</span>`;
}
function decisionBadge(d) {
  if (!d) return `<span class="muted">—</span>`;
  const cls = d === "READY_FOR_HUMAN" ? "ok" : d === "REJECT" ? "bad" : "warn";
  return `<span class="badge ${cls}">${esc(d)}</span>`;
}
function when(ts) { return ts ? new Date(ts * 1000).toLocaleString() : ""; }
function esc(s) { return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

async function loadRuns() {
  try {
    const data = await api("/api/runs");
    const rows = data.runs.map((r) => `<tr class="clickable" data-open-run="${esc(r.name)}">`
      + `<td>${esc(r.name)}</td><td>${esc(r.review_kind || r.kind)}</td><td>${badge(r.status)}</td>`
      + `<td>${r.review_kind === "paper" ? "—" : decisionBadge(r.decision)}</td><td class="muted">${when(r.updated_at)}</td></tr>`);
    $("runs-body").innerHTML = rows.join("") || `<tr><td colspan="5" class="muted">No runs yet.</td></tr>`;
    renderRecent(data.runs);
  } catch (e) { $("runs-body").innerHTML = `<tr><td colspan="5" class="error">${esc(e.message)}</td></tr>`; }
}
// The sidebar's most recently updated runs, each with a status dot.
function renderRecent(runs) {
  const items = runs.slice(0, 8).map((r) => {
    const [label, cls] = STATUS_LABEL[r.status] || [r.status, ""];
    const active = r.name === state.currentRun ? " active" : "";
    return `<button class="recent-item${active}" data-open-run="${esc(r.name)}" title="${esc(label)}">`
      + `<span class="dot ${cls}"></span><span class="recent-name">${esc(r.name)}</span></button>`;
  });
  $("recent-runs").innerHTML = items.join("") || `<p class="muted recent-empty">Runs you start appear here.</p>`;
}
function markRecent() {
  document.querySelectorAll("#recent-runs [data-open-run]").forEach((item) =>
    item.classList.toggle("active", item.dataset.openRun === state.currentRun));
}

// ---- safe Markdown rendering: begin ------------------------------------------------
// Reports, proofs and questions quote untrusted papers and model output. Raw HTML in the
// Markdown is shown as text, links keep only web, mail, fragment and relative targets,
// images are never fetched, and the parsed result passes a tag/attribute allowlist
// before it reaches the page. Any failure falls back to escaped plain text.
const SAFE_SCHEMES = ["http", "https", "mailto"];
function safeUrl(value) {
  const url = String(value ?? "").trim();
  // Browsers ignore whitespace and control characters inside a scheme; judge that form.
  const probe = url.replace(/[\u0000- \u007f-\u009f]/g, "");
  const scheme = /^([a-z][a-z0-9+.-]*):/i.exec(probe);
  return scheme && !SAFE_SCHEMES.includes(scheme[1].toLowerCase()) ? null : url;
}
const SAFE_MARKED = {
  tokenizer: {
    // Inline tags become escaped text, so no tag can switch marked into raw-HTML mode.
    tag(src) {
      const cap = this.rules.inline.tag.exec(src);
      return cap ? { type: "text", raw: cap[0], text: esc(cap[0]) } : false;
    },
  },
  renderer: {
    // marked 12 passes positional arguments; later versions pass one token object.
    html(token, block) {
      const text = token && typeof token === "object" ? token.text : token;
      const isBlock = token && typeof token === "object" ? token.block : block;
      return isBlock ? `<p>${esc(text)}</p>` : esc(text);
    },
    link(href, title, text) {
      if (href && typeof href === "object") { text = this.parser.parseInline(href.tokens); href = href.href; }
      const url = safeUrl(href);
      return url === null ? text : `<a href="${esc(url)}">${text}</a>`;
    },
    image(href, title, text) {
      return esc(href && typeof href === "object" ? href.text : text);
    },
  },
};
const SAFE_TAGS = new Set(["A", "B", "BLOCKQUOTE", "BR", "CODE", "DEL", "DIV", "EM", "H1", "H2", "H3", "H4", "H5", "H6",
  "HR", "I", "INPUT", "LI", "OL", "P", "PRE", "S", "SPAN", "STRONG", "SUB", "SUP", "TABLE", "TBODY", "TD", "TH", "THEAD", "TR", "UL"]);
function keepAttribute(tag, name, value) {
  if (tag === "A") return name === "href" && safeUrl(value) !== null;
  if (tag === "TD" || tag === "TH") return name === "align";
  if (tag === "OL") return name === "start";
  if (tag === "CODE") return name === "class" && /^language-[\w+#.-]+$/.test(value);
  if (tag === "INPUT") return name === "checked" || name === "type";
  return false;
}
function sanitizeInto(el, html) {
  const template = document.createElement("template");
  template.innerHTML = html; // parsed inert: nothing here runs or loads
  const clean = (parent) => {
    for (const node of [...parent.childNodes]) {
      if (node.nodeType === Node.TEXT_NODE) continue;
      const element = node.nodeType === Node.ELEMENT_NODE;
      if (!element || !SAFE_TAGS.has(node.tagName) || (node.tagName === "INPUT" && node.getAttribute("type") !== "checkbox")) {
        node.replaceWith(element ? node.textContent : "");
        continue;
      }
      for (const { name, value } of [...node.attributes]) {
        if (!keepAttribute(node.tagName, name.toLowerCase(), value)) node.removeAttribute(name);
      }
      if (node.tagName === "INPUT") node.setAttribute("disabled", "");
      if (node.tagName === "A" && node.hasAttribute("href") && !node.getAttribute("href").startsWith("#")) {
        node.setAttribute("target", "_blank"); node.setAttribute("rel", "noopener noreferrer");
      }
      clean(node);
    }
  };
  clean(template.content);
  el.replaceChildren(template.content);
}
let markdownConfigured = false;
function markdownParser() {
  if (!window.marked) return null;
  if (!markdownConfigured) {
    try { marked.use(SAFE_MARKED); markdownConfigured = true; } catch (e) { return null; }
  }
  return marked;
}
// ---- safe Markdown rendering: end --------------------------------------------------

// ---- run detail ---------------------------------------------------------------
function renderMarkdown(el, text) {
  if (!text) { el.innerHTML = `<p class="muted">Nothing yet.</p>`; return; }
  const math = []; // protect math from the Markdown parser
  const mathPattern = /\$\$[\s\S]+?\$\$|\\\[[\s\S]+?\\\]|\\\([\s\S]+?\\\)|\$[^$\n]+?\$/g;
  const protectedText = text.replace(mathPattern, (m) => { math.push(m); return `\u0000${math.length - 1}\u0000`; });
  const parser = markdownParser();
  let html = null;
  if (parser) { try { html = parser.parse(protectedText); } catch (e) { html = null; } }
  if (typeof html !== "string") html = `<pre>${esc(protectedText)}</pre>`;
  html = html.replace(/\u0000(\d+)\u0000/g, (_, i) => esc(math[Number(i)]));
  sanitizeInto(el, html);
  if (window.renderMathInElement) renderMathInElement(el, { delimiters: MATH_DELIMITERS, throwOnError: false });
}
const MATH_DELIMITERS = [
  { left: "$$", right: "$$", display: true }, { left: "\\[", right: "\\]", display: true },
  { left: "\\(", right: "\\)", display: false }, { left: "$", right: "$", display: false },
];
function numbered(text) {
  const lines = (text || "").split("\n").map((l) => `<div><span>${esc(l)}</span></div>`);
  return `<div class="numbered">${lines.join("")}</div>`;
}

function stopPolling() { if (state.timer) clearTimeout(state.timer); state.timer = null; }
function closeRun() {
  stopPolling(); state.currentRun = null; markRecent();
  history.replaceState(null, "", "#runs");
  $("run-detail").classList.add("hidden"); $("runs-list").classList.remove("hidden");
  loadRuns();
}

async function openRun(name) {
  stopPolling(); state.currentRun = name; state.eventsOffset = 0; state.inputs = null; state.runSection = null;
  state.runStatus = null; markRecent();
  history.replaceState(null, "", "#run/" + encodeURIComponent(name));
  $("runs-list").classList.add("hidden"); $("run-detail").classList.remove("hidden");
  $("run-detail").innerHTML = `<div class="panel"><p class="muted">Loading ${esc(name)}…</p></div>`;
  await refreshRun(true);
}

function summaryLines(d) {
  const lines = [];
  if (d.kind === "prove" && d.summary) {
    const s = d.summary;
    lines.push(`Rounds completed: ${s.rounds_completed} · generation turns: ${s.generation_turns_completed}`
      + ` · candidates reviewed: ${s.candidates_generated} · branches: ${s.generation_branches}`);
  }
  if (d.kind === "verify" && d.report) {
    if (d.report.termination_reason) {
      lines.push(`Termination: ${d.report.termination_reason} · coverage complete: ${d.report.coverage_complete}`);
    }
    if (d.report.review_kind === "paper") {
      const issues = d.report.issues || [];
      const count = (severity) => issues.filter((i) => i.severity === severity).length;
      lines.push(`Major: ${count("major")} · Minor: ${count("minor")} · Unclassified: ${count("unknown")}`
        + ` · Classification: ${d.report.classification_status}`);
    }
    lines.push(`Approved feedback items: ${(d.report.feedback || []).length}`);
  }
  if (d.error) lines.push(`Note: ${d.error}`);
  if (d.job) {
    const exit = d.job.exit_code !== null && d.job.exit_code !== undefined ? ` (exit ${d.job.exit_code})` : "";
    const cancel = d.job.cancel_requested_at ? ` · cancel requested ${d.job.cancel_requested_at}` : "";
    lines.push(`Process: ${d.job.status}${exit} · started ${d.job.started_at}${cancel}`);
  }
  return lines;
}

// The page skeleton for one run; refreshRun fills its elements on every poll. One
// section shows at a time; the section tabs choose it.
function runShell(d) {
  const copyButton = (id, key, disabled) =>
    `<button class="secondary small" id="${id}" data-action="copy-raw" data-key="${key}"${disabled ? " disabled" : ""}>Copy source</button>`;
  const section = (name, body, extra = "") => `<div class="panel run-section" data-section="${name}"${extra}>${body}</div>`;
  const header = `
    <header class="run-head">
      <button class="back" data-action="close-run">← Runs</button>
      <div class="run-title">
        <h1>${esc(d.name)}</h1>
        <span class="badge">${d.kind === "prove" ? "prove" : "verify"}</span>
        <span id="rd-status"></span><span id="rd-decision"></span>
        <span class="spacer"></span>
        <span id="rd-actions" class="run-actions"></span>
      </div>
      <div class="muted" id="rd-summary"></div>
      <div class="muted run-folder">Folder <code>${esc(d.path)}</code></div>
      <div class="error" id="rd-error"></div>
      <div id="rd-auto-report" class="muted"></div><div id="rd-draft-status" class="muted"></div>
    </header>`;
  const tabs = [
    ...(d.kind === "prove" ? [["proof", "Proof"]] : [["question", "Question"]]),
    ["feedback", d.kind === "prove" ? "Latest feedback" : "Feedback"],
    ...(d.kind === "prove" ? [["reviews", "Reviews"]] : []),
    ["inputs", "Inputs"], ["activity", "Activity"], ["log", "Process log"],
  ];
  const nav = `<div class="seg" role="tablist">${tabs.map(([name, label]) =>
    `<button role="tab" data-action="run-section" data-section="${name}">${label}`
    + `${name === "reviews" ? ` <span class="count" id="rd-reviews-count"></span>` : ""}</button>`).join("")}</div>`;
  const proof = section("proof", `
    <div class="section-head"><h2>Proof ready for human review</h2>${copyButton("rd-proof-copy", "proof", false)}</div>
    <div class="notice">The verifier finished with no unresolved issue. This is a handoff for a human
      reader, not a certificate. Hashes are in <code>handoff.json</code>.</div>
    <div class="md" id="rd-proof"></div>
    <details><summary>Source (Markdown with LaTeX math)</summary><pre id="rd-proof-src"></pre></details>`, ` id="rd-proof-panel"`);
  const focus = section("question", `<h2>Question prepared from the paper</h2><div id="rd-focus"></div>`, ` id="rd-focus-panel"`);
  const report = section("feedback", `
    <div class="section-head"><h2 id="rd-report-title">Audited feedback</h2>${copyButton("rd-report-copy", "report", true)}</div>
    <span id="rd-export"></span><div class="md" id="rd-report"></div>`);
  const reviews = section("reviews", `
    <h2>Reviews of generated candidates</h2>
    <table><thead><tr><th>#</th><th>Decision</th><th>Status</th><th>Feedback items</th><th>Reviewed</th></tr></thead>
      <tbody id="rd-reviews"></tbody></table>
    <div id="rd-review-view"></div>`);
  const rest = section("inputs", `<h2>Inputs</h2><div id="rd-inputs"></div>`)
    + section("activity", `<h2>Activity</h2><p class="muted">Internal execution trace. Prose here is not released feedback.</p>
      <div class="events" id="rd-events"></div>`)
    + section("log", `<h2>Process log</h2><pre id="rd-stderr"></pre>`);
  return header + nav + (d.kind === "prove" ? proof + report + reviews : focus + report) + rest;
}

// The proof for a handoff, the prepared question for a paper, otherwise the feedback.
function defaultRunSection(d) {
  if (d.kind === "prove" && d.proof_text) return "proof";
  if (d.kind === "verify" && d.paper_mode) return "question";
  return "feedback";
}
function showRunSection(name) {
  state.runSection = name;
  document.querySelectorAll("#run-detail .run-section").forEach((el) => el.classList.toggle("current", el.dataset.section === name));
  document.querySelectorAll("#run-detail .seg button").forEach((el) => {
    el.classList.toggle("active", el.dataset.section === name);
    el.setAttribute("aria-selected", el.dataset.section === name ? "true" : "false");
  });
}
// Tabs whose content does not exist (yet) are hidden; a hidden choice falls back.
function syncRunSections(d) {
  const available = { proof: !!d.proof_text, question: !!d.paper_mode };
  document.querySelectorAll("#run-detail .seg button").forEach((el) => {
    if (el.dataset.section in available) el.classList.toggle("hidden", !available[el.dataset.section]);
  });
  const current = state.runSection;
  if (!current || available[current] === false) showRunSection(defaultRunSection(d));
}

function renderInputs(d) {
  const inputs = state.inputs;
  $("rd-inputs").innerHTML = d.kind === "prove"
    ? `<h3>Problem</h3>${numbered(inputs.problem)}`
    : `<h3>Question</h3>${numbered(inputs.question)}`
      + `<h3>Candidate solution (line numbers as cited in feedback)</h3>${numbered(inputs.solution)}`;
}

function runActions(d) {
  if (d.status === "running") return `<button class="secondary" data-action="cancel-run">Cancel</button>`;
  if (d.resumable && d.job) {
    const rounds = d.job.settings && d.job.settings.prove ? d.job.settings.prove.max_rounds : "";
    const more = d.kind === "prove"
      ? `<label class="check">more rounds: <input type="number" id="rd-rounds" min="1" style="width:80px" value="${esc(rounds)}"></label>`
      : "";
    return more + `<button class="primary" data-action="resume-run">Resume</button>`;
  }
  if (d.resumable) return `<span class="muted">Started outside the GUI; resume it with its original command.</span>`;
  return "";
}

function renderProve(d) {
  state.texts.proof = d.proof_text || "";
  if (d.proof_text && $("rd-proof-src").textContent !== d.proof_text) {
    renderMarkdown($("rd-proof"), d.proof_text);
    $("rd-proof-src").textContent = d.proof_text;
  }
  $("rd-reviews-count").textContent = (d.reviews || []).length || "";
  $("rd-report-title").textContent = d.latest_review ? "Latest review feedback" : "Latest review feedback (none yet)";
  const rows = (d.reviews || []).map((r, i) => `<tr class="clickable" data-open-review="${esc(r.key)}">`
    + `<td>${i + 1}</td><td>${decisionBadge(r.decision)}</td><td>${esc(r.run_status)}</td>`
    + `<td>${esc(r.feedback_count)}</td><td class="muted">${when(r.updated_at)}</td></tr>`);
  $("rd-reviews").innerHTML = rows.join("") || `<tr><td colspan="5" class="muted">No candidate has been reviewed yet.</td></tr>`;
}

const PAPER_MODES = {
  smart: "Smart (main theorems and key steps selected by agents)",
  lazy: "Lazy (theorems in the introduction)",
  manual: "Manual (labels chosen by the user)",
};
function renderPaperFocus(d) {
  if (!d.paper_mode) return;
  const modeLabel = PAPER_MODES[d.paper_mode] || d.paper_mode;
  if (!d.extraction) {
    const note = d.status === "running" ? "Preparing the question from the paper…"
      : "The question was not prepared; see the process log below.";
    $("rd-focus").innerHTML = `<p class="muted">Mode: ${esc(modeLabel)}. ${note}</p>`;
    return;
  }
  const x = d.extraction.extraction;
  const attempts = d.extraction.backend.attempts ? ` · ${esc(d.extraction.backend.attempts)} extraction attempt(s)` : "";
  const rows = x.targets.map((t, i) => `<tr><td>${i + 1}</td><td>${esc(t.name)}</td><td><code>${esc(t.label || "")}</code></td>`
    + `<td>${esc(t.role)}</td><td class="muted">${esc(t.reason)}</td></tr>`);
  $("rd-focus").innerHTML = `<p class="muted">Mode: ${esc(modeLabel)} · ${esc(x.targets.length)} target(s)${attempts}</p>`
    + `<table><thead><tr><th>#</th><th>Result</th><th>Label</th><th>Role</th><th>Why selected</th></tr></thead>`
    + `<tbody>${rows.join("")}</tbody></table>`
    + `<details style="margin-top:8px"><summary>Full question text</summary><div class="md" id="rd-question"></div></details>`;
  renderMarkdown($("rd-question"), state.inputs.question);
}

function templateReportLine(name, template) {
  if (template?.status === "completed") {
    const label = template.partial ? "Download partial PDF (quota exhausted)" : "Download template PDF";
    return `<a href="/api/runs/${encodeURIComponent(name)}/template-pdf" download="referee-report.pdf">${label}</a>`;
  }
  if (template?.status === "running") return `Template PDF: ${esc(template.phase)}…`;
  if (template?.status === "failed") return `Template PDF failed: ${esc(template.error)} Verification results are preserved.`;
  return "";
}

async function refreshRun(full) {
  const name = state.currentRun; if (!name) return;
  // Inputs never change once a run starts: fetch them only when the page is rebuilt.
  const rebuild = full || !$("rd-status") || !state.inputs;
  let d;
  try { d = await api(`/api/runs/${encodeURIComponent(name)}${rebuild ? "" : "?inputs=0"}`); }
  catch (e) {
    $("run-detail").innerHTML = `<div class="panel"><div class="error">${esc(e.message)}</div>`
      + `<button class="secondary" data-action="close-run">Back</button></div>`;
    return;
  }
  if (state.currentRun !== name) return;
  if (rebuild) {
    state.inputs = d.inputs || {};
    $("run-detail").innerHTML = runShell(d);
    renderInputs(d);
    state.eventsOffset = 0; $("rd-events").innerHTML = "";
  }
  if (state.runStatus && state.runStatus !== d.status) loadRuns();  // the sidebar's status dot
  state.runStatus = d.status;
  $("rd-status").innerHTML = badge(d.status);
  $("rd-decision").innerHTML = d.review_kind === "paper" ? "" : decisionBadge(d.decision);
  $("rd-summary").innerHTML = summaryLines(d).map(esc).join("<br>");
  $("rd-stderr").textContent = d.stderr_tail || "";
  $("rd-actions").innerHTML = runActions(d);
  if (d.kind === "prove") renderProve(d);
  if (d.kind === "verify") renderPaperFocus(d);
  syncRunSections(d);
  $("rd-export").innerHTML = d.report_markdown ? exportLinks(name, d.latest_review || null) : "";
  const template = d.template_report;
  $("rd-auto-report").innerHTML = templateReportLine(name, template);
  state.texts.report = d.report_markdown || "";
  $("rd-report-copy").disabled = !state.texts.report;
  if ($("rd-report").dataset.text !== (d.report_markdown || "")) {
    $("rd-report").dataset.text = d.report_markdown || "";
    renderMarkdown($("rd-report"), d.report_markdown);
  }
  await refreshEvents();
  if (d.status === "running" || template?.status === "running") state.timer = setTimeout(() => refreshRun(false), 3000);
}

async function refreshEvents() {
  const name = state.currentRun; if (!name) return;
  const data = await api(`/api/runs/${encodeURIComponent(name)}/events?offset=${state.eventsOffset}`);
  if (state.currentRun !== name) return;
  if (data.offset < state.eventsOffset) $("rd-events").innerHTML = "";
  state.eventsOffset = data.offset;
  const box = $("rd-events");
  for (const ev of data.events) {
    const rest = Object.fromEntries(Object.entries(ev).filter(([k]) => !["at", "event", "sequence"].includes(k)));
    const div = document.createElement("div");
    div.innerHTML = `<span class="t">${esc((ev.at || "").slice(11, 19))}</span><span class="e">${esc(ev.event)}</span>`
      + `<span class="muted">${esc(JSON.stringify(rest))}</span>`;
    box.appendChild(div);
  }
  if (data.events.length) box.scrollTop = box.scrollHeight;
}

const activeReportDrafts = new Set();

function exportLinks(name, key = null) {
  const base = `/api/runs/${encodeURIComponent(name)}` + (key ? `/reviews/${encodeURIComponent(key)}` : "");
  const busy = activeReportDrafts.has(name + "/" + (key || "")) ? " disabled" : "";
  return `<div class="row">`
    + `<a href="${base}/export?format=latex" download="report.tex">Download LaTeX (.tex)</a>`
    + `<a href="${base}/export?format=markdown" download="report.md">Download Markdown (.md)</a>`
    + `<button class="secondary" data-action="draft-report" data-run="${esc(name)}" data-review="${esc(key || "")}"${busy}>`
    + `Draft template report (2 Codex calls)</button></div>`;
}

async function draftReport(button) {
  const name = button.dataset.run, review = button.dataset.review || null;
  const jobKey = name + "/" + (review || "");
  if (activeReportDrafts.has(jobKey)) return;
  activeReportDrafts.add(jobKey);
  button.disabled = true;
  const show = (text) => { if (state.currentRun === name && $("rd-draft-status")) $("rd-draft-status").innerHTML = text; };
  const finish = (text) => { show(text); activeReportDrafts.delete(jobKey); button.disabled = false; };
  show("Writing report and checking editorial fidelity… Verification results remain unchanged.");
  try {
    const job = await api(`/api/runs/${encodeURIComponent(name)}/report-draft`, { method: "POST", body: JSON.stringify({ review }) });
    const poll = async () => {
      try {
        const result = await api(`/api/report-drafts/${job.draft_id}`);
        if (result.status === "running") { setTimeout(poll, 1500); return; }
        if (result.status === "completed") {
          finish(`<a href="/api/report-drafts/${job.draft_id}/download" download="referee-report.tex">`
            + `Download drafted referee report (.tex)</a> — editorial check passed; verification was not rerun.`);
        } else finish(`Report not released: ${esc(result.error || "editorial check or execution failed")}`);
      } catch (e) { finish(esc(e.message)); }
    };
    await poll();
  } catch (e) { finish(esc(e.message)); }
}

async function openReview(key) {
  const name = state.currentRun;
  try {
    const r = await api(`/api/runs/${encodeURIComponent(name)}/reviews/${encodeURIComponent(key)}`);
    state.texts.review = r.report_markdown || ""; state.texts.candidate = r.candidate_solution || "";
    const copy = (textKey, label) =>
      `<button class="secondary" data-action="copy-raw" data-key="${textKey}"${state.texts[textKey] ? "" : " disabled"}>${label}</button>`;
    $("rd-review-view").innerHTML = `<div class="row"><h3 style="margin:0">Review ${esc(key.slice(0, 12))}</h3>`
      + `<span style="flex:1"></span>${copy("review", "Copy feedback source")}${copy("candidate", "Copy candidate source")}</div>`
      + (r.report_markdown ? exportLinks(name, key) : "")
      + `<div class="md" id="rv-md"></div>`
      + `<details><summary>Candidate solution reviewed (line numbers as cited)</summary>${numbered(r.candidate_solution)}</details>`;
    renderMarkdown($("rv-md"), r.report_markdown);
  } catch (e) { $("rd-review-view").innerHTML = `<div class="error">${esc(e.message)}</div>`; }
}
async function cancelRun() {
  if (!confirm("Interrupt this run? Completed work is kept and the run can be resumed.")) return;
  try { await api(`/api/runs/${encodeURIComponent(state.currentRun)}/cancel`, { method: "POST" }); }
  catch (e) { $("rd-error").textContent = e.message; }
  refreshRun(false);
}
async function resumeRun() {
  $("rd-error").textContent = "";
  const rounds = $("rd-rounds") ? $("rd-rounds").value : undefined;
  try {
    await api(`/api/runs/${encodeURIComponent(state.currentRun)}/resume`, { method: "POST", body: JSON.stringify({ max_rounds: rounds }) });
  } catch (e) { $("rd-error").textContent = e.message; }
  refreshRun(false);
}

// Clipboard: the async API first; some browsers (e.g. WebKit) may refuse it, so fall back
// to a hidden textarea and the legacy copy command.
async function copyToClipboard(text) {
  try { if (navigator.clipboard && navigator.clipboard.writeText) { await navigator.clipboard.writeText(text); return true; } } catch (e) {}
  const area = document.createElement("textarea");
  area.value = text; area.setAttribute("readonly", ""); area.style.position = "fixed"; area.style.top = "0"; area.style.opacity = "0";
  document.body.appendChild(area); area.focus(); area.select(); area.setSelectionRange(0, text.length);
  let ok = false;
  try { ok = document.execCommand("copy"); } catch (e) {}
  document.body.removeChild(area);
  return ok;
}
async function copyWithFeedback(text, button) {
  const label = button.textContent;
  const ok = text ? await copyToClipboard(text) : false;
  button.textContent = ok ? "Copied" : "Copy failed: open Source and select";
  setTimeout(() => { button.textContent = label; }, ok ? 1500 : 4000);
}
// Copies the raw text (Markdown with LaTeX math) kept in state.texts, never the rendered HTML.
function copyRaw(key, button) { copyWithFeedback(state.texts[key] || "", button); }
function copyText(id, button) { copyWithFeedback($(id).textContent, button); }

// ---- event dispatch -----------------------------------------------------------
// The page has no inline handlers (its CSP forbids inline script): elements name an
// action in data-action / data-change, and rows carry their run or review name as data.
// Nothing taken from run data ever becomes code.
const CLICK_ACTIONS = {
  "pick-file": (el) => pickFile(el.dataset.input, el.dataset.target),
  "start-verify": () => startVerify(),
  "start-prove": () => startProve(),
  "load-runs": () => loadRuns(),
  "check-cli": () => checkCodex(true),
  "sign-in": (el) => startLoginHere(el.dataset.slot),
  "open-login-page": () => window.open($("login-open").dataset.url, "_blank"),
  "cancel-login": () => cancelLogin(),
  "save-settings": () => saveSettings(),
  "copy-text": (el) => copyText(el.dataset.source, el),
  "copy-raw": (el) => copyRaw(el.dataset.key, el),
  "close-run": () => closeRun(),
  "cancel-run": () => cancelRun(),
  "resume-run": () => resumeRun(),
  "draft-report": (el) => draftReport(el),
  "run-section": (el) => showRunSection(el.dataset.section),
};
const CHANGE_ACTIONS = {
  "theme": (el) => setTheme(el.value),
  "follow-backend": (el) => followBackend(el.id, el.dataset.model),
};
document.addEventListener("click", (event) => {
  const target = event.target instanceof Element ? event.target : null;
  if (!target) return;
  const action = target.closest("[data-action]");
  if (action && CLICK_ACTIONS[action.dataset.action]) { CLICK_ACTIONS[action.dataset.action](action); return; }
  const run = target.closest("[data-open-run]");
  if (run) {
    if ($("tab-runs").classList.contains("hidden")) showTab("runs", true);
    openRun(run.dataset.openRun);
    return;
  }
  const review = target.closest("[data-open-review]");
  if (review) openReview(review.dataset.openReview);
});
document.addEventListener("change", (event) => {
  const target = event.target instanceof Element ? event.target : null;
  const action = target && target.closest("[data-change]");
  if (action && CHANGE_ACTIONS[action.dataset.change]) CHANGE_ACTIONS[action.dataset.change](action);
});

// ---- boot ---------------------------------------------------------------------
(async function boot() {
  renderSettingsForm();
  try {
    const st = await api("/api/state");
    if (st.backend_models) state.backendModels = st.backend_models;
    fillSettings(st.settings);
    $("runs-root").textContent = `Run folders live in ${st.runs_root}`;
    $("about-version").textContent = "version " + (st.version || "dev");
    $("about-home").textContent = st.home;
    if (st.settings_error) $("s-error").textContent = st.settings_error;
  } catch (e) { $("v-error").textContent = e.message; }
  checkCodex(false);
  loadRuns();  // fills the sidebar's recent runs
  const hash = decodeURIComponent(location.hash.slice(1));
  if (hash.startsWith("run/")) { showTab("runs", true); openRun(hash.slice(4)); }
  else if (["verify", "prove", "runs", "settings", "about"].includes(hash)) showTab(hash, true);
  else showTab("about", true);
})();
