"use strict";
const $ = (id) => document.getElementById(id);
const names = { products: "产品列表", exports: "导出记录", queue: "任务队列", logs: "日志", outputs: "输出图管理" };
const titles = { products: "全部产品", exports: "产品导出", queue: "队列记录", logs: "任务日志", outputs: "已生成图片" };
const kinds = { mockup: "样机图", subject: "透明图", detail: "详情页" };
const states = {
  draft: "草稿",
  blocked: "等待前置任务",
  pending: "待投递",
  queued: "排队中",
  running: "生成中",
  done: "已完成",
  failed: "失败",
  skipped: "已跳过",
  cancelled: "已取消",
  mockups: "生成 SKU 样机",
  subject: "生成透明图",
  detail: "制作详情页",
  removed: "输出已删除",
};
const icons = {
  archive: "M3 4h18v5H3z M5 9v11h14V9 M10 13h4",
  layers: "M3 8l9-5 9 5-9 5z M3 12l9 5 9-5 M3 16l9 5 9-5",
  queue: "M8 6h13 M8 12h13 M8 18h13 M3 6h1 M3 12h1 M3 18h1",
  logs: "M5 3h11l3 3v15H5z M8 9h8 M8 13h8 M8 17h5",
  gallery: "M3 4h18v16H3z M4 17l5-6 4 4 3-3 4 4 M15 8h1",
  pillow: "M4 5q8 2 16 0-1 7 0 14-8-2-16 0 1-7 0-14z",
  background: "M4 4h16v16H4z M4 14l10-10 M7 20L20 7 M15 20l5-5",
  upload: "M12 15V3 M7 8l5-5 5 5 M4 15v5h16v-5",
  play: "M6 3l14 9-14 9z",
  refresh: "M20 7v5h-5 M4 17v-5h5 M5 8a8 8 0 0114-1 M5 17a8 8 0 0014-1",
  plus: "M12 5v14 M5 12h14",
};
function icon(name) {
  const ns = "http://www.w3.org/2000/svg",
    svg = document.createElementNS(ns, "svg"),
    p = document.createElementNS(ns, "path");
  for (const [key, value] of Object.entries({
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    "stroke-width": "1.65",
    "stroke-linecap": "round",
    "stroke-linejoin": "round",
    "aria-hidden": "true",
  }))
    svg.setAttribute(key, value);
  p.setAttribute("d", icons[name]);
  svg.append(p);
  return svg;
}
for (const el of document.querySelectorAll("[data-icon]")) el.append(icon(el.dataset.icon));
function node(tag, text, cls) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (cls) el.className = cls;
  return el;
}
function notice(text) {
  $("notice").textContent = text;
  $("notice").hidden = !text;
  clearTimeout(notice.timer);
  notice.timer = setTimeout(() => {
    $("notice").hidden = true;
  }, 6500);
}
let token = sessionStorage.getItem("mockup-token") || "",
  authenticated = false,
  templateCount = 0,
  detailTemplate = null;
let view = "products",
  offset = 0,
  sequence = 0,
  busy = false,
  auto = true,
  theme = null,
  thumbUrls = [],
  viewerUrl,
  viewerSequence = 0,
  draft,
  draftId,
  skuId,
  openingProduct = false,
  productBusy = false,
  batchBusy = false,
  lastRecords = "";
const selected = new Set();
const deletingProducts = new Set();
const downloadingExports = new Set();
const limit = 24,
  assets = { art: null, pillow: null },
  loading = { art: false, pillow: false },
  versions = { art: 0, pillow: 0 };
