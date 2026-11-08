/* Samsara Fleet — Telegram Mini App. Build bosqichisiz oddiy JS: API /api/app/*, matnlar /me'dan (uz/ru/en). */
"use strict";

const tg = window.Telegram && window.Telegram.WebApp;
const state = { me: null, tab: "home", alerts: { flt: "open", q: "", severity: "", items: [], total: 0 }, fleetTab: "vehicles", docsTab: null, period: "today", usersFilter: "pending" };
const $ = (sel, root = document) => root.querySelector(sel);
const view = () => $("#view");

// --- Yordamchilar ---

function T(key, vars = {}) {
  const text = (state.me && state.me.texts[key]) || key;
  return text.replace(/\{(\w+)\}/g, (_, name) => (name in vars ? vars[name] : `{${name}}`));
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function locale() {
  return { ru: "ru-RU", en: "en-US" }[state.me?.language] || "ru-RU";
}

// Chegara admin sozlamasidan keladi; 0 — ogohlantirish o'chirilgan.
function lowFuel(fuel) {
  return fuel != null && state.me.fuel_threshold > 0 && fuel < state.me.fuel_threshold;
}

function fmtTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString(locale(), { timeZone: state.me.timezone, day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  } catch (_) {
    return new Date(iso).toLocaleString();
  }
}

function fmtDate(isoDate) {
  if (!isoDate) return "—";
  const [y, m, d] = isoDate.split("-");
  return `${d}.${m}.${y}`;
}

function fmtDuration(ms) {
  if (ms === null || ms === undefined) return "—";
  const minutes = Math.max(Math.floor(ms / 60000), 0);
  return `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, "0")}m`;
}

function daysUntil(isoDate) {
  const today = new Date(new Date().toLocaleString("en-US", { timeZone: state.me.timezone }));
  today.setHours(0, 0, 0, 0);
  return Math.round((new Date(`${isoDate}T00:00:00`) - today) / 86400000);
}

function toast(message, isError = false) {
  const el = $("#toast");
  el.textContent = message;
  el.className = `toast${isError ? " error" : ""}`;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.add("hidden"), 2600);
  if (tg?.HapticFeedback) tg.HapticFeedback.notificationOccurred(isError ? "error" : "success");
}

async function api(path, options = {}) {
  const headers = { Authorization: `tma ${tg?.initData || ""}`, ...(options.headers || {}) };
  if (options.json !== undefined) {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(options.json);
  }
  const response = await fetch(`/api/app${path}`, { ...options, headers });
  let data = null;
  try { data = await response.json(); } catch (_) { /* bo'sh javob */ }
  if (!response.ok) {
    const detail = data && (typeof data.detail === "string" ? data.detail : null);
    throw new Error(detail || `HTTP ${response.status}`);
  }
  return data;
}

async function run(button, action) {
  if (button) button.disabled = true;
  try {
    return await action();
  } catch (error) {
    toast(error.message, true);
    return null;
  } finally {
    if (button) button.disabled = false;
  }
}

function html(strings, ...values) {
  return strings.reduce((out, s, i) => out + s + (i < values.length ? values[i] : ""), "");
}

// --- Sheet (pastdan chiquvchi oyna) ---

function openSheet(content) {
  $("#sheet-content").innerHTML = content;
  $("#sheet").classList.remove("hidden");
  if (tg?.BackButton) { tg.BackButton.show(); }
}

function closeSheet() {
  $("#sheet").classList.add("hidden");
  $("#sheet-content").innerHTML = "";
  if (tg?.BackButton) tg.BackButton.hide();
}

document.addEventListener("click", (event) => {
  if (event.target.closest("[data-close]")) closeSheet();
});

// --- Navigatsiya ---

const TAB_ICONS = { home: "🏠", alerts: "🚨", map: "🗺", fleet: "🚚", docs: "📁", reports: "📊", users: "👥" };
const RENDERERS = {};

function renderTabs() {
  const nav = $("#tabs");
  nav.innerHTML = state.me.sections
    .map((s) => `<button data-tab="${s}" class="${s === state.tab ? "active" : ""}"><span class="ico">${TAB_ICONS[s]}</span>${esc(T(`webapp.tab.${s}`))}</button>`)
    .join("");
  nav.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => go(b.dataset.tab)));
}

function go(tab) {
  if (!state.me.sections.includes(tab)) tab = "home";
  state.tab = tab;
  renderTabs();
  $("#title").textContent = T(`webapp.tab.${tab}`);
  view().innerHTML = `<div class="center muted">${esc(T("webapp.loading"))}</div>`;
  window.scrollTo(0, 0);
  run(null, async () => {
    await RENDERERS[tab]();
    view().querySelectorAll(".tile[data-go]").forEach((t) => t.addEventListener("click", () => go(t.dataset.go)));
  });
}

