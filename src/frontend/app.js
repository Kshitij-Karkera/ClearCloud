"use strict";

const $ = id => document.getElementById(id);
const money = n => "$" + Number(n).toFixed(2);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
};

const TYPE_LABELS = {
  unattached_disk: "Unattached disk",
  unused_public_ip: "Unused public IP",
  idle_vm: "Idle VM",
};
const TYPE_COLORS = ["#2563eb", "#0ea5e9", "#f59e0b", "#8b5cf6", "#22c55e"];
const CLOUD_NAMES = { aws: "AWS", azure: "Azure", gcp: "GCP" };
const VIEW_TITLES = { overview: "Overview", findings: "Findings", accounts: "Accounts", spend: "AWS spend" };

const typeLabel = type => TYPE_LABELS[type] || String(type || "other").replace(/_/g, " ");

let config = {};
let idToken = null;
let report = null;
let currentView = "overview";

function storeToken(token) {
  idToken = token;
  try {
    if (token) sessionStorage.setItem("cc_token", token);
    else sessionStorage.removeItem("cc_token");
  } catch (e) { }
}

function readStoredToken() {
  try { return sessionStorage.getItem("cc_token"); } catch (e) { return null; }
}

function tokenPayload(token) {
  try {
    const part = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    return JSON.parse(atob(part + "=".repeat((4 - (part.length % 4)) % 4)));
  } catch (e) {
    return {};
  }
}

function tokenIsValid(token) {
  const payload = tokenPayload(token);
  return Boolean(payload.exp) && payload.exp * 1000 > Date.now() + 30000;
}

function setMessage(id, text, kind) {
  const node = $(id);
  node.textContent = text || "";
  node.className = "msg" + (kind ? " " + kind : "");
}

async function api(path, options) {
  const headers = { "Content-Type": "application/json" };
  if (idToken) headers.Authorization = "Bearer " + idToken;
  const res = await fetch(path, Object.assign({}, options, { headers }));
  const data = await res.json().catch(() => ({}));
  if (res.status === 401 && idToken && !config.demo) {
    signOut();
    throw new Error("Your session expired. Please sign in again.");
  }
  if (!res.ok) throw new Error(data.error || data.message || "Request failed (" + res.status + ")");
  return data;
}

/* ---------- views ---------- */

function showView(name) {
  if (!VIEW_TITLES[name]) name = "overview";
  currentView = name;
  document.querySelectorAll("section[data-view]").forEach(section => {
    section.hidden = section.dataset.view !== name;
  });
  document.querySelectorAll(".nav").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.view === name);
  });
  $("page-title").textContent = VIEW_TITLES[name];
  try { history.replaceState(null, "", "#" + name); } catch (e) { }
  if (name === "accounts" && !$("app").hidden) loadAlerts();
}

function wireNav() {
  window.addEventListener("hashchange", () => {
    if (!$("app").hidden) showView((location.hash || "").replace("#", ""));
  });
  document.querySelectorAll(".nav").forEach(btn => {
    btn.addEventListener("click", () => showView(btn.dataset.view));
  });
  document.querySelectorAll("[data-goto]").forEach(btn => {
    btn.addEventListener("click", () => showView(btn.dataset.goto));
  });
}

/* ---------- auth ---------- */

function showAuth(tab) {
  $("auth").hidden = false;
  $("app").hidden = true;
  $("userbar").hidden = true;
  $("form-signin").hidden = tab !== "signin";
  $("form-signup").hidden = tab !== "signup";
  $("tab-signin").classList.toggle("active", tab === "signin");
  $("tab-signup").classList.toggle("active", tab === "signup");
  setMessage("auth-msg", "");
}

function showApp() {
  $("auth").hidden = true;
  $("app").hidden = false;
  if (!config.demo) {
    $("userbar").hidden = false;
    const email = tokenPayload(idToken).email || "";
    $("user-email").textContent = email;
    $("user-email").title = email;
  }
  showView((location.hash || "").replace("#", "") || "overview");
  refreshAll();
}

function signOut() {
  storeToken(null);
  report = null;
  showAuth("signin");
}

async function authenticate(path, email, password) {
  const data = await api(path, { method: "POST", body: JSON.stringify({ email: email, password: password }) });
  storeToken(data.token);
  showApp();
}

