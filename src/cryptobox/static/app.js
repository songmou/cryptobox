const $ = (selector) => document.querySelector(selector);
const state = {
  csrf: "",
  status: null,
  settings: { auto_lock_minutes: 3, theme: "system" },
  selected: null,
  selectedDirectory: "",
  treeNodes: new Map(),
  poll: null,
  previewController: null,
  previewCleanup: null,
  previewRequest: 0,
  operationFinishedAt: null,
  drawerOpen: false,
  autoLockDeadlineMs: null,
  lastActivityReportAt: 0,
  activityRequest: null,
  checkingAutoLock: false,
  activityTimer: null,
  mediaHeartbeat: null,
  mediaPlaying: false,
  locking: false,
  lockReason: "",
};

const TEXT_PREVIEW_LIMIT = 5 * 1024 * 1024;
const DOCUMENT_PREVIEW_LIMIT = 50 * 1024 * 1024;
const SANDBOX_KINDS = new Set(["text", "unknown", "markdown", "table", "html", "svg", "word", "spreadsheet", "presentation", "ebook", "archive"]);
const ZOOM_MIN = 0.1;
const ZOOM_MAX = 8;
const ACTIVITY_REPORT_INTERVAL = 5000;
const MEDIA_HEARTBEAT_INTERVAL = 30000;
const TREE_CHEVRON = '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="m8 5 5 5-5 5"/></svg>';

function formatBytes(value = 0) {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let index = 0, number = Number(value);
  while (number >= 1024 && index < units.length - 1) { number /= 1024; index++; }
  return `${number.toFixed(index ? 1 : 0)} ${units[index]}`;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[char]));
}

function clearActivePreview() {
  state.previewRequest += 1;
  if (state.previewController) state.previewController.abort();
  if (state.previewCleanup) state.previewCleanup();
  state.previewController = null;
  state.previewCleanup = null;
  setMediaPlaying(false);
}

function setMediaPlaying(playing) {
  state.mediaPlaying = playing;
  clearInterval(state.mediaHeartbeat);
  state.mediaHeartbeat = null;
  if (playing) {
    reportActivity(true);
    state.mediaHeartbeat = setInterval(() => reportActivity(true), MEDIA_HEARTBEAT_INTERVAL);
  } else if (state.status?.unlocked && !state.locking) reportActivity(true);
  updateAutoLockCountdown();
}

function previewMessage(message, symbol = "⇩", kind = "", action = null) {
  const target = $("#preview");
  const box = document.createElement("div");
  box.className = `empty-state${kind ? ` ${kind}` : ""}`;
  const icon = document.createElement("span");
  const text = document.createElement("p");
  icon.textContent = symbol;
  text.textContent = message;
  box.append(icon, text);
  if (action) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "secondary preview-action";
    button.textContent = action.label;
    button.addEventListener("click", action.handler);
    box.appendChild(button);
  }
  target.replaceChildren(box);
}

const FILE_ICONS = {
  directory: '<svg viewBox="0 0 24 24"><path d="M3 6.5h6l2 2h10v9.5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>',
  image: '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="8.5" cy="9" r="1.5"/><path d="m5 18 5-5 3 3 2-2 4 4"/></svg>',
  audio: '<svg viewBox="0 0 24 24"><path d="M9 18V6l10-2v12"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="16.5" cy="16" r="2.5"/></svg>',
  video: '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="14" height="14" rx="2"/><path d="m17 10 4-2v8l-4-2z"/></svg>',
  pdf: '<svg viewBox="0 0 24 24"><path d="M6 2h8l4 4v16H6z"/><path d="M14 2v5h5M8 16c3-1 5-4 6-7 0 4 1 6 3 7-3-1-6-1-9 0z"/></svg>',
  text: '<svg viewBox="0 0 24 24"><path d="M6 2h8l4 4v16H6z"/><path d="M14 2v5h5M9 12h6M9 16h6"/></svg>',
  document: '<svg viewBox="0 0 24 24"><path d="M6 2h8l4 4v16H6z"/><path d="M14 2v5h5M9 12h6M9 16h4"/></svg>',
  spreadsheet: '<svg viewBox="0 0 24 24"><rect x="4" y="3" width="16" height="18" rx="2"/><path d="M4 9h16M4 15h16M10 9v12"/></svg>',
  presentation: '<svg viewBox="0 0 24 24"><path d="M4 4h16v12H4zM12 16v5M8 21h8"/><path d="m8 13 3-3 2 2 3-4"/></svg>',
  archive: '<svg viewBox="0 0 24 24"><path d="M5 3h14v18H5zM9 3v3h3V3m-3 6h3v3H9m0 3h3v3H9"/></svg>',
  generic: '<svg viewBox="0 0 24 24"><path d="M6 2h8l4 4v16H6z"/><path d="M14 2v5h5"/></svg>',
};

function fileIcon(entry) {
  if (entry.kind === "directory") return FILE_ICONS.directory;
  const kind = entry.preview_kind;
  const extension = entry.name.includes(".") ? entry.name.split(".").pop().toLowerCase() : "";
  if (extension === "doc") return FILE_ICONS.document;
  if (extension === "xls") return FILE_ICONS.spreadsheet;
  if (extension === "ppt") return FILE_ICONS.presentation;
  if (["image", "svg"].includes(kind)) return FILE_ICONS.image;
  if (["audio", "video", "pdf"].includes(kind)) return FILE_ICONS[kind];
  if (["text", "unknown", "markdown", "html"].includes(kind)) return FILE_ICONS.text;
  if (kind === "table" || kind === "spreadsheet") return FILE_ICONS.spreadsheet;
  if (kind === "presentation") return FILE_ICONS.presentation;
  if (["archive", "ebook"].includes(kind)) return FILE_ICONS.archive;
  if (kind === "word") return FILE_ICONS.document;
  return FILE_ICONS.generic;
}