const slots = [
  ["art", "A 面"],
  ["b", "B 面"],
  ["pillow", "枕套"],
  ["bg", "背景"],
];
function account(ok) {
  authenticated = ok;
  $("accessLabel").textContent = ok ? "已连接 · 访问设置" : "连接工作台";
  $("access").classList.toggle("online", ok);
  if (ok) loadStorage();
  else {
    $("diskDrive").textContent = "";
    $("diskTotal").textContent = "—";
    $("diskFree").textContent = "—";
    $("projectUsed").textContent = "—";
    $("diskFreeBar").style.width = "0%";
    $("storageBar").setAttribute("aria-valuenow", "0");
  }
}
function formatSize(bytes) {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = Number(bytes) || 0,
    index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index++;
  }
  return value.toFixed(index >= 3 ? 1 : 0) + " " + units[index];
}
async function loadStorage() {
  if (!authenticated) return;
  try {
    const data = await (await api("storage")).json();
    const percent = data.total > 0 ? Math.max(0, Math.min(100, (data.free / data.total) * 100)) : 0;
    $("diskDrive").textContent = data.drive || "";
    $("diskTotal").textContent = formatSize(data.total);
    $("diskFree").textContent = formatSize(data.free);
    $("projectUsed").textContent = formatSize(data.project_used);
    $("diskFreeBar").style.width = percent.toFixed(1) + "%";
    $("storageBar").setAttribute("aria-valuenow", String(Math.round(percent)));
    $("storage").title = "数据目录所在磁盘：" + (data.drive || "未知") + "；已占用为项目数据目录大小";
  } catch {
    $("diskTotal").textContent = "—";
    $("diskFree").textContent = "—";
    $("projectUsed").textContent = "—";
    $("diskFreeBar").style.width = "0%";
    $("storageBar").setAttribute("aria-valuenow", "0");
  }
}
async function api(path, options = {}) {
  const response = await fetch("/api/" + path, {
    ...options,
    headers: { Authorization: "Bearer " + token, ...options.headers },
  });
  if (!response.ok) {
    if (response.status === 401) account(false);
    let detail;
    try {
      detail = (await response.json()).detail;
    } catch {}
    throw Error(typeof detail === "string" ? detail : "请求失败（" + response.status + "）");
  }
  return response;
}
function login() {
  if (!$("login").open) $("login").showModal();
  $("token").focus();
}
$("access").onclick = login;
for (const close of document.querySelectorAll(".close"))
  close.onclick = () => close.closest("dialog").close();
