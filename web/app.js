/* Todas las URLs son relativas: funciona en /dashboard/ y en /spam-status/. */
"use strict";
const $ = id => document.getElementById(id);
const number = v => new Intl.NumberFormat("es").format(v || 0);
const date = v => new Date(v * 1000).toLocaleString("es", {dateStyle: "short", timeStyle: "short"});
const chosen = new Map();
let overviewAbort, usersAbort, searchTimer, data, offset = 0, userOffset = 0;
const kinds = {blocked: "Bloqueo de cuenta", unblocked: "Desbloqueo", held: "Retención", hold_ended: "Fin de retención", rejected: "Rechazo"};

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
async function request(endpoint, params, signal) {
  const response = await fetch("api/" + endpoint + "?" + params.toString(), {signal, cache: "no-store", credentials: "same-origin"});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "No se pudo consultar el histórico");
  return result;
}
function params() {
  const p = new URLSearchParams();
  let start, end;
  if ($("period").value === "custom") {
    start = new Date($("from").value).getTime() / 1000;
    end = new Date($("to").value).getTime() / 1000;
    if (!Number.isFinite(start) || !Number.isFinite(end)) throw new Error("Indique las fechas del periodo personalizado");
  } else {
    end = Math.ceil(Date.now() / 60000) * 60;
    start = end - Number($("period").value);
    // Evitar pedir más de 60 días por el redondeo del minuto actual.
    start = Math.max(start, Math.floor(Date.now() / 60000) * 60 - 5184000);
  }
  p.set("from", start); p.set("to", end);
  for (const key of ["bucket", "origin", "destination", "ignored"]) p.set(key, $(key).value);
  for (const account of chosen.keys()) p.append("user", account);
  p.set("offset", offset);
  return p;
}
function error(message) { $("error").textContent = message; $("error").hidden = !message; }
async function load(reset = false) {
  if (reset) offset = 0;
  if (overviewAbort) overviewAbort.abort();
  overviewAbort = new AbortController();
  $("refresh").disabled = true;
  try {
    const result = await request("overview", params(), overviewAbort.signal);
    data = result;
    error(""); render(result);
  } catch (e) {
    if (e.name !== "AbortError") { error(e.message); $("notice").textContent = "No se ha actualizado el panel. Las cifras anteriores, si existen, conservan sus filtros anteriores."; }
  } finally { if (!overviewAbort.signal.aborted) $("refresh").disabled = false; }
}
function selections() {
  $("selected-count").textContent = chosen.size ? "· " + chosen.size + " seleccionado(s)" : "· Todos";
  $("selected-users").replaceChildren();
  for (const [account, label] of chosen) {
    const chip = node("button", label + " ×"); chip.type = "button";
    chip.addEventListener("click", () => { chosen.delete(account); syncOptions(); selections(); load(true); });
    $("selected-users").append(chip);
  }
}
function syncOptions() { for (const option of $("users").options) option.selected = chosen.has(option.value); }
async function users(more = false) {
  if (usersAbort) usersAbort.abort();
  usersAbort = new AbortController();
  if (!more) userOffset = 0;
  try {
    const result = await request("users", new URLSearchParams({search: $("user-search").value, offset: userOffset}), usersAbort.signal);
    if (!more) $("users").replaceChildren();
    for (const user of result.users) {
      const label = user.label + (user.ignored ? " · ignorada" : "");
      const option = node("option", label); option.value = user.account; option.selected = chosen.has(user.account);
      $("users").append(option);
    }
    userOffset += result.users.length;
    $("more-users").hidden = !result.has_more;
  } catch (e) { if (e.name !== "AbortError") error(e.message); }
}
function render(result) {
  const h = result.history, w = result.worker;
  const status = ["Datos analizados hasta " + (h.analysed_until ? date(h.analysed_until) : "pendiente")];
  if (!w.ok || Date.now() / 1000 - (w.heartbeat || 0) > 90) status.push("trabajador estadístico no disponible o atrasado");
  if (h.analysed_until && Date.now() / 1000 - h.analysed_until > 180) status.push("hay retraso en los datos");
  if (h.started_at && result.from < h.started_at) status.push("el intervalo comienza antes de la activación del histórico");
  if (h.dropped || h.loss_detected_at) status.push("hay pérdida de cobertura registrada");
  if (h.restored_at) status.push("histórico restaurado de una copia; revise su cobertura");
  if (h.coverage && h.coverage.length) status.push("cobertura: " + h.coverage.join("; "));
  if (result.incomplete_messages) status.push(number(result.incomplete_messages) + " mensajes con destinatarios incompletos");
  $("notice").textContent = status.join(" · ");
  $("range-label").textContent = date(result.from) + " — " + date(result.to) + " · " + ({minute: "minutos", hour: "horas", day: "días"}[result.bucket]);
  const totals = result.totals;
  const active = kind => result.active.find(r => r.kind === kind)?.n || 0;
  const cards = [["Mensajes aceptados", totals.messages], ["Destinatarios aceptados", totals.recipients],
    ["Diferidos (cohorte)", totals.deferred], ["Intentos rechazados", totals.rejected],
    ["Nuevas retenciones", totals.held], ["Cuentas bloqueadas ahora", active("blocked")]];
  $("cards").replaceChildren();
  for (const [title, amount] of cards) { const card = node("div", undefined, "card"); card.append(node("span", title), node("strong", number(amount))); $("cards").append(card); }
  chart(result);
  for (const destination of ["remote", "local", "unknown"]) table($(destination + "-table"), result.table.filter(r => r.destination === destination));
  eventTable(result.events);
  $("previous").disabled = result.offset === 0;
  $("next").disabled = !result.has_more;
}
function table(container, rows) {
  container.replaceChildren();
  if (!rows.length) { container.append(node("p", "Sin actividad en esta página para los filtros seleccionados.", "empty")); return; }
  const table = node("table"), header = node("tr");
  for (const label of ["Usuario", "Origen", "Mensajes", "Destinatarios", "Entregados", "Diferidos", "Fallidos", "Pendientes", "Última aceptación"]) header.append(node("th", label));
  const thead = node("thead"); thead.append(header); table.append(thead);
  const body = node("tbody");
  for (const row of rows) {
    const tr = node("tr"), cell = node("td"), button = node("button", row.label); button.type = "button";
    button.addEventListener("click", () => { chosen.clear(); chosen.set(row.account, row.label); selections(); syncOptions(); load(true); });
    cell.append(button); if (row.ignored) cell.append(node("span", "ignorada", "badge"));
    tr.append(cell, node("td", row.origin === "smtp" ? "SMTP" : "sendmail"));
    for (const n of [row.messages, row.recipients, row.sent, row.deferred, row.bounced, Math.max(0, row.recipients - row.sent - row.deferred - row.bounced)]) tr.append(node("td", number(n), "number"));
    tr.append(node("td", date(row.last_seen))); body.append(tr);
  }
  table.append(body); container.append(table);
}
function eventTable(rows) {
  $("events").replaceChildren();
  if (!rows.length) { $("events").append(node("p", "Sin incidencias observadas en el periodo.", "empty")); return; }
  const table = node("table"), head = node("tr");
  for (const label of ["Fecha", "Usuario", "Evento", "ID de cola", "Detalle"]) head.append(node("th", label));
  const thead = node("thead"); thead.append(head); table.append(thead); const body = node("tbody");
  for (const row of rows) {
    const tr = node("tr"); tr.append(node("td", date(row.at)), node("td", row.label), node("td", kinds[row.kind] || row.kind, row.kind === "rejected" ? "rejected" : "held"), node("td", row.qid || "—"), node("td", row.detail || "—", "detail")); body.append(tr);
  }
  table.append(body); $("events").append(table);
}
function svg(tag, attrs, text) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, val] of Object.entries(attrs)) element.setAttribute(key, val);
  if (text !== undefined) element.textContent = text;
  return element;
}
function chart(result) {
  const root = $("chart"); root.replaceChildren();
  const step = result.step, start = Math.floor(result.from / step) * step;
  const count = Math.ceil((result.to - start) / step);
  const series = Object.fromEntries(["remote", "local", "unknown", "rejected", "held"].map(k => [k, Array(count).fill(0)]));
  if ($("metric").value !== "deliveries") {
    for (const row of result.graph) { const i = Math.floor((row.at - start) / step); if (series[row.destination] && i >= 0 && i < count) series[row.destination][i] += row[$("metric").value]; }
  }
  for (const row of result.event_graph) {
    const key = row.kind === "sent" ? ($("metric").value === "deliveries" ? row.destination : null) : row.kind;
    const i = Math.floor((row.at - start) / step);
    if (series[key] && i >= 0 && i < count) series[key][i] += row.n;
  }
  // Los contadores son enteros: elegir intervalos enteros antes de dibujar,
  // en lugar de redondear etiquetas fraccionarias que acabarían repitiéndose.
  const peak = Math.max(1, ...Object.values(series).flat());
  const magnitude = 10 ** Math.floor(Math.log10(Math.max(1, peak / 4)));
  const tick = [1, 2, 5, 10].map(n => n * magnitude).find(n => n >= peak / 4);
  const intervals = Math.ceil(peak / tick), max = intervals * tick;
  const left = 65, top = 20, width = 1000, height = 250;
  const x = i => left + (i + 0.5) * width / count, y = n => top + height - n * height / max;
  for (let i = 0; i <= intervals; i++) {
    const val = tick * i, py = y(val);
    root.append(svg("line", {x1: left, y1: py, x2: left + width, y2: py, class: "grid"}), svg("text", {x: left - 8, y: py + 4, "text-anchor": "end"}, number(val)));
  }
  for (let i = 0; i < Math.min(6, count); i++) {
    const index = Math.floor(i * (count - 1) / Math.max(1, Math.min(6, count) - 1));
    root.append(svg("text", {x: x(index), y: 298, "text-anchor": "middle"}, date(start + index * step)));
  }
  for (const [key, values] of Object.entries(series)) {
    root.append(svg("path", {d: values.map((v, i) => (i ? "L" : "M") + x(i).toFixed(2) + "," + y(v).toFixed(2)).join(" "), class: "series " + key}));
    if (count === 1) root.append(svg("circle", {cx: x(0), cy: y(values[0]), r: 3, class: "marker"}));
  }
  for (const event of result.events.filter(e => e.kind === "blocked" || e.kind === "unblocked")) {
    const px = left + (event.at - start) / (count * step) * width;
    const marker = svg("text", {x: px, y: 16, class: "marker"}, event.kind === "blocked" ? "▼" : "▲");
    marker.append(svg("title", {}, date(event.at) + " · " + kinds[event.kind] + " · " + event.label)); root.append(marker);
  }
  const overlay = svg("rect", {x: left, y: top, width, height, fill: "transparent", tabindex: "0", "aria-label": "Pase el cursor o pulse un tramo para analizarlo"});
  function index(event) { const point = root.createSVGPoint(); point.x = event.clientX; point.y = event.clientY; const local = point.matrixTransform(root.getScreenCTM().inverse()); return Math.max(0, Math.min(count - 1, Math.floor((local.x - left) / width * count))); }
  overlay.addEventListener("mousemove", event => {
    const i = index(event); $("chart-detail").textContent = date(Math.max(result.from, start + i * step)) + " · " + ["remote", "local", "unknown", "rejected", "held"].map((k, n) => ["Remotos", "Locales", "Sin clasificar", "Rechazados", "Retenidos"][n] + ": " + number(series[k][i])).join(" · ");
  });
  overlay.addEventListener("click", event => {
    const i = index(event); $("period").value = "custom"; $("custom-range").hidden = false;
    $("from").value = localInput(Math.max(result.from, start + i * step)); $("to").value = localInput(Math.min(result.to, start + (i + 1) * step)); load(true);
  });
  root.append(overlay);
}
function localInput(seconds) { const d = new Date(seconds * 1000); return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16); }
$("timezone").textContent = "Horas: " + Intl.DateTimeFormat().resolvedOptions().timeZone + "; tramos alineados en UTC";
$("from").value = localInput(Date.now() / 1000 - 86400); $("to").value = localInput(Date.now() / 1000);
for (const id of ["bucket", "origin", "destination", "ignored"]) $(id).addEventListener("change", () => load(true));
$("period").addEventListener("change", () => { $("custom-range").hidden = $("period").value !== "custom"; if ($("period").value !== "custom") load(true); });
$("metric").addEventListener("change", () => { if (data) chart(data); });
$("users").addEventListener("change", () => { for (const option of $("users").options) { if (option.selected) chosen.set(option.value, option.textContent); else chosen.delete(option.value); } selections(); load(true); });
$("user-search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => users(), 250); });
$("clear-users").addEventListener("click", () => { chosen.clear(); syncOptions(); selections(); load(true); });
$("more-users").addEventListener("click", () => users(true));
$("refresh").addEventListener("click", () => { load(); users(); });
$("apply").addEventListener("click", () => load(true));
$("previous").addEventListener("click", () => { offset = Math.max(0, offset - 100); load(); });
$("next").addEventListener("click", () => { offset += 100; load(); });
setInterval(() => { if (!document.hidden && $("period").value !== "custom") { load(); if (!$ ("user-search").value) users(); } }, 60000);
document.addEventListener("visibilitychange", () => { if (!document.hidden && $("period").value !== "custom") load(); });
users(); load();