// --- Bosh sahifa ---

RENDERERS.home = async () => {
  const d = await api("/dashboard");
  const tiles = [];
  if (d.open !== undefined) {
    tiles.push(tile(d.critical_open, T("webapp.critical_open"), "critical", "alerts"));
    tiles.push(tile(d.new, T("webapp.new_alerts"), "warning", "alerts"));
    tiles.push(tile(d.open, T("webapp.open_alerts"), "", "alerts"));
    tiles.push(tile(d.today, T("webapp.today"), "", "reports"));
  }
  if (d.vehicles !== undefined) {
    tiles.push(tile(d.vehicles, T("webapp.vehicles"), "", "fleet"));
    tiles.push(tile(d.low_fuel, T("webapp.low_fuel"), d.low_fuel ? "warning" : "", "fleet"));
  }
  if (d.expiring_docs !== undefined) tiles.push(tile(d.expiring_docs, T("webapp.expiring_docs"), d.expiring_docs ? "warning" : "", "docs"));

  let out = `<div class="tiles">${tiles.join("")}</div>`;
  if (d.week) {
    const max = Math.max(1, ...d.week.map((x) => x.count));
    out += `<div class="card"><h3>${esc(T("webapp.week_chart"))}</h3><div class="bars">${d.week
      .map((x) => `<div class="bar"><b>${x.count || ""}</b><i style="height:${(x.count / max) * 70}%"></i><span>${fmtDate(x.date).slice(0, 5)}</span></div>`)
      .join("")}</div></div>`;
    out += `<div class="card"><h3>${esc(T("webapp.recent_alerts"))}</h3><div class="list">${
      d.recent.length ? d.recent.map(alertRow).join("") : `<div class="muted">${esc(T("webapp.empty"))}</div>`
    }</div></div>`;
  }
  view().innerHTML = out;
  bindAlertRows();
};

function tile(num, label, cls, target) {
  return `<div class="tile ${cls}" data-go="${target}"><div class="num">${num ?? 0}</div><div class="lbl">${esc(label)}</div></div>`;
}

// --- Alertlar ---

function alertRow(a) {
  const who = [a.vehicle, a.driver].filter(Boolean).map(esc).join(" · ") || "—";
  return `<div class="item" data-alert="${a.id}">
    <span class="dot ${esc(a.severity)}"></span>
    <div class="grow">
      <div class="row between"><b class="ellipsis">${esc(a.type_label)}</b><span class="badge ${esc(a.status)}">${esc(T(`webapp.status.${a.status}`))}</span></div>
      <div class="small ellipsis">${who}</div>
      <div class="small muted">#${a.id} · ${fmtTime(a.happened_at)}${a.address ? " · " + esc(a.address) : ""}</div>
    </div></div>`;
}

function bindAlertRows(root = view()) {
  root.querySelectorAll("[data-alert]").forEach((el) => el.addEventListener("click", () => openAlert(Number(el.dataset.alert))));
}

RENDERERS.alerts = async () => {
  const s = state.alerts;
  const filters = ["open", "new", "resolved", "all"]
    .map((f) => `<button data-flt="${f}" class="${f === s.flt ? "active" : ""}">${esc(T(`webapp.filter.${f}`))}</button>`).join("");
  const severities = ["", "critical", "warning", "info"]
    .map((v) => `<button data-sev="${v}" class="${v === s.severity ? "active" : ""}">${esc(T(`webapp.severity.${v || "any"}`))}</button>`).join("");
  view().innerHTML = `<div class="seg">${filters}</div><div class="seg">${severities}</div>
    <input class="search" id="aq" placeholder="${esc(T("webapp.search_alerts"))}" value="${esc(s.q)}">
    <div class="card"><div class="muted small" id="atotal"></div><div class="list" id="alist"></div>
    <button class="btn secondary block hidden" id="amore">${esc(T("webapp.load_more"))}</button></div>`;
  view().querySelectorAll("[data-flt]").forEach((b) => b.addEventListener("click", () => { s.flt = b.dataset.flt; RENDERERS.alerts(); }));
  view().querySelectorAll("[data-sev]").forEach((b) => b.addEventListener("click", () => { s.severity = b.dataset.sev; RENDERERS.alerts(); }));
  let timer;
  $("#aq").addEventListener("input", (e) => { clearTimeout(timer); timer = setTimeout(() => { s.q = e.target.value.trim(); loadAlerts(true); }, 350); });
  $("#amore").addEventListener("click", (e) => run(e.currentTarget, () => loadAlerts(false)));
  await loadAlerts(true);
};