function wireAuth() {
  $("tab-signin").addEventListener("click", () => showAuth("signin"));
  $("tab-signup").addEventListener("click", () => showAuth("signup"));
  $("signout").addEventListener("click", signOut);

  $("form-signin").addEventListener("submit", async ev => {
    ev.preventDefault();
    setMessage("auth-msg", "Signing in...");
    try {
      await authenticate("/api/auth/login", $("si-email").value.trim(), $("si-password").value);
      $("si-password").value = "";
    } catch (err) {
      setMessage("auth-msg", err.message, "error");
    }
  });

  $("form-signup").addEventListener("submit", async ev => {
    ev.preventDefault();
    setMessage("auth-msg", "Creating account...");
    try {
      await authenticate("/api/auth/register", $("su-email").value.trim(), $("su-password").value);
      $("su-password").value = "";
    } catch (err) {
      setMessage("auth-msg", err.message, "error");
    }
  });
}

/* ---------- accounts ---------- */

function statusChip(status) {
  const text = status || "never scanned";
  if (/^error/i.test(text)) {
    const bad = el("span", "chip", "Scan failed");
    bad.classList.add("bad");
    return bad;
  }
  const chip = el("span", "chip", text.split(" (")[0]);
  if (/^ok/i.test(text)) chip.classList.add("ok");
  return chip;
}

function renderAccounts(accounts) {
  const box = $("accounts");
  box.replaceChildren();
  $("onboard").hidden = accounts.length > 0 || Boolean(config.demo);
  if (!accounts.length) {
    box.appendChild(el("div", "empty", "No accounts connected yet. Use \"Connect account\" to start scanning."));
    return;
  }
  accounts.forEach(account => {
    const wrap = el("div", "account");
    const top = el("div", "top");
    const name = el("span", "name");
    name.appendChild(el("span", "badge " + account.provider, (CLOUD_NAMES[account.provider] || account.provider).toUpperCase()));
    name.appendChild(document.createTextNode(account.label));
    top.appendChild(name);

    const right = el("span", "name");
    if (account.verified === false) {
      const warn = el("span", "chip", "Not verified");
      warn.classList.add("warn");
      right.appendChild(warn);
    } else {
      right.appendChild(statusChip(account.last_status));
    }
    if (!config.demo) {
      const del = el("button", "link danger", "Remove");
      del.type = "button";
      del.addEventListener("click", async () => {
        if (!confirm("Remove " + account.label + "?")) return;
        try {
          await api("/api/accounts/" + account.account_id, { method: "DELETE" });
          refreshAll();
        } catch (err) {
          setMessage("scan-msg", err.message, "error");
        }
      });
      right.appendChild(del);
    }
    top.appendChild(right);
    wrap.appendChild(top);

    const when = account.last_scan_at ? "Last scan " + new Date(account.last_scan_at).toLocaleString() : "Not scanned yet";
    wrap.appendChild(el("div", "meta", when));
    if (/^error/i.test(account.last_status || "")) {
      wrap.appendChild(el("div", "acc-error", account.last_status.replace(/^error:\s*/i, "")));
    }
    if (account.role_arn) {
      wrap.appendChild(el("div", "meta", "Role: " + account.role_arn));
      wrap.appendChild(el("div", "meta", "External ID (add to the role's trust policy): " + account.external_id));
      if (account.verified === false) {
        wrap.appendChild(el("div", "meta", "Replace PASTE-EXTERNAL-ID-HERE in the role's trust policy with the External ID above. Then press Verify. ClearCloud will not scan this account until it is verified."));
        const row = el("div", "verify-row");
        const btn = el("button", "btn primary small", "Verify");
        btn.type = "button";
        const msg = el("span", "hint");
        btn.addEventListener("click", async () => {
          btn.disabled = true;
          msg.className = "hint";
          msg.textContent = "Checking the role...";
          try {
            await api("/api/accounts/" + account.account_id + "/verify", { method: "POST" });
            await refreshAll();
          } catch (err) {
            msg.className = "hint verify-error";
            msg.textContent = err.message;
            btn.disabled = false;
          }
        });
        row.appendChild(btn);
        row.appendChild(msg);
        wrap.appendChild(row);
      }
    } else if (account.host_account) {
      wrap.appendChild(el("div", "meta", "Scans the account that hosts ClearCloud"));
    }
    box.appendChild(wrap);
  });
}

/* ---------- AWS connect help ---------- */

const GUID = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;

