"use strict";

const state = { games: [], sources: [], statuses: [], imports: [], imageChanged: false, me: null, users: [], limits: null };
// Cover image in the open edit dialog: the game and the images to choose from
const editorImage = { game: null, choices: null };
const $ = (selector) => document.querySelector(selector);

// Interface texts in the page's language, embedded by the server from app/locales/*.json
const I18N = JSON.parse(document.getElementById("i18n").textContent);
const LANG = I18N.language;
const pluralRules = new Intl.PluralRules(LANG);

// Text for `key` with {name} placeholders filled from `params`. With `params.count`, a key
// with plural forms (`key.one`, `key.other`) picks the form that fits the number.
function t(key, params = {}) {
  let text = I18N.messages[key];
  if (text === undefined && "count" in params) {
    const messages = I18N.messages;
    text = messages[`${key}.${pluralRules.select(params.count)}`] ?? messages[`${key}.other`];
  }
  if (text === undefined) return key;
  return text.replace(/\{(\w+)\}/g, (match, name) => (name in params ? String(params[name]) : match));
}

const dateFormat = new Intl.DateTimeFormat(LANG, { dateStyle: "medium" });
const dateTimeFormat = new Intl.DateTimeFormat(LANG, { dateStyle: "medium", timeStyle: "short" });
const collator = new Intl.Collator(LANG, { sensitivity: "base", numeric: true });
const listFormat = new Intl.ListFormat(LANG, { type: "conjunction" });

// Labels for the codes the API stores; unknown codes (like source names) are shown as they are
const codeLabel = (prefix, code) => {
  const key = `${prefix}.${code}`;
  return key in I18N.messages ? t(key) : code;
};
const statusLabel = (code) => codeLabel("status", code);
const sourceLabel = (code) => codeLabel("source", code);

// Small helpers for localStorage, which can be missing, e.g. in private mode
const store = {
  get(key) {
    try { return localStorage.getItem(key); } catch { return null; }
  },
  set(key, value) {
    try { localStorage.setItem(key, value); } catch {}
  },
};

function lastPlayedText(iso) {
  if (!iso) return t("played.never");
  const then = new Date(iso);
  const startOfToday = new Date();
  startOfToday.setHours(0, 0, 0, 0);
  const days = Math.ceil((startOfToday - then) / 86400000);
  if (days <= 0) return t("played.today");
  if (days === 1) return t("played.yesterday");
  if (days < 30) return t("played.days_ago", { count: days });
  return t("played.on", { date: dateFormat.format(then) });
}

function versionText(version) {
  return /^\d/.test(version) ? `v${version}` : version;
}