function contentUrl(entry) {
  return `/api/content/${encodeURIComponent(entry.id)}`;
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function showMediaPreview(entry, kind) {
  const target = $("#preview");
  const shell = document.createElement("section");
  shell.className = "media-shell";
  const toolbar = document.createElement("div");
  toolbar.className = "media-toolbar";
  toolbar.innerHTML = `
    <button type="button" data-action="out" aria-label="缩小">−</button>
    <button type="button" data-action="in" aria-label="放大">＋</button>
    <button type="button" data-action="fit">适应窗口</button>
    <button type="button" data-action="actual">100%</button>
    <span class="zoom-label" aria-live="polite">100%</span>
    <button type="button" data-action="fullscreen">全屏</button>`;
  const stage = document.createElement("div");
  stage.className = "media-stage";
  const media = document.createElement(kind === "image" ? "img" : "video");
  media.className = "zoom-media";
  media.alt = kind === "image" ? entry.name : "";
  media.draggable = false;
  if (kind === "video") {
    media.controls = true;
    media.preload = "metadata";
    media.setAttribute("playsinline", "");
  }
  stage.appendChild(media);
  shell.append(toolbar, stage);
  target.replaceChildren(shell);

  let naturalWidth = 1, naturalHeight = 1, scale = 1, fitScale = 1, panX = 0, panY = 0;
  const pointers = new Map();
  let dragStart = null, pinchStart = null, resizeObserver = null;
  const label = toolbar.querySelector(".zoom-label");

  const applyTransform = () => {
    scale = clamp(scale, ZOOM_MIN, ZOOM_MAX);
    media.style.transform = `translate(-50%, -50%) translate(${panX}px, ${panY}px) scale(${scale})`;
    label.textContent = `${Math.round(scale * 100)}%`;
    stage.classList.toggle("can-pan", scale > fitScale + 0.001);
  };
  const fit = () => {
    const rect = stage.getBoundingClientRect();
    if (!rect.width || !rect.height || !naturalWidth || !naturalHeight) return;
    fitScale = clamp(Math.min((rect.width - 24) / naturalWidth, (rect.height - 24) / naturalHeight, 1), ZOOM_MIN, ZOOM_MAX);
    scale = fitScale;
    panX = panY = 0;
    applyTransform();
  };
  const zoomBy = (factor) => {
    scale = clamp(scale * factor, ZOOM_MIN, ZOOM_MAX);
    if (scale <= fitScale) panX = panY = 0;
    applyTransform();
  };
  const ready = () => {
    naturalWidth = kind === "image" ? media.naturalWidth : media.videoWidth;
    naturalHeight = kind === "image" ? media.naturalHeight : media.videoHeight;
    fit();
  };

  media.addEventListener(kind === "image" ? "load" : "loadedmetadata", ready, { once: true });
  media.addEventListener("error", () => previewMessage("浏览器无法解码此文件或媒体编码，可使用下载按钮保存后查看。", "!", "preview-warning"), { once: true });
  if (kind === "video") {
    media.addEventListener("play", () => setMediaPlaying(true));
    media.addEventListener("timeupdate", recordActivity);
    for (const event of ["pause", "ended", "error", "emptied"]) media.addEventListener(event, () => setMediaPlaying(false));
  }

  toolbar.addEventListener("click", async (event) => {
    const action = event.target.closest("button")?.dataset.action;
    if (action === "out") zoomBy(0.8);
    if (action === "in") zoomBy(1.25);
    if (action === "fit") fit();
    if (action === "actual") { scale = 1; panX = panY = 0; applyTransform(); }
    if (action === "fullscreen") {
      try {
        if (document.fullscreenElement === shell) await document.exitFullscreen();
        else if (shell.requestFullscreen) await shell.requestFullscreen();
        else toast("当前浏览器不支持全屏预览");
      } catch (_) { toast("无法进入全屏预览"); }
    }
  });
  stage.addEventListener("wheel", (event) => {
    event.preventDefault();
    zoomBy(event.deltaY < 0 ? 1.12 : 1 / 1.12);
  }, { passive: false });
  stage.addEventListener("pointerdown", (event) => {
    if (kind === "video" && event.target === media) {
      const mediaRect = media.getBoundingClientRect();
      if (event.clientY >= mediaRect.bottom - 58) return;
    }
    pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    stage.setPointerCapture(event.pointerId);
    if (pointers.size === 1) dragStart = { x: event.clientX, y: event.clientY, panX, panY };
    if (pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      pinchStart = { distance: Math.hypot(a.x - b.x, a.y - b.y), scale };
    }
  });
  stage.addEventListener("pointermove", (event) => {
    if (!pointers.has(event.pointerId)) return;
    pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (pointers.size === 2 && pinchStart) {
      const [a, b] = [...pointers.values()];
      scale = clamp(pinchStart.scale * Math.hypot(a.x - b.x, a.y - b.y) / Math.max(1, pinchStart.distance), ZOOM_MIN, ZOOM_MAX);
      applyTransform();
    } else if (pointers.size === 1 && dragStart && scale > fitScale) {
      panX = dragStart.panX + event.clientX - dragStart.x;
      panY = dragStart.panY + event.clientY - dragStart.y;
      applyTransform();
    }
  });
  const releasePointer = (event) => {
    pointers.delete(event.pointerId);
    if (pointers.size < 2) pinchStart = null;
    if (pointers.size === 0) dragStart = null;
  };
  stage.addEventListener("pointerup", releasePointer);
  stage.addEventListener("pointercancel", releasePointer);
  const fullscreenChange = () => { if (!document.fullscreenElement) requestAnimationFrame(fit); };
  document.addEventListener("fullscreenchange", fullscreenChange);
  resizeObserver = new ResizeObserver(() => { if (Math.abs(scale - fitScale) < 0.001) fit(); });
  resizeObserver.observe(stage);
  media.src = contentUrl(entry);
  state.previewCleanup = () => {
    setMediaPlaying(false);
    resizeObserver?.disconnect();
    document.removeEventListener("fullscreenchange", fullscreenChange);
    media.pause?.();
    media.removeAttribute("src");
    media.load?.();
  };
}

function showNativePreview(entry, kind) {
  if (["image", "video"].includes(kind)) return showMediaPreview(entry, kind);
  const target = $("#preview");
  const node = document.createElement(kind === "audio" ? "audio" : "iframe");
  if (kind === "audio") {
    node.controls = true;
    node.preload = "metadata";
    node.addEventListener("play", () => setMediaPlaying(true));
    node.addEventListener("timeupdate", recordActivity);
    for (const event of ["pause", "ended", "error", "emptied"]) node.addEventListener(event, () => setMediaPlaying(false));
  } else node.title = entry.name;
  node.addEventListener("error", () => previewMessage("浏览器无法解码此文件或媒体编码，可使用下载按钮保存后查看。", "!", "preview-warning"), { once: true });
  node.src = contentUrl(entry);
  target.replaceChildren(node);
  state.previewCleanup = () => {
    setMediaPlaying(false);
    if (kind === "audio") { node.pause(); node.removeAttribute("src"); node.load(); }
    else node.src = "about:blank";
  };
}

function createSandboxFrame(requestId, errorPrefix = "无法预览") {
  const target = $("#preview");
  const frame = document.createElement("iframe");
  frame.title = "安全文件预览";
  frame.className = "sandbox-preview";
  frame.setAttribute("sandbox", "allow-scripts");
  frame.src = "/static/preview-host.html";
  let readyResolve, readyReject;
  const ready = new Promise((resolve, reject) => { readyResolve = resolve; readyReject = reject; });
  const timeout = setTimeout(() => readyReject(new Error("安全预览器加载超时")), 20000);
  const onMessage = (event) => {
    if (event.source !== frame.contentWindow) return;
    if (event.data?.type === "cryptobox-preview-ready") { clearTimeout(timeout); readyResolve(); }
    else if (event.data?.requestId === requestId && event.data?.type === "cryptobox-preview-error") previewMessage(`${errorPrefix}：${event.data.message || "文件解析失败"}`, "!", "preview-warning");
  };
  window.addEventListener("message", onMessage);
  state.previewCleanup = () => { clearTimeout(timeout); window.removeEventListener("message", onMessage); frame.src = "about:blank"; };
  target.replaceChildren(frame);
  return { frame, ready };
}

async function showSandboxPreview(entry, kind, requestId, errorPrefix = "无法预览") {
  const limit = ["text", "unknown", "markdown", "table", "html", "svg"].includes(kind) ? TEXT_PREVIEW_LIMIT : DOCUMENT_PREVIEW_LIMIT;
  if (entry.size > limit) {
    previewMessage(`文件超过 ${limit === TEXT_PREVIEW_LIMIT ? "5 MB" : "50 MB"} 的安全预览上限，可使用下载按钮保存。`, "⇩", "preview-warning");
    return;
  }
  const { frame, ready } = createSandboxFrame(requestId, errorPrefix);
  state.previewController = new AbortController();
  const responsePromise = fetch(contentUrl(entry), { credentials: "same-origin", signal: state.previewController.signal }).then(async (response) => {
    if (!response.ok) {
      let message = `${response.status} ${response.statusText}`;
      try { message = (await response.json()).detail || message; } catch (_) {}
      throw new Error(message);
    }
    return response.arrayBuffer();
  });
  try {
    const [buffer] = await Promise.all([responsePromise, ready]);
    if (requestId !== state.previewRequest || !frame.contentWindow) return;
    frame.contentWindow.postMessage({
      type: "cryptobox-preview", requestId, kind, name: entry.name,
      mediaType: entry.media_type || "application/octet-stream", buffer,
      theme: resolvedTheme(),
    }, "*", [buffer]);
  } catch (error) {
    if (error.name === "AbortError" || requestId !== state.previewRequest) return;
    previewMessage(`${errorPrefix}：${error.message}`, "!", "preview-warning");
  }
}

async function api(url, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (state.csrf && options.method && options.method !== "GET") headers["X-Cryptobox-CSRF"] = state.csrf;
  if (options.body && typeof options.body !== "string") { headers["Content-Type"] = "application/json"; options.body = JSON.stringify(options.body); }
  const response = await fetch(url, { credentials: "same-origin", ...options, headers });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try { message = (await response.json()).detail || message; } catch (_) {}
    throw new Error(message);
  }
  const type = response.headers.get("content-type") || "";
  return type.includes("application/json") ? response.json() : response;
}