// Checks the connect form before sending it, so people get a plain explanation of what to fix.
function accountProblem(provider, payload) {
  if (!payload.label) return "Give the account a name, so you can tell it apart later.";
  if (provider === "aws") {
    const role = payload.role_arn;
    if (role && !/^arn:aws:iam::\d{12}:role\/.+/.test(role)) {
      return "That does not look like a role ARN. It should look like arn:aws:iam::123456789012:role/ClearCloudReadOnly. You can copy it from the role's page in IAM.";
    }
  } else if (provider === "azure") {
    const c = payload.credentials;
    const guids = [["Tenant ID", c.tenant_id], ["Subscription ID", c.subscription_id], ["Client (app) ID", c.client_id]];
    for (const [name, value] of guids) {
      if (!value) return name + " is empty. You can find it in the Azure portal under your app registration.";
      if (!GUID.test(value)) return name + " should look like 11111111-2222-3333-4444-555555555555. Check that you copied the whole value.";
    }
    if (!c.client_secret) return "Client secret is empty. Create one under Certificates & secrets on the app registration and paste its value.";
  } else if (!String(payload.credentials || "").trim()) {
    return "Paste the contents of your service account key file. It is a JSON file that starts with {.";
  }
  return "";
}

function fillAwsHelp() {
  const arn = config.trusted_role_arn || "";
  const principal = arn || (config.demo ? "arn:aws:iam::123456789012:role/clearcloud-LambdaRole-EXAMPLE" : "ARN-OF-THE-CLEARCLOUD-ROLE");
  const trust = {
    Version: "2012-10-17",
    Statement: [{
      Effect: "Allow",
      Principal: { AWS: principal },
      Action: "sts:AssumeRole",
      Condition: { StringEquals: { "sts:ExternalId": "PASTE-EXTERNAL-ID-HERE" } }
    }]
  };
  const perms = {
    Version: "2012-10-17",
    Statement: [{
      Effect: "Allow",
      Action: ["ec2:Describe*", "cloudwatch:GetMetricStatistics", "ce:GetCostAndUsage"],
      Resource: "*"
    }]
  };
  $("aws-trust").textContent = JSON.stringify(trust, null, 2);
  $("aws-perms").textContent = JSON.stringify(perms, null, 2);
  $("aws-help-missing").hidden = Boolean(arn) || Boolean(config.demo);
  document.querySelectorAll("[data-copy]").forEach(btn => {
    btn.addEventListener("click", async () => {
      const text = $(btn.dataset.copy).textContent;
      try {
        await navigator.clipboard.writeText(text);
        btn.textContent = "Copied";
      } catch (e) {
        const range = document.createRange();
        range.selectNodeContents($(btn.dataset.copy));
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
        btn.textContent = "Selected, press Ctrl+C";
      }
      setTimeout(() => { btn.textContent = "Copy"; }, 2000);
    });
  });
}

/* ---------- email alerts ---------- */

let alertState = "off";

function renderAlerts(data) {
  alertState = data.state;
  const text = $("alerts-text");
  const btn = $("alerts-btn");
  if (data.state === "on") {
    text.textContent = "Alerts are on. ClearCloud emails " + data.email + " when it finds new waste in your accounts.";
    btn.textContent = "Turn off";
  } else if (data.state === "pending") {
    text.textContent = "Almost done. Open the email AWS sent to " + data.email + " and click Confirm subscription.";
    btn.textContent = "I confirmed it";
  } else {
    text.textContent = "Get an email at " + data.email + " when ClearCloud finds new waste in your accounts.";
    btn.textContent = "Turn on email alerts";
  }
  $("alerts-card").hidden = false;
}

async function loadAlerts() {
  if (config.demo) return;
  try {
    renderAlerts(await api("/api/alerts"));
  } catch (err) {
    $("alerts-card").hidden = true;
  }
}

function wireAlerts() {
  $("alerts-btn").addEventListener("click", async () => {
    const btn = $("alerts-btn");
    btn.disabled = true;
    setMessage("alerts-msg", "");
    try {
      if (alertState === "off") {
        const data = await api("/api/alerts", { method: "POST", body: "{}" });
        renderAlerts(data);
        setMessage("alerts-msg", "Check your inbox for the confirmation email from AWS.", "ok");
      } else if (alertState === "pending") {
        const data = await api("/api/alerts");
        renderAlerts(data);
        if (data.state === "pending") setMessage("alerts-msg", "Not confirmed yet. Click the link in the AWS email first.", "error");
      } else {
        renderAlerts(await api("/api/alerts", { method: "DELETE" }));
        setMessage("alerts-msg", "Email alerts are off.", "ok");
      }
    } catch (err) {
      setMessage("alerts-msg", err.message, "error");
    } finally {
      btn.disabled = false;
    }
  });
}

