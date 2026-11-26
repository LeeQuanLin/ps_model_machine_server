"use strict";
const $ = (id) => document.getElementById(id);
const names = { generate: "生成样机", queue: "任务队列", logs: "运行日志", outputs: "输出图" };
const titles = { queue: "队列记录", logs: "任务日志", outputs: "已生成图片" };
const states = {
  pending: "待投递",
  queued: "排队中",
  running: "生成中",
  done: "已完成",
  failed: "失败",
  skipped: "已跳过",
};
const icons = {
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
  templateCount = 0;
let view = "generate",
  offset = 0,
  sequence = 0,
  busy = false,
  auto = true,
  theme = null,
  thumbUrls = [],
  viewerUrl;
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
  $("scope").textContent = $("template").value
    ? "本次生成 1 个样机"
    : authenticated
      ? "本次生成 " + templateCount + " 个样机"
      : "连接后可选择样机";
  $("submit").disabled = busy || loading.art || loading.pillow || (authenticated && !templateCount);
}
async function loadTemplates() {
  const data = await (await api("templates")).json(),
    current = $("template").value;
  templateCount = data.items.length;
  $("template").replaceChildren(new Option("全部样机", ""));
  for (const name of data.items) $("template").add(new Option(name, name));
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
      $(id + "Name").textContent = asset ? asset.file.name : "选择" + label + "图片";
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
for (const [name, color] of [
  ["雾蓝", "#a4b6c9"],
  ["米白", "#e9e2d4"],
  ["豆绿", "#a3b59c"],
  ["藕粉", "#d7b0ac"],
  ["灰紫", "#b9afc5"],
  ["炭灰", "#535b60"],
]) {
  const b = node("button", undefined, "swatch");
  b.style.backgroundColor = color;
  b.dataset.color = color;
  b.title = name;
  b.setAttribute("aria-label", name);
  b.onclick = () => {
    $("bColor").value = color;
    sync();
  };
  $("swatches").append(b);
}
for (const id of Object.keys(assets)) {
  $(id).onchange = async () => {
    const file = $(id).files[0];
    if (!file) return;
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
  };
  $(id === "art" ? "clearArt" : "clearPillow").onclick = () => {
    versions[id]++;
    loading[id] = false;
    if (assets[id]) URL.revokeObjectURL(assets[id].url);
    assets[id] = null;
    $(id).value = "";
    if (id === "art") theme = null;
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
$("submit").onclick = async () => {
  if (!authenticated) {
    login();
    return;
  }
  busy = true;
  scope();
  const form = new FormData();
  if ($("template").value) form.set("template", $("template").value);
  if (assets.art) form.set("a_image", assets.art.file);
  if (assets.pillow) form.set("pillow_image", assets.pillow.file);
  form.set("b_color", $("bColor").value);
  form.set("background_color", $("bgColor").value);
  try {
    const data = await (await api("jobs", { method: "POST", body: form })).json();
    location.hash = "queue";
    notice("已提交 " + data.count + " 个样机任务");
  } catch (err) {
    notice(err.message);
    if (!authenticated) login();
  } finally {
    busy = false;
    scope();
  }
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
function makeTable(items, log) {
  const wrap = node("div", undefined, "table-scroll"),
    table = node("table"),
    head = table.createTHead().insertRow(),
    body = table.createTBody();
  for (const title of log
    ? ["日期", "级别", "样机名", "任务 ID", "日志"]
    : ["任务 ID", "样机名", "状态", "提交日期"])
    head.append(node("th", title));
  for (const row of items) {
    const tr = body.insertRow(),
      id = node("span", log ? row.job_id : row.id, "mono");
    const values = log
      ? [date(row.at), state(row.level, true), row.template, id, row.message]
      : [id, row.template, state(row.status), date(row.created_at)];
    for (const value of values) {
      const cell = tr.insertCell();
      cell.append(value);
    }
    tr.title = row.detail || "";
  }
  wrap.append(table);
  return wrap;
}
async function original(row, download) {
  try {
    const response = await api("outputs/" + row.id + "/" + (download ? "download" : "image"));
    const url = URL.createObjectURL(await response.blob());
    if (download) {
      const a = node("a");
      a.href = url;
      a.download =
        row.template
          .split("/")
          .pop()
          .replace(/\.[^.]+$/, "") +
        "-" +
        row.id.slice(0, 8) +
        ".png";
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 30000);
    } else {
      if (viewerUrl) URL.revokeObjectURL(viewerUrl);
      viewerUrl = url;
      $("fullImage").src = url;
      $("viewerTitle").textContent = row.template;
      if (!$("viewer").open) $("viewer").showModal();
    }
  } catch (err) {
    notice(err.message);
  }
}
$("viewer").onclose = () => {
  if (viewerUrl) URL.revokeObjectURL(viewerUrl);
  viewerUrl = null;
  $("fullImage").removeAttribute("src");
};
function gallery(items, seq) {
  const grid = node("div", undefined, "outputs");
  for (const row of items) {
    const card = node("article", undefined, "output-card"),
      preview = node("button"),
      img = node("img");
    img.alt = row.template;
    preview.append(img);
    preview.onclick = () => original(row, false);
    const download = node("button", "下载 PNG", "textbtn");
    download.onclick = () => original(row, true);
    card.append(preview, node("b", row.template), node("small", date(row.updated_at)), download);
    grid.append(card);
    api("outputs/" + row.id + "/thumbnail")
      .then((r) => r.blob())
      .then((blob) => {
        if (seq !== sequence) return;
        const url = URL.createObjectURL(blob);
        img.src = url;
        thumbUrls.push(url);
      })
      .catch(() => {
        img.alt = "缩略图加载失败";
      });
  }
  return grid;
}
function releaseThumbs() {
  thumbUrls.forEach((url) => URL.revokeObjectURL(url));
  thumbUrls = [];
}
async function refresh() {
  if (view === "generate") return;
  const seq = ++sequence;
  if (!authenticated) {
    $("recordsBody").replaceChildren(node("p", "连接工作台后查看记录", "empty-list"));
    $("pager").hidden = true;
    $("recordCount").textContent = "";
    return;
  }
  try {
    const endpoint = view === "queue" ? "jobs" : view;
    const data = await (await api(endpoint + "?offset=" + offset + "&limit=" + limit)).json();
    if (seq !== sequence) return;
    releaseThumbs();
    $("recordCount").textContent = data.total + " 条";
    const result = data.items.length
      ? view === "outputs"
        ? gallery(data.items, seq)
        : makeTable(data.items, view === "logs")
      : node("p", view === "outputs" ? "任务完成后，输出图将显示在这里" : "暂无记录", "empty-list");
    $("recordsBody").replaceChildren(result);
    $("pager").hidden = data.total <= limit;
    $("prev").disabled = offset === 0;
    $("next").disabled = offset + limit >= data.total;
    $("pageNumber").textContent =
      1 + Math.floor(offset / limit) + " / " + Math.max(1, Math.ceil(data.total / limit));
  } catch (err) {
    if (seq === sequence) {
      $("recordsBody").replaceChildren(node("p", err.message, "empty-list"));
      $("pager").hidden = true;
    }
  }
}
function route() {
  const hash = location.hash.slice(1);
  view = Object.hasOwn(names, hash) ? hash : "generate";
  offset = 0;
  sequence++;
  releaseThumbs();
  $("generate").hidden = view !== "generate";
  $("records").hidden = view === "generate";
  $("crumb").textContent = names[view];
  $("pageTitle").textContent = names[view];
  $("recordTitle").textContent = titles[view] || "";
  for (const a of document.querySelectorAll("[data-view]")) {
    if (a.dataset.view === view) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
  if (view !== "generate") {
    $("recordsBody").replaceChildren();
    $("recordCount").textContent = "";
    $("pager").hidden = true;
    refresh();
  } else if (authenticated) loadTemplates().catch((err) => notice(err.message));
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
  if (authenticated && !document.hidden && (view === "queue" || view === "logs")) refresh();
}, 5000);