$("loginForm").onsubmit = async (event) => {
  event.preventDefault();
  const previous = token;
  token = $("token").value.trim();
  try {
    await loadTemplates();
    sessionStorage.setItem("mockup-token", token);
    $("login").close();
    $("token").value = "";
    $("loginError").textContent = "";
    route();
  } catch (err) {
    token = previous;
    $("loginError").textContent = err.message;
  }
};
function scope() {
  $("scope").textContent = templateCount ? "每个 SKU 使用一款样机" : "没有可用的样机模板";
  $("submit").disabled = busy || loading.art || loading.pillow || (authenticated && !templateCount);
}
async function loadTemplates() {
  const data = await (await api("templates")).json();
  const current = $("template").value;
  templateCount = data.items.length;
  detailTemplate = data.detail_template;
  $("templateFiles").replaceChildren(...data.items.map((name) => new Option(name, name)));
  if (data.items.includes(current)) $("template").value = current;
  account(true);
  scope();
}
$("template").onchange = scope;
function themeOf(image) {
  const c = document.createElement("canvas"),
    scale = 96 / Math.max(image.naturalWidth, image.naturalHeight);
  c.width = Math.max(1, Math.round(image.naturalWidth * scale));
  c.height = Math.max(1, Math.round(image.naturalHeight * scale));
  const ctx = c.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(image, 0, 0, c.width, c.height);
  const data = ctx.getImageData(0, 0, c.width, c.height).data,
    bins = new Map();
  let best = null;
  for (let i = 0; i < data.length; i += 4) {
    const weight = data[i + 3] / 255;
    if (weight < 0.15) continue;
    const key = (data[i] >> 4) * 256 + (data[i + 1] >> 4) * 16 + (data[i + 2] >> 4);
    const bin = bins.get(key) || [0, 0, 0, 0];
    bins.set(key, bin);
    bin[0] += weight;
    for (let k = 0; k < 3; k++) bin[1 + k] += data[i + k] * weight;
    if (!best || bin[0] > best[0]) best = bin;
  }
  return best
    ? "#" +
        best
          .slice(1)
          .map((sum) =>
            Math.round(sum / best[0])
              .toString(16)
              .padStart(2, "0"),
          )
          .join("")
    : null;
}
function sync() {
  if (auto) $("bgColor").value = theme || "#eeeae3";
  for (const [id, label] of slots) {
    const sample = $("sample-" + id),
      caption = $("caption-" + id),
      image = id in assets;
    const asset = assets[id];
    sample.classList.toggle("empty", image && !asset);
    if (image) {
      sample.style.backgroundImage = asset ? 'url("' + asset.url + '")' : "";
      sample.style.backgroundSize = asset ? "42% auto" : "";
      sample.replaceChildren();
      if (!asset) {
        const plus = node("span");
        plus.append(icon("plus"));
        sample.append(plus);
      }
      caption.textContent = asset ? asset.file.name : "待上传";
      sample.setAttribute("aria-label", "选择" + label + "图片");
      $(id + "Name").textContent = asset ? asset.file.name : "点击或拖入" + label + "图片";
      $(id === "art" ? "clearArt" : "clearPillow").hidden = !asset;
    } else {
      const color = $(id === "b" ? "bColor" : "bgColor").value;
      sample.style.backgroundColor = color;
      caption.textContent = color.toUpperCase();
    }
  }
  $("bHex").textContent = $("bColor").value.toUpperCase();
  $("bgHex").textContent = $("bgColor").value.toUpperCase();
  $("bgHint").textContent = auto ? (theme ? "A 面主题色" : "默认背景色") : "自选颜色";
  $("bgColor").disabled = auto;
  $("autoColor").setAttribute("aria-pressed", auto);
  $("manualColor").setAttribute("aria-pressed", !auto);
  for (const b of $("swatches").children)
    b.setAttribute("aria-pressed", b.dataset.color === $("bColor").value);
  scope();
}
for (const [id, label] of slots) {
  const figure = node("figure", undefined, "tile"),
    image = id in assets,
    sample = node(image ? "button" : "div", undefined, "sample");
  sample.id = "sample-" + id;
  if (image) sample.onclick = () => $(id).click();
  const cap = node("figcaption"),
    sub = node("small");
  sub.id = "caption-" + id;
  cap.append(node("b", label), sub);
  figure.append(sample, cap);
  $("tiles").append(figure);
}
const swatches = $("swatches").children;
if (swatches.length) $("bColor").value = swatches[0].dataset.color;
for (const b of swatches) {
  const color = b.dataset.color;
  b.style.backgroundColor = color;
  b.onclick = () => {
    $("bColor").value = color;
    sync();
  };
}
function clearImage(id) {
  versions[id]++;
  loading[id] = false;
  if (assets[id]) URL.revokeObjectURL(assets[id].url);
  assets[id] = null;
  $(id).value = "";
  if (id === "art") theme = null;
}
for (const id of Object.keys(assets)) {
  const input = $(id);
  const dropZone = input.closest(".file");
  async function loadFile(file) {
    if (!file || busy) return;
    const version = ++versions[id];
    loading[id] = true;
    scope();
    let url;
    try {
      if (file.size > 50 * 1024 * 1024 || !["image/png", "image/jpeg"].includes(file.type))
        throw Error("请选择 50 MB 以内的 PNG 或 JPEG 图片");
      url = URL.createObjectURL(file);
      const img = new Image();
      img.src = url;
      await img.decode();
      if (version !== versions[id]) return;
      const nextTheme = id === "art" ? themeOf(img) : null;
      if (assets[id]) URL.revokeObjectURL(assets[id].url);
      assets[id] = { file, url };
      url = null;
      if (id === "art") theme = nextTheme;
    } catch (err) {
      notice(err.message || "无法读取图片");
    } finally {
      if (url) URL.revokeObjectURL(url);
      if (version === versions[id]) {
        loading[id] = false;
        $(id).value = "";
        sync();
      }
    }
  }
  input.onchange = () => loadFile(input.files[0]);
  dropZone.ondragover = (event) => {
    if (!event.dataTransfer.types.includes("Files")) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = busy ? "none" : "copy";
    dropZone.classList.toggle("dragging", !busy);
  };
  dropZone.ondragleave = (event) => {
    if (!dropZone.contains(event.relatedTarget)) dropZone.classList.remove("dragging");
  };
  dropZone.ondrop = (event) => {
    event.preventDefault();
    dropZone.classList.remove("dragging");
    if (busy) return;
    const files = event.dataTransfer.files;
    if (files.length !== 1) return notice("每个图层请拖入一张图片");
    loadFile(files[0]);
  };
  $(id === "art" ? "clearArt" : "clearPillow").onclick = () => {
    clearImage(id);
    sync();
  };
}
$("bColor").oninput = sync;
$("bgColor").oninput = sync;
$("autoColor").onclick = () => {
  auto = true;
  sync();
};
$("manualColor").onclick = () => {
  auto = false;
  sync();
};
function newId() {
  // getRandomValues also works when the studio is opened over its LAN HTTP address.
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
}
function jsonOptions(method, payload) {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) };
}
function skuList() {
  const items = draft?.skus || [];
  $("skuCount").textContent = items.length + " 项";
  if (!items.length) {
    $("skuList").replaceChildren(node("p", "还没有添加产品图", "empty-skus"));
  } else {
    const list = node("ul", undefined, "sku-list");
    for (const sku of items) {
      const row = node("li"), label = node("div");
      label.append(node("b", sku.name), node("small", sku.template));
      row.append(label, state(sku.status));
      if (sku.status === "failed" || sku.status === "skipped")
        row.append(node("p", sku.message, "row-detail"));
      list.append(row);
    }
    $("skuList").replaceChildren(list);
  }
  $("saveProduct").disabled = productBusy || !items.length;
  $("addSku").disabled = productBusy;
  $("productName").readOnly = productBusy;
  $("productDialog").querySelector(".close").disabled = productBusy;
  $("productHint").textContent = detailTemplate || !items.length
    ? "提交 SKU 后立即排队出图。"
    : "保存前请配置详情页模板。";
}
async function openProduct(id) {
  if (openingProduct || $("productDialog").open) return;
  if (!authenticated) return login();
  openingProduct = true;
  try {
    if (id) {
      draft = await (await api("products/" + id)).json();
      if (draft.saved_at) return refresh();
    } else {
      draft = null;
    }
    draftId = draft?.id || newId();
    $("productName").value = draft?.name || "";
    $("productTitle").textContent = draft ? "继续配置产品" : "新增产品";
    $("productError").textContent = "";
    skuList();
    if (!$("productDialog").open) $("productDialog").showModal();
  } catch (err) {
    notice(err.message);
  } finally {
    openingProduct = false;
  }
}
$("newProduct").onclick = () => openProduct();
$("productDialog").addEventListener("cancel", (event) => {
  if (productBusy || busy) event.preventDefault();
});
$("generate").addEventListener("cancel", (event) => {
  if (busy) event.preventDefault();
});
$("productDialog").onclose = () => {
  draft = null;
  if (view === "products") refresh();
};
$("addSku").onclick = async () => {
  if (!$("productName").reportValidity()) return;
  if (!authenticated) return login();
  productBusy = true;
  $("productError").textContent = "";
  skuList();
  try {
    await loadTemplates();
    if (!draft)
      draft = await (await api("products/" + draftId + "/draft",
        jsonOptions("PUT", { name: $("productName").value }))).json();
    if (draft.saved_at) throw Error("产品已保存，请刷新产品列表");
    skuId = newId();
    for (const id of Object.keys(assets)) clearImage(id);
    $("skuName").value = "";
    $("template").value = "random";
    $("bColor").value = swatches[0]?.dataset.color || "#a4b6c9";
    $("skuError").textContent = "";
    auto = true;
    sync();
    $("generate").showModal();
    $("skuName").focus();
  } catch (err) {
    $("productError").textContent = err.message;
  } finally {
    productBusy = false;
    skuList();
  }
};
$("productForm").onsubmit = async (event) => {
  event.preventDefault();
  if (!draft?.skus.length || productBusy) return;
  if (!authenticated) return login();
  productBusy = true;
  $("productError").textContent = "";
  skuList();
  try {
    await api("products/" + draft.id + "/save", jsonOptions("PUT", { name: $("productName").value }));
    $("productDialog").close();
    if (view !== "products") location.hash = "products";
    notice("产品已保存");
  } catch (err) {
    $("productError").textContent = err.message;
  } finally {
    productBusy = false;
    skuList();
  }
};
async function refreshDraft() {
  if (!draft || !$("productDialog").open || $("generate").open || productBusy) return;
  const current = draft;
  try {
    const next = await (await api("products/" + current.id)).json();
    if (draft === current && $("productDialog").open && !$("generate").open && !productBusy) {
      draft = next;
      skuList();
    }
  } catch {}
}
$("submit").onclick = async () => {
  if (!$("skuName").reportValidity() || busy) return;
  if (!authenticated) return login();
  busy = true;
  $("generate").inert = true;
  $("skuError").textContent = "";
  scope();
  const form = new FormData();
  form.set("sku_name", $("skuName").value);
  form.set("template", $("template").value);
  if (assets.art) form.set("a_image", assets.art.file);
  if (assets.pillow) form.set("pillow_image", assets.pillow.file);
  form.set("b_color", $("bColor").value);
  form.set("background_color", $("bgColor").value);
  try {
    draft = await (await api("products/" + draft.id + "/skus/" + skuId,
      { method: "POST", body: form })).json();
    $("generate").close();
    skuList();
  } catch (err) {
    $("skuError").textContent = err.message;
  } finally {
    busy = false;
    $("generate").inert = false;
    scope();
  }
};
$("generate").onclose = () => {
  for (const id of Object.keys(assets)) clearImage(id);
  sync();
  refreshDraft();
};
$("skuName").onkeydown = (event) => {
  if (event.key === "Enter" && !$("submit").disabled) $("submit").click();
};
function date(value) {
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}
function state(value, log = false) {
  return node(
    "span",
    (log ? { info: "信息", warning: "提示", error: "错误" } : states)[value] || value,
    "state " + value,
  );
}
function syncSelection() {
  const visible = [...document.querySelectorAll("[data-product-select]:not(:disabled)")];
  for (const checkbox of visible) checkbox.checked = selected.has(checkbox.dataset.productSelect);
  const header = $("selectPage");
  if (header) {
    const checked = visible.filter((checkbox) => checkbox.checked).length;
    header.checked = !!checked && checked === visible.length;
    header.indeterminate = checked > 0 && checked < visible.length;
    header.disabled = !visible.length;
  }
  $("exportSelected").disabled = !selected.size || batchBusy;
  $("exportSelected").textContent = selected.size ? `批量导出（${selected.size}）` : "批量导出";
}
const renameDialog = $("renameDialog");
let renameId;
function openRename(row) {
  if (renameDialog.open) return;
  renameId = row.id;
  $("renameName").value = row.name;
  $("renameError").textContent = "";
  renameDialog.showModal();
  $("renameName").select();
}
$("cancelRename").onclick = () => renameDialog.close();
renameDialog.addEventListener("cancel", (event) => {
  if ($("saveRename").disabled) event.preventDefault();
});
$("renameForm").onsubmit = async (event) => {
  event.preventDefault();
  const save = $("saveRename");
  if (save.disabled) return;
  const controls = [$("cancelRename"), renameDialog.querySelector(".close"), save];
  controls.forEach((button) => { button.disabled = true; });
  $("renameName").readOnly = true;
  $("renameError").textContent = "";
  try {
    await api("products/" + renameId, jsonOptions("PATCH", { name: $("renameName").value }));
    renameDialog.close();
    notice("产品名已更新");
    refresh();
  } catch (err) {
    $("renameError").textContent = err.message;
  } finally {
    controls.forEach((button) => { button.disabled = false; });
    $("renameName").readOnly = false;
  }
};
async function removeProduct(row, button) {
  if (deletingProducts.has(row.id) || !confirm(
    `确定删除「${row.name}」？排队任务将被取消，已生成的图片会被删除。此操作无法撤销。`,
  )) return;
  deletingProducts.add(row.id);
  button.disabled = true;
  try {
    const result = await (await api("products/" + row.id, { method: "DELETE" })).json();
    selected.delete(row.id);
    syncSelection();
    notice(result.cleanup_pending ? "产品已删除，占用中的文件将在服务重启后重试清理" : "产品已删除");
    await refresh();
    loadStorage();
  } catch (err) {
    notice(err.message);
  } finally {
    deletingProducts.delete(row.id);
    button.disabled = false;
    for (const current of document.querySelectorAll("[data-product-delete]")) {
      if (current.dataset.productDelete === row.id) current.disabled = false;
    }
  }
}
function makeProducts(items) {
  const wrap = node("div", undefined, "table-scroll");
  const table = node("table", undefined, "product-table");
  const head = table.createTHead().insertRow();
  const all = node("input");
  all.id = "selectPage";
  all.type = "checkbox";
  all.setAttribute("aria-label", "选择本页可导出产品");
  all.onchange = () => {
    for (const row of items.filter((item) => item.downloadable))
      all.checked ? selected.add(row.id) : selected.delete(row.id);
    syncSelection();
  };
  const checkHead = node("th", undefined, "check-cell");
  checkHead.append(all);
  head.append(checkHead);
  for (const label of ["产品名", "SKU 进度", "状态", "创建时间", "下载时间", "操作"])
    head.append(node("th", label));
  const body = table.createTBody();
  for (const row of items) {
    const tr = body.insertRow();
    const checkbox = node("input");
    checkbox.type = "checkbox";
    checkbox.disabled = !row.downloadable;
    checkbox.dataset.productSelect = row.id;
    checkbox.setAttribute("aria-label", "选择 " + row.name);
    checkbox.onchange = () => {
      checkbox.checked ? selected.add(row.id) : selected.delete(row.id);
      syncSelection();
    };
    if (!row.downloadable) selected.delete(row.id);
    const identity = node("div", undefined, "product-identity");
    const cover = node("div", undefined, "product-cover");
    const fallback = node("span", "暂无缩略图", "fine");
    cover.append(fallback);
    if (row.thumbnail_job_id) {
      const image = node("img");
      image.alt = row.name + "的首张 SKU 样机图";
      image.hidden = true;
      image.onload = () => {
        fallback.hidden = true;
        image.hidden = false;
      };
      cover.append(image);
      loadThumbnail(image, row.thumbnail_job_id).catch(() => {});
    }
    identity.append(cover, node("b", row.name));
    const downloaded = row.downloaded_at
      ? node("time", date(row.downloaded_at))
      : node("span", "未下载", "fine");
    if (row.downloaded_at) downloaded.dateTime = row.downloaded_at;
    const status = node("div");
    status.append(state(row.status));
    if (row.message) status.append(node("small", row.message, "row-detail"));
    const action = node("button", row.status === "draft" ? "继续配置" : "导出", "textbtn row-action");
    action.disabled = row.status !== "draft" && !row.downloadable;
    action.onclick = async () => {
      if (row.status === "draft") return openProduct(row.id);
      action.disabled = true;
      try {
        await requestExport([row.id]);
      } catch (err) {
        notice(err.message);
      } finally {
        action.disabled = false;
      }
    };
    const actions = node("div", undefined, "product-actions");
    const rename = node("button", "修改名称", "textbtn row-action");
    rename.onclick = () => openRename(row);
    const remove = node("button", "删除", "textbtn row-action danger");
    remove.dataset.productDelete = row.id;
    remove.setAttribute("aria-label", "删除 " + row.name);
    remove.disabled = deletingProducts.has(row.id);
    remove.onclick = () => removeProduct(row, remove);
    actions.append(action, rename, remove);
    for (const value of [checkbox, identity, `${row.sku_done} / ${row.sku_count}`,
      status, date(row.created_at), downloaded, actions]) tr.insertCell().append(value);
    tr.cells[0].className = "check-cell";
  }
  wrap.append(table);
  return wrap;
}
async function requestExport(ids) {
  await api("exports", jsonOptions("POST", { ids }));
  ids.forEach((id) => selected.delete(id));
  syncSelection();
  notice("导出已提交，打包完成后可在导出记录下载");
  if (view === "exports") {
    offset = 0;
    refresh();
  } else {
    location.hash = "exports";
  }
}
$("exportSelected").onclick = async () => {
  if (batchBusy || !selected.size) return;
  batchBusy = true;
  syncSelection();
  try {
    await requestExport([...selected]);
  } catch (err) {
    notice(err.message);
  } finally {
    batchBusy = false;
    syncSelection();
  }
};
function makeExportTable(items) {
  const wrap = node("div", undefined, "table-scroll");
  const table = node("table", undefined, "export-table");
  const head = table.createTHead().insertRow();
  for (const label of ["导出文件", "产品数量", "状态", "提交时间", "导出时间", "下载时间", "文件大小", "操作"])
    head.append(node("th", label));
  const body = table.createTBody();
  const labels = { pending: "等待打包", running: "打包中", done: "可下载", failed: "打包失败" };
  for (const row of items) {
    const tr = body.insertRow();
    const status = node("div");
    status.append(node("span", labels[row.status], "state " + row.status));
    if (row.error) status.append(node("small", row.error, "row-detail"));
    const download = node("button", "下载", "textbtn row-action");
    download.disabled = row.status !== "done" || downloadingExports.has(row.id);
    download.dataset.exportDownload = row.id;
    download.onclick = async () => {
      if (downloadingExports.has(row.id)) return;
      downloadingExports.add(row.id);
      download.disabled = true;
      try {
        await downloadFile(await api("exports/" + row.id + "/download"), row.filename);
        if (view === "exports") await refresh();
      } catch (err) {
        notice(err.message);
      } finally {
        downloadingExports.delete(row.id);
        for (const button of document.querySelectorAll("[data-export-download]")) {
          if (button.dataset.exportDownload === row.id) button.disabled = false;
        }
      }
    };
    for (const value of [node("b", row.filename), String(row.product_count), status, date(row.created_at),
      row.status === "done" ? date(row.finished_at) : "—",
      row.downloaded_at ? date(row.downloaded_at) : node("span", "未下载", "fine"),
      row.size == null ? "—" : formatSize(row.size), download]) tr.insertCell().append(value);
  }
  wrap.append(table);
  return wrap;
}
function makeTable(items, log) {
  const wrap = node("div", undefined, "table-scroll"),
    table = node("table"),
    head = table.createTHead().insertRow(),
    body = table.createTBody();
  for (const title of log
    ? ["日期", "级别", "产品", "任务类型", "任务 ID", "日志"]
    : ["产品", "任务", "模板", "状态", "提交时间"])
    head.append(node("th", title));
  for (const row of items) {
    const tr = body.insertRow(),
      id = node("span", log ? row.job_id || "—" : row.id, "mono");
    const name = kinds[row.kind] + (row.sku_name ? " · " + row.sku_name : "");
    const task = node("div");
    task.append(node("b", name), id);
    const status = node("div");
    if (!log) {
      status.append(state(row.status));
      if (["failed", "skipped", "blocked", "cancelled"].includes(row.status))
        status.append(node("small", row.detail, "row-detail"));
    }
    const values = log
      ? [date(row.at), state(row.level, true), row.product_name || "—", kinds[row.kind] || "—",
        id, node("div", row.message, "log-message")]
      : [row.product_name || "—", task, row.template, status, date(row.created_at)];
    for (const value of values) {
      const cell = tr.insertCell();
      cell.append(value);
    }
    tr.title = row.detail || "";
  }
  wrap.append(table);
  return wrap;
}
async function downloadFile(response, fallbackName) {
  const url = URL.createObjectURL(await response.blob());
  const a = node("a");
  a.href = url;
  const disposition = response.headers.get("Content-Disposition") || "";
  const encoded = disposition.match(/filename\*=utf-8''([^;]+)/i);
  const quoted = disposition.match(/filename="([^"]+)"/i);
  a.download = encoded ? decodeURIComponent(encoded[1]) : quoted?.[1] || fallbackName;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}