async function loadAlerts(reset) {
  const s = state.alerts;
  if (reset) s.items = [];
  const params = new URLSearchParams({ flt: s.flt, offset: s.items.length });
  if (s.q) params.set("q", s.q);
  if (s.severity) params.set("severity", s.severity);
  const data = await api(`/alerts?${params}`);
  s.items = s.items.concat(data.items);
  s.total = data.total;
  if (!$("#alist")) return;
  $("#atotal").textContent = T("webapp.total", { count: s.total });
  $("#alist").innerHTML = s.items.length ? s.items.map(alertRow).join("") : `<div class="center muted">${esc(T("webapp.empty"))}</div>`;
  $("#amore").classList.toggle("hidden", s.items.length >= s.total);
  bindAlertRows($("#alist"));
}

async function openAlert(id) {
  const a = await run(null, () => api(`/alerts/${id}`));
  if (!a) return;
  const map = a.lat != null ? `<a href="https://www.google.com/maps/search/?api=1&query=${a.lat},${a.lon}" target="_blank">🗺 ${esc(T("webapp.open_map"))}</a>` : "";
  const samsara = a.samsara_url ? `<a href="${esc(a.samsara_url)}" target="_blank">↗ Samsara</a>` : "";
  const rows = [
    [T("webapp.field.status"), `<span class="badge ${esc(a.status)}">${esc(T(`webapp.status.${a.status}`))}</span>`],
    [T("webapp.field.vehicle"), esc(a.vehicle || "—")],
    [T("webapp.field.driver"), esc(a.driver || "—")],
    [T("webapp.field.time"), fmtTime(a.happened_at)],
    [T("webapp.field.place"), esc(a.address || "—")],
    [T("webapp.field.details"), esc(a.message || "—")],
  ];
  if (a.acknowledged_by) rows.push([T("webapp.field.acked"), `${esc(a.acknowledged_by)} · ${fmtTime(a.acknowledged_at)}`]);
  if (a.assigned_to) rows.push([T("webapp.field.assigned"), esc(a.assigned_to)]);
  if (a.resolved_by) rows.push([T("webapp.field.resolved"), `${esc(a.resolved_by)} · ${fmtTime(a.resolved_at)}`]);
  if (a.resolution_reason) rows.push([T("webapp.field.reason"), esc(T(`alert.reason.${a.resolution_reason}`))]);
  if (a.note) rows.push([T("webapp.field.note"), esc(a.note).replace(/\n/g, "<br>")]);

  const buttons = [];
  if (a.can.ack) buttons.push(`<button class="btn" data-act="ack">✅ ${esc(T("webapp.act.ack"))}</button>`);
  if (a.can.assign) buttons.push(`<button class="btn secondary" data-act="assign">🙋 ${esc(T("webapp.act.assign"))}</button>`);
  let resolve = "";
  if (a.can.resolve) {
    resolve = `<label class="field">${esc(T("webapp.field.reason"))}</label>
      <select class="input" id="reason">${state.me.reasons.map((r) => `<option value="${r}">${esc(T(`alert.reason.${r}`))}</option>`).join("")}</select>
      <button class="btn block" data-act="resolve">🏁 ${esc(T("webapp.act.resolve"))}</button>`;
  }
  openSheet(`<h2><span class="dot ${esc(a.severity)}" style="display:inline-block;margin:0 6px 2px 0"></span>${esc(a.type_label)} #${a.id}</h2>
    <dl class="kv">${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join("")}</dl>
    <div class="row" style="gap:16px;margin-top:10px">${map}${samsara}</div>
    <div class="btns">${buttons.join("")}</div>${resolve}`);
  $("#sheet-content").querySelectorAll("[data-act]").forEach((b) =>
    b.addEventListener("click", () => run(b, async () => {
      const body = { action: b.dataset.act };
      if (body.action === "resolve") body.reason = $("#reason").value;
      const result = await api(`/alerts/${id}/action`, { method: "POST", json: body });
      toast(result.message);
      closeSheet();
      if (state.tab === "alerts") await loadAlerts(true); else go(state.tab);
    })));
}

// --- Xarita ---

function loadLeaflet() {
  if (window.L) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const css = document.createElement("link");
    css.rel = "stylesheet";
    css.href = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css";
    document.head.appendChild(css);
    const js = document.createElement("script");
    js.src = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
    js.onload = resolve;
    js.onerror = () => reject(new Error(T("webapp.map_failed")));
    document.head.appendChild(js);
  });
}