function sizeText(bytes) {
  if (!bytes) return "0 KB";
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toLocaleString(LANG, { maximumFractionDigits: 1 })} GB`;
  if (bytes >= 1024 ** 2) return `${Math.round(bytes / 1024 ** 2)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

// Cover images

function hasImage(game) {
  return game.image_mode === "upload" || (game.image_mode !== "none" && Boolean(game.image));
}

function imageUrl(game) {
  // Changes with every new choice, so the browser does not show an old cached image
  const version = [game.image_mode, game.image, game.folder, game.updated_at].join("|");
  return `/api/games/${game.id}/image?v=${encodeURIComponent(version)}`;
}

function fillThumb(box, game) {
  const placeholder = () => {
    box.replaceChildren();
    box.classList.add("empty");
    box.textContent = (game.title.trim()[0] || "?").toUpperCase();
  };
  if (!hasImage(game)) {
    placeholder();
    return;
  }
  box.classList.remove("empty");
  const img = document.createElement("img");
  img.loading = "lazy";
  img.decoding = "async";
  img.alt = "";
  img.addEventListener("error", placeholder);
  img.src = imageUrl(game);
  box.replaceChildren(img);
}

function errorText(body, status) {
  if (Array.isArray(body.detail)) return body.detail.map((e) => e.message || e.msg).join("\n");
  return body.detail || t("error.status", { status });
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (response.status === 401) {
    // Session expired: go to the login and come back afterwards
    location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}`;
    throw new Error(t("error.login_first"));
  }
  const body = response.status === 204 ? {} : await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(errorText(body, response.status));
  return body;
}

function showNotice(text) {
  const notice = $("#notice");
  notice.textContent = text;
  notice.hidden = !text;
}

async function load() {
  try {
    state.games = await api("/api/games");
  } catch (err) {
    showNotice(t("games.load_failed", { error: err.message }));
    return;
  }
  render();
}

// Search, filters, sort order

function visibleGames() {
  const query = $("#search").value.trim().toLowerCase();
  const source = $("#source-filter").value;
  const status = $("#status-filter").value;
  const todoOnly = $("#todo-only").checked;
  const updateOnly = $("#update-only").checked;
  const favFirst = $("#fav-first").checked;
  const sort = $("#sort").value;
  const byTitle = (a, b) => collator.compare(a.title, b.title);
  const statusMatches = (g) => {
    if (!status) return true;
    if (status === "-") return !g.status;
    if (status === "*") return g.favorite;
    return g.status === status;
  };
  return state.games
    .filter((g) => !query || [g.title, g.folder, g.notes || ""].join("\n").toLowerCase().includes(query))
    .filter((g) => !source || g.source === source)
    .filter(statusMatches)
    .filter((g) => !todoOnly || !g.version)
    .filter((g) => !updateOnly || g.update?.available)
    .sort((a, b) => {
      if (favFirst && a.favorite !== b.favorite) return a.favorite ? -1 : 1;
      if (sort === "title") return byTitle(a, b);
      // Newest first; games never played go last
      return (b[sort] || "").localeCompare(a[sort] || "") || byTitle(a, b);
    });
}

function render() {
  const list = $("#list");
  const games = visibleGames();
  const present = state.games.filter((g) => !g.missing);
  const withoutVersion = present.filter((g) => !g.version).length;
  const missing = state.games.length - present.length;
  const parts = [t("summary.games", { count: present.length })];
  if (withoutVersion) parts.push(t("summary.without_version", { count: withoutVersion }));
  const withUpdate = present.filter((g) => g.update?.available).length;
  if (withUpdate) parts.push(t("summary.with_update", { count: withUpdate }));
  if (missing) parts.push(t("summary.missing", { count: missing }));
  if (games.length !== state.games.length) parts.push(t("summary.shown", { count: games.length }));
  $("#summary").textContent = parts.join(" · ");

  list.replaceChildren();
  if (!games.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = state.games.length ? t("list.no_matches") : t("list.empty");
    list.append(empty);
    return;
  }

  const template = $("#card");
  for (const game of games) {
    const card = template.content.firstElementChild.cloneNode(true);
    card.querySelector(".title").textContent = game.title;
    fillThumb(card.querySelector(".thumb"), game);
    const version = card.querySelector(".version");
    version.textContent = game.version ? versionText(game.version) : t("card.version_missing");
    version.classList.toggle("todo", !game.version);

    const meta = [game.source && sourceLabel(game.source), lastPlayedText(game.last_played)];
    if (game.play_count) meta.push(t("card.started", { count: game.play_count }));
    if (game.missing) meta.unshift(t("card.folder_missing"));
    card.querySelector(".meta").textContent = meta.filter(Boolean).join(" · ");
    showUpdateStatus(card.querySelector(".update-status"), game);
    card.querySelector(".notes").textContent = game.notes || "";
    card.classList.toggle("missing", game.missing);

    const fav = card.querySelector(".fav");
    fav.textContent = game.favorite ? "★" : "☆";
    fav.setAttribute("aria-pressed", String(game.favorite));
    fav.setAttribute("aria-label", t(game.favorite ? "fav.remove" : "fav.add"));
    fav.title = fav.getAttribute("aria-label");
    fav.addEventListener("click", () => quickUpdate(game, { favorite: !game.favorite }));

    const status = card.querySelector(".status-select");
    status.append(...state.statuses.map((s) => new Option(statusLabel(s), s, false, s === game.status)));
    status.classList.toggle("unset", !game.status);
    status.addEventListener("change", () => quickUpdate(game, { status: status.value }));

    const play = card.querySelector(".play");
    if (game.missing) play.remove();
    else play.href = `/play/${game.id}`;

    const update = card.querySelector(".update");
    if (game.update_url) update.href = game.update_url;
    else update.remove();

    card.querySelector(".edit").addEventListener("click", () => openEditor(game));
    const forget = card.querySelector(".forget");
    if (game.missing && isAdmin()) forget.addEventListener("click", () => forgetGame(game));
    else forget.remove();
    list.append(card);
  }
}

// Status and favorite right on the card

async function quickUpdate(game, fields) {
  try {
    const updated = await api(`/api/games/${game.id}`, { method: "PATCH", body: JSON.stringify(fields) });
    const index = state.games.findIndex((g) => g.id === game.id);
    if (index >= 0) state.games[index] = updated;
    render();
  } catch (err) {
    showNotice(err.message);
    render(); // reset the selection to the saved state
  }
}

// Edit dialog

async function openEditor(game) {
  const form = $("#edit-form");
  form.dataset.id = game.id;
  $("#edit-folder").textContent = t("editor.folder", { folder: game.folder });
  // Keep a stored source the server no longer offers (ADULT_SOURCES=false) selectable
  const sources = form.elements.source;
  if (game.source && ![...sources.options].some((o) => o.value === game.source)) {
    sources.append(new Option(sourceLabel(game.source), game.source));
  }
  for (const name of ["title", "version", "source", "update_url", "notes", "status"]) {
    form.elements[name].value = game[name] || "";
  }
  form.elements.favorite.checked = game.favorite;

  const hint = $("#version-hint");
  hint.replaceChildren();
  if (game.version_hint && game.version_hint !== game.version) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "secondary";
    button.textContent = game.version_hint;
    button.title = t("editor.use_suggestion");
    button.addEventListener("click", () => {
      form.elements.version.value = game.version_hint;
      form.elements.version.focus();
    });
    hint.append(`${t("editor.suggestion")} `, button);
  }

  const entry = form.elements.entry;
  entry.replaceChildren(new Option(game.entry, game.entry, true, true));
  entry.disabled = true;
  $("#edit-error").textContent = "";
  $("#edit-delete").textContent = t(isAdmin() ? "delete.button" : "remove.button");
  showUpdateResult(game);
  $("#history").hidden = true;
  showOwners(game);
  showImageChoices(game, null);
  $("#editor").showModal();
  loadHistory(game);
  if (!game.missing) loadImageChoices(game);

  if (game.missing) return;
  try {
    const files = await api(`/api/games/${game.id}/html-files`);
    if (form.dataset.id !== String(game.id)) return; // dialog now shows another game
    entry.replaceChildren(...files.map((f) => new Option(f, f, f === game.entry, f === game.entry)));
    if (!files.includes(game.entry)) {
      entry.prepend(new Option(t("editor.entry_missing", { entry: game.entry }), "", true, true));
    }
    entry.disabled = false;
  } catch (err) {
    $("#edit-error").textContent = err.message;
  }
}

async function saveEditor(event) {
  event.preventDefault();
  const form = event.target;
  const error = $("#edit-error");
  if (!form.elements.version.value.trim()) {
    error.textContent = t("error.version_required");
    form.elements.version.focus();
    return;
  }
  const data = {};
  for (const name of ["title", "version", "source", "update_url", "notes", "status"]) {
    data[name] = form.elements[name].value;
  }
  data.favorite = form.elements.favorite.checked;
  if (!form.elements.entry.disabled && form.elements.entry.value) {
    data.entry = form.elements.entry.value;
  }
  const submit = form.querySelector("button[type=submit]");
  submit.disabled = true;
  try {
    const before = state.games.find((g) => String(g.id) === form.dataset.id);
    const saved = await api(`/api/games/${form.dataset.id}`, { method: "PATCH", body: JSON.stringify(data) });
    $("#editor").close();
    await load();
    // New update link: check it online right away, the result shows on the card
    if (saved.update_url && saved.update_url !== before?.update_url) {
      api(`/api/games/${saved.id}/check-update`, { method: "POST" }).then(load, () => {});
    }
  } catch (err) {
    error.textContent = err.message;
  } finally {
    submit.disabled = false;
  }
}

// Online update check

function checkedText(update) {
  return t("update.checked", { date: dateTimeFormat.format(new Date(update.checked_at)) });
}

function onlineText(update) {
  if (update.version) return versionText(update.version);
  return t("update.page_changed", { date: dateFormat.format(new Date(update.date)) });
}

// Line on the card: update available, error, or a hint to check by hand
function showUpdateStatus(line, game) {
  const update = game.update;
  line.replaceChildren();
  line.className = "update-status";
  if (!update) return;
  line.hidden = false;
  const updateLink = (text) => {
    const link = document.createElement("a");
    link.href = game.update_url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = text;
    return link;
  };
  if (update.available) {
    line.classList.add("available");
    line.title = checkedText(update);
    line.append(updateLink(t("update.available", { version: onlineText(update) })));
  } else if (update.status === "error") {
    line.classList.add("failed");
    line.textContent = t("update.failed", { error: update.error });
    line.title = checkedText(update);
  } else if (update.status === "manual") {
    // A link, so checking by hand is one click away
    line.classList.add("manual");
    line.append(updateLink(t("update.manual")));
    line.title = update.error;
  } else if (update.version && !game.version) {
    line.textContent = t("update.online", { version: onlineText(update) });
    line.title = checkedText(update);
  } else {
    line.hidden = true;
  }
}

// Result in the edit dialog, more detailed than on the card
function showUpdateResult(game) {
  const result = $("#update-result");
  const update = game.update;
  $("#check-now").disabled = !game.update_url;
  if (!game.update_url) {
    result.textContent = t("update.no_link");
  } else if (!update) {
    result.textContent = t("update.not_checked");
  } else if (update.status === "ok") {
    result.textContent = t("update.result", {
      version: onlineText(update),
      state: t(update.available ? "update.newer" : "update.not_newer"),
      checked: checkedText(update),
    });
  } else {
    result.textContent = `${update.error} (${checkedText(update)})`;
  }
}

async function checkNow() {
  const form = $("#edit-form");
  const button = $("#check-now");
  const game = state.games.find((g) => String(g.id) === form.dataset.id);
  if (!game) return;
  if ((form.elements.update_url.value.trim() || null) !== game.update_url) {
    $("#update-result").textContent = t("update.save_link_first");
    return;
  }
  button.disabled = true;
  $("#update-result").textContent = t("update.checking");
  try {
    const checked = await api(`/api/games/${game.id}/check-update`, { method: "POST" });
    Object.assign(game, checked);
    showUpdateResult(game);
    render();
  } catch (err) {
    $("#update-result").textContent = err.message;
  } finally {
    button.disabled = !game.update_url;
  }
}

async function checkAllUpdates() {
  const button = $("#check-updates");
  button.disabled = true;
  try {
    const result = await api("/api/updates/check", { method: "POST" });
    showNotice(
      result.started ? t("update.started", { count: result.count }) : t("update.running"),
    );
    // Wait for progress, results appear one by one
    while ((await api("/api/updates/status")).running) {
      await new Promise((resolve) => setTimeout(resolve, 5000));
      await load();
    }
    await load();
    const available = state.games.filter((g) => g.update?.available).length;
    const failed = state.games.filter((g) => g.update?.status === "error").length;
    const parts = [t("update.done", { count: available })];
    if (failed) parts.push(t("update.done_failed", { count: failed }));
    showNotice(`${parts.join(", ")}.`);
  } catch (err) {
    showNotice(t("update.failed", { error: err.message }));
  } finally {
    button.disabled = false;
  }
}

// Cover image in the edit dialog; changes apply immediately

function showImageChoices(game, choices) {
  editorImage.game = game;
  editorImage.choices = choices;
  fillThumb($("#edit-form .thumb.preview"), game);
  const select = $("#image-choice");
  const auto = choices ? choices.auto || t("image.none_found") : "…";
  const options = [new Option(t("image.auto", { image: auto }), "auto"), new Option(t("image.none"), "none")];
  if (game.image_mode === "upload") options.push(new Option(t("image.uploaded"), "upload"));
  for (const path of choices?.images || []) options.push(new Option(path, `file:${path}`));
  if (game.image_mode === "file" && !(choices?.images || []).includes(game.image)) {
    options.push(new Option(game.image, `file:${game.image}`));
  }
  select.replaceChildren(...options);
  select.value = game.image_mode === "file" ? `file:${game.image}` : game.image_mode;
  select.disabled = game.missing || !choices;
  $("#image-upload").disabled = game.missing;
}

async function loadImageChoices(game) {
  try {
    const choices = await api(`/api/games/${game.id}/images`);
    if ($("#edit-form").dataset.id !== String(game.id)) return;
    showImageChoices(game, choices);
  } catch (err) {
    $("#edit-error").textContent = err.message;
  }
}

async function applyImage(request) {
  const { game, choices } = editorImage;
  const error = $("#edit-error");
  error.textContent = "";
  $("#image-choice").disabled = true;
  $("#image-upload").disabled = true;
  try {
    const updated = await request(game);
    state.imageChanged = true;
    showImageChoices(updated, choices);
  } catch (err) {
    error.textContent = err.message;
    showImageChoices(game, choices);
  }
}

function chooseImage() {
  const value = $("#image-choice").value;
  if (value === "upload") return;
  const body = value.startsWith("file:") ? { mode: "file", path: value.slice(5) } : { mode: value };
  applyImage((game) => api(`/api/games/${game.id}/image`, { method: "PUT", body: JSON.stringify(body) }));
}

function uploadImage(event) {
  const [file] = event.target.files;
  event.target.value = "";
  if (!file) return;
  applyImage((game) =>
    api(`/api/games/${game.id}/image`, {
      method: "POST",
      body: file,
      headers: { "Content-Type": file.type || "application/octet-stream" },
    }),
  );
}

// Who sees a game (admins only): checkboxes for the accounts that are not admins

function showOwners(game) {
  const box = $("#owners");
  const people = state.users.filter((u) => u.role !== "admin");
  box.hidden = !(state.me?.accounts && isAdmin() && people.length && game.owners);
  if (box.hidden) return;
  const list = $("#owner-list");
  list.replaceChildren(
    ...people.map((user) => {
      const label = el("label", "check");
      const input = document.createElement("input");
      input.type = "checkbox";
      input.value = user.id;
      input.checked = game.owners.includes(user.id);
      input.addEventListener("change", () => saveOwners(game));
      label.append(input, ` ${user.username}`);
      return label;
    }),
  );
  $("#owners-hint").textContent = t(game.owners.length ? "editor.owners_hint" : "editor.no_owner");
}

async function saveOwners(game) {
  const admins = new Set(state.users.filter((u) => u.role === "admin").map((u) => u.id));
  const checked = [...$("#owner-list").querySelectorAll("input:checked")].map((i) => Number(i.value));
  const ids = [...game.owners.filter((id) => admins.has(id)), ...checked];
  const inputs = $("#owner-list").querySelectorAll("input");
  inputs.forEach((i) => (i.disabled = true));
  $("#edit-error").textContent = "";
  try {
    const updated = await api(`/api/games/${game.id}/owners`, {
      method: "PUT",
      body: JSON.stringify({ user_ids: ids }),
    });
    game.owners = updated.owners;
    const index = state.games.findIndex((g) => g.id === game.id);
    if (index >= 0) state.games[index].owners = updated.owners;
  } catch (err) {
    $("#edit-error").textContent = err.message;
  }
  showOwners(game);
}

// Version history and backups in the edit dialog

async function loadHistory(game) {
  let data;
  try {
    data = await api(`/api/games/${game.id}/history`);
  } catch {
    return;
  }
  if ($("#edit-form").dataset.id !== String(game.id)) return;
  const history = $("#history-list");
  history.replaceChildren(
    ...data.history.map((h) => {
      const item = document.createElement("li");
      item.textContent = t("history.line", {
        version: h.version ? versionText(h.version) : t("history.no_version"),
        action: codeLabel("history.action", h.action),
        date: dateTimeFormat.format(new Date(h.at)),
      });
      return item;
    }),
  );
  const backups = $("#backup-list");
  backups.replaceChildren(
    ...data.backups.map((b) => {
      const item = document.createElement("li");
      const text = document.createElement("span");
      const line = t("history.backup_line", {
        version: b.version ? versionText(b.version) : t("history.no_version"),
        date: dateTimeFormat.format(new Date(b.created_at)),
      });
      text.textContent = b.size === null ? line : `${line} · ${sizeText(b.size)}`;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "secondary small";
      button.textContent = t("history.restore");
      button.addEventListener("click", () => restoreBackup(game, b, button));
      const remove = el("button", "secondary small danger", t("history.delete_backup"));
      remove.type = "button";
      remove.addEventListener("click", async () => {
        if (await deleteBackup({ ...b, title: game.title }, remove)) loadHistory(game);
      });
      const buttons = el("span", "buttons");
      buttons.append(button, remove);
      item.append(text, buttons);
      return item;
    }),
  );
  $("#backups-heading").hidden = !data.backups.length;
  $("#history").hidden = !data.history.length && !data.backups.length;
}

async function restoreBackup(game, backup, button) {
  const version = backup.version ? versionText(backup.version) : t("restore.saved_version");
  if (!confirm(t("restore.confirm", { title: game.title, version }))) return;
  button.disabled = true;
  try {
    const restored = await api(`/api/games/${game.id}/restore`, {
      method: "POST",
      body: JSON.stringify({ backup_id: backup.id }),
    });
    $("#editor").close();
    await load();
    showNotice(t("restore.done", { title: restored.title, version }));
  } catch (err) {
    $("#edit-error").textContent = err.message;
    button.disabled = false;
  }
}

// Deleting: admins delete a game for everyone, other accounts remove it from their library

async function deleteGame(game) {
  const admin = isAdmin();
  if (!confirm(t(admin ? "delete.confirm" : "remove.confirm", { title: game.title }))) return;
  const button = $("#edit-delete");
  button.disabled = true;
  try {
    let notice;
    if (admin) {
      await api(`/api/games/${game.id}`, { method: "DELETE" });
      notice = t("delete.done", { title: game.title });
    } else {
      const result = await api(`/api/games/${game.id}/remove`, { method: "POST" });
      notice = t(result.deleted ? "remove.done_deleted" : "remove.done", { title: game.title });
    }
    $("#editor").close();
    await load();
    showNotice(notice);
  } catch (err) {
    $("#edit-error").textContent = err.message;
  } finally {
    button.disabled = false;
  }
}

async function deleteBackup(backup, button) {
  const version = backup.version ? versionText(backup.version) : t("history.no_version");
  if (!confirm(t("backup.delete_confirm", { title: backup.title, version }))) return false;
  button.disabled = true;
  try {
    await api(`/api/backups/${backup.id}`, { method: "DELETE" });
    return true;
  } catch (err) {
    showNotice(err.message);
    button.disabled = false;
    return false;
  }
}

async function forgetGame(game) {
  if (!confirm(t("forget.confirm", { title: game.title }))) return;
  try {
    await api(`/api/games/${game.id}`, { method: "DELETE" });
    await load();
  } catch (err) {
    showNotice(err.message);
  }
}

async function rescan() {
  const button = $("#rescan");
  button.disabled = true;
  showNotice("");
  try {
    const result = await api("/api/scan", { method: "POST" });
    await load();
    showNotice(t("rescan.done", result));
  } catch (err) {
    showNotice(t("rescan.failed", { error: err.message }));
  } finally {
    button.disabled = false;
  }
}

// Statistics

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function statsSection(heading, rows, emptyText, note) {
  const section = el("section");
  section.append(el("h3", "", heading));
  if (!rows.length) {
    section.append(el("p", "muted", emptyText));
    return section;
  }
  const list = el("ol", "stats-list");
  for (const [title, value] of rows) {
    const item = el("li");
    item.append(el("span", "name", title), el("span", "value", value));
    list.append(item);
  }
  section.append(list);
  if (note) section.append(el("p", "muted small-text", note));
  return section;
}

function gameLabel(game) {
  return game.version ? `${game.title} ${versionText(game.version)}` : game.title;
}

function renderStats(data) {
  const size = data.size;
  const tiles = el("div", "stats-tiles");
  const tile = (value, label) => {
    const box = el("div", "tile");
    box.append(el("strong", "", value), el("span", "muted", label));
    return box;
  };
  tiles.append(
    tile(String(data.games), t("stats.games")),
    tile(String(data.starts), t("stats.starts")),
    tile(sizeText(size.games), t("stats.size_games")),
    tile(sizeText(size.backups), t("stats.size_backups", { count: size.backup_count })),
    tile(sizeText(size.total), t("stats.size_total")),
  );

  const statusParts = Object.entries(data.by_status)
    .filter(([, count]) => count)
    .map(([status, count]) => `${statusLabel(status)}: ${count}`);
  if (data.favorites) statusParts.unshift(t("stats.favorites", { count: data.favorites }));
  const statusLine = el("p", "muted small-text", statusParts.join(" · "));

  const more = (total, shown) => (total > shown ? t("stats.more", { count: total - shown }) : "");
  const most = statsSection(
    t("stats.most_played"),
    data.most_played.map((g) => [gameLabel(g), t("card.started", { count: g.play_count })]),
    t("stats.no_starts"),
  );
  const longAgo = statsSection(
    t("stats.long_ago", { days: data.long_ago_days }),
    data.long_ago.map((g) => [gameLabel(g), dateFormat.format(new Date(g.last_played))]),
    t("stats.none"),
    more(data.long_ago_count, data.long_ago.length),
  );
  const never = statsSection(
    t("stats.never", { count: data.never_played_count }),
    data.never_played.map((g) => [gameLabel(g), ""]),
    t("stats.none"),
    more(data.never_played_count, data.never_played.length),
  );
  const bySize = statsSection(
    t("stats.by_size"),
    data.by_size.map((g) => {
      const value = g.size === null ? t("stats.unknown") : sizeText(g.size);
      if (!g.backups) return [gameLabel(g), value];
      return [gameLabel(g), t("stats.plus_backups", { size: value, backups: sizeText(g.backups_size) })];
    }),
    t("stats.no_games"),
    size.unknown ? t("stats.size_unknown", { count: size.unknown }) : "",
  );
  bySize.classList.add("scroll");

  $("#stats-body").replaceChildren(tiles, statusLine, most, longAgo, never, bySize);
}

async function openStats() {
  $("#stats-body").replaceChildren(el("p", "muted", t("loading")));
  $("#stats").showModal();
  try {
    renderStats(await api("/api/stats"));
  } catch (err) {
    $("#stats-body").replaceChildren(el("p", "error", err.message));
  }
}

// Backups of all games the user sees; for admins also folders without a game

async function openBackups() {
  $("#backups-body").replaceChildren(el("p", "muted", t("loading")));
  $("#backups-error").textContent = "";
  $("#backups").showModal();
  await renderBackups();
}

function itemRow(name, value, button) {
  const item = el("li");
  item.append(el("span", "name", name), el("span", "value", value));
  if (button) item.append(button);
  return item;
}

function deleteButton(action) {
  const button = el("button", "secondary small danger", t("history.delete_backup"));
  button.type = "button";
  button.addEventListener("click", () => action(button));
  return button;
}

async function renderBackups() {
  let data;
  try {
    data = await api("/api/backups");
  } catch (err) {
    $("#backups-body").replaceChildren(el("p", "error", err.message));
    return;
  }
  $("#backups-intro").textContent = t("backups.intro", { keep: data.keep });
  const total = data.backups.reduce((sum, b) => sum + (b.size || 0), 0);
  const parts = [];
  if (!data.backups.length) {
    parts.push(el("p", "muted", t("backups.none")));
  } else {
    parts.push(el("p", "", t("backups.summary", { count: data.backups.length, size: sizeText(total) })));
    const list = el("ul", "item-list scroll-list");
    for (const backup of data.backups) {
      const version = backup.version ? versionText(backup.version) : t("history.no_version");
      const date = t("backups.line", { date: dateFormat.format(new Date(backup.created_at)) });
      const size = backup.size === null ? t("stats.unknown") : sizeText(backup.size);
      const remove = deleteButton(async (button) => {
        if (await deleteBackup(backup, button)) {
          await renderBackups();
          $("#backups-error").textContent = "";
        }
      });
      list.append(itemRow(`${backup.title} ${version}`, `${date} · ${size}`, remove));
    }
    parts.push(list);
  }
  if (data.orphans?.length) {
    parts.push(el("h3", "", t("backups.orphans")), el("p", "muted small-text", t("backups.orphans_hint")));
    const list = el("ul", "item-list");
    for (const orphan of data.orphans) {
      const remove = deleteButton(async (button) => {
        if (!confirm(t("backups.orphan_confirm", { path: orphan.path }))) return;
        button.disabled = true;
        try {
          await api(`/api/backups?path=${encodeURIComponent(orphan.path)}`, { method: "DELETE" });
          await renderBackups();
        } catch (err) {
          $("#backups-error").textContent = err.message;
          button.disabled = false;
        }
      });
      list.append(itemRow(orphan.path, sizeText(orphan.size), remove));
    }
    parts.push(list);
  }
  $("#backups-body").replaceChildren(...parts);
}

// Library overview (admins): which account has which games

function overviewSection(title, summary, games, userId, hint) {
  const box = el("details");
  const head = el("summary");
  head.append(el("strong", "", title), el("span", "muted", summary));
  box.append(head);
  if (hint) box.append(el("p", "muted small-text", hint));
  if (!games.length) {
    box.append(el("p", "muted small-text", t("overview.no_games")));
    return box;
  }
  const list = el("ul", "item-list");
  for (const game of games) {
    const meta = [];
    if (userId !== null) {
      meta.push(lastPlayedText(game.last_played));
      if (game.play_count) meta.push(t("card.started", { count: game.play_count }));
      if (game.status) meta.push(statusLabel(game.status));
    }
    if (game.missing) meta.unshift(t("card.folder_missing"));
    if (game.size !== null) meta.push(sizeText(game.size));
    list.append(itemRow(`${game.favorite ? "★ " : ""}${gameLabel(game)}`, meta.join(" · ")));
  }
  box.append(list);
  return box;
}

async function openOverview() {
  const body = $("#overview-body");
  body.replaceChildren(el("p", "muted", t("loading")));
  $("#overview").showModal();
  let data;
  try {
    data = await api("/api/overview");
  } catch (err) {
    body.replaceChildren(el("p", "error", err.message));
    return;
  }
  const sections = data.users.map((user) =>
    overviewSection(
      `${user.username} (${t(`role.${user.role}`)})`,
      t("overview.user", { count: user.games.length, size: sizeText(user.size) }),
      user.games,
      user.id,
    ),
  );
  if (data.unowned.length) {
    const size = data.unowned.reduce((sum, g) => sum + (g.size || 0), 0);
    sections.push(
      overviewSection(
        t("overview.unowned"),
        t("overview.user", { count: data.unowned.length, size: sizeText(size) }),
        data.unowned,
        null,
        t("overview.unowned_hint"),
      ),
    );
  }
  const box = el("div", "overview");
  box.append(...sections);
  body.replaceChildren(box);
}

// Pending imports

async function loadImports() {
  try {
    state.imports = await api("/api/imports");
  } catch (err) {
    showNotice(t("imports.load_failed", { error: err.message }));
    return;
  }
  renderImports();
}

function renderImports() {
  const count = state.imports.length;
  $("#imports").hidden = !count;
  $("#imports-heading").textContent = t("imports.heading_count", { count });
  // Keep existing cards so that input is not lost when reloading
  const list = $("#import-list");
  const existing = new Map([...list.children].map((card) => [card.dataset.id, card]));
  list.replaceChildren(...state.imports.map((imp) => existing.get(String(imp.id)) || importCard(imp)));
}

function importCard(imp) {
  const form = $("#import-card").content.firstElementChild.cloneNode(true);
  form.dataset.id = imp.id;
  form.querySelector(".title").textContent = imp.title;
  form.querySelector(".version").textContent = imp.version_hint ? versionText(imp.version_hint) : "";
  form.querySelector(".meta").textContent = [
    t("import.archive", { name: imp.filename }),
    t("editor.folder", { folder: imp.folder }),
    sizeText(imp.size),
  ].join(" · ");

  const match = imp.suggestion;
  let suggestion = t("import.no_match");
  if (match) {
    const reason = listFormat.format(match.matches.map((code) => t(`import.match.${code}`)));
    suggestion = t("import.likely_update", {
      reason: reason[0].toUpperCase() + reason.slice(1),
      title: match.title,
    });
    if (match.version) suggestion += ` ${t("import.installed", { version: versionText(match.version) })}`;
  }
  form.querySelector(".suggestion").textContent = suggestion;

  const target = form.elements.target;
  target.append(new Option(t("import.as_new"), "new"));
  const games = [...state.games].sort((a, b) => collator.compare(a.title, b.title));
  for (const game of games) {
    const version = game.version ? ` (${versionText(game.version)})` : "";
    target.append(new Option(t("import.update_of", { title: game.title }) + version, game.id));
  }
  target.value = match ? String(match.game_id) : "new";

  const titleLabel = form.querySelector(".new-only");
  const toggleTitle = () => {
    titleLabel.hidden = target.value !== "new";
  };
  target.addEventListener("change", toggleTitle);
  toggleTitle();

  form.elements.title.value = imp.title;
  form.elements.version.value = imp.version_hint || "";
  const entry = form.elements.entry;
  entry.append(...imp.html_files.map((f) => new Option(f, f, f === imp.entry, f === imp.entry)));
  entry.dataset.detected = imp.entry;

  form.addEventListener("submit", (event) => confirmImport(event, imp));
  form.querySelector(".discard").addEventListener("click", () => discardImport(imp, form));
  return form;
}

async function confirmImport(event, imp) {
  event.preventDefault();
  const form = event.target;
  const error = form.querySelector(".error");
  const version = form.elements.version.value.trim();
  if (!version) {
    error.textContent = t("error.version_required");
    form.elements.version.focus();
    return;
  }
  const isNew = form.elements.target.value === "new";
  const data = { version };
  if (isNew) data.title = form.elements.title.value;
  else data.game_id = Number(form.elements.target.value);
  // Only send a changed start file; otherwise an update keeps the previous choice
  if (form.elements.entry.value !== form.elements.entry.dataset.detected) {
    data.entry = form.elements.entry.value;
  }

  const buttons = form.querySelectorAll("button");
  buttons.forEach((b) => (b.disabled = true));
  error.textContent = "";
  try {
    const game = await api(`/api/imports/${imp.id}/confirm`, {
      method: "POST",
      body: JSON.stringify(data),
    });
    form.remove();
    await Promise.all([load(), loadImports()]);
    showNotice(
      isNew
        ? t("import.added", { title: game.title })
        : t("import.updated", { title: game.title, version: versionText(game.version) }),
    );
  } catch (err) {
    error.textContent = err.message;
  } finally {
    buttons.forEach((b) => (b.disabled = false));
  }
}

async function discardImport(imp, form) {
  if (!confirm(t("import.discard_confirm", { title: imp.title }))) return;
  try {
    await api(`/api/imports/${imp.id}`, { method: "DELETE" });
    form.remove();
    await loadImports();
  } catch (err) {
    form.querySelector(".error").textContent = err.message;
  }
}

// Upload by file picker or drag and drop, one file after the other

const uploadQueue = [];
let uploading = false;
// An upload that sends nothing for this long is given up; a reverse proxy that rejects a
// large file often just stops reading it, and the progress bar would stand still forever
const UPLOAD_STALL_MS = 60000;

function uploadItem(file) {
  const item = document.createElement("li");
  const name = document.createElement("span");
  name.className = "name";
  name.textContent = `${file.name} (${sizeText(file.size)})`;
  const progress = document.createElement("progress");
  progress.max = 1;
  progress.value = 0;
  const status = document.createElement("span");
  status.className = "status";
  status.textContent = t("upload.waiting");
  const close = document.createElement("button");
  close.type = "button";
  close.className = "secondary small";
  close.textContent = "✕";
  close.setAttribute("aria-label", t("upload.close"));
  close.addEventListener("click", () => removeUploadItem(item));
  item.append(name, progress, status, close);
  $("#uploads").append(item);
  $("#uploads").hidden = false;
  return { item, progress, status };
}

function removeUploadItem(item) {
  item.remove();
  $("#uploads").hidden = !$("#uploads").children.length;
}

function queueUploads(files) {
  for (const file of files) {
    const view = uploadItem(file);
    // Without the limits yet, the server decides
    const formats = state.limits?.formats;
    if (formats && !formats.some((suffix) => file.name.toLowerCase().endsWith(suffix))) {
      failUpload(view, t("upload.not_archive", { formats: formats.join(", ") }));
      continue;
    }
    const limit = state.limits?.max_upload_bytes;
    if (limit && file.size > limit) {
      failUpload(view, t("upload.too_large", { max: sizeText(limit) }));
      continue;
    }
    uploadQueue.push({ file, view });
  }
  if (!uploading) processUploads();
}

function failUpload(view, message) {
  view.progress.remove();
  view.status.textContent = message;
  view.item.classList.add("failed");
}

async function processUploads() {
  uploading = true;
  while (uploadQueue.length) {
    const { file, view } = uploadQueue.shift();
    try {
      await uploadFile(file, view);
      view.progress.remove();
      view.status.textContent = t("upload.done");
      view.item.classList.add("done");
      setTimeout(() => removeUploadItem(view.item), 6000);
      await loadImports();
    } catch (err) {
      failUpload(view, err.message);
    }
  }
  uploading = false;
}

function uploadFile(file, view) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/imports?filename=${encodeURIComponent(file.name)}`);
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    let sent = false;
    let stalled = false;
    let watchdog;
    const watch = () => {
      clearTimeout(watchdog);
      watchdog = setTimeout(() => {
        stalled = true;
        xhr.abort();
      }, UPLOAD_STALL_MS);
    };
    xhr.upload.addEventListener("progress", (event) => {
      watch();
      if (!event.lengthComputable) return;
      view.progress.value = event.loaded / event.total;
      view.status.textContent = t("upload.progress", { percent: Math.floor((event.loaded / event.total) * 100) });
    });
    xhr.upload.addEventListener("load", () => {
      sent = true;
      clearTimeout(watchdog); // extracting a large archive may take minutes
      view.progress.removeAttribute("value"); // keeps moving without a fixed value
      view.status.textContent = t("upload.extracting");
    });
    xhr.addEventListener("load", () => {
      clearTimeout(watchdog);
      let body = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {}
      if (xhr.status >= 200 && xhr.status < 300) resolve(body || {});
      // Without a JSON answer the 413 comes from a reverse proxy, not from Playshelf
      else if (xhr.status === 413 && !body?.detail) reject(new Error(t("upload.proxy_too_large")));
      else reject(new Error(errorText(body || {}, xhr.status)));
    });
    xhr.addEventListener("error", () => {
      clearTimeout(watchdog);
      reject(new Error(t(sent ? "upload.connection_lost" : "upload.connection_lost_sending")));
    });
    xhr.addEventListener("abort", () => {
      if (stalled) reject(new Error(t("upload.stalled")));
    });
    view.status.textContent = t("upload.uploading");
    watch();
    xhr.send(file);
  });
}

