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

let config = {};
let idToken = null;

function storeToken(token) {
  idToken = token;
  try {
    if (token) sessionStorage.setItem("cc_token", token);
    else sessionStorage.removeItem("cc_token");
  } catch (e) {}
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
    $("user-email").textContent = tokenPayload(idToken).email || "";
  }
  refreshAll();
}

function signOut() {
  storeToken(null);
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

function renderAccounts(accounts) {
  const box = $("accounts");
  box.replaceChildren();
  if (!accounts.length) {
    box.appendChild(el("div", "empty", "No accounts connected yet. Add one below to start scanning."));
    return;
  }
  accounts.forEach(account => {
    const wrap = el("div", "account");
    const top = el("div", "top");
    const title = el("span");
    title.appendChild(el("span", "badge " + account.provider, account.provider.toUpperCase()));
    title.appendChild(document.createTextNode(account.label));
    top.appendChild(title);
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
      top.appendChild(del);
    }
    wrap.appendChild(top);

    const status = account.last_scan_at
      ? account.last_status + " (" + new Date(account.last_scan_at).toLocaleString() + ")"
      : account.last_status || "never scanned";
    wrap.appendChild(el("div", "meta", status));
    if (account.role_arn) {
      wrap.appendChild(el("div", "meta", "Role: " + account.role_arn));
      wrap.appendChild(el("div", "meta", "External ID (add to the role's trust policy): " + account.external_id));
    } else if (account.host_account) {
      wrap.appendChild(el("div", "meta", "Scans the account that hosts ClearCloud"));
    }
    box.appendChild(wrap);
  });
}

function renderReport(d) {
  $("sub").textContent = d.scanned_at
    ? "Last scan: " + new Date(d.scanned_at).toLocaleString() + " · Clouds: " + (d.providers.join(", ") || "none")
    : "No scan has completed yet";
  $("t-clouds").textContent = d.providers.length;
  $("t-save").textContent = money(d.summary.total_est_monthly_savings);
  $("t-count").textContent = d.summary.count;

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
      row.appendChild(el("div", "", s.service));
      const bar = el("div", "bar");
      const fill = el("span");
      fill.style.width = (max > 0 ? (s.cost / max) * 100 : 0) + "%";
      bar.appendChild(fill);
      row.appendChild(bar);
      row.appendChild(el("div", "", money(s.cost)));
      spendBox.appendChild(row);
    });
  }

  const box = $("findings");
  box.replaceChildren();
  if (!d.findings.length) {
    box.appendChild(el("div", "empty",
      d.scanned_at ? "No wasted resources detected. Nice." : "Run a scan to see recommendations."));
    return;
  }
  d.findings.forEach(f => {
    const wrap = el("div", "finding");
    const top = el("div", "top");
    top.appendChild(el("span", "", f.title));
    top.appendChild(el("span", "save", "save ~" + money(f.est_monthly_savings) + "/mo"));
    wrap.appendChild(top);
    const meta = el("div", "meta");
    meta.appendChild(el("span", "badge " + f.provider, f.provider.toUpperCase()));
    meta.appendChild(document.createTextNode(
      (f.account_label ? f.account_label + " · " : "") + f.resource_id + " · " + f.region));
    wrap.appendChild(meta);
    wrap.appendChild(el("div", "rec", f.recommendation));
    box.appendChild(wrap);
  });
}

async function refreshAll() {
  try {
    const [accounts, report] = await Promise.all([api("/api/accounts"), api("/api/report")]);
    renderAccounts(accounts.accounts);
    renderReport(report);
    return report;
  } catch (err) {
    setMessage("scan-msg", err.message, "error");
    return null;
  }
}

async function pollForUpdate(previous) {
  for (let i = 0; i < 24; i++) {
    await sleep(5000);
    const report = await refreshAll();
    if (report && report.scanned_at && report.scanned_at !== previous) return true;
  }
  return false;
}

function wireApp() {
  $("scan-now").addEventListener("click", async () => {
    const button = $("scan-now");
    button.disabled = true;
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
      button.disabled = false;
    }
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
    setMessage("acc-msg", "Connecting...");
    try {
      const result = await api("/api/accounts", { method: "POST", body: JSON.stringify(payload) });
      ["acc-label", "acc-regions", "acc-role", "az-tenant", "az-client", "az-secret", "az-sub", "acc-gcp"]
        .forEach(id => { $(id).value = ""; });
      const extra = result.account.external_id
        ? " Add this External ID to the role's trust policy: " + result.account.external_id
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
  wireApp();

  if (config.demo) {
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
