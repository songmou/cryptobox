const $ = (selector) => document.querySelector(selector);
const state = {
  csrf: "",
  status: null,
  currentPath: "",
  nextOffset: 0,
  history: [{ id: "", name: "根目录" }],
  selected: null,
  poll: null,
  previewController: null,
  previewCleanup: null,
  previewRequest: 0,
};

const TEXT_PREVIEW_LIMIT = 5 * 1024 * 1024;
const DOCUMENT_PREVIEW_LIMIT = 50 * 1024 * 1024;
const SANDBOX_KINDS = new Set(["text", "unknown", "markdown", "table", "html", "svg", "word", "spreadsheet", "presentation", "ebook", "archive"]);

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
}

function previewMessage(message, symbol = "⇩", kind = "") {
  const target = $("#preview");
  const box = document.createElement("div");
  box.className = `empty-state${kind ? ` ${kind}` : ""}`;
  const icon = document.createElement("span");
  const text = document.createElement("p");
  icon.textContent = symbol;
  text.textContent = message;
  box.append(icon, text);
  target.replaceChildren(box);
}

function contentUrl(entry) {
  return `/api/content/${encodeURIComponent(entry.id)}`;
}

function showNativePreview(entry, kind) {
  const target = $("#preview");
  const url = contentUrl(entry);
  let node;
  if (kind === "image") {
    node = document.createElement("img");
    node.alt = entry.name;
  } else if (kind === "video") {
    node = document.createElement("video");
    node.controls = true;
    node.preload = "metadata";
  } else if (kind === "audio") {
    node = document.createElement("audio");
    node.controls = true;
    node.preload = "metadata";
  } else {
    node = document.createElement("iframe");
    node.title = entry.name;
  }
  node.addEventListener("error", () => {
    previewMessage("浏览器无法解码此文件或媒体编码，可使用下载按钮保存后查看。", "!", "preview-warning");
  }, { once: true });
  node.src = url;
  target.replaceChildren(node);
}

function createSandboxFrame(requestId) {
  const target = $("#preview");
  const frame = document.createElement("iframe");
  frame.title = "安全文件预览";
  frame.className = "sandbox-preview";
  frame.setAttribute("sandbox", "allow-scripts");
  frame.src = "/static/preview-host.html";

  let readyResolve;
  let readyReject;
  const ready = new Promise((resolve, reject) => {
    readyResolve = resolve;
    readyReject = reject;
  });
  const timeout = setTimeout(() => readyReject(new Error("安全预览器加载超时")), 20000);
  const onMessage = (event) => {
    if (event.source !== frame.contentWindow) return;
    if (event.data?.type === "cryptobox-preview-ready") {
      clearTimeout(timeout);
      readyResolve();
    } else if (event.data?.requestId === requestId && event.data?.type === "cryptobox-preview-error") {
      toast(`无法预览：${event.data.message || "文件解析失败"}`);
    }
  };
  window.addEventListener("message", onMessage);
  state.previewCleanup = () => {
    clearTimeout(timeout);
    window.removeEventListener("message", onMessage);
    frame.src = "about:blank";
  };
  target.replaceChildren(frame);
  return { frame, ready };
}

async function showSandboxPreview(entry, kind, requestId) {
  const limit = ["text", "unknown", "markdown", "table", "html", "svg"].includes(kind)
    ? TEXT_PREVIEW_LIMIT
    : DOCUMENT_PREVIEW_LIMIT;
  if (entry.size > limit) {
    const label = limit === TEXT_PREVIEW_LIMIT ? "5 MB" : "50 MB";
    previewMessage(`文件超过 ${label} 的安全预览上限，可使用下载按钮保存。`, "⇩", "preview-warning");
    return;
  }

  const { frame, ready } = createSandboxFrame(requestId);
  state.previewController = new AbortController();
  const responsePromise = fetch(contentUrl(entry), {
    credentials: "same-origin",
    signal: state.previewController.signal,
  }).then(async (response) => {
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
      type: "cryptobox-preview",
      requestId,
      kind,
      name: entry.name,
      mediaType: entry.media_type || "application/octet-stream",
      buffer,
    }, "*", [buffer]);
  } catch (error) {
    if (error.name === "AbortError" || requestId !== state.previewRequest) return;
    previewMessage(`无法预览：${error.message}`, "!", "preview-warning");
  }
}