let toastTimer;
function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.add("hidden"), 3600);
}

function showError(message) {
  const node = $("#accessError");
  node.textContent = message;
  node.classList.remove("hidden");
}

function setStatusPill(label, kind = "") {
  const node = $("#vaultState");
  node.className = `status-pill ${kind}`;
  node.querySelector("span").textContent = label;
}

function resolvedTheme() {
  if (state.settings.theme !== "system") return state.settings.theme;
  return matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function applyTheme(theme = state.settings.theme) {
  state.settings.theme = theme;
  document.documentElement.dataset.theme = resolvedTheme();
  const frame = document.querySelector("#preview iframe.sandbox-preview");
  if (frame?.contentWindow) frame.contentWindow.postMessage({ type: "cryptobox-preview-theme", theme: resolvedTheme() }, "*");
}

async function loadSettings() {
  try {
    const settings = await api("/api/settings");
    state.settings = settings;
    applyTheme(settings.theme);
    $("#settingsRoot").textContent = settings.root;
    $("#autoLockMinutes").value = settings.auto_lock_minutes;
    const radio = document.querySelector(`input[name="theme"][value="${settings.theme}"]`);
    if (radio) radio.checked = true;
  } catch (_) { applyTheme(); }
}

async function refreshStatus() {
  try {
    const wasUnlocked = Boolean(state.status?.unlocked);
    const info = await api("/api/status");
    state.status = info;
    state.csrf = info.csrf;
    syncAutoLockDeadline(info.auto_lock_remaining_seconds);
    $("#rootPath").value = info.root;
    $("#rootLabel").textContent = info.root;
    $("#settingsRoot").textContent = info.root;
    if (state.locking) return info;
    if (wasUnlocked && !info.unlocked) state.lockReason = `已因连续 ${state.settings.auto_lock_minutes} 分钟无操作而自动锁定。`;
    if (!info.initialized) showInit();
    else if (!info.unlocked) showUnlock();
    else showWorkspace(info);
    renderOperation(info.operation);
    return info;
  } catch (error) {
    showError(error.message);
    setStatusPill("访问链接无效", "error");
  }
}

function showInit() {
  hideAutoLockCountdown();
  closeDrawer();
  closeSettings();
  $(".topbar").classList.remove("workspace-visible");
  document.querySelectorAll(".workspace-header-control").forEach((node) => node.classList.add("hidden"));
  $("#settingsButton").classList.add("hidden");
  $("#workspace").classList.add("hidden");
  $("#accessView").classList.remove("hidden");
  $("#unlockForm").classList.add("hidden");
  $("#initForm").classList.remove("hidden");
  $("#lockNotice").classList.add("hidden");
  $("#accessTitle").textContent = "创建本机保险库";
  $("#accessDescription").textContent = "确认目录后，Cryptobox 会原子加密其中的普通文件。";
  setStatusPill("等待初始化");
  loadPreview();
}

function showUnlock() {
  hideAutoLockCountdown();
  closeDrawer();
  closeSettings();
  $(".topbar").classList.remove("workspace-visible");
  document.querySelectorAll(".workspace-header-control").forEach((node) => node.classList.add("hidden"));
  $("#settingsButton").classList.add("hidden");
  $("#workspace").classList.add("hidden");
  $("#accessView").classList.remove("hidden");
  $("#initForm").classList.add("hidden");
  $("#unlockForm").classList.remove("hidden");
  $("#accessTitle").textContent = "解锁你的文件";
  $("#accessDescription").textContent = "密码只用于本机解锁，不会保存到磁盘。";
  const notice = $("#lockNotice");
  if (state.lockReason) { notice.textContent = state.lockReason; notice.classList.remove("hidden"); state.lockReason = ""; }
  else notice.classList.add("hidden");
  setStatusPill("已锁定");
}

function showWorkspace(info) {
  $("#accessView").classList.add("hidden");
  $("#workspace").classList.remove("hidden");
  $(".topbar").classList.add("workspace-visible");
  document.querySelectorAll(".workspace-header-control").forEach((node) => node.classList.remove("hidden"));
  $("#settingsButton").classList.toggle("hidden", !info.unlocked);
  const phase = info.operation.phase;
  setStatusPill(phase === "ready" ? "已保护" : phase === "error" ? "需要处理" : "正在处理", phase === "ready" ? "ready" : phase === "error" ? "error" : "");
  const finishedAt = info.operation.finished_at || null;
  if (!state.treeNodes.size || (finishedAt && finishedAt !== state.operationFinishedAt)) {
    state.operationFinishedAt = finishedAt;
    refreshTreePreservingExpansion();
  }
  if (!state.poll) state.poll = setInterval(refreshStatus, 1000);
  updateAutoLockCountdown();
}

async function loadPreview() {
  try {
    const summary = await api("/api/init/preview");
    $("#rootSummary").innerHTML = `<span>文件 ${summary.files.toLocaleString()}</span><span>容量 ${formatBytes(summary.bytes)}</span>`;
  } catch (error) { showError(error.message); }
}

function renderOperation(operation) {
  const panel = $("#operationPanel");
  if (!state.locking && !["scanning", "encrypting", "verifying", "error"].includes(operation.phase)) { panel.classList.add("hidden"); return; }
  panel.classList.remove("hidden");
  const labels = { scanning: "正在扫描目录", encrypting: "正在原子加密", verifying: "正在完整校验", error: "处理完成，但存在错误" };
  $("#operationText").textContent = state.locking ? "正在安全锁定，请稍候" : (labels[operation.phase] || operation.phase);
  $("#operationCount").textContent = `${operation.processed_files || 0} / ${operation.total_files || 0}`;
  const ratio = operation.total_files ? (operation.processed_files / operation.total_files) * 100 : 0;
  $("#progressBar").style.width = `${Math.min(100, ratio)}%`;
  $("#operationErrors").innerHTML = (operation.errors || []).slice(-5).map((item) => `<div>${escapeHtml(item)}</div>`).join("");
}

function makeTreeNode(id, name, parentId = null, depth = 0) {
  return { id, name, parentId, depth, expanded: id === "", loaded: false, loading: false, entries: [], nextOffset: 0, hasMore: false, error: "" };
}

async function fetchTreeNode(nodes, id = "", append = false) {
  let node = nodes.get(id);
  if (!node) {
    node = makeTreeNode(id, id ? "文件夹" : "根目录");
    nodes.set(id, node);
  }
  if (node.loading) return;
  node.loading = true;
  node.error = "";
  try {
    const offset = append ? node.nextOffset : 0;
    const data = await api(`/api/tree?path_id=${encodeURIComponent(id)}&offset=${offset}&limit=500`);
    node.entries = append ? node.entries.concat(data.entries) : data.entries;
    node.nextOffset = data.next_offset;
    node.hasMore = data.has_more;
    node.loaded = true;
    for (const entry of data.entries) {
      if (entry.kind === "directory") {
        const existing = nodes.get(entry.id);
        if (existing) { existing.name = entry.name; existing.parentId = id; existing.depth = node.depth + 1; }
        else nodes.set(entry.id, makeTreeNode(entry.id, entry.name, id, node.depth + 1));
      }
    }
  } catch (error) { node.error = error.message; }
  finally { node.loading = false; }
}

async function loadTreeNode(id = "", append = false) {
  let node = state.treeNodes.get(id);
  if (!node) {
    node = makeTreeNode(id, id ? "文件夹" : "根目录");
    state.treeNodes.set(id, node);
  }
  if (node.loading) return;
  node.loading = true;
  renderTree();
  node.loading = false;
  await fetchTreeNode(state.treeNodes, id, append);
  renderTree();
}

function renderTreeEntries(node, container) {
  for (const entry of node.entries) {
    const row = document.createElement("div");
    const encrypted = entry.kind === "file" && entry.encrypted === true;
    row.className = `tree-row${entry.kind === "file" && !encrypted ? " unencrypted" : ""}${state.selected?.id === entry.id || (entry.kind === "directory" && state.selectedDirectory === entry.id) ? " active" : ""}`;
    row.style.setProperty("--depth", node.depth + 1);
    row.setAttribute("role", "treeitem");
    row.tabIndex = 0;
    row.dataset.id = entry.id;
    row.dataset.kind = entry.kind;
    if (entry.kind === "directory") {
      const child = state.treeNodes.get(entry.id);
      row.setAttribute("aria-expanded", String(Boolean(child?.expanded)));
      row.innerHTML = `<button class="tree-toggle" type="button" aria-expanded="${Boolean(child?.expanded)}" aria-label="${child?.expanded ? "收起" : "展开"} ${escapeHtml(entry.name)}">${TREE_CHEVRON}</button><span class="file-icon" aria-hidden="true">${fileIcon(entry)}</span><button class="tree-name" type="button">${escapeHtml(entry.name)}</button><span></span>`;
      row.querySelector(".tree-toggle").addEventListener("click", () => toggleDirectory(entry.id));
      row.querySelector(".tree-name").addEventListener("click", () => selectDirectory(entry.id));
    } else {
      const status = encrypted ? '<span class="file-status">已加密</span>' : '<span class="file-status plain">未加密</span>';
      row.innerHTML = `<span class="tree-spacer"></span><span class="file-icon" aria-hidden="true">${fileIcon(entry)}</span><button class="tree-name" type="button">${escapeHtml(entry.name)}</button><span class="file-meta">${status}<span class="file-size">${formatBytes(entry.size)}</span></span>`;
      row.querySelector(".tree-name").addEventListener("click", () => { previewFile(entry); closeDrawerOnMobile(); });
    }
    row.addEventListener("keydown", (event) => handleTreeKey(event, entry));
    container.appendChild(row);
    if (entry.kind === "directory") {
      const child = state.treeNodes.get(entry.id);
      if (child?.expanded) {
        if (child.loading) appendTreeMessage(container, child.depth + 1, "正在加载…");
        else if (child.error) appendTreeMessage(container, child.depth + 1, child.error, true);
        else if (child.loaded && !child.entries.length) appendTreeMessage(container, child.depth + 1, "空文件夹");
        if (child.loaded) renderTreeEntries(child, container);
      }
    }
  }
  if (node.hasMore) {
    const more = document.createElement("button");
    more.className = "secondary tree-more";
    more.style.setProperty("--depth", node.depth + 1);
    more.textContent = "加载更多";
    more.addEventListener("click", () => loadTreeNode(node.id, true));
    container.appendChild(more);
  }
}

function appendTreeMessage(container, depth, message, error = false) {
  const row = document.createElement("div");
  row.className = `tree-message${error ? " error-text" : ""}`;
  row.style.setProperty("--depth", depth);
  row.textContent = message;
  container.appendChild(row);
}

function captureTreeViewState() {
  const tree = $("#fileTree");
  const active = document.activeElement;
  const row = active?.closest?.(".tree-row");
  let focusTarget = "";
  if (active?.classList?.contains("tree-toggle")) focusTarget = "toggle";
  else if (active?.classList?.contains("tree-name")) focusTarget = "name";
  else if (row === active) focusTarget = "row";
  return {
    scrollTop: tree?.scrollTop || 0,
    focusId: row?.dataset.id ?? null,
    focusTarget,
  };
}

function restoreTreeViewState(viewState) {
  const tree = $("#fileTree");
  if (!tree || !viewState) return;
  tree.scrollTop = viewState.scrollTop;
  if (viewState.focusId === null) return;
  const row = [...tree.querySelectorAll(".tree-row")].find((candidate) => candidate.dataset.id === viewState.focusId);
  if (!row) return;
  const target = viewState.focusTarget === "toggle" ? row.querySelector(".tree-toggle")
    : viewState.focusTarget === "name" ? row.querySelector(".tree-name") : row;
  target?.focus({ preventScroll: true });
}

function renderTree() {
  const tree = $("#fileTree");
  const viewState = captureTreeViewState();
  tree.replaceChildren();
  const root = state.treeNodes.get("");
  if (!root) return;
  const rootRow = document.createElement("div");
  rootRow.className = `tree-row root-row${state.selectedDirectory === "" ? " active" : ""}`;
  rootRow.setAttribute("role", "treeitem");
  rootRow.setAttribute("aria-expanded", "true");
  rootRow.tabIndex = 0;
  rootRow.dataset.id = "";
  rootRow.dataset.kind = "directory";
  rootRow.innerHTML = `<span class="tree-toggle fixed" aria-hidden="true">${TREE_CHEVRON}</span><span class="file-icon">${FILE_ICONS.directory}</span><button class="tree-name" type="button">根目录</button><span></span>`;
  rootRow.querySelector(".tree-name").addEventListener("click", () => selectDirectory(""));
  tree.appendChild(rootRow);
  if (root.loading) appendTreeMessage(tree, 1, "正在加载…");
  else if (root.error) appendTreeMessage(tree, 1, root.error, true);
  else if (root.loaded && !root.entries.length) appendTreeMessage(tree, 1, "此目录为空");
  if (root.loaded) renderTreeEntries(root, tree);
  restoreTreeViewState(viewState);
}

async function toggleDirectory(id, force = null) {
  const node = state.treeNodes.get(id);
  if (!node) return;
  node.expanded = force === null ? !node.expanded : force;
  renderTree();
  if (node.expanded && !node.loaded) await loadTreeNode(id);
}

function selectDirectory(id) {
  state.selectedDirectory = id;
  renderTree();
}

function handleTreeKey(event, entry) {
  const rows = [...document.querySelectorAll("#fileTree .tree-row")];
  const index = rows.indexOf(event.currentTarget);
  if (event.key === "ArrowDown") { event.preventDefault(); rows[index + 1]?.focus(); }
  else if (event.key === "ArrowUp") { event.preventDefault(); rows[index - 1]?.focus(); }
  else if (event.key === "ArrowRight" && entry.kind === "directory") { event.preventDefault(); toggleDirectory(entry.id, true); }
  else if (event.key === "ArrowLeft" && entry.kind === "directory") { event.preventDefault(); toggleDirectory(entry.id, false); }
  else if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    if (entry.kind === "directory") selectDirectory(entry.id);
    else { previewFile(entry); closeDrawerOnMobile(); }
  }
}