// Import from a download link: the server downloads, the list shows its progress

const DOWNLOAD_ACTIVE = ["queued", "downloading", "extracting"];
const downloadViews = new Map();
let downloadPolling = false;

function openLinkDialog() {
  const form = $("#link-form");
  form.reset();
  $("#link-error").textContent = "";
  $("#link-dialog").showModal();
}

async function startDownload(event) {
  event.preventDefault();
  const form = event.target;
  const url = form.elements.url.value.trim();
  const error = $("#link-error");
  if (!url) {
    error.textContent = t("link.empty");
    return;
  }
  const submit = form.querySelector('button[type="submit"]');
  submit.disabled = true;
  try {
    const job = await api("/api/downloads", { method: "POST", body: JSON.stringify({ url }) });
    $("#link-dialog").close();
    showDownload(job);
    pollDownloads();
  } catch (err) {
    error.textContent = err.message;
  } finally {
    submit.disabled = false;
  }
}

function downloadView(job) {
  let view = downloadViews.get(job.id);
  if (view) return view;
  const item = document.createElement("li");
  const name = document.createElement("span");
  name.className = "name";
  const progress = document.createElement("progress");
  progress.max = 1;
  const status = document.createElement("span");
  status.className = "status";
  const close = document.createElement("button");
  close.type = "button";
  close.className = "secondary small";
  close.textContent = "✕";
  item.append(name, progress, status, close);
  $("#uploads").append(item);
  $("#uploads").hidden = false;
  view = { item, name, progress, status, close, job: { ...job, state: null }, last: null, speed: 0 };
  close.addEventListener("click", () => removeDownload(view));
  downloadViews.set(job.id, view);
  return view;
}