RENDERERS.map = async () => {
  const [data] = await Promise.all([api("/vehicles"), loadLeaflet()]);
  const located = data.items.filter((v) => v.lat != null && v.lon != null);
  view().innerHTML = `<div class="muted small" style="margin-bottom:6px">${esc(T("webapp.on_map", { count: located.length, total: data.items.length }))}</div><div id="map"></div>`;
  const map = L.map("map");
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18, attribution: "© OpenStreetMap" }).addTo(map);
  const bounds = [];
  located.forEach((v) => {
    const low = lowFuel(v.fuel);
    const marker = L.circleMarker([v.lat, v.lon], { radius: 8, weight: 2, color: "#fff", fillColor: low ? "#e5484d" : "#2481cc", fillOpacity: 0.95 });
    marker.bindPopup(`<b>${esc(v.name)}</b>${v.plate ? " · " + esc(v.plate) : ""}<br>${esc(v.address || "")}<br>${
      v.fuel != null ? "⛽ " + Math.round(v.fuel) + "% · " : ""}${fmtTime(v.last_seen)}`);
    marker.addTo(map);
    bounds.push([v.lat, v.lon]);
  });
  if (bounds.length) map.fitBounds(bounds, { padding: [30, 30], maxZoom: 12 });
  else map.setView([41.8781, -87.6298], 6); // Chicago
};

// --- Park ---

RENDERERS.fleet = async () => {
  const tabs = ["vehicles", "drivers", "hos", "fuel"]
    .map((t) => `<button data-ft="${t}" class="${t === state.fleetTab ? "active" : ""}">${esc(T(`webapp.fleet.${t}`))}</button>`).join("");
  view().innerHTML = `<div class="seg">${tabs}</div><div id="fleet"></div>`;
  view().querySelectorAll("[data-ft]").forEach((b) => b.addEventListener("click", () => { state.fleetTab = b.dataset.ft; RENDERERS.fleet(); }));
  const box = $("#fleet");
  box.innerHTML = `<div class="center muted">${esc(T("webapp.loading"))}</div>`;
  await FLEET[state.fleetTab](box);
};

const FLEET = {
  async vehicles(box) {
    box.innerHTML = `<input class="search" id="vq" placeholder="${esc(T("webapp.search_vehicles"))}"><div class="card list" id="vlist"></div>`;
    const load = async (q = "") => {
      const data = await api(`/vehicles${q ? "?q=" + encodeURIComponent(q) : ""}`);
      $("#vlist").innerHTML = data.items.length ? data.items.map((v) => `<div class="item"><div class="grow">
        <div class="row between"><b class="ellipsis">🚚 ${esc(v.name)}</b>${fuelBar(v.fuel)}</div>
        <div class="small muted ellipsis">${[v.plate, v.depot].filter(Boolean).map(esc).join(" · ") || "—"}</div>
        <div class="small muted ellipsis">📍 ${esc(v.address || "—")} · ${fmtTime(v.last_seen)}</div></div></div>`).join("")
        : `<div class="center muted">${esc(T("webapp.empty"))}</div>`;
    };
    let timer;
    $("#vq").addEventListener("input", (e) => { clearTimeout(timer); timer = setTimeout(() => run(null, () => load(e.target.value.trim())), 350); });
    await load();
  },
  async drivers(box) {
    box.innerHTML = `<input class="search" id="dq" placeholder="${esc(T("webapp.search_drivers"))}"><div class="card list" id="dlist"></div>`;
    const load = async (q = "") => {
      const data = await api(`/drivers${q ? "?q=" + encodeURIComponent(q) : ""}`);
      $("#dlist").innerHTML = data.items.length ? data.items.map((d) => `<div class="item"><div class="grow">
        <b>👤 ${esc(d.name)}</b><div class="small muted">${esc(d.depot || "—")}</div></div>
        ${d.phone ? `<a href="tel:${esc(d.phone)}">📞 ${esc(d.phone)}</a>` : ""}</div>`).join("")
        : `<div class="center muted">${esc(T("webapp.empty"))}</div>`;
    };
    let timer;
    $("#dq").addEventListener("input", (e) => { clearTimeout(timer); timer = setTimeout(() => run(null, () => load(e.target.value.trim())), 350); });
    await load();
  },
  async hos(box) {
    const data = await api("/hos");
    if (data.error) { box.innerHTML = `<div class="card center muted">${esc(data.error)}</div>`; return; }
    box.innerHTML = `<div class="card list">${data.items.length ? data.items.map((c) => `<div class="item">
      <span>${c.working ? (c.warning ? "⚠️" : "🟢") : "💤"}</span><div class="grow">
      <div class="row between"><b class="ellipsis">${esc(c.driver)}</b><span class="badge">${esc(c.status || "—")}</span></div>
      <div class="small">🚛 ${fmtDuration(c.drive_ms)} · 🕐 ${fmtDuration(c.shift_ms)} · 🔁 ${fmtDuration(c.cycle_ms)}</div>
      <div class="small muted">${esc(c.vehicle || "—")}</div></div></div>`).join("") : `<div class="center muted">${esc(T("webapp.empty"))}</div>`}</div>
      <div class="small muted">🚛 ${esc(T("webapp.hos.drive"))} · 🕐 ${esc(T("webapp.hos.shift"))} · 🔁 ${esc(T("webapp.hos.cycle"))}</div>`;
  },
  async fuel(box) {
    const data = await api("/vehicles");
    const items = data.items.filter((v) => v.fuel != null).sort((a, b) => a.fuel - b.fuel);
    box.innerHTML = `<div class="card">${items.length ? items.map((v) => `<div class="hbar"><div class="row between small">
      <span class="ellipsis">${esc(v.name)}</span><b>${Math.round(v.fuel)}%</b></div>
      <div class="track"><div class="fill" style="width:${Math.max(2, v.fuel)}%;background:${lowFuel(v.fuel) ? "var(--critical)" : "var(--info)"}"></div></div></div>`).join("")
      : `<div class="center muted">${esc(T("webapp.no_fuel"))}</div>`}</div>`;
  },
};