function findTreeEntry(nodes, id, kind = null) {
  for (const node of nodes.values()) {
    const entry = node.entries.find((candidate) => candidate.id === id && (!kind || candidate.kind === kind));
    if (entry) return { entry, parentId: node.id };
  }
  return null;
}

async function refreshTreePreservingExpansion() {
  const previousNodes = state.treeNodes;
  const expanded = [...previousNodes.values()]
    .filter((node) => node.expanded && node.id)
    .sort((a, b) => a.depth - b.depth)
    .map((node) => node.id);
  const selectedFileParent = state.selected ? findTreeEntry(previousNodes, state.selected.id, "file")?.parentId : null;
  const nodesToLoad = new Set(expanded);
  for (let id = selectedFileParent; id; id = previousNodes.get(id)?.parentId || "") nodesToLoad.add(id);
  for (let id = state.selectedDirectory ? previousNodes.get(state.selectedDirectory)?.parentId : null; id; id = previousNodes.get(id)?.parentId || "") nodesToLoad.add(id);

  const refreshed = new Map([["", makeTreeNode("", "根目录")]]);
  await fetchTreeNode(refreshed, "");
  const orderedIds = [...nodesToLoad].sort((a, b) => (previousNodes.get(a)?.depth || 0) - (previousNodes.get(b)?.depth || 0));
  for (const id of orderedIds) {
    const node = refreshed.get(id);
    if (!node) continue;
    node.expanded = expanded.includes(id);
    await fetchTreeNode(refreshed, id);
  }
  const failedNode = [...refreshed.values()].find((node) => node.error);
  if (failedNode) {
    toast(`文件列表刷新失败：${failedNode.error}`);
    return;
  }

  const viewState = captureTreeViewState();
  const refreshedSelection = state.selected ? findTreeEntry(refreshed, state.selected.id, "file")?.entry : null;
  const selectedFileMissing = Boolean(state.selected && !refreshedSelection);
  if (refreshedSelection) state.selected = refreshedSelection;
  if (state.selectedDirectory && !refreshed.has(state.selectedDirectory)) state.selectedDirectory = "";
  state.treeNodes = refreshed;
  if (selectedFileMissing) {
    clearActivePreview();
    state.selected = null;
    $("#downloadButton").disabled = true;
    $("#previewTitle").textContent = "选择文件";
    previewMessage("此前选择的文件已不存在，请重新选择。", "!", "preview-warning");
  }
  renderTree();
  restoreTreeViewState(viewState);
}