/* ---------- overview ---------- */

function groupSavings(findings, key) {
  const groups = {};
  findings.forEach(f => {
    const g = groups[f[key]] || (groups[f[key]] = { key: f[key], savings: 0, count: 0 });
    g.savings += f.est_monthly_savings;
    g.count += 1;
  });
  return Object.values(groups).sort((a, b) => b.savings - a.savings);
}

function renderByCloud(findings) {
  const box = $("by-cloud");
  box.replaceChildren();
  const groups = groupSavings(findings, "provider");
  if (!groups.length) {
    box.appendChild(el("div", "empty", report && report.scanned_at ? "No waste found on any cloud." : "Run a scan to see savings by cloud."));
    return;
  }
  const max = Math.max.apply(null, groups.map(g => g.savings)) || 1;
  groups.forEach(g => {
    const row = el("div", "bar-row");
    row.appendChild(el("div", "name", (CLOUD_NAMES[g.key] || g.key) + " · " + g.count + (g.count === 1 ? " issue" : " issues")));
    const bar = el("div", "bar");
    const fill = el("span", g.key);
    fill.style.width = Math.max(4, (g.savings / max) * 100) + "%";
    bar.appendChild(fill);
    row.appendChild(bar);
    row.appendChild(el("div", "amt", money(g.savings)));
    box.appendChild(row);
  });
}

function renderByType(findings) {
  const box = $("by-type");
  box.replaceChildren();
  const groups = groupSavings(findings, "type").sort((a, b) => b.count - a.count);
  if (!groups.length) {
    box.appendChild(el("div", "empty", report && report.scanned_at ? "Nothing to fix. Nice." : "Run a scan to see findings by type."));
    return;
  }
  const total = groups.reduce((sum, g) => sum + g.count, 0);
  let angle = 0;
  const stops = groups.map((g, i) => {
    const start = angle;
    angle += (g.count / total) * 360;
    return TYPE_COLORS[i % TYPE_COLORS.length] + " " + start + "deg " + angle + "deg";
  });
  const wrap = el("div", "donut-wrap");
  const donut = el("div", "donut");
  donut.style.background = "conic-gradient(" + stops.join(", ") + ")";
  const hole = el("div");
  const num = el("b", "", String(total));
  hole.appendChild(num);
  hole.appendChild(document.createTextNode(total === 1 ? "issue" : "issues"));
  donut.appendChild(hole);
  wrap.appendChild(donut);
  const legend = el("div", "legend");
  groups.forEach((g, i) => {
    const row = el("div");
    const dot = el("span", "dot");
    dot.style.background = TYPE_COLORS[i % TYPE_COLORS.length];
    row.appendChild(dot);
    row.appendChild(document.createTextNode(typeLabel(g.key) + " · " + g.count + " · " + money(g.savings) + "/mo"));
    legend.appendChild(row);
  });
  wrap.appendChild(legend);
  box.appendChild(wrap);
}

function findingNode(f) {
  const wrap = el("div", "finding");
  const top = el("div", "top");
  top.appendChild(el("span", "title", f.title));
  top.appendChild(el("span", "save", "save ~" + money(f.est_monthly_savings) + "/mo"));
  wrap.appendChild(top);
  const meta = el("div", "meta");
  meta.appendChild(el("span", "badge " + f.provider, (CLOUD_NAMES[f.provider] || f.provider).toUpperCase()));
  meta.appendChild(el("span", "chip", typeLabel(f.type)));
  if (f.account_label) meta.appendChild(el("span", "", f.account_label));
  meta.appendChild(el("code", "", f.resource_id));
  if (f.region) meta.appendChild(el("span", "", f.region));
  wrap.appendChild(meta);
  if (f.recommendation) wrap.appendChild(el("div", "rec", f.recommendation));
  return wrap;
}

function renderTop(findings) {
  const box = $("top-findings");
  box.replaceChildren();
  if (!findings.length) {
    box.appendChild(el("div", "empty", report && report.scanned_at ? "No wasted resources detected." : "Run a scan to see recommendations."));
    return;
  }
  findings.slice(0, 3).forEach(f => box.appendChild(findingNode(f)));
}