async function original(row, kind, slice = null) {
  const seq = kind === "image" ? ++viewerSequence : null;
  try {
    const query = slice ? "?slice_name=" + encodeURIComponent(slice) : "";
    const response = await api("outputs/" + row.id + "/" + kind + query);
    if (kind !== "image") {
      await downloadFile(response, outputName(row));
    } else {
      const image = await response.blob();
      if (seq !== viewerSequence) return;
      if (viewerUrl) URL.revokeObjectURL(viewerUrl);
      viewerUrl = URL.createObjectURL(image);
      $("fullImage").src = viewerUrl;
      $("viewerTitle").textContent = row.kind === "detail" ? (row.product_name || "详情页") : outputName(row);
      $("slicePicker").hidden = !row.slices?.length;
      if (row.slices?.length) {
        $("sliceSelect").replaceChildren(...row.slices.map((name) => new Option(name, name)));
        $("sliceSelect").value = slice || row.slices[0];
        $("sliceSelect").onchange = () => original(row, kind, $("sliceSelect").value);
      }
      if (!$("viewer").open) $("viewer").showModal();
    }
  } catch (err) {
    notice(err.message);
  }
}
$("viewer").onclose = () => {
  viewerSequence++;
  if (viewerUrl) URL.revokeObjectURL(viewerUrl);
  viewerUrl = null;
  $("fullImage").removeAttribute("src");
};
function outputName(row) {
  return row.download_name || row.template;
}
async function loadThumbnail(img, jobId) {
  const response = await api("outputs/" + jobId + "/thumbnail");
  const blob = await response.blob();
  if (!img.isConnected) return;
  const url = URL.createObjectURL(blob);
  img.src = url;
  thumbUrls.push(url);
}
function gallery(items) {
  const grid = node("div", undefined, "outputs");
  for (const row of items) {
    const name = outputName(row),
      card = node("article", undefined, "output-card"),
      preview = node("button"),
      img = node("img");
    img.alt = name;
    preview.append(img);
    preview.onclick = () => original(row, "image");
    const actions = node("div", undefined, "output-actions"),
      download = node("button", row.kind === "detail" ? "下载切片 ZIP" : "下载 PNG", "textbtn"),
      remove = node("button", "删除", "textbtn danger");
    download.onclick = () => original(row, "download");
    remove.setAttribute("aria-label", "删除 " + name);
    remove.onclick = () => removeOutput(row, remove);
    actions.append(download);
    if (row.has_transparent) {
      const subject = node("button", "下载透明图", "textbtn");
      subject.onclick = () => original(row, "transparent");
      actions.append(subject);
    }
    remove.disabled = !row.can_delete;
    if (remove.disabled) remove.title = "产品任务结束后可以删除";
    actions.append(remove);
    const context = [row.product_name, kinds[row.kind], row.template, date(row.updated_at)].filter(Boolean);
    card.append(preview, node("b", name), node("small", context.join(" · ")), actions);
    grid.append(card);
    loadThumbnail(img, row.id).catch(() => {
      img.alt = "缩略图加载失败";
    });
  }
  return grid;
}
async function removeOutput(row, button) {
  if (!confirm("确定删除「" + outputName(row) + "」的输出图吗？删除后无法恢复。")) return;
  button.disabled = true;
  try {
    await api("outputs/" + row.id, { method: "DELETE" });
    notice("已删除输出图");
    await refresh();
    loadStorage();
  } catch (err) {
    notice(err.message);
    button.disabled = false;
  }
}
function releaseThumbs() {
  thumbUrls.forEach((url) => URL.revokeObjectURL(url));
  thumbUrls = [];
}
async function refresh() {
  const seq = ++sequence;
  if (!authenticated) {
    lastRecords = "";
    releaseThumbs();
    $("recordsBody").replaceChildren(node("p", "连接工作台后查看记录", "empty-list"));
    $("pager").hidden = true;
    $("recordCount").textContent = "";
    return;
  }
  try {
    const endpoint = view === "queue" ? "jobs" : view;
    const data = await (await api(endpoint + "?offset=" + offset + "&limit=" + limit)).json();
    if (seq !== sequence) return;
    if (view === "products" || view === "exports") {
      if (offset > 0 && offset >= data.total) {
        offset = Math.max(0, Math.ceil(data.total / limit) - 1) * limit;
        return refresh();
      }
      const current = JSON.stringify([offset, data]);
      if (current === lastRecords) return;
      lastRecords = current;
    }
    releaseThumbs();
    $("recordCount").textContent = data.total + " 条";
    const result = data.items.length
      ? view === "outputs"
        ? gallery(data.items)
        : view === "products" ? makeProducts(data.items)
          : view === "exports" ? makeExportTable(data.items) : makeTable(data.items, view === "logs")
      : node("p", { products: "点击「新增产品」开始添加 SKU", exports: "暂无导出记录",
        outputs: "任务完成后，输出图将显示在这里" }[view]
        || "暂无记录", "empty-list");
    $("recordsBody").replaceChildren(result);
    syncSelection();
    $("pager").hidden = data.total <= limit;
    $("prev").disabled = offset === 0;
    $("next").disabled = offset + limit >= data.total;
    $("pageNumber").textContent =
      1 + Math.floor(offset / limit) + " / " + Math.max(1, Math.ceil(data.total / limit));
  } catch (err) {
    if (seq === sequence) {
      lastRecords = "";
      releaseThumbs();
      $("recordsBody").replaceChildren(node("p", err.message, "empty-list"));
      $("pager").hidden = true;
    }
  }
}
function route() {
  const hash = location.hash.slice(1);
  view = Object.hasOwn(names, hash) ? hash : "products";
  offset = 0;
  sequence++;
  lastRecords = "";
  releaseThumbs();
  $("newProduct").hidden = view !== "products";
  $("exportSelected").hidden = view !== "products";
  $("crumb").textContent = names[view];
  $("pageTitle").textContent = names[view];
  $("recordTitle").textContent = titles[view] || "";
  for (const a of document.querySelectorAll("[data-view]")) {
    if (a.dataset.view === view) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
  $("recordsBody").replaceChildren();
  $("recordCount").textContent = "";
  $("pager").hidden = true;
  refresh();
}
$("prev").onclick = () => {
  offset = Math.max(0, offset - limit);
  refresh();
};
$("next").onclick = () => {
  offset += limit;
  refresh();
};
$("refresh").onclick = refresh;
window.addEventListener("hashchange", route);
$("today").textContent = new Date().toLocaleDateString("zh-CN", {
  year: "numeric",
  month: "long",
  day: "numeric",
  weekday: "short",
});
sync();
route();
if (token)
  loadTemplates()
    .then(() => refresh())
    .catch((err) => notice(err.message));
setInterval(() => {
  if (authenticated && !document.hidden) {
    if (view !== "outputs") refresh();
    refreshDraft();
  }
}, 5000);
setInterval(() => {
  if (authenticated) loadStorage();
}, 60000);