function fuelBar(fuel) {
  if (fuel == null) return "";
  return `<span class="row small" style="gap:6px"><span class="fuel ${lowFuel(fuel) ? "low" : ""}"><i style="width:${Math.max(2, fuel)}%"></i></span>${Math.round(fuel)}%</span>`;
}

// --- Hujjatlar ---

RENDERERS.docs = async () => {
  const isDriver = state.me.role === "driver";
  const tabs = isDriver ? ["mine", "upload"] : ["company", "expiring", "all", "upload"];
  if (!tabs.includes(state.docsTab)) state.docsTab = tabs[0];
  view().innerHTML = `<div class="seg">${tabs.map((t) => `<button data-dt="${t}" class="${t === state.docsTab ? "active" : ""}">${esc(T(`webapp.docs.${t}`))}</button>`).join("")}</div><div id="docs"></div>`;
  view().querySelectorAll("[data-dt]").forEach((b) => b.addEventListener("click", () => { state.docsTab = b.dataset.dt; RENDERERS.docs(); }));
  const box = $("#docs");
  if (state.docsTab === "upload") return uploadForm(box);
  const query = { company: "?owner=company", expiring: "?expiring=true", all: "", mine: "" }[state.docsTab];
  const data = await api(`/documents${query}`);
  box.innerHTML = `<div class="card list">${data.items.length ? data.items.map(docRow).join("") : `<div class="center muted">${esc(T("webapp.empty"))}</div>`}</div>`;
  box.querySelectorAll("[data-doc]").forEach((el) => el.addEventListener("click", () => openDoc(data.items.find((d) => d.id === Number(el.dataset.doc)))));
};

function expiryBadge(doc) {
  if (!doc.expires_at) return "";
  const days = daysUntil(doc.expires_at);
  const cls = days < 0 ? "expired" : days <= 30 ? "soon" : "";
  return `<span class="badge ${cls}">⏰ ${fmtDate(doc.expires_at)}</span>`;
}

function docRow(d) {
  return `<div class="item" data-doc="${d.id}"><span>${/pdf/.test(d.mime_type || "") ? "📕" : /sheet|excel|csv/.test(d.mime_type || "") ? "📗" : /image/.test(d.mime_type || "") ? "🖼" : "📄"}</span>
    <div class="grow"><div class="row between"><b class="ellipsis">${esc(d.title)}</b>${expiryBadge(d)}</div>
    <div class="small muted ellipsis">${esc(T(`docs.type.${d.type}`))} · ${esc(d.owner)}</div></div></div>`;
}