/* ---------- findings ---------- */

function fillSelect(id, values, labeler) {
  const select = $(id);
  const chosen = select.value;
  const first = select.options[0];
  select.replaceChildren(first);
  values.forEach(v => {
    const opt = el("option", "", labeler(v));
    opt.value = v;
    select.appendChild(opt);
  });
  if (values.indexOf(chosen) >= 0) select.value = chosen;
}

function renderFindingsList() {
  const box = $("findings");
  box.replaceChildren();
  const all = report ? report.findings : [];
  if (!all.length) {
    box.appendChild(el("div", "empty", report && report.scanned_at ? "No wasted resources detected. Nice." : "Run a scan to see recommendations."));
    return;
  }
  const term = $("f-search").value.trim().toLowerCase();
  const cloud = $("f-cloud").value;
  const type = $("f-type").value;
  let rows = all.filter(f =>
    (!cloud || f.provider === cloud) &&
    (!type || f.type === type) &&
    (!term || [f.title, f.resource_id, f.account_label, f.region].join(" ").toLowerCase().indexOf(term) >= 0));
  const sort = $("f-sort").value;
  rows = rows.slice().sort((a, b) => {
    if (sort === "cloud") return a.provider.localeCompare(b.provider) || b.est_monthly_savings - a.est_monthly_savings;
    if (sort === "type") return a.type.localeCompare(b.type) || b.est_monthly_savings - a.est_monthly_savings;
    return b.est_monthly_savings - a.est_monthly_savings;
  });
  if (!rows.length) {
    box.appendChild(el("div", "empty", "No findings match these filters."));
    return;
  }
  rows.forEach(f => box.appendChild(findingNode(f)));
}

function wireFindings() {
  ["f-search", "f-cloud", "f-type", "f-sort"].forEach(id => {
    $(id).addEventListener(id === "f-search" ? "input" : "change", renderFindingsList);
  });
}

/* ---------- report ---------- */

function renderReport(d) {
  report = d;
  const findings = d.findings || [];
  $("sub").textContent = d.scanned_at
    ? "Last scan: " + new Date(d.scanned_at).toLocaleString() + " · Clouds: " + (d.providers.join(", ") || "none")
    : "No scan has completed yet";

  $("t-clouds").textContent = d.providers.length;
  $("t-save").textContent = money(d.summary.total_est_monthly_savings);
  $("t-count").textContent = d.summary.count;
  $("t-count-foot").textContent = d.summary.count === 1 ? "open recommendation" : "open recommendations";
  const clouds = d.providers.map(p => CLOUD_NAMES[p] || p).join(", ");
  $("t-clouds-foot").textContent = clouds || "none yet";

  const pill = $("nav-count");
  pill.hidden = !d.summary.count;
  pill.textContent = d.summary.count;

  const notes = $("notes");
  notes.replaceChildren();
  if (d.demo_note) notes.appendChild(el("div", "note", d.demo_note));
  if (d.cost_explorer_status !== "ok" && d.cost_explorer_status !== "not requested") {
    notes.appendChild(el("div", "note",
      "Spend data is unavailable (Cost Explorer: " + d.cost_explorer_status +
      "). Findings are still live. Savings are estimates based on typical on-demand prices."));
  }
  (d.errors || []).forEach(message => notes.appendChild(el("div", "note", "Scan warning: " + message)));

  const spendBox = $("spend");
  spendBox.replaceChildren();
  if (!d.spend.length) {
    spendBox.appendChild(el("div", "empty", "No spend data available."));
    $("t-spend").textContent = "n/a";
  } else {
    const max = Math.max.apply(null, d.spend.map(s => s.cost));
    $("t-spend").textContent = money(d.spend.reduce((sum, s) => sum + s.cost, 0));
    d.spend.slice(0, 10).forEach(s => {
      const row = el("div", "bar-row");
      row.appendChild(el("div", "name", s.service));
      const bar = el("div", "bar");
      const fill = el("span");
      fill.style.width = (max > 0 ? Math.max(2, (s.cost / max) * 100) : 0) + "%";
      bar.appendChild(fill);
      row.appendChild(bar);
      row.appendChild(el("div", "amt", money(s.cost)));
      spendBox.appendChild(row);
    });
  }

  renderByCloud(findings);
  renderByType(findings);
  renderTop(findings);
  fillSelect("f-cloud", Array.from(new Set(findings.map(f => f.provider))), v => CLOUD_NAMES[v] || v);
  fillSelect("f-type", Array.from(new Set(findings.map(f => f.type))), typeLabel);
  renderFindingsList();
}

