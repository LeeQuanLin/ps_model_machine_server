const $ = (id) => document.getElementById(id);
const pageNames = {create: "生成样机", templates: "样机管理", renders: "生成图"};
const statusNames = {queued: "排队中", running: "生成中", completed: "已完成", failed: "失败"};
let token = sessionStorage.getItem("mockup.token") || "";
let templates = [];
let renders = [];
let artwork = null;
let activePage = "create";
let refreshingRenders = false;
let refreshingTemplates = false;
let submittingRender = false;
let toastTimer;

function node(tag, className, text) {
    const element = document.createElement(tag);
    element.className = className || "";
    if (text != null) element.textContent = text;
    return element;
}

function action(text, handler, className = "text-button") {
    const button = node("button", className, text);
    button.type = "button";
    button.addEventListener("click", () => handle(() => handler(button)));
    return button;
}

function query(path, params) {
    return `${path}?${new URLSearchParams(params)}`;
}

async function api(path, {method = "GET", body, accessToken = token} = {}) {
    const headers = accessToken ? {Authorization: `Bearer ${accessToken}`} : {};
    const response = await fetch(path, {method, body, headers, cache: "no-store"});
    if (!response.ok) {
        const message = await response.json().catch(() => ({}));
        if (response.status === 401) throw Error("API Token 无效，请重新连接服务。");
        throw Error(typeof message.detail === "string" ? message.detail : `请求失败（${response.status}）。`);
    }
    return response;
}

function notify(message, error = false) {
    const toast = $("toast");
    clearTimeout(toastTimer);
    toast.textContent = message;
    toast.classList.toggle("error", error);
    toast.hidden = false;
    toastTimer = setTimeout(() => { toast.hidden = true; }, error ? 7000 : 3800);
}

async function handle(operation) {
    try { return await operation(); }
    catch (error) { notify(error.message || "无法连接服务。", true); }
}

function requireConnection() {
    if (token) return true;
    $("connection").click();
    return false;
}

function connectionStatus() {
    $("connection").classList.toggle("connected", Boolean(token));
    $("connection-text").textContent = token ? "服务已连接" : "连接服务";
}