function openDoc(d) {
  openSheet(`<h2>${esc(d.title)}</h2><dl class="kv">
    <dt>${esc(T("webapp.field.type"))}</dt><dd>${esc(T(`docs.type.${d.type}`))}</dd>
    <dt>${esc(T("webapp.field.owner"))}</dt><dd>${esc(d.owner)}</dd>
    <dt>${esc(T("webapp.field.expires"))}</dt><dd>${d.expires_at ? fmtDate(d.expires_at) : "—"} ${expiryBadge(d)}</dd>
    <dt>${esc(T("webapp.field.file"))}</dt><dd>${esc(d.file_name || "—")}${d.size ? ` · ${(d.size / 1024).toFixed(0)} KB` : ""}</dd>
    <dt>${esc(T("webapp.field.uploaded"))}</dt><dd>${fmtTime(d.uploaded_at)}</dd></dl>
    <div class="btns"><button class="btn" id="dsend">📩 ${esc(T("webapp.send_to_chat"))}</button>
    ${d.can_delete ? `<button class="btn danger" id="ddel">🗑 ${esc(T("webapp.delete"))}</button>` : ""}</div>`);
  $("#dsend").addEventListener("click", (e) => run(e.currentTarget, async () => toast((await api(`/documents/${d.id}/send`, { method: "POST" })).message)));
  const del = $("#ddel");
  if (del) del.addEventListener("click", (e) => {
    const confirmDelete = () => run(e.currentTarget, async () => { toast((await api(`/documents/${d.id}`, { method: "DELETE" })).message); closeSheet(); RENDERERS.docs(); });
    if (tg?.showConfirm) tg.showConfirm(T("webapp.confirm_delete"), (ok) => ok && confirmDelete());
    else if (confirm(T("webapp.confirm_delete"))) confirmDelete();
  });
}

async function uploadForm(box) {
  const isDriver = state.me.role === "driver";
  const owners = isDriver ? ["driver"] : ["company", "driver", "vehicle"];
  box.innerHTML = `<form class="card" id="upform">
    ${isDriver ? "" : `<label class="field">${esc(T("webapp.field.owner"))}</label>
      <select class="input" name="owner_type" id="otype">${owners.map((o) => `<option value="${o}">${esc(T(`docs.owner.${o}`))}</option>`).join("")}</select>
      <div id="opick"></div>`}
    <label class="field">${esc(T("webapp.field.type"))}</label><select class="input" name="doc_type" id="dtype"></select>
    <label class="field">${esc(T("webapp.field.title"))}</label><input class="input" name="title" maxlength="200">
    <label class="field">${esc(T("webapp.field.expires"))}</label><input class="input" type="date" name="expires_at">
    <label class="field">${esc(T("webapp.field.file"))}</label>
    <input class="input" type="file" name="file" required accept=".pdf,.xlsx,.xls,.csv,.docx,.doc,.txt,image/*">
    <button class="btn block" type="submit">⬆️ ${esc(T("webapp.upload"))}</button></form>`;
  const ownerType = () => (isDriver ? "driver" : $("#otype").value);
  const fillTypes = () => {
    $("#dtype").innerHTML = state.me.doc_types[ownerType()].map((d) => `<option value="${d}">${esc(T(`docs.type.${d}`))}</option>`).join("");
  };
  const fillOwners = async () => {
    const pick = $("#opick");
    if (!pick) return;
    const type = ownerType();
    if (type === "company") { pick.innerHTML = ""; return; }
    const data = await api(type === "driver" ? "/drivers" : "/vehicles");
    pick.innerHTML = `<label class="field">${esc(T(type === "driver" ? "webapp.fleet.drivers" : "webapp.fleet.vehicles"))}</label>
      <select class="input" name="owner_id" required>${data.items.map((x) => `<option value="${x.id}">${esc(x.name)}</option>`).join("")}</select>`;
  };
  fillTypes();
  if (!isDriver) $("#otype").addEventListener("change", () => { fillTypes(); run(null, fillOwners); });
  $("#upform").addEventListener("submit", (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    if (!data.get("expires_at")) data.delete("expires_at");
    if (!data.get("title")) data.delete("title");
    run(form.querySelector("button[type=submit]"), async () => {
      const result = await api("/documents", { method: "POST", body: data });
      toast(result.message.replace(/<[^>]+>/g, ""));
      form.reset();
      fillTypes();
    });
  });
}

// --- Hisobotlar ---