async function previewFile(entry) {
  clearActivePreview();
  state.selected = entry.encrypted ? entry : null;
  $("#previewTitle").textContent = entry.name;
  $("#downloadButton").disabled = !entry.encrypted;
  $("#preview").classList.remove("empty");
  renderTree();
  if (!entry.encrypted) {
    previewMessage("此文件尚未加密或加密未成功，完成重新扫描前不能预览或下载。", "!", "preview-warning");
    return;
  }
  const kind = entry.preview_kind || "unsupported";
  if (["image", "video", "audio", "pdf"].includes(kind)) showNativePreview(entry, kind);
  else if (SANDBOX_KINDS.has(kind)) {
    previewMessage("正在解密并准备安全预览…", "◇");
    await showSandboxPreview(entry, kind, state.previewRequest);
  } else {
    const legacy = ["doc", "xls", "ppt"].includes(entry.name.split(".").pop().toLowerCase());
    previewMessage(legacy ? "旧版 Office 格式暂不支持网页解析，可使用下载按钮保存。" : "此格式没有可用的安全网页预览器，可使用下载按钮保存。", "⇩", "", {
      label: "尝试以文本打开", handler: async () => {
        clearActivePreview();
        previewMessage("正在解密并尝试以 UTF-8 文本打开…", "◇");
        await showSandboxPreview(entry, "text", state.previewRequest, "无法作为 UTF-8 文本打开");
      },
    });
  }
}