function formatBytes(bytes) {
    if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
    return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function dimensions(item) {
    return `${item.width} × ${item.height} px`;
}

async function showImage(container, path, name, fallback = "无法读取预览") {
    if (!path) return;
    const marker = Symbol();
    container.imageRequest = marker;
    try {
        const blob = await (await api(path)).blob();
        if (container.imageRequest !== marker || !container.isConnected) return;
        const image = node("img");
        image.alt = name;
        const url = URL.createObjectURL(blob);
        image.addEventListener("load", () => URL.revokeObjectURL(url), {once: true});
        image.addEventListener("error", () => URL.revokeObjectURL(url), {once: true});
        image.src = url;
        container.replaceChildren(image);
    } catch {
        if (container.imageRequest === marker) container.textContent = fallback;
    }
}

function updateTemplates(items, selected = $("template-select").value) {
    templates = items;
    const select = $("template-select");
    select.replaceChildren(new Option(items.length ? "请选择样机" : "暂无样机", ""));
    for (const template of items) select.add(new Option(template.relative_path, template.id));
    select.value = items.some((item) => item.id === selected) ? selected : (items[0]?.id || "");
    if (activePage === "create") drawProof();
    if (activePage === "templates") drawTemplates();
}

function drawProof() {
    const template = templates.find((item) => item.id === $("template-select").value);
    drawLayers(template);
    const stage = $("proof-stage");
    stage.imageRequest = null;
    $("preview-name").textContent = template?.relative_path || "暂未选择样机";
    $("preview-size").textContent = template ? dimensions(template) : "";
    $("preview-status").hidden = !template?.thumbnail_pending;
    const empty = node("div", "empty-proof");
    empty.append(node("span", "", "▧"), template?.thumbnail_pending ? "正在生成缩略图…"
        : template ? "暂无预览" : "选择一个样机以预览");
    stage.replaceChildren(empty);
    if (template) showImage(stage, template.thumbnail_url, template.filename);
}

function updateRenderButton() {
    $("render-submit").disabled = submittingRender || !$("layer").value;
}

function drawLayers(template) {
    const select = $("layer");
    const previous = select.dataset.template === template?.id ? select.value : "";
    const layers = template?.replacement_layers || [];
    const placeholder = !template ? "请先选择样机" : layers.length ? "请选择要替换的图层" : "暂无可替换图层";
    select.replaceChildren(new Option(placeholder, ""));
    for (const layer of layers) {
        const path = layer.path.replace(/^.*?贴图区 \/ /, "");
        const label = layers.some((other) => other.id !== layer.id && other.path === layer.path)
            ? `${path}（ID ${layer.id}）` : path;
        select.add(new Option(label, layer.id));
    }
    select.dataset.template = template?.id || "";
    select.disabled = layers.length === 0;
    select.value = layers.some((layer) => String(layer.id) === previous) ? previous
        : layers.length === 1 ? String(layers[0].id) : "";
    const note = $("layer-note");
    note.hidden = !template || layers.length > 0;
    note.textContent = note.hidden ? "" : "请在样机的「贴图区」组内放置可替换的智能对象，再刷新样机目录。";
    updateRenderButton();
}

function drawTemplates() {
    const search = $("template-search").value.trim().toLowerCase();
    const shown = templates.filter((item) => item.relative_path.toLowerCase().includes(search));
    $("template-count").textContent = `${shown.length} 个样机`;
    const grid = $("template-grid");
    grid.replaceChildren();
    if (!shown.length) {
        grid.append(node("div", "empty-state", !token ? "连接服务后查看样机。" : search ? "未找到匹配的样机。" : "暂无样机。上传一个 PSD 或 PSB 开始使用。"));
        return;
    }
    for (const template of shown) {
        const card = node("article", "asset-card");
        const image = node("div", "asset-image", template.thumbnail_pending ? "正在生成缩略图…" : "暂无预览");
        const info = node("div", "asset-info");
        const controls = node("div", "actions");
        controls.append(action("使用模板", () => {
            $("template-select").value = template.id;
            location.hash = "create";
        }), action("删除", () => confirmDelete("删除样机", template.relative_path, async () => {
            await api(`templates/${template.id}`, {method: "DELETE"});
            updateTemplates(templates.filter((item) => item.id !== template.id));
            notify("样机已删除");
        }), "text-button delete-button"));
        info.append(node("div", "asset-name", template.relative_path),
            node("div", "asset-meta", `${dimensions(template)}　${formatBytes(template.size_bytes)}`), controls);
        card.append(image, info);
        grid.append(card);
        showImage(image, template.thumbnail_url, template.filename);
    }
}

function renderURL(item, options = null) {
    const path = `renders/${item.id}/image`;
    return options ? query(path, options) : path;
}

async function download(item) {
    const file = await (await api(renderURL(item, {download: "true"}))).blob();
    const url = URL.createObjectURL(file);
    const anchor = node("a");
    anchor.href = url;
    anchor.download = item.filename;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function preview(item) {
    $("dialog-title").textContent = item.filename;
    $("dialog-preview").textContent = "正在载入原图…";
    $("preview-dialog").showModal();
    showImage($("dialog-preview"), renderURL(item), item.filename);
}

function drawRenders() {
    const filter = $("render-filter").value;
    const shown = filter === "all" ? renders : renders.filter((item) => item.status === filter);
    $("render-count").textContent = `${shown.length} 条生成记录`;
    const list = $("render-list");
    list.replaceChildren();
    if (!shown.length) {
        list.append(node("div", "empty-state", !token ? "连接服务后查看生成图。" : filter === "all" ? "暂无生成图。上传一张印花图片开始生成。" : "当前状态暂无记录。"));
        return;
    }
    for (const item of shown) {
        const row = node("article", "render-row");
        const completed = item.status === "completed";
        const thumb = completed ? action("▧", () => preview(item), "render-thumb") : node("div", "render-thumb", "▧");
        if (completed) thumb.setAttribute("aria-label", `预览 ${item.filename}`);
        const info = node("div");
        const title = node("div", "result-name", item.filename);
        title.append(node("span", `status-chip ${item.status}`, statusNames[item.status] || item.status));
        const time = new Date(item.created_at).toLocaleString("zh-CN", {hour12: false});
        const meta = node("div", "result-meta", [item.template_name, time, completed ? dimensions(item) : ""].filter(Boolean).join("　"));
        info.append(title, meta);
        if (item.error) info.append(node("div", "result-error", item.error));
        const controls = node("div", "actions");
        if (completed) controls.append(action("预览", () => preview(item)), action("下载 PNG", () => download(item)));
        if (completed || item.status === "failed") {
            controls.append(action("删除", () => confirmDelete("删除生成记录", item.filename, async () => {
                await api(`renders/${item.id}`, {method: "DELETE"});
                await loadRenders();
                notify("生成记录已删除");
            }), "text-button delete-button"));
        }
        row.append(thumb, info, controls);
        list.append(row);
        if (completed) showImage(thumb, renderURL(item, {thumbnail: "true"}), item.filename, "▧");
    }
}

async function loadRenders() {
    if (!token || refreshingRenders) return;
    refreshingRenders = true;
    try {
        const items = await (await api("renders")).json();
        const changed = JSON.stringify(items) !== JSON.stringify(renders);
        renders = items;
        if (activePage === "renders" && changed) drawRenders();
    } finally { refreshingRenders = false; }
}

async function openPage() {
    const page = location.hash.slice(1);
    activePage = Object.hasOwn(pageNames, page) ? page : "create";
    for (const key of Object.keys(pageNames)) {
        $(`page-${key}`).hidden = key !== activePage;
        const link = document.querySelector(`[data-page="${key}"]`);
        if (key === activePage) link.setAttribute("aria-current", "page");
        else link.removeAttribute("aria-current");
    }
    $("current-page").textContent = pageNames[activePage];
    if (activePage === "create") drawProof();
    if (activePage === "templates") drawTemplates();
    if (activePage === "renders") {
        drawRenders();
        await loadRenders();
    }
}

async function confirmDelete(title, name, operation) {
    $("confirm-title").textContent = title;
    $("confirm-message").textContent = `确定删除「${name}」？`;
    const dialog = $("confirm-dialog");
    dialog.returnValue = "cancel";
    const choice = new Promise((resolve) => dialog.addEventListener("close", () => resolve(dialog.returnValue), {once: true}));
    dialog.showModal();
    if (await choice === "delete") await operation();
}

function selectArtwork(file) {
    artwork = file;
    $("upload-text").textContent = file?.name || "点击选择，或拖放图片";
    $("upload-meta").textContent = file ? formatBytes(file.size) : "PNG、JPEG · 最大 50 MB";
}

$("connection").addEventListener("click", () => {
    $("login-error").hidden = true;
    $("token-input").value = "";
    if (!$("login-dialog").open) $("login-dialog").showModal();
});
$("login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const accessToken = $("token-input").value.trim();
    $("login-submit").disabled = true;
    $("login-error").hidden = true;
    try {
        const items = await (await api("templates", {accessToken})).json();
        token = accessToken;
        sessionStorage.setItem("mockup.token", token);
        connectionStatus();
        updateTemplates(items);
        $("login-dialog").close();
        await openPage();
    } catch (error) {
        $("login-error").textContent = error.message;
        $("login-error").hidden = false;
    } finally { $("login-submit").disabled = false; }
});
for (const button of document.querySelectorAll("[data-close]")) {
    button.addEventListener("click", () => $(button.dataset.close).close());
}
$("template-select").addEventListener("change", drawProof);
$("layer").addEventListener("change", updateRenderButton);
$("template-search").addEventListener("input", drawTemplates);
$("render-filter").addEventListener("change", drawRenders);
$("artwork-input").addEventListener("change", (event) => selectArtwork(event.target.files[0] || null));
const dropZone = $("drop-zone");
dropZone.addEventListener("dragover", (event) => { event.preventDefault(); dropZone.classList.add("dragging"); });
dropZone.addEventListener("dragleave", () => dropZone.classList.remove("dragging"));
dropZone.addEventListener("drop", (event) => {
    event.preventDefault();
    dropZone.classList.remove("dragging");
    if (event.dataTransfer.files.length) {
        $("artwork-input").files = event.dataTransfer.files;
        selectArtwork(event.dataTransfer.files[0]);
    }
});
$("render-form").addEventListener("submit", (event) => {
    event.preventDefault();
    handle(async () => {
        if (!requireConnection() || !artwork || !$("layer").value || submittingRender) return;
        const button = $("render-submit");
        const params = {template_id: $("template-select").value, filename: artwork.name, layer_id: $("layer").value};
        submittingRender = true;
        updateRenderButton();
        button.textContent = "正在提交…";
        try {
            const queued = await (await api(query("renders", params), {method: "POST", body: artwork})).json();
            renders = [queued, ...renders];
            $("artwork-input").value = "";
            selectArtwork(null);
            $("render-filter").value = "all";
            if (activePage === "renders") drawRenders();
            else location.hash = "renders";
            notify("已提交生成任务");
        } finally {
            submittingRender = false;
            updateRenderButton();
            button.textContent = "✦ 生成 PNG";
        }
    });
});
$("refresh-templates").addEventListener("click", () => handle(async () => {
    if (!requireConnection()) return;
    const button = $("refresh-templates");
    button.disabled = true;
    try {
        updateTemplates(await (await api("templates/refresh", {method: "POST"})).json());
        notify("样机目录已刷新");
    } finally { button.disabled = false; }
}));
$("upload-template").addEventListener("click", () => {
    if (requireConnection()) $("template-file").click();
});
$("template-file").addEventListener("change", (event) => handle(async () => {
    const file = event.target.files[0];
    if (!file) return;
    const button = $("upload-template");
    button.disabled = true;
    button.textContent = "正在上传…";
    try {
        const item = await (await api(query("templates", {filename: file.name}), {method: "POST", body: file})).json();
        $("template-search").value = "";
        updateTemplates([...templates, item]);
        notify("样机已上传");
    } finally {
        event.target.value = "";
        button.disabled = false;
        button.textContent = "＋ 上传样机";
    }
}));
window.addEventListener("hashchange", () => handle(openPage));
async function loadPendingPreviews() {
    if (refreshingTemplates) return;
    refreshingTemplates = true;
    const previous = templates;
    try {
        const items = await (await api("templates")).json();
        if (templates === previous && JSON.stringify(items) !== JSON.stringify(previous)) updateTemplates(items);
    } finally { refreshingTemplates = false; }
}

setInterval(() => {
    if (activePage !== "renders" && token && templates.some((item) => item.thumbnail_pending)) {
        handle(loadPendingPreviews);
    }
    if (activePage === "renders" && token && renders.some((item) => item.status === "queued" || item.status === "running")) {
        handle(loadRenders);
    }
}, 1800);

handle(async () => {
    if (token) {
        try { updateTemplates(await (await api("templates")).json()); }
        catch (error) {
            token = "";
            sessionStorage.removeItem("mockup.token");
            notify(error.message, true);
        }
    }
    connectionStatus();
    await openPage();
    if (!token) $("connection").click();
});