function showDownload(job) {
  const view = downloadView(job);
  const previous = view.job.state;
  view.job = job;
  view.name.textContent = job.filename;
  const active = DOWNLOAD_ACTIVE.includes(job.state);
  view.close.setAttribute("aria-label", t(active ? "download.cancel" : "upload.close"));
  view.close.title = active ? t("download.cancel") : "";
  view.close.disabled = job.state === "extracting";
  if (job.state === "queued") {
    view.progress.removeAttribute("value");
    view.status.textContent = t("download.queued");
  } else if (job.state === "downloading") {
    const now = performance.now();
    if (view.last && now > view.last.time) {
      const speed = ((job.received - view.last.received) / (now - view.last.time)) * 1000;
      view.speed = view.speed ? view.speed * 0.7 + speed * 0.3 : speed; // smoothed
    }
    view.last = { received: job.received, time: now };
    let text;
    if (!job.received) {
      text = t("download.connecting");
    } else if (job.total) {
      text = t("download.progress", {
        received: sizeText(job.received),
        total: sizeText(job.total),
        percent: Math.floor((job.received / job.total) * 100),
      });
    } else {
      text = t("download.progress_unknown", { received: sizeText(job.received) });
    }
    if (view.speed > 0) text += ` · ${t("download.speed", { speed: sizeText(view.speed) })}`;
    if (job.total) view.progress.value = job.received / job.total;
    else view.progress.removeAttribute("value");
    view.status.textContent = text;
  } else if (job.state === "extracting") {
    view.progress.removeAttribute("value");
    view.status.textContent = t("upload.extracting");
  } else if (job.state === "done") {
    view.progress.remove();
    view.status.textContent = t("upload.done");
    view.item.classList.add("done");
    if (previous !== "done") {
      loadImports();
      setTimeout(() => removeDownload(view), 6000);
    }
  } else {
    view.progress.remove();
    view.status.textContent = job.error || t("error.status", { status: job.state });
    view.item.classList.add("failed");
  }
}

