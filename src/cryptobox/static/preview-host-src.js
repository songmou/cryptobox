import { renderAsync as renderDocx } from "docx-preview";
import DOMPurify from "dompurify";
import JSZip from "jszip";
import MarkdownIt from "markdown-it";
import { createPptxViewer } from "pptx-vanilla-viewer";
import "pptx-vanilla-viewer/styles.css";
import * as XLSX from "xlsx";

function installEphemeralStorage(name) {
  try {
    void window[name];
    return;
  } catch (_) {
    const values = new Map();
    const storage = {
      get length() { return values.size; },
      clear() { values.clear(); },
      getItem(key) { return values.has(String(key)) ? values.get(String(key)) : null; },
      key(index) { return [...values.keys()][index] ?? null; },
      removeItem(key) { values.delete(String(key)); },
      setItem(key, value) { values.set(String(key), String(value)); },
    };
    Object.defineProperty(window, name, { configurable: true, value: storage });
  }
}

installEphemeralStorage("localStorage");
installEphemeralStorage("sessionStorage");

const root = document.querySelector("#previewRoot");
const markdown = new MarkdownIt({ html: false, linkify: true, typographer: true });
let activeViewer = null;
const objectUrls = new Set();
const MAX_PACKAGE_ENTRIES = 10000;
const MAX_PACKAGE_EXPANDED_BYTES = 250 * 1024 * 1024;

root.addEventListener("click", (event) => {
  if (event.target.closest("a")) event.preventDefault();
}, true);
root.addEventListener("submit", (event) => event.preventDefault(), true);

function clearPreview() {
  if (activeViewer?.destroy) activeViewer.destroy();
  activeViewer = null;
  for (const url of objectUrls) URL.revokeObjectURL(url);
  objectUrls.clear();
  root.replaceChildren();
}

function decodeUtf8(buffer) {
  const bytes = new Uint8Array(buffer);
  if (bytes.includes(0)) throw new Error("文件包含二进制数据，无法作为文本预览");
  return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
}