async function api(url, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (state.csrf && options.method && options.method !== "GET") headers["X-Cryptobox-CSRF"] = state.csrf;
  if (options.body && typeof options.body !== "string") {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(options.body);
  }
  const response = await fetch(url, { credentials: "same-origin", ...options, headers });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try { message = (await response.json()).detail || message; } catch (_) {}
    throw new Error(message);
  }
  const type = response.headers.get("content-type") || "";
  return type.includes("application/json") ? response.json() : response;
}

function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.remove("hidden");
  setTimeout(() => node.classList.add("hidden"), 3600);
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

async function refreshStatus() {
  try {
    const info = await api("/api/status");
    state.status = info;
    state.csrf = info.csrf;
    $("#rootPath").value = info.root;
    $("#rootLabel").textContent = info.root;
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
  $("#workspace").classList.add("hidden");
  $("#accessView").classList.remove("hidden");
  $("#unlockForm").classList.add("hidden");
  $("#initForm").classList.remove("hidden");
  $("#accessTitle").textContent = "创建本机保险库";
  $("#accessDescription").textContent = "确认目录后，Cryptobox 会原子加密其中的普通文件。";
  setStatusPill("等待初始化");
  loadPreview();
}

function showUnlock() {
  $("#workspace").classList.add("hidden");
  $("#accessView").classList.remove("hidden");
  $("#initForm").classList.add("hidden");
  $("#unlockForm").classList.remove("hidden");
  $("#accessTitle").textContent = "解锁你的文件";
  $("#accessDescription").textContent = "密码只用于本机解锁，不会保存到磁盘。";
  setStatusPill("已锁定");
}

function showWorkspace(info) {
  $("#accessView").classList.add("hidden");
  $("#workspace").classList.remove("hidden");
  const phase = info.operation.phase;
  setStatusPill(phase === "ready" ? "已保护" : phase === "error" ? "需要处理" : "正在处理", phase === "ready" ? "ready" : phase === "error" ? "error" : "");
  if (phase === "ready" && $("#fileList").children.length === 0) loadTree(state.currentPath);
  if (!state.poll) state.poll = setInterval(refreshStatus, 1000);
}

async function loadPreview() {
  try {
    const summary = await api("/api/init/preview");
    $("#rootSummary").innerHTML = `<span>文件 ${summary.files.toLocaleString()}</span><span>容量 ${formatBytes(summary.bytes)}</span>`;
  } catch (error) { showError(error.message); }
}

function renderOperation(operation) {
  const panel = $("#operationPanel");
  if (!["scanning", "encrypting", "verifying", "error"].includes(operation.phase)) {
    panel.classList.add("hidden");
    return;
  }
  panel.classList.remove("hidden");
  const labels = { scanning: "正在扫描目录", encrypting: "正在原子加密", verifying: "正在完整校验", error: "处理完成，但存在错误" };
  $("#operationText").textContent = labels[operation.phase] || operation.phase;
  $("#operationCount").textContent = `${operation.processed_files || 0} / ${operation.total_files || 0}`;
  const ratio = operation.total_files ? (operation.processed_files / operation.total_files) * 100 : 0;
  $("#progressBar").style.width = `${Math.min(100, ratio)}%`;
  $("#operationErrors").innerHTML = (operation.errors || []).slice(-5).map((item) => `<div>${escapeHtml(item)}</div>`).join("");
}

function renderBreadcrumbs() {
  $("#breadcrumbs").innerHTML = state.history.map((item, index) => `<button data-index="${index}">${escapeHtml(item.name)}</button>`).join("<span>/</span>");
  $("#breadcrumbs").querySelectorAll("button").forEach((button) => button.addEventListener("click", () => {
    const index = Number(button.dataset.index);
    state.history = state.history.slice(0, index + 1);
    loadTree(state.history[index].id);
  }));
}

async function loadTree(pathId = "", append = false) {
  try {
    const offset = append ? state.nextOffset : 0;
    const data = await api(`/api/tree?path_id=${encodeURIComponent(pathId)}&offset=${offset}&limit=500`);
    state.currentPath = pathId;
    state.nextOffset = data.next_offset;
    renderBreadcrumbs();
    const list = $("#fileList");
    if (!append) list.innerHTML = data.entries.length ? "" : '<div class="empty-state"><p>此目录为空</p></div>';
    for (const entry of data.entries) {
      const row = document.createElement("div");
      row.className = "file-row";
      row.innerHTML = `<span class="file-icon">${entry.kind === "directory" ? "▱" : "◇"}</span><span class="file-name">${escapeHtml(entry.name)}</span><span class="file-size">${entry.kind === "file" ? formatBytes(entry.size) : ""}</span>`;
      row.addEventListener("click", () => {
        if (entry.kind === "directory") {
          state.history.push({ id: entry.id, name: entry.name });
          loadTree(entry.id);
        } else {
          document.querySelectorAll(".file-row").forEach((node) => node.classList.remove("active"));
          row.classList.add("active");
          previewFile(entry);
        }
      });
      list.appendChild(row);
    }
    if (data.has_more) {
      const more = document.createElement("button");
      more.className = "secondary load-more";
      more.textContent = "加载更多";
      more.addEventListener("click", () => { more.remove(); loadTree(pathId, true); });
      list.appendChild(more);
    }
  } catch (error) { toast(error.message); }
}

async function previewFile(entry) {
  clearActivePreview();
  state.selected = entry;
  $("#previewTitle").textContent = entry.name;
  $("#downloadButton").disabled = false;
  const target = $("#preview");
  target.classList.remove("empty");
  const kind = entry.preview_kind || "unsupported";
  if (["image", "video", "audio", "pdf"].includes(kind)) {
    showNativePreview(entry, kind);
  } else if (SANDBOX_KINDS.has(kind)) {
    previewMessage("正在解密并准备安全预览…", "◇");
    await showSandboxPreview(entry, kind, state.previewRequest);
  } else if (["doc", "xls", "ppt"].includes(entry.name.split(".").pop().toLowerCase())) {
    previewMessage("旧版 Office 格式暂不支持网页解析，可使用下载按钮保存。", "⇩");
  } else {
    previewMessage("此格式没有可用的安全网页预览器，可使用下载按钮保存。", "⇩");
  }
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
    await api("/api/init", { method: "POST", body: {
      password: $("#initPassword").value,
      password_confirmation: $("#initConfirmation").value
    }});
    $("#initPassword").value = $("#initConfirmation").value = "";
    await refreshStatus();
  } catch (error) {
    if (/already initialized/i.test(error.message)) { await refreshStatus(); }
    else showError(error.message);
  }
});