function forgetDownload(view) {
  downloadViews.delete(view.job.id);
  removeUploadItem(view.item);
}

async function removeDownload(view) {
  if (!downloadViews.has(view.job.id)) return;
  const active = DOWNLOAD_ACTIVE.includes(view.job.state);
  if (active && !confirm(t("download.cancel_confirm", { name: view.job.filename }))) return;
  try {
    await api(`/api/downloads/${view.job.id}`, { method: "DELETE" });
  } catch (err) {
    // Already gone on the server (for example after a restart): just remove it here
    if (active) {
      showNotice(err.message);
      return;
    }
  }
  forgetDownload(view);
}

// Polls the account's downloads while one is running; also restores the list after a reload
async function pollDownloads() {
  if (downloadPolling) return;
  downloadPolling = true;
  try {
    for (;;) {
      let jobs;
      try {
        jobs = await api("/api/downloads");
      } catch {
        jobs = null;
      }
      if (jobs) {
        const ids = new Set(jobs.map((job) => job.id));
        for (const job of jobs) showDownload(job);
        for (const view of [...downloadViews.values()]) {
          if (!ids.has(view.job.id)) forgetDownload(view);
        }
        if (!jobs.some((job) => DOWNLOAD_ACTIVE.includes(job.state))) break;
      }
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  } finally {
    downloadPolling = false;
  }
}

function setupDropZone() {
  const zone = $("#drop-zone");
  const hasFiles = (event) => [...(event.dataTransfer?.types || [])].includes("Files");
  let depth = 0;
  document.addEventListener("dragenter", (event) => {
    if (!hasFiles(event)) return;
    event.preventDefault();
    depth += 1;
    zone.hidden = false;
  });
  document.addEventListener("dragover", (event) => {
    if (hasFiles(event)) event.preventDefault();
  });
  document.addEventListener("dragleave", (event) => {
    if (!hasFiles(event)) return;
    depth = Math.max(0, depth - 1);
    if (!depth) zone.hidden = true;
  });
  document.addEventListener("drop", (event) => {
    if (!hasFiles(event)) return;
    event.preventDefault();
    depth = 0;
    zone.hidden = true;
    queueUploads(event.dataTransfer.files);
  });
}

// Accounts

const isAdmin = () => Boolean(state.me?.user.is_admin);

async function loadMe() {
  try {
    state.me = await api("/api/me");
  } catch {
    return;
  }
  const { accounts, user } = state.me;
  $("#logout").hidden = !accounts;
  $("#account").hidden = !accounts;
  $("#account-name").textContent = user.username;
  $("#users-button").hidden = !(accounts && user.is_admin);
  $("#overview-button").hidden = !(accounts && user.is_admin);
  $("#rescan").hidden = !user.is_admin;
  if (accounts && user.is_admin) await loadUsers();
}

async function loadUsers() {
  try {
    state.users = await api("/api/users");
  } catch (err) {
    $("#users-error").textContent = err.message;
  }
}

function openAccount() {
  const { user } = state.me;
  $("#account-who").textContent = t("account.signed_in_as", { name: user.username, role: t(`role.${user.role}`) });
  $("#account-inbox").textContent = t("account.inbox", { name: user.username });
  $("#account-language").value = user.language || "";
  $("#password-form").reset();
  $("#password-error").textContent = "";
  $("#password-done").textContent = "";
  $("#account-dialog").showModal();
}

async function changeLanguage() {
  const language = $("#account-language").value || null;
  try {
    await api("/api/me", { method: "PATCH", body: JSON.stringify({ language }) });
    location.reload(); // the server renders the page in the new language
  } catch (err) {
    $("#password-error").textContent = err.message;
  }
}

async function changePassword(event) {
  event.preventDefault();
  const form = event.target;
  const error = $("#password-error");
  $("#password-done").textContent = "";
  if (form.elements.new.value !== form.elements.repeat.value) {
    error.textContent = t("account.error.mismatch");
    return;
  }
  error.textContent = "";
  try {
    await api("/api/me/password", {
      method: "POST",
      body: JSON.stringify({ current: form.elements.current.value, new: form.elements.new.value }),
    });
    form.reset();
    $("#password-done").textContent = t("account.password_changed");
  } catch (err) {
    error.textContent = err.message;
  }
}

async function openUsers() {
  $("#users-error").textContent = "";
  $("#users-notice").textContent = "";
  $("#users-dialog").showModal();
  await loadUsers();
  renderUsers();
}

function renderUsers() {
  const me = state.me.user;
  $("#user-list").replaceChildren(
    ...state.users.map((user) => {
      const item = el("li");
      const name = el("span", "name", user.id === me.id ? `${user.username} (${t("users.you")})` : user.username);
      const role = document.createElement("select");
      role.setAttribute("aria-label", t("users.role"));
      role.append(new Option(t("role.user"), "user"), new Option(t("role.admin"), "admin"));
      role.value = user.role;
      role.addEventListener("change", () => editUser(user, { role: role.value }));
      const button = (key, action) => {
        const b = el("button", "secondary small", t(key));
        b.type = "button";
        b.addEventListener("click", action);
        return b;
      };
      item.append(
        name,
        role,
        button("users.rename", () => renameUser(user)),
        button("users.reset", () => resetPassword(user)),
      );
      if (user.id !== me.id) {
        const remove = button("users.remove", () => removeUser(user));
        remove.classList.add("danger");
        item.append(remove);
      }
      return item;
    }),
  );
}

async function editUser(user, fields, notice = "") {
  $("#users-error").textContent = "";
  $("#users-notice").textContent = "";
  try {
    await api(`/api/users/${user.id}`, { method: "PATCH", body: JSON.stringify(fields) });
    $("#users-notice").textContent = notice;
    if (user.id === state.me.user.id) await loadMe();
  } catch (err) {
    $("#users-error").textContent = err.message;
  }
  await loadUsers();
  renderUsers();
}

function renameUser(user) {
  const name = prompt(t("users.rename_prompt", { name: user.username }), user.username);
  if (name && name.trim() !== user.username) editUser(user, { username: name });
}

function resetPassword(user) {
  const password = prompt(t("users.reset_prompt", { name: user.username }));
  if (password) editUser(user, { password }, t("users.reset_done", { name: user.username }));
}

async function removeUser(user) {
  if (!confirm(t("users.remove_confirm", { name: user.username }))) return;
  try {
    await api(`/api/users/${user.id}`, { method: "DELETE" });
  } catch (err) {
    $("#users-error").textContent = err.message;
  }
  await loadUsers();
  renderUsers();
}

async function createUser(event) {
  event.preventDefault();
  const form = event.target;
  $("#users-error").textContent = "";
  $("#users-notice").textContent = "";
  const data = Object.fromEntries(new FormData(form));
  try {
    const user = await api("/api/users", { method: "POST", body: JSON.stringify(data) });
    form.reset();
    $("#users-notice").textContent = t("users.created", { name: user.username });
  } catch (err) {
    $("#users-error").textContent = err.message;
  }
  await loadUsers();
  renderUsers();
}

// Color scheme: automatic → light → dark

const THEMES = ["auto", "light", "dark"];

function applyTheme(theme) {
  if (theme === "light" || theme === "dark") document.documentElement.dataset.theme = theme;
  else delete document.documentElement.dataset.theme;
  const name = THEMES.includes(theme) ? theme : "auto";
  $("#theme use").setAttribute("href", `#icon-${name}`);
  $("#theme-name").textContent = t(`theme.${name}`);
  $("#theme").title = t("theme.title");
}

function cycleTheme() {
  const current = store.get("theme") || "auto";
  const next = THEMES[(THEMES.indexOf(current) + 1) % THEMES.length];
  store.set("theme", next);
  applyTheme(next);
}

// Startup

async function init() {
  applyTheme(store.get("theme") || "auto");
  $("#theme").addEventListener("click", cycleTheme);

  const sort = store.get("sort");
  if (sort && [...$("#sort").options].some((o) => o.value === sort)) $("#sort").value = sort;

  [state.sources, state.statuses] = await Promise.all([
    api("/api/sources").catch(() => []),
    api("/api/statuses").catch(() => []),
    loadMe(),
  ]);
  // Without the limits the server still refuses a file that is too large, only later
  api("/api/imports/limits").then((limits) => {
    state.limits = limits;
    // The file picker offers the formats this server imports (".tar.gz" as ".gz")
    const endings = new Set(limits.formats.map((suffix) => `.${suffix.split(".").pop()}`));
    $("#upload-input").accept = [...endings].join(",");
  }, () => {});
  for (const source of state.sources) {
    $("#source-filter").append(new Option(sourceLabel(source), source));
    $("#edit-form").elements.source.append(new Option(sourceLabel(source), source));
  }
  // Status filter: after "all" the single statuses, then "no status" and "favorites"
  const statusFilter = $("#status-filter");
  const firstSpecial = statusFilter.querySelector('option[value="-"]');
  for (const status of state.statuses) {
    statusFilter.insertBefore(new Option(statusLabel(status), status), firstSpecial);
    $("#edit-form").elements.status.append(new Option(statusLabel(status), status));
  }
  $("#fav-first").checked = store.get("favFirst") === "1";

  for (const id of ["#search", "#source-filter", "#status-filter", "#sort", "#todo-only", "#update-only", "#fav-first"]) {
    $(id).addEventListener("input", render);
  }
  $("#sort").addEventListener("change", () => store.set("sort", $("#sort").value));
  $("#fav-first").addEventListener("change", () => store.set("favFirst", $("#fav-first").checked ? "1" : "0"));
  $("#rescan").addEventListener("click", rescan);
  $("#check-updates").addEventListener("click", checkAllUpdates);
  $("#check-now").addEventListener("click", checkNow);
  $("#show-stats").addEventListener("click", openStats);
  $("#stats-close").addEventListener("click", () => $("#stats").close());
  $("#show-backups").addEventListener("click", openBackups);
  $("#backups-close").addEventListener("click", () => $("#backups").close());
  // Deleted backups change the sizes on the tiles and in the statistics
  $("#backups").addEventListener("close", load);
  $("#overview-button").addEventListener("click", openOverview);
  $("#overview-close").addEventListener("click", () => $("#overview").close());
  $("#edit-delete").addEventListener("click", () => {
    const game = state.games.find((g) => String(g.id) === $("#edit-form").dataset.id);
    if (game) deleteGame(game);
  });
  $("#upload").addEventListener("click", () => $("#upload-input").click());
  $("#upload-input").addEventListener("change", (event) => {
    queueUploads(event.target.files);
    event.target.value = "";
  });
  setupDropZone();
  $("#link-import").addEventListener("click", openLinkDialog);
  $("#link-cancel").addEventListener("click", () => $("#link-dialog").close());
  $("#link-form").addEventListener("submit", startDownload);
  $("#edit-form").addEventListener("submit", saveEditor);
  $("#account").addEventListener("click", openAccount);
  $("#account-close").addEventListener("click", () => $("#account-dialog").close());
  $("#account-language").addEventListener("change", changeLanguage);
  $("#password-form").addEventListener("submit", changePassword);
  $("#users-button").addEventListener("click", openUsers);
  $("#users-close").addEventListener("click", () => $("#users-dialog").close());
  $("#user-form").addEventListener("submit", createUser);
  $("#edit-cancel").addEventListener("click", () => $("#editor").close());
  $("#image-choice").addEventListener("change", chooseImage);
  $("#image-upload").addEventListener("click", () => $("#image-input").click());
  $("#image-input").addEventListener("change", uploadImage);
  $("#editor").addEventListener("close", () => {
    if (!state.imageChanged) return;
    state.imageChanged = false;
    load();
  });
  // Refresh "last played" after playing (in another tab)
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && !$("#editor").open) {
      load();
      loadImports();
    }
  });
  $("#list").addEventListener("click", (event) => {
    if (event.target.closest(".play")) setTimeout(load, 800);
  });

  // Imports from the inbox folder appear without reloading the page
  setInterval(() => {
    if (document.visibilityState === "visible") loadImports();
  }, 30000);

  await load();
  await loadImports();
  pollDownloads();
}

init();
