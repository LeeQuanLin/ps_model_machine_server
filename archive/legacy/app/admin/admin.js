const $ = (id) => document.getElementById(id);
const pageNames = {create: "生成样机", templates: "样机管理", renders: "生成图"};
const previewModels = JSON.parse($("preview-models").textContent);
const viewerURL = new URL(document.currentScript.dataset.viewer, document.baseURI).href;
const proofParts = [
    {
        key: "background", bounds: [0, 0, 440, 456], label: [58, 36],
        outline: "M18 0H422Q440 0 440 18V438Q440 456 422 456H18Q0 456 0 438V18Q0 0 18 0Z",
    },
    {
        key: "pillowcase", bounds: [126, 49, 188, 88], label: [220, 93],
        outline: "M140 56Q220 43 300 56Q314 59 311 73L306 92L311 111Q313 126 297 129Q220 141 143 129Q127 126 129 111L134 92L129 73Q126 59 140 56Z",
        stitch: "M146 64Q220 53 294 64Q302 65 301 75L297 92L301 110Q303 119 293 121Q220 132 147 121Q137 119 139 110L143 92L139 75Q138 65 146 64Z",
    },
    {
        key: "a_side", bounds: [76, 199, 288, 219], label: [220, 313],
        outline: "M105 200H335L362 389Q365 406 349 411Q220 424 91 411Q75 406 78 389Z",
        stitch: "M111 229L89 387Q87 399 100 401Q220 412 340 401Q353 399 351 387L329 229",
    },
    {
        key: "b_side", bounds: [92, 150, 256, 80], label: [220, 190],
        outline: "M112 155Q220 147 328 155Q338 155 340 165L347 209Q349 219 338 221Q220 236 102 221Q91 219 93 209L100 165Q102 155 112 155Z",
        stitch: "M103 207Q220 221 337 207",
    },
];
const statusNames = {queued: "排队中", running: "生成中", completed: "已完成", failed: "失败"};
let token = sessionStorage.getItem("mockup.token") || "";
let templates = [];
let renders = [];
const artworks = new Map();
let selectedSlot = null;
let previewViewer = null;
let configuration = null;
let activePage = "create";
let refreshingRenders = false;
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