$("#applyRoot").addEventListener("click", async () => {
  try { await api("/api/root", { method: "PUT", body: { path: $("#rootPath").value } }); await refreshStatus(); }
  catch (error) { showError(error.message); }
});
$("#rescanButton").addEventListener("click", async () => { try { await api("/api/rescan", { method: "POST" }); } catch (error) { toast(error.message); } });
$("#verifyButton").addEventListener("click", async () => { try { await api("/api/verify", { method: "POST" }); toast("完整校验已开始"); } catch (error) { toast(error.message); } });
$("#lockButton").addEventListener("click", async () => { try { clearActivePreview(); await api("/api/lock", { method: "POST" }); state.selected = null; $("#fileList").innerHTML = ""; await refreshStatus(); } catch (error) { toast(error.message); } });
$("#shutdownButton").addEventListener("click", async () => { if (confirm("确定退出 Cryptobox？")) { try { await api("/api/shutdown", { method: "POST" }); document.body.innerHTML = '<div class="access-card"><h1>Cryptobox 已安全退出</h1><p>现在可以关闭此页面。</p></div>'; } catch (error) { toast(error.message); } } });
$("#downloadButton").addEventListener("click", () => { if (state.selected) window.location.href = `/api/download/${encodeURIComponent(state.selected.id)}`; });
$("#exportFolderButton").addEventListener("click", async () => {
  try {
    const result = await api("/api/export-ticket", { method: "POST", body: { ids: [state.currentPath] } });
    window.location.href = result.url;
  } catch (error) { toast(error.message); }
});
$("#passwordButton").addEventListener("click", async () => {
  const first = prompt("输入新密码");
  if (!first) return;
  const second = prompt("再次输入新密码");
  if (first !== second) { toast("两次输入的密码不一致"); return; }
  try { await api("/api/password", { method: "POST", body: { new_password: first, confirmation: second } }); toast("密码已修改"); }
  catch (error) { toast(error.message); }
});

async function loadVersion() {
  try {
    const info = await api("/api/version");
    const node = $("#appVersion");
    if (node && info.version) node.textContent = `Cryptobox v${info.version}`;
  } catch (_) { /* version badge is best-effort */ }
}

refreshStatus();
loadVersion();