RENDERERS.reports = async () => {
  const periods = ["today", "yesterday", "7d", "30d"]
    .map((p) => `<button data-p="${p}" class="${p === state.period ? "active" : ""}">${esc(T(`reports.period.${p}`))}</button>`).join("");
  const r = await api(`/reports?period=${state.period}`);
  const minutes = (v) => (v == null ? "—" : `${Math.round(v)} ${T("webapp.min")}`);
  const bars = (items, labelKey) => {
    const max = Math.max(1, ...items.map((x) => x.count));
    return items.length ? items.map((x) => `<div class="hbar"><div class="row between small"><span class="ellipsis">${esc(labelKey(x))}</span><b>${x.count}</b></div>
      <div class="track"><div class="fill" style="width:${(x.count / max) * 100}%"></div></div></div>`).join("") : `<div class="muted small">—</div>`;
  };
  view().innerHTML = `<div class="seg">${periods}</div><div class="muted small" style="margin-bottom:8px">${esc(r.label)}</div>
    <div class="tiles">
      ${tile(r.total, T("webapp.total_alerts"), "", "alerts")}
      ${tile(r.by_severity.critical || 0, T("webapp.severity.critical"), "critical", "alerts")}
      ${tile(r.acknowledged, T("webapp.acknowledged"), "", "alerts")}
      ${tile(r.resolved, T("webapp.resolved"), "", "alerts")}
    </div>
    <div class="card"><dl class="kv"><dt>${esc(T("webapp.avg_response"))}</dt><dd>${minutes(r.avg_response_min)}</dd>
      <dt>${esc(T("webapp.median_response"))}</dt><dd>${minutes(r.median_response_min)}</dd>
      <dt>${esc(T("webapp.avg_resolve"))}</dt><dd>${minutes(r.avg_resolve_min)}</dd></dl></div>
    <div class="card"><h3>${esc(T("webapp.by_type"))}</h3>${bars(r.by_type, (x) => x.label)}</div>
    <div class="card"><h3>${esc(T("webapp.top_vehicles"))}</h3>${bars(r.top_vehicles, (x) => x.name)}</div>
    <div class="card"><h3>${esc(T("webapp.top_drivers"))}</h3>${bars(r.top_drivers, (x) => x.name)}</div>
    <div class="card"><h3>${esc(T("webapp.by_reason"))}</h3>${bars(r.by_reason, (x) => T(`alert.reason.${x.reason}`))}</div>
    ${r.can_export ? `<div class="card"><h3>${esc(T("webapp.export"))}</h3><div class="muted small">${esc(T("webapp.export_hint"))}</div>
      <div class="btns"><button class="btn" data-fmt="xlsx">📊 Excel</button><button class="btn" data-fmt="pdf">📄 PDF</button><button class="btn secondary" data-fmt="csv">🧾 CSV</button></div></div>` : ""}`;
  view().querySelectorAll("[data-p]").forEach((b) => b.addEventListener("click", () => { state.period = b.dataset.p; go("reports"); }));
  view().querySelectorAll("[data-fmt]").forEach((b) => b.addEventListener("click", () =>
    run(b, async () => toast((await api("/reports/send", { method: "POST", json: { period: state.period, fmt: b.dataset.fmt } })).message))));
};

// --- Foydalanuvchilar ---

RENDERERS.users = async () => {
  const data = await api("/users");
  state.depots = data.depots;
  const counts = { pending: 0, active: 0, blocked: 0 };
  data.items.forEach((u) => { counts[u.status] = (counts[u.status] || 0) + 1; });
  const filters = ["pending", "active", "blocked"]
    .map((f) => `<button data-uf="${f}" class="${f === state.usersFilter ? "active" : ""}">${esc(T(`status.${f}`))} (${counts[f] || 0})</button>`).join("");
  const items = data.items.filter((u) => u.status === state.usersFilter);
  view().innerHTML = `<div class="seg">${filters}</div><div class="card list">${items.length ? items.map((u) => `<div class="item" data-user="${u.id}">
    <div class="grow"><div class="row between"><b class="ellipsis">${esc(u.name)}</b><span class="badge">${esc(T(`role.${u.role}`))}</span></div>
    <div class="small muted ellipsis">${[u.position, u.phone, u.username ? "@" + u.username : ""].filter(Boolean).map(esc).join(" · ") || "—"}</div>
    ${u.depots.length ? `<div class="small muted ellipsis">🏢 ${u.depots.map(esc).join(", ")}</div>` : ""}</div></div>`).join("")
    : `<div class="center muted">${esc(T("webapp.empty"))}</div>`}</div>`;
  view().querySelectorAll("[data-uf]").forEach((b) => b.addEventListener("click", () => { state.usersFilter = b.dataset.uf; RENDERERS.users(); }));
  view().querySelectorAll("[data-user]").forEach((el) => el.addEventListener("click", () => openUser(data.items.find((u) => u.id === Number(el.dataset.user)))));
};