async function api(path, {method = "GET", body, json, accessToken = token} = {}) {
    const headers = accessToken ? {Authorization: `Bearer ${accessToken}`} : {};
    if (json !== undefined) {
        headers["Content-Type"] = "application/json";
        body = JSON.stringify(json);
    }
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

async function showImage(container, path, name, fallback = "无法读取预览", onLoad) {
    if (!path) return;
    const marker = Symbol();
    container.imageRequest = marker;
    try {
        const blob = await (await api(path)).blob();
        if (container.imageRequest !== marker || !container.isConnected) return;
        const image = node("img");
        image.alt = name;
        const url = URL.createObjectURL(blob);
        image.addEventListener("load", () => {
            URL.revokeObjectURL(url);
            if (container.imageRequest === marker && image.isConnected) onLoad?.(image);
        }, {once: true});
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

function selectedTemplate() {
    return templates.find((item) => item.id === $("template-select").value);
}

function svgNode(tag, attributes = {}) {
    const element = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [name, value] of Object.entries(attributes)) element.setAttribute(name, value);
    return element;
}

function templateSlots(template) {
    const layers = template?.replacement_layers || [];
    return previewModel(template).parts
        .map((slot) => ({...slot, layer: layers.find((layer) => layer.id === template?.slots?.[slot.key])}))
        .filter((slot) => slot.layer);
}

function previewModel(template) {
    return previewModels[template?.preview_model] || previewModels.bedding;
}

function selectProofSlot(key) {
    const openPicker = selectedSlot === key || !artworks.has(key);
    if (key !== selectedSlot) $("copy-artwork").open = false;
    selectedSlot = key;
    updateProofSelection();
    if (openPicker && selectedLayerId()) $("artwork-input").click();
}

function clearProofViewer() {
    previewViewer?.dispose();
    previewViewer = null;
    $("proof-stage").replaceChildren();
}

function drawProof() {
    const template = selectedTemplate();
    const stage = $("proof-stage");
    const previous = stage.dataset.template;
    stage.dataset.template = template?.id || "";
    if (previous !== stage.dataset.template) selectedSlot = null;
    const choices = templateSlots(template);
    $("copy-artwork").open = false;
    for (const [key, entry] of artworks) {
        if (previous !== stage.dataset.template || !choices.some((slot) => slot.key === key && slot.layer.id === entry.layerId)) {
            URL.revokeObjectURL(entry.image.src);
            artworks.delete(key);
        }
    }
    if (!choices.some((slot) => slot.key === selectedSlot)) selectedSlot = choices.length === 1 ? choices[0].key : null;
    $("preview-name").textContent = template?.relative_path || "暂未选择样机";
    $("preview-size").textContent = template ? dimensions(template) : "";
    const toolbar = $("preview-parts");
    toolbar.replaceChildren();
    toolbar.hidden = !template;
    clearProofViewer();
    if (!template) {
        stage.replaceChildren(node("div", "empty-proof", "选择一个样机以预览"));
        updateProofSelection();
        return;
    }
    const model = previewModel(template);
    for (const part of model.parts) {
        const configured = choices.some(({key}) => key === part.key);
        const button = action("", () => selectProofSlot(part.key),
            configured ? "preview-part" : "preview-part is-unconfigured");
        const swatch = node("span", "part-swatch");
        const description = node("span", "part-description");
        description.append(node("strong", "", part.label), node("small", "part-state", configured ? "待选图" : "未绑定"));
        button.append(swatch, description);
        button.disabled = !configured;
        button.dataset.slot = part.key;
        button.setAttribute("aria-label", configured ? `为${part.label}选择图片` : `${part.label}尚未绑定图层`);
        toolbar.append(button);
    }
    const host = node("div", "proof-canvas");
    host.append(node("span", "empty-proof", "正在加载 3D 预览…"));
    stage.replaceChildren(host);
    updateProofSelection();
    const fallback = (error) => {
        if (!host.isConnected) return;
        console.warn("3D preview failed", error);
        previewViewer?.dispose();
        previewViewer = null;
        drawProofFallback(stage, model, choices);
    };
    const startViewer = (preferCanvas = false) => import(viewerURL).then(({loadPreview3D}) => {
        if (!host.isConnected) return;
        return loadPreview3D(host, model, selectProofSlot, () => {
            if (!host.isConnected) return;
            previewViewer?.dispose();
            previewViewer = null;
            startViewer(true);
        }, preferCanvas);
    }).then((viewer) => {
        if (!viewer) return;
        if (!host.isConnected) { viewer.dispose(); return; }
        previewViewer = viewer;
        host.dataset.renderer = viewer.mode;
        updateProofSelection();
    }).catch(fallback);
    startViewer();
}

function drawProofFallback(stage, model, choices) {
    const frame = svgNode("svg", {class: "proof-image", viewBox: "0 0 440 456",
        role: "group", "aria-label": `${model.label}替换部位示意图`});
    stage.replaceChildren(frame);
    drawProofRegions(frame, model, choices);
    updateProofSelection();
}

function drawProofRegions(frame, model, choices) {
    const definitions = svgNode("defs");
    const plane = svgNode("g", {transform: "matrix(.75 .20 -.26 .71 100 14)"});
    frame.append(definitions, plane);
    const isPillow = model === previewModels.pillowcase;
    const parts = isPillow ? model.parts.map(({key}, index) => ({
        ...proofParts.find((part) => part.key === "pillowcase"), key,
        transform: `translate(-88 ${index ? 157 : 12}) scale(1.4)`,
    })) : proofParts;
    for (const part of parts) {
        const slot = model.parts.find(({key}) => key === part.key);
        if (!slot) continue;
        const configured = choices.some(({key}) => key === slot.key);
        const shape = svgNode("g", {class: configured ? "proof-part" : "proof-part is-unconfigured",
            "data-slot": slot.key, role: "button", tabindex: "0",
            "aria-label": configured ? `为${slot.label}选择图片` : `${slot.label}尚未绑定图层`});
        if (part.transform) shape.setAttribute("transform", part.transform);
        shape.append(svgNode("path", {class: "proof-depth", d: part.outline, transform: "translate(0 7)"}));
        shape.append(svgNode("path", {class: "proof-surface", d: part.outline}));
        plane.append(shape);
        const clipId = `proof-clip-${part.key}`;
        const clip = svgNode("clipPath", {id: clipId});
        clip.append(svgNode("path", {d: part.outline}));
        definitions.append(clip);
        const [x, y, width, height] = part.bounds;
        shape.append(svgNode("image", {x, y, width, height, class: "proof-artwork",
            preserveAspectRatio: "xMidYMid slice", "clip-path": `url(#${clipId})`}));
        if (part.stitch) shape.append(svgNode("path", {class: "proof-stitch", d: part.stitch}));

        shape.append(svgNode("path", {class: "proof-hit", d: part.outline}));
        const [labelX, labelY] = part.label;
        const label = svgNode("g", {class: "proof-label", transform: `translate(${labelX} ${labelY})`,
            "aria-hidden": "true"});
        const badge = svgNode("rect", {x: -43, y: -14, width: 86, height: 28, rx: 8});
        const text = svgNode("text", {x: 0, y: 0, dy: ".35em"});
        text.textContent = slot.label;
        label.append(badge, text);
        shape.append(label);
        const activate = () => selectProofSlot(slot.key);
        shape.addEventListener("click", activate);
        shape.addEventListener("keydown", (event) => {
            if (event.key !== "Enter" && event.key !== " ") return;
            event.preventDefault();
            activate();
        });
    }
}

function updateProofSelection() {
    const template = selectedTemplate();
    const choices = templateSlots(template);
    const slot = previewModel(template).parts.find(({key}) => key === selectedSlot);
    const layerId = choices.find(({key}) => key === selectedSlot)?.layer.id;
    const entry = artworks.get(selectedSlot);
    const artworkPreview = entry?.image;
    $("artwork-input").disabled = !layerId;
    $("clear-artwork").hidden = !entry;
    const copy = $("copy-artwork");
    copy.hidden = !artworkPreview?.naturalWidth || choices.length < 2;
    if (copy.hidden) copy.open = false;
    $("upload-text").textContent = entry?.file.name || (layerId ? "点击选择，或拖放图片" : "请先选择替换部位");
    $("upload-meta").textContent = entry ? formatBytes(entry.file.size) : "PNG、JPEG · 最大 50 MB";
    for (const item of document.querySelectorAll("#proof-stage [data-slot], #preview-parts [data-slot]")) {
        const selected = item.dataset.slot === selectedSlot;
        item.classList.toggle("is-selected", selected);
        item.setAttribute("aria-pressed", String(selected));
        const art = artworks.get(item.dataset.slot)?.image;
        const image = item.querySelector("image");
        if (image) {
            if (art?.naturalWidth) image.setAttribute("href", art.src);
            else image.removeAttribute("href");
        } else if (!item.disabled) {
            item.querySelector(".part-swatch").style.backgroundImage = art?.naturalWidth ? `url("${art.src}")` : "";
            const state = art?.naturalWidth ? "已设图" : "待选图";
            item.querySelector(".part-state").textContent = state;
            item.setAttribute("aria-label", `${item.querySelector("strong").textContent}，${state}`);
        }
    }
    previewViewer?.show(layerId ? selectedSlot : null, artworks);
    $("slot-selection").textContent = slot ? (layerId ? `替换部位 · ${slot.label}` : `${slot.label}尚未绑定图层`)
        : "请点选要替换的部位";
    const status = $("preview-status");
    if (slot && !layerId) status.textContent = `请在样机管理中为「${slot.label}」绑定图层。`;
    else if (template && !template.slots) status.textContent = "请先在样机管理中配置替换部位。";
    else if (template && !choices.length) status.textContent = "配置中没有启用替换部位。";
    else if (artworkPreview?.complete && !artworkPreview.naturalWidth) status.textContent = "无法预览这张图片。";
    else if (slot && artworkPreview?.naturalWidth) status.textContent = `生成 PNG 时只替换「${slot.label}」。`;
    else status.textContent = "";
    status.hidden = !status.textContent;
    updateRenderButton();
}

function selectedLayerId(slotKey = selectedSlot) {
    return templateSlots(selectedTemplate()).find(({key}) => key === slotKey)?.layer.id || "";
}

function updateRenderButton() {
    $("render-submit").disabled = submittingRender || !selectedLayerId() || !artworks.get(selectedSlot)?.image.naturalWidth;
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
        const image = node("div", "asset-image", "暂无预览");
        const info = node("div", "asset-info");
        const controls = node("div", "actions");
        if (templateSlots(template).length) controls.append(action("使用模板", () => {
            $("template-select").value = template.id;
            location.hash = "create";
        }));
        controls.append(action("配置", () => openConfiguration(template)), action("删除", () => confirmDelete("删除样机", template.relative_path, async () => {
            await api(`templates/${template.id}`, {method: "DELETE"});
            updateTemplates(templates.filter((item) => item.id !== template.id));
            notify("样机已删除");
        }), "text-button delete-button"));
        info.append(node("div", "asset-name", template.relative_path),
            node("div", "asset-meta", `${dimensions(template)}　${formatBytes(template.size_bytes)}`),
            node("div", "asset-meta", template.slots
                ? `${previewModel(template).label} · ${templateSlots(template).length} 个部位` : "待配置"), controls);
        card.append(image, info);
        grid.append(card);
        showImage(image, template.thumbnail_url, template.filename);
    }
}

async function openConfiguration(template) {
    const dialog = $("configuration-dialog");
    const draft = {template, step: 0, saving: false};
    configuration = draft;
    $("configuration-template").textContent = template.relative_path;
    $("configuration-message").textContent = "正在打开 Photoshop 并读取图层…";
    $("configuration-flow").hidden = true;
    dialog.showModal();
    try {
        const inspection = await (await api(`templates/${template.id}/inspect`, {method: "POST"})).json();
        if (configuration !== draft || !dialog.open) return;
        draft.inspection = inspection;
        draft.previewModel = inspection.preview_model || "bedding";
        const ids = new Set(inspection.layers.map((layer) => layer.id));
        draft.slots = Object.fromEntries(Object.entries(inspection.slots).filter(([, id]) => ids.has(id)));
        $("configuration-model").replaceChildren(...Object.entries(previewModels).map(([key, model]) => new Option(model.label, key)));
        $("configuration-model").value = draft.previewModel;
        $("configuration-message").textContent = "";
        $("configuration-flow").hidden = false;
        drawConfiguration();
    } catch (error) {
        if (configuration === draft && dialog.open) $("configuration-message").textContent = error.message;
    }
}

function drawConfiguration() {
    const draft = configuration;
    const parts = previewModels[draft.previewModel].parts;
    const slot = parts[draft.step];
    const current = draft.slots[slot.key];
    const chosen = draft.inspection.layers.find((layer) => layer.id === current);
    const steps = $("configuration-steps");
    steps.replaceChildren(...parts.map(({key, label}, index) => {
        const step = node("li", draft.slots[key] ? "is-bound" : "", label);
        if (index === draft.step) step.setAttribute("aria-current", "step");
        return step;
    }));
    $("configuration-layer-label").textContent = `选择${slot.label}对应的图层`;
    const list = $("configuration-layers");
    list.replaceChildren();
    if (!draft.inspection.layers.length) list.append(node("p", "layer-empty", "未找到可替换的嵌入式智能对象。"));
    for (const layer of draft.inspection.layers) {
        const label = node("label", "layer-choice");
        const radio = node("input");
        radio.name = "configuration-layer";
        radio.type = "radio";
        radio.value = String(layer.id);
        radio.checked = layer.id === current;
        radio.disabled = parts.some(({key}) => key !== slot.key && draft.slots[key] === layer.id);
        radio.addEventListener("change", () => {
            draft.slots[slot.key] = layer.id;
            drawConfigurationRegions(layer);
            $("configuration-next").disabled = false;
        });
        const row = node("span", "layer-row");
        const thumb = node("span", "layer-thumb", "▧");
        thumb.setAttribute("aria-hidden", "true");
        const name = node("span", "layer-row-name", layer.name);
        const details = radio.disabled ? "已绑定到其他部位" : layer.path === layer.name ? "" : layer.path;
        if (details) name.append(node("small", "layer-row-path", details));
        const check = node("span", "layer-selected", "✓");
        check.setAttribute("aria-hidden", "true");
        row.append(thumb, name, check);
        label.append(radio, row);
        list.append(label);
        if (layer.thumbnail_url) showImage(thumb, layer.thumbnail_url, "", "▧");
    }
    const board = $("configuration-proof");
    if (board.dataset.template !== draft.template.id) {
        board.dataset.template = draft.template.id;
        const frame = node("div", "configuration-image", "暂无预览");
        board.replaceChildren(frame);
        if (draft.inspection.thumbnail_url) showImage(frame, draft.inspection.thumbnail_url, draft.template.filename,
            undefined, () => {
                const active = previewModels[draft.previewModel].parts[draft.step];
                drawConfigurationRegions(draft.inspection.layers.find(({id}) => id === draft.slots[active.key]));
            });
    }
    drawConfigurationRegions(chosen);
    $("configuration-back").disabled = !draft.step;
    $("configuration-next").disabled = !current;
    $("configuration-next").textContent = draft.step === parts.length - 1 ? "保存配置" : "下一步";
    $("configuration-skip").textContent = draft.step === parts.length - 1 ? "跳过并保存" : "跳过";
}

function drawConfigurationRegions(layer) {
    const frame = $("configuration-proof").querySelector(".configuration-image");
    frame?.querySelectorAll(".configuration-region").forEach((region) => region.remove());
    if (!frame?.querySelector("img")?.naturalWidth) return;
    for (const {left, top, right, bottom} of layer?.preview_regions || []) {
        const region = node("span", "configuration-region");
        Object.assign(region.style, {left: `${left * 100}%`, top: `${top * 100}%`,
            width: `${(right - left) * 100}%`, height: `${(bottom - top) * 100}%`});
        frame.append(region);
    }
}

async function advanceConfiguration(skip) {
    const draft = configuration;
    if (draft.saving) return;
    const parts = previewModels[draft.previewModel].parts;
    if (skip) draft.slots[parts[draft.step].key] = null;
    if (draft.step < parts.length - 1) {
        draft.step++;
        drawConfiguration();
        return;
    }
    draft.saving = true;
    const buttons = [$("configuration-next"), $("configuration-skip"), $("configuration-back"), $("configuration-model")];
    buttons.forEach((button) => { button.disabled = true; });
    $("configuration-message").textContent = "正在保存配置…";
    try {
        const updated = await (await api(`templates/${draft.template.id}/configuration`, {method: "PUT",
            json: {revision: draft.inspection.revision, preview_model: draft.previewModel,
                slots: Object.fromEntries(parts.map(({key}) => [key, draft.slots[key] ?? null]))}})).json();
        updateTemplates(templates.map((item) => item.id === updated.id ? updated : item));
        if (configuration === draft) $("configuration-dialog").close();
        notify("样机配置已保存");
    } catch (error) {
        if (configuration === draft) $("configuration-message").textContent = error.message;
    } finally {
        draft.saving = false;
        if (configuration === draft) {
            $("configuration-skip").disabled = false;
            $("configuration-model").disabled = false;
            drawConfiguration();
        }
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
    else clearProofViewer();
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

function selectArtwork(file, key = selectedSlot) {
    if (!file) return;
    const layerId = selectedLayerId(key);
    if (!layerId) return notify("请先选择已绑定图层的部位。", true);
    const previous = artworks.get(key);
    if (previous) URL.revokeObjectURL(previous.image.src);
    const image = new Image();
    artworks.set(key, {file, image, layerId});
    const update = () => { if (artworks.get(key)?.image === image) updateProofSelection(); };
    image.addEventListener("load", update, {once: true});
    image.addEventListener("error", update, {once: true});
    image.src = URL.createObjectURL(file);
    updateProofSelection();
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
$("configuration-dialog").addEventListener("close", () => {
    configuration = null;
    $("configuration-proof").dataset.template = "";
    $("configuration-proof").replaceChildren();
    $("configuration-skip").disabled = false;
    $("configuration-model").disabled = false;
});
$("configuration-model").addEventListener("change", (event) => {
    configuration.previewModel = event.target.value;
    configuration.step = 0;
    drawConfiguration();
});
$("configuration-back").addEventListener("click", () => {
    configuration.step--;
    drawConfiguration();
});
$("configuration-skip").addEventListener("click", () => advanceConfiguration(true));
$("configuration-next").addEventListener("click", () => advanceConfiguration(false));
$("template-search").addEventListener("input", drawTemplates);
$("render-filter").addEventListener("change", drawRenders);
$("artwork-input").addEventListener("change", (event) => {
    selectArtwork(event.target.files[0]);
    event.target.value = "";
});
$("clear-artwork").addEventListener("click", () => {
    const entry = artworks.get(selectedSlot);
    if (!entry) return;
    URL.revokeObjectURL(entry.image.src);
    artworks.delete(selectedSlot);
    updateProofSelection();
});
$("copy-artwork").addEventListener("toggle", () => {
    if (!$("copy-artwork").open) return;
    const targets = $("copy-targets");
    targets.replaceChildren();
    for (const slot of templateSlots(selectedTemplate())) {
        if (slot.key === selectedSlot) continue;
        const label = node("label");
        const input = node("input");
        input.type = "checkbox";
        input.value = slot.key;
        label.append(input, artworks.has(slot.key) ? `${slot.label}（替换）` : slot.label);
        targets.append(label);
    }
    $("apply-artwork").disabled = true;
});
$("copy-targets").addEventListener("change", () => {
    $("apply-artwork").disabled = !$("copy-targets").querySelector(":checked");
});
$("apply-artwork").addEventListener("click", () => {
    const source = artworks.get(selectedSlot);
    if (!source?.image.naturalWidth) return;
    for (const target of $("copy-targets").querySelectorAll(":checked")) selectArtwork(source.file, target.value);
    $("copy-artwork").open = false;
});
const dropZone = $("drop-zone");
dropZone.addEventListener("dragover", (event) => { event.preventDefault(); dropZone.classList.add("dragging"); });
dropZone.addEventListener("dragleave", () => dropZone.classList.remove("dragging"));
dropZone.addEventListener("drop", (event) => {
    event.preventDefault();
    dropZone.classList.remove("dragging");
    selectArtwork(event.dataTransfer.files[0]);
});
$("render-form").addEventListener("submit", (event) => {
    event.preventDefault();
    handle(async () => {
        const key = selectedSlot;
        const entry = artworks.get(key);
        if (!requireConnection() || !entry?.image.naturalWidth || !selectedLayerId() || submittingRender) return;
        const button = $("render-submit");
        const params = {template_id: $("template-select").value, filename: entry.file.name, layer_id: entry.layerId};
        submittingRender = true;
        updateRenderButton();
        button.textContent = "正在提交…";
        try {
            const queued = await (await api(query("renders", params), {method: "POST", body: entry.file})).json();
            renders = [queued, ...renders];
            if (artworks.get(key) === entry) {
                URL.revokeObjectURL(entry.image.src);
                artworks.delete(key);
                updateProofSelection();
            }
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
setInterval(() => {
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