function resetWorkspace() {
  clearActivePreview();
  state.selected = null;
  state.selectedDirectory = "";
  state.treeNodes.clear();
  state.operationFinishedAt = null;
  $("#downloadButton").disabled = true;
  $("#previewTitle").textContent = "选择文件";
  previewMessage("从文件树中选择文件进行安全预览", "◇");
  $("#fileTree").replaceChildren();
}

function syncAutoLockDeadline(remainingSeconds) {
  state.autoLockDeadlineMs = Number.isFinite(remainingSeconds) ? Date.now() + Number(remainingSeconds) * 1000 : null;
}

function hideAutoLockCountdown() {
  state.autoLockDeadlineMs = null;
  $("#autoLockCountdown").classList.add("hidden");
  $("#footerDivider").classList.add("hidden");
}

function updateAutoLockCountdown() {
  const node = $("#autoLockCountdown");
  if (!state.status?.unlocked && !state.locking) { hideAutoLockCountdown(); return; }
  node.className = "footer-countdown";
  node.classList.remove("hidden");
  $("#footerDivider").classList.remove("hidden");
  if (state.locking) {
    node.textContent = "正在锁定…";
    node.classList.add("locking");
    return;
  }
  if (state.mediaPlaying) {
    node.textContent = "播放中 · 自动锁定已暂停";
    node.classList.add("paused");
    return;
  }
  const remaining = Math.max(0, Math.ceil(((state.autoLockDeadlineMs || Date.now()) - Date.now()) / 1000));
  const minutes = Math.floor(remaining / 60);
  const seconds = remaining % 60;
  node.textContent = `自动锁定 ${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

async function reportActivity(force = false) {
  if (!state.status?.unlocked || state.locking || state.checkingAutoLock) return;
  const now = Date.now();
  if (!force && (state.activityRequest || now - state.lastActivityReportAt < ACTIVITY_REPORT_INTERVAL)) return;
  state.lastActivityReportAt = now;
  const request = api("/api/activity", { method: "POST" });
  state.activityRequest = request;
  try {
    const result = await request;
    syncAutoLockDeadline(result.auto_lock_remaining_seconds);
    updateAutoLockCountdown();
  } catch (_) {
    await refreshStatus();
  } finally {
    if (state.activityRequest === request) state.activityRequest = null;
  }
}

function recordActivity() {
  reportActivity(false);
}

async function autoLockCheck() {
  updateAutoLockCountdown();
  if (!state.status?.unlocked || state.locking || state.mediaPlaying || state.autoLockDeadlineMs === null) return;
  if (Date.now() >= state.autoLockDeadlineMs) {
    state.checkingAutoLock = true;
    try { await refreshStatus(); }
    finally { state.checkingAutoLock = false; }
  }
}

async function lockVault(automatic = false) {
  if (state.locking || !state.status?.unlocked) return;
  state.locking = true;
  $("#settingsButton").classList.add("hidden");
  updateAutoLockCountdown();
  closeDrawer();
  closeSettings();
  clearActivePreview();
  setStatusPill("正在安全锁定");
  renderOperation(state.status.operation || {});
  try {
    await api("/api/lock", { method: "POST" });
    resetWorkspace();
    state.status.unlocked = false;
    if (automatic) state.lockReason = `已因连续 ${state.settings.auto_lock_minutes} 分钟无操作而自动锁定。`;
  } catch (error) { toast(error.message); }
  finally { state.locking = false; await refreshStatus(); }
}

function openDrawer() {
  if (matchMedia("(max-width: 900px)").matches) {
    state.drawerOpen = true;
    $("#sidebar").classList.add("open");
    $("#drawerScrim").classList.remove("hidden");
    $("#drawerButton").setAttribute("aria-expanded", "true");
    document.body.classList.add("drawer-open");
  }
}

function closeDrawer() {
  state.drawerOpen = false;
  $("#sidebar")?.classList.remove("open");
  $("#drawerScrim")?.classList.add("hidden");
  $("#drawerButton")?.setAttribute("aria-expanded", "false");
  document.body.classList.remove("drawer-open");
}

function closeDrawerOnMobile() { if (matchMedia("(max-width: 900px)").matches) closeDrawer(); }

function openSettings() {
  if (!state.status?.unlocked || state.locking) return;
  $("#settingsError").classList.add("hidden");
  $("#settingsRoot").textContent = state.status?.root || state.settings.root || "";
  $("#autoLockMinutes").value = state.settings.auto_lock_minutes;
  const radio = document.querySelector(`input[name="theme"][value="${state.settings.theme}"]`);
  if (radio) radio.checked = true;
  $("#modalBackdrop").classList.remove("hidden");
  setTimeout(() => $("#autoLockMinutes").focus(), 0);
}

function closeSettings() {
  $("#modalBackdrop").classList.add("hidden");
  applyTheme(state.settings.theme);
}

async function switchRoot() {
  const path = prompt("输入要打开的保险库绝对路径", state.status?.root || "");
  if (!path || path === state.status?.root) return;
  clearActivePreview();
  try {
    await api("/api/root", { method: "PUT", body: { path } });
    resetWorkspace();
    closeSettings();
    await Promise.all([refreshStatus(), loadSettings()]);
  } catch (error) { toast(error.message); }
}

$("#unlockForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("#accessError").classList.add("hidden");
  try {
    await api("/api/unlock", { method: "POST", body: { password: $("#unlockPassword").value } });
    $("#unlockPassword").value = "";
    await refreshStatus();
  } catch (error) { showError(error.message); }
});

$("#initForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("#accessError").classList.add("hidden");
  try {
    await api("/api/init", { method: "POST", body: { password: $("#initPassword").value, password_confirmation: $("#initConfirmation").value } });
    $("#initPassword").value = $("#initConfirmation").value = "";
    await refreshStatus();
  } catch (error) {
    if (/already initialized/i.test(error.message)) await refreshStatus();
    else showError(error.message);
  }
});

$("#applyRoot").addEventListener("click", async () => {
  try { await api("/api/root", { method: "PUT", body: { path: $("#rootPath").value } }); await Promise.all([refreshStatus(), loadSettings()]); }
  catch (error) { showError(error.message); }
});
$("#switchRootButton").addEventListener("click", switchRoot);
$("#settingsSwitchRoot").addEventListener("click", switchRoot);
$("#rescanButton").addEventListener("click", async () => { try { await api("/api/rescan", { method: "POST" }); } catch (error) { toast(error.message); } });
$("#verifyButton").addEventListener("click", async () => { closeSettings(); try { await api("/api/verify", { method: "POST" }); toast("完整校验已开始"); } catch (error) { toast(error.message); } });
$("#lockButton").addEventListener("click", () => lockVault(false));
$("#shutdownButton").addEventListener("click", async () => {
  closeSettings();
  if (confirm("确定退出 Cryptobox？")) {
    try { await api("/api/shutdown", { method: "POST" }); document.body.innerHTML = '<div class="access-card"><h1>Cryptobox 已安全退出</h1><p>现在可以关闭此页面。</p></div>'; }
    catch (error) { toast(error.message); }
  }
});
$("#downloadButton").addEventListener("click", () => { if (state.selected) window.location.href = `/api/download/${encodeURIComponent(state.selected.id)}`; });
$("#exportFolderButton").addEventListener("click", async () => {
  try {
    const result = await api("/api/export-ticket", { method: "POST", body: { ids: [state.selectedDirectory] } });
    window.location.href = result.url;
  } catch (error) { toast(error.message); }
});
$("#passwordButton").addEventListener("click", async () => {
  closeSettings();
  const first = prompt("输入新密码");
  if (!first) return;
  const second = prompt("再次输入新密码");
  if (first !== second) { toast("两次输入的密码不一致"); return; }
  try { await api("/api/password", { method: "POST", body: { new_password: first, confirmation: second } }); toast("密码已修改"); }
  catch (error) { toast(error.message); }
});

$("#settingsButton").addEventListener("click", openSettings);
$("#settingsCloseButton").addEventListener("click", closeSettings);
$("#settingsCancelButton").addEventListener("click", closeSettings);
$("#modalBackdrop").addEventListener("click", (event) => { if (event.target === $("#modalBackdrop")) closeSettings(); });
$("#settingsForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const autoLock = Number($("#autoLockMinutes").value);
  const theme = document.querySelector('input[name="theme"]:checked')?.value || "system";
  const errorNode = $("#settingsError");
  errorNode.classList.add("hidden");
  try {
    const settings = await api("/api/settings", { method: "PUT", body: { auto_lock_minutes: autoLock, theme } });
    state.settings = settings;
    applyTheme(theme);
    syncAutoLockDeadline(autoLock * 60);
    updateAutoLockCountdown();
    closeSettings();
    toast("设置已保存");
  } catch (error) { errorNode.textContent = error.message; errorNode.classList.remove("hidden"); }
});
document.querySelectorAll('input[name="theme"]').forEach((input) => input.addEventListener("change", () => {
  document.documentElement.dataset.theme = input.value === "system"
    ? (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark")
    : input.value;
}));

$("#drawerButton").addEventListener("click", openDrawer);
$("#drawerCloseButton").addEventListener("click", closeDrawer);
$("#drawerScrim").addEventListener("click", closeDrawer);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") { closeDrawer(); closeSettings(); }
});

const systemTheme = matchMedia("(prefers-color-scheme: light)");
systemTheme.addEventListener("change", () => { if (state.settings.theme === "system") applyTheme("system"); });
for (const eventName of ["pointerdown", "pointermove", "keydown", "wheel", "touchstart", "scroll"]) {
  window.addEventListener(eventName, recordActivity, { passive: true });
}
document.addEventListener("visibilitychange", () => { if (!document.hidden) autoLockCheck(); });
window.addEventListener("focus", autoLockCheck, true);
state.activityTimer = setInterval(autoLockCheck, 1000);

async function loadVersion() {
  try {
    const info = await api("/api/version");
    if ($("#appVersion") && info.version) $("#appVersion").textContent = `Cryptobox v${info.version}`;
  } catch (_) {}
}

applyTheme("system");
refreshStatus().then(loadSettings);
loadVersion();