function openUser(u) {
  const roles = state.me.assignable_roles.map((r) => `<option value="${r}" ${r === u.role ? "selected" : ""}>${esc(T(`role.${r}`))}</option>`).join("");
  const depots = (state.depots || []).map((d) => `<label><input type="checkbox" name="depot" value="${d.id}" ${u.depot_ids.includes(d.id) ? "checked" : ""}> ${esc(d.name)}</label>`).join("");
  const isGlobal = (role) => role === "owner" || role === "admin";
  let actions = "";
  if (u.status === "pending") {
    actions = `<label class="field">${esc(T("webapp.field.role"))}</label><select class="input" id="urole">${roles}</select>
      ${depots ? `<label class="field">${esc(T("webapp.field.depots"))}</label><div class="checks" id="udepots">${depots}</div>` : ""}
      <div class="btns"><button class="btn" data-ua="approve">✅ ${esc(T("webapp.approve"))}</button><button class="btn danger" data-ua="reject">❌ ${esc(T("webapp.reject"))}</button></div>`;
  } else if (u.status === "active" && u.role !== "owner") {
    actions = `<label class="field">${esc(T("webapp.field.role"))}</label><select class="input" id="urole">${roles}</select>
      <button class="btn secondary block" data-ua="role">💾 ${esc(T("webapp.save_role"))}</button>
      ${depots && !isGlobal(u.role) ? `<label class="field">${esc(T("webapp.field.depots"))}</label><div class="checks" id="udepots">${depots}</div>
      <button class="btn secondary block" data-ua="depots">💾 ${esc(T("webapp.save_depots"))}</button>` : ""}
      <button class="btn danger block" data-ua="block">🚫 ${esc(T("webapp.block"))}</button>`;
  } else if (u.status === "blocked") {
    actions = `<button class="btn block" data-ua="unblock">♻️ ${esc(T("webapp.unblock"))}</button>`;
  }
  openSheet(`<h2>${esc(u.name)}</h2><dl class="kv">
    <dt>${esc(T("webapp.field.role"))}</dt><dd>${esc(T(`role.${u.role}`))}</dd>
    <dt>${esc(T("webapp.field.status"))}</dt><dd>${esc(T(`status.${u.status}`))}</dd>
    <dt>${esc(T("webapp.field.position"))}</dt><dd>${esc(u.position || "—")}</dd>
    <dt>${esc(T("webapp.field.phone"))}</dt><dd>${u.phone ? `<a href="tel:${esc(u.phone)}">${esc(u.phone)}</a>` : "—"}</dd>
    <dt>Telegram</dt><dd>${u.username ? `<a href="https://t.me/${esc(u.username)}" target="_blank">@${esc(u.username)}</a>` : "—"}</dd>
    <dt>${esc(T("webapp.field.depots"))}</dt><dd>${u.depots.map(esc).join(", ") || "—"}</dd></dl>${actions}`);
  $("#sheet-content").querySelectorAll("[data-ua]").forEach((b) => b.addEventListener("click", () => run(b, async () => {
    const body = { action: b.dataset.ua };
    if ($("#urole")) body.role = $("#urole").value;
    if ($("#udepots")) body.depot_ids = [...$("#udepots").querySelectorAll("input:checked")].map((i) => Number(i.value));
    const result = await api(`/users/${u.id}`, { method: "POST", json: body });
    toast(result.message);
    closeSheet();
    RENDERERS.users();
  })));
}

// --- Ishga tushirish ---

const FALLBACK = {
  uz: "Bu ilova Telegram ichida ochiladi: botdagi «Fleet» tugmasini bosing.",
  ru: "Это приложение открывается внутри Telegram: нажмите кнопку «Fleet» в боте.",
  en: "This app opens inside Telegram: tap the “Fleet” button in the bot.",
};

async function boot() {
  if (tg) {
    tg.ready();
    tg.expand();
    document.documentElement.dataset.tg = "1";
    if (tg.BackButton) tg.BackButton.onClick(closeSheet);
  }
  if (!tg || !tg.initData) {
    const lang = (navigator.language || "uz").slice(0, 2);
    $("#boot").textContent = FALLBACK[lang] || FALLBACK.uz;
    return;
  }
  try {
    state.me = await api("/me");
  } catch (error) {
    $("#boot").textContent = error.message;
    return;
  }
  document.documentElement.lang = state.me.language;
  $("#who").textContent = `${state.me.name} · ${T(`role.${state.me.role}`)}`;
  const start = new URLSearchParams(location.search).get("tab") || (tg.initDataUnsafe && tg.initDataUnsafe.start_param) || "home";
  go(start);
}

boot();