function sanitize(markup, profile = "html") {
  const clean = DOMPurify.sanitize(markup, {
    USE_PROFILES: profile === "svg" ? { svg: true, svgFilters: true } : { html: true },
    FORBID_TAGS: ["script", "iframe", "frame", "object", "embed", "form", "input", "button", "meta", "base", "link"],
    FORBID_ATTR: ["srcset", "ping", "formaction"],
  });
  const template = document.createElement("template");
  template.innerHTML = clean;
  template.content.querySelectorAll("a").forEach((link) => link.removeAttribute("href"));
  template.content.querySelectorAll("[src]").forEach((node) => {
    const value = node.getAttribute("src") || "";
    if (!value.startsWith("data:") && !value.startsWith("blob:")) node.removeAttribute("src");
  });
  template.content.querySelectorAll("[style]").forEach((node) => {
    const value = node.getAttribute("style") || "";
    if (/url\s*\(/i.test(value)) node.removeAttribute("style");
  });
  return template.content;
}

function renderText(buffer) {
  const pre = document.createElement("pre");
  pre.className = "document-text";
  pre.textContent = decodeUtf8(buffer);
  root.appendChild(pre);
}

function renderMarkdown(buffer) {
  const article = document.createElement("article");
  article.className = "document-text";
  article.appendChild(sanitize(markdown.render(decodeUtf8(buffer))));
  root.appendChild(article);
}

function renderHtml(buffer) {
  const article = document.createElement("article");
  article.className = "document-text";
  article.appendChild(sanitize(decodeUtf8(buffer)));
  root.appendChild(article);
}

function renderSvg(buffer) {
  const fragment = sanitize(decodeUtf8(buffer), "svg");
  const svg = fragment.querySelector("svg");
  if (!svg) throw new Error("文件中没有可显示的 SVG 图形");
  const blob = new Blob([svg.outerHTML], { type: "image/svg+xml" });
  const url = URL.createObjectURL(blob);
  objectUrls.add(url);
  const image = document.createElement("img");
  image.className = "svg-preview";
  image.alt = "SVG 预览";
  image.src = url;
  root.appendChild(image);
}

function showWorkbook(workbook) {
  const shell = document.createElement("section");
  shell.className = "sheet-shell";
  const tabs = document.createElement("nav");
  tabs.className = "sheet-tabs";
  const content = document.createElement("div");
  content.className = "sheet-content";

  const selectSheet = (name, button) => {
    tabs.querySelectorAll("button").forEach((item) => item.classList.toggle("active", item === button));
    content.replaceChildren(sanitize(XLSX.utils.sheet_to_html(workbook.Sheets[name]), "html"));
    const table = content.querySelector("table");
    if (table) table.classList.add("sheet-table");
  };

  workbook.SheetNames.forEach((name, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = name;
    button.addEventListener("click", () => selectSheet(name, button));
    tabs.appendChild(button);
    if (index === 0) queueMicrotask(() => selectSheet(name, button));
  });
  shell.append(tabs, content);
  root.appendChild(shell);
}

function renderTable(buffer, name) {
  const type = name.toLowerCase().endsWith(".tsv") ? "\t" : ",";
  const workbook = XLSX.read(decodeUtf8(buffer), { type: "string", FS: type });
  showWorkbook(workbook);
}

function renderSpreadsheet(buffer) {
  const workbook = XLSX.read(new Uint8Array(buffer), { type: "array", cellFormula: true, cellStyles: true });
  showWorkbook(workbook);
}

async function validatePackage(buffer) {
  const archive = await JSZip.loadAsync(buffer, { createFolders: true, checkCRC32: false });
  const entries = Object.values(archive.files);
  if (entries.length > MAX_PACKAGE_ENTRIES) {
    throw new Error(`压缩包条目超过 ${MAX_PACKAGE_ENTRIES.toLocaleString()} 个，已停止预览`);
  }
  const expandedBytes = entries.reduce((total, entry) => {
    const size = Number(entry?._data?.uncompressedSize || 0);
    return total + (Number.isFinite(size) ? size : 0);
  }, 0);
  if (expandedBytes > MAX_PACKAGE_EXPANDED_BYTES) {
    throw new Error("文档展开后超过 250 MB，已停止预览");
  }
  return archive;
}

async function renderWord(buffer) {
  await validatePackage(buffer);
  await renderDocx(buffer, root, root, {
    breakPages: true,
    ignoreLastRenderedPageBreak: false,
    renderAltChunks: false,
    renderComments: false,
    useBase64URL: false,
  });
}

async function renderPresentation(buffer, name) {
  await validatePackage(buffer);
  const host = document.createElement("div");
  host.className = "pptx-host";
  root.appendChild(host);
  await new Promise((resolve, reject) => {
    activeViewer = createPptxViewer(host, {
      source: buffer,
      fileName: name,
      editable: false,
      showToolbar: false,
      showThumbnails: true,
      onLoad: resolve,
      onError: (message) => reject(new Error(message || "演示文稿解析失败")),
    });
  });
  activeViewer.zoomToFit();
}

function archivePath(base, relative) {
  const url = new URL(relative, `https://cryptobox.invalid/${base}`);
  return decodeURIComponent(url.pathname.replace(/^\//, ""));
}

function parseXml(xml, label) {
  const documentNode = new DOMParser().parseFromString(xml, "application/xml");
  if (documentNode.querySelector("parsererror")) throw new Error(`${label} XML 无法解析`);
  return documentNode;
}

async function renderEbook(buffer) {
  const archive = await validatePackage(buffer);
  const containerEntry = archive.file("META-INF/container.xml");
  if (!containerEntry) throw new Error("EPUB 缺少 META-INF/container.xml");
  const container = parseXml(await containerEntry.async("text"), "EPUB container");
  const packagePath = container.querySelector("rootfile")?.getAttribute("full-path");
  if (!packagePath) throw new Error("EPUB 没有可读取的 package 文档");
  const packageEntry = archive.file(packagePath);
  if (!packageEntry) throw new Error("EPUB package 文档不存在");
  const packageDocument = parseXml(await packageEntry.async("text"), "EPUB package");
  const packageBase = packagePath.includes("/") ? packagePath.slice(0, packagePath.lastIndexOf("/") + 1) : "";
  const manifest = new Map();
  packageDocument.querySelectorAll("manifest item").forEach((item) => {
    const id = item.getAttribute("id");
    const href = item.getAttribute("href");
    if (id && href) manifest.set(id, archivePath(packageBase, href));
  });
  const chapters = Array.from(packageDocument.querySelectorAll("spine itemref"))
    .map((item) => manifest.get(item.getAttribute("idref")))
    .filter(Boolean);
  if (!chapters.length) throw new Error("EPUB spine 中没有可显示的章节");

  const shell = document.createElement("section");
  shell.className = "epub-shell";
  const area = document.createElement("div");
  area.className = "epub-view";
  const controls = document.createElement("nav");
  controls.className = "epub-controls";
  const previous = document.createElement("button");
  const next = document.createElement("button");
  const position = document.createElement("span");
  previous.type = next.type = "button";
  previous.textContent = "上一页";
  next.textContent = "下一页";
  controls.append(previous, position, next);
  shell.append(area, controls);
  root.appendChild(shell);

  let chapterIndex = 0;
  const showChapter = async (index) => {
    const chapterPath = chapters[index];
    const entry = archive.file(chapterPath);
    if (!entry) throw new Error(`EPUB 章节不存在：${chapterPath}`);
    const chapterDocument = new DOMParser().parseFromString(await entry.async("text"), "text/html");
    const chapterBase = chapterPath.includes("/") ? chapterPath.slice(0, chapterPath.lastIndexOf("/") + 1) : "";
    for (const image of chapterDocument.querySelectorAll("img[src]")) {
      const source = image.getAttribute("src") || "";
      if (/^(data:|https?:|\/\/)/i.test(source)) {
        if (!source.startsWith("data:")) image.removeAttribute("src");
        continue;
      }
      const imageEntry = archive.file(archivePath(chapterBase, source));
      if (!imageEntry) {
        image.removeAttribute("src");
        continue;
      }
      const extension = source.split(".").pop()?.toLowerCase();
      const type = { png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", gif: "image/gif", webp: "image/webp", svg: "image/svg+xml" }[extension] || "application/octet-stream";
      const url = URL.createObjectURL(new Blob([await imageEntry.async("uint8array")], { type }));
      objectUrls.add(url);
      image.setAttribute("src", url);
    }
    const article = document.createElement("article");
    article.className = "document-text";
    article.appendChild(sanitize(chapterDocument.body?.innerHTML || ""));
    area.replaceChildren(article);
    chapterIndex = index;
    position.textContent = `${index + 1} / ${chapters.length}`;
    previous.disabled = index === 0;
    next.disabled = index === chapters.length - 1;
  };
  previous.addEventListener("click", () => showChapter(Math.max(0, chapterIndex - 1)));
  next.addEventListener("click", () => showChapter(Math.min(chapters.length - 1, chapterIndex + 1)));
  await showChapter(0);
}

async function renderArchive(buffer) {
  const archive = await validatePackage(buffer);
  const entries = Object.values(archive.files).sort((left, right) => left.name.localeCompare(right.name));
  const list = document.createElement("section");
  list.className = "archive-list";
  const head = document.createElement("div");
  head.className = "archive-head";
  head.textContent = `${entries.length.toLocaleString()} 个条目（仅显示目录，不解压）`;
  list.appendChild(head);
  for (const entry of entries.slice(0, 5000)) {
    const row = document.createElement("div");
    row.className = "archive-row";
    const name = document.createElement("span");
    const kind = document.createElement("span");
    name.textContent = entry.name;
    kind.textContent = entry.dir ? "目录" : "文件";
    row.append(name, kind);
    list.appendChild(row);
  }
  if (entries.length > 5000) {
    const row = document.createElement("div");
    row.className = "archive-row";
    row.textContent = `其余 ${(entries.length - 5000).toLocaleString()} 个条目未显示`;
    list.appendChild(row);
  }
  root.appendChild(list);
}

async function render(message) {
  clearPreview();
  const { kind, buffer, name } = message;
  if (kind === "text" || kind === "unknown") renderText(buffer);
  else if (kind === "markdown") renderMarkdown(buffer);
  else if (kind === "html") renderHtml(buffer);
  else if (kind === "svg") renderSvg(buffer);
  else if (kind === "table") renderTable(buffer, name);
  else if (kind === "word") await renderWord(buffer);
  else if (kind === "spreadsheet") {
    await validatePackage(buffer);
    renderSpreadsheet(buffer);
  }
  else if (kind === "presentation") await renderPresentation(buffer, name);
  else if (kind === "ebook") await renderEbook(buffer);
  else if (kind === "archive") await renderArchive(buffer);
  else throw new Error("没有可用的安全预览器");
}

window.addEventListener("message", async (event) => {
  if (event.source !== window.parent || event.data?.type !== "cryptobox-preview") return;
  const { requestId } = event.data;
  try {
    await render(event.data);
    window.parent.postMessage({ type: "cryptobox-preview-complete", requestId }, "*");
  } catch (error) {
    clearPreview();
    const node = document.createElement("div");
    node.className = "preview-error";
    node.textContent = error instanceof Error ? error.message : String(error);
    root.appendChild(node);
    window.parent.postMessage({
      type: "cryptobox-preview-error",
      requestId,
      message: node.textContent,
    }, "*");
  }
});

window.parent.postMessage({ type: "cryptobox-preview-ready" }, "*");