async function refreshAll() {
  try {
    const [accounts, data] = await Promise.all([api("/api/accounts"), api("/api/report")]);
    renderAccounts(accounts.accounts);
    renderReport(data);
    return data;
  } catch (err) {
    setMessage("scan-msg", err.message, "error");
    return null;
  }
}

async function pollForUpdate(previous) {
  for (let i = 0; i < 24; i++) {
    await sleep(5000);
    const data = await refreshAll();
    if (data && data.scanned_at && data.scanned_at !== previous) return true;
  }
  return false;
}

/* ---------- actions ---------- */

function setScanning(on) {
  const button = $("scan-now");
  button.disabled = on;
  button.classList.toggle("loading", on);
  button.querySelector(".btn-label").textContent = on ? "Scanning..." : "Scan now";
}

function wireApp() {
  $("scan-now").addEventListener("click", async () => {
    setScanning(true);
    setMessage("scan-msg", "Queuing scans...");
    try {
      const before = (await api("/api/report")).scanned_at;
      const result = await api("/api/scan", { method: "POST", body: "{}" });
      if (!result.queued) {
        setMessage("scan-msg", "Scans were requested very recently. Try again in a minute.");
      } else {
        setMessage("scan-msg", result.queued + " scan(s) queued. Results appear here when they finish.", "ok");
        const updated = await pollForUpdate(before);
        setMessage("scan-msg", updated ? "Scan finished." : "Still running. Refresh in a minute.", updated ? "ok" : "");
      }
    } catch (err) {
      setMessage("scan-msg", err.message, "error");
    } finally {
      setScanning(false);
    }
  });

  $("toggle-form").addEventListener("click", () => {
    const card = $("account-form-card");
    card.hidden = !card.hidden;
    $("toggle-form").textContent = card.hidden ? "Connect account" : "Cancel";
    if (!card.hidden) $("acc-label").focus();
  });

  $("acc-provider").addEventListener("change", () => {
    const provider = $("acc-provider").value;
    document.querySelectorAll("[data-for]").forEach(section => {
      section.hidden = section.dataset.for !== provider;
    });
  });

  $("form-account").addEventListener("submit", async ev => {
    ev.preventDefault();
    const provider = $("acc-provider").value;
    const payload = { provider: provider, label: $("acc-label").value.trim() };
    if (provider === "aws") {
      payload.regions = $("acc-regions").value;
      const role = $("acc-role").value.trim();
      if (role) payload.role_arn = role;
    } else if (provider === "azure") {
      payload.credentials = {
        tenant_id: $("az-tenant").value.trim(),
        client_id: $("az-client").value.trim(),
        client_secret: $("az-secret").value.trim(),
        subscription_id: $("az-sub").value.trim(),
      };
    } else {
      payload.credentials = $("acc-gcp").value;
    }
    const problem = accountProblem(provider, payload);
    if (problem) {
      setMessage("acc-msg", problem, "error");
      return;
    }
    setMessage("acc-msg", "Connecting...");
    try {
      const result = await api("/api/accounts", { method: "POST", body: JSON.stringify(payload) });
      ["acc-label", "acc-regions", "acc-role", "az-tenant", "az-client", "az-secret", "az-sub", "acc-gcp"]
        .forEach(id => { $(id).value = ""; });
      const extra = result.account.external_id
        ? " It is not verified yet. Add this External ID to the role's trust policy, then press Verify on the account card: " + result.account.external_id
        : "";
      setMessage("acc-msg", "Account connected." + extra, "ok");
      refreshAll();
    } catch (err) {
      setMessage("acc-msg", err.message, "error");
    }
  });
}

async function start() {
  try {
    const res = await fetch("/config.json", { cache: "no-store" });
    config = await res.json();
  } catch (e) {
    config = {};
  }
  wireAuth();
  wireNav();
  wireAlerts();
  fillAwsHelp();
  wireFindings();
  wireApp();

  if (config.demo) {
    $("toggle-form").hidden = true;
    $("account-form-card").hidden = true;
    showApp();
    return;
  }
  const stored = readStoredToken();
  if (stored && tokenIsValid(stored)) {
    idToken = stored;
    showApp();
  } else {
    showAuth("signin");
  }
}

start();