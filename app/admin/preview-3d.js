import {
    Box3, CanvasTexture, DirectionalLight, HemisphereLight, PCFShadowMap, PerspectiveCamera, Raycaster, Scene,
    SRGBColorSpace, Vector2, Vector3, WebGLRenderer,
} from "three";
import {GLTFLoader} from "three/addons/loaders/GLTFLoader.js";
import {OrbitControls} from "three/addons/controls/OrbitControls.js";
import {CanvasPreviewRenderer} from "./preview-canvas.js";

function artworkTexture(image, aspect, baseColor) {
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(1024 * Math.min(aspect, 1)));
    canvas.height = Math.max(1, Math.round(1024 / Math.max(aspect, 1)));
    const context = canvas.getContext("2d");
    context.fillStyle = baseColor;
    context.fillRect(0, 0, canvas.width, canvas.height);
    const width = Math.min(image.naturalWidth, image.naturalHeight * aspect);
    const height = Math.min(image.naturalHeight, image.naturalWidth / aspect);
    context.drawImage(image, (image.naturalWidth - width) / 2, (image.naturalHeight - height) / 2,
        width, height, 0, 0, canvas.width, canvas.height);
    const texture = new CanvasTexture(canvas);
    texture.colorSpace = SRGBColorSpace;
    texture.flipY = false;
    return texture;
}

function createRenderer(preferCanvas) {
    if (!preferCanvas) {
        try {
            const canvas = document.createElement("canvas");
            const context = canvas.getContext("webgl2", {antialias: true, alpha: true});
            if (context) return new WebGLRenderer({canvas, context, antialias: true, alpha: true});
        } catch (error) {
            console.warn("WebGL initialization failed. Using the Canvas 3D renderer.", error);
        }
    }
    return new CanvasPreviewRenderer();
}

export async function loadPreview3D(host, model, pick, unavailable, preferCanvas = false) {
    const url = new URL(`./models/${model.asset}`, import.meta.url);
    const {scene: object} = await new GLTFLoader().loadAsync(url.href);
    const geometries = new Set(), materials = new Set(), textures = new Set();
    const parts = new Map(model.parts.map((part) => [part.key, part]));
    const surfaces = [], meshes = [], bounds = new Box3();
    object.traverse((mesh) => {
        if (!mesh.isMesh) return;
        geometries.add(mesh.geometry);
        const key = mesh.userData.previewPart || mesh.name;
        mesh.castShadow = key !== "background";
        mesh.receiveShadow = true;
        if (key !== "background") bounds.expandByObject(mesh);
        const list = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
        for (const material of list) {
            materials.add(material);
            for (const property of Object.values(material)) if (property?.isTexture) textures.add(property);
        }
        if (!parts.has(key) || !mesh.geometry.getAttribute("uv")) return;
        mesh.userData.previewPart = key;
        meshes.push(mesh);
        const copies = list.map((material) => {
            const copy = material.clone();
            materials.add(copy);
            surfaces.push({key, material: copy, color: copy.color.clone(), map: copy.map,
                emissive: copy.emissive?.clone()});
            return copy;
        });
        mesh.material = Array.isArray(mesh.material) ? copies : copies[0];
    });
    const releaseModel = () => {
        geometries.forEach((geometry) => geometry.dispose());
        materials.forEach((material) => material.dispose());
        textures.forEach((texture) => texture.dispose());
    };
    if (!host.isConnected) {
        releaseModel();
        return null;
    }
    if (new Set(meshes.map((mesh) => mesh.userData.previewPart)).size !== parts.size) {
        releaseModel();
        throw Error("3D 模型缺少已登记的贴图部位。");
    }
    let renderer;
    try {
        renderer = createRenderer(preferCanvas);
    } catch (error) {
        releaseModel();
        throw error;
    }
    renderer.setPixelRatio?.(Math.min(window.devicePixelRatio || 1, 2));
    const canvas = renderer.domElement;
    canvas.setAttribute("aria-label", `${model.label} 3D 预览`);
    host.replaceChildren(canvas);

    const scene = new Scene();
    const fill = new HemisphereLight(0xfffbf6, 0x9d91b6, 1.6);
    const key = new DirectionalLight(0xfff0dc, 2.1);
    key.position.set(-2, 4, 3);
    scene.add(object, fill, key);
    const camera = new PerspectiveCamera(36, 1, .01, 100);
    if (bounds.isEmpty()) bounds.setFromObject(object);
    const center = bounds.getCenter(new Vector3());
    const size = bounds.getSize(new Vector3());
    const span = Math.max(size.x, size.z, size.y, 1);
    if (parts.has("background") && renderer.shadowMap) {
        renderer.shadowMap.enabled = true;
        renderer.shadowMap.type = PCFShadowMap;
        key.castShadow = true;
        const shadowRange = span * 1.2;
        Object.assign(key.shadow.camera, {left: -shadowRange, right: shadowRange,
            top: shadowRange, bottom: -shadowRange, near: .1, far: 20});
        key.shadow.camera.updateProjectionMatrix();
        key.shadow.mapSize.set(1024, 1024);
        key.shadow.normalBias = .018;
    }
    camera.position.copy(center).add(new Vector3(span * .45, span * 1.05, span * 1.38));
    const controls = new OrbitControls(camera, canvas);
    controls.target.copy(center);
    controls.enablePan = false;
    controls.minDistance = span * .55;
    controls.maxDistance = span * 3.5;
    if (parts.has("background")) controls.maxPolarAngle = Math.PI / 2 - .06;
    controls.update();

    let frame = null;
    const render = () => {
        if (frame !== null) return;
        frame = requestAnimationFrame(() => {
            frame = null;
            try {
                renderer.render(scene, camera);
            } catch (error) {
                if (!renderer.isWebGLRenderer) throw error;
                console.warn("WebGL rendering failed. Using the Canvas 3D renderer.", error);
                unavailable();
            }
        });
    };
    controls.addEventListener("change", render);
    const observer = new ResizeObserver(() => {
        const {clientWidth: width, clientHeight: height} = host;
        if (!width || !height) return;
        renderer.setSize(width, height, false);
        camera.aspect = width / height;
        camera.updateProjectionMatrix();
        render();
    });
    observer.observe(host);

    const ray = new Raycaster(), pointer = new Vector2();
    const partAt = (event) => {
        const box = canvas.getBoundingClientRect();
        pointer.set((event.clientX - box.left) / box.width * 2 - 1,
            -((event.clientY - box.top) / box.height * 2 - 1));
        camera.updateMatrixWorld();
        ray.setFromCamera(pointer, camera);
        return ray.intersectObjects(meshes, false)[0]?.object.userData.previewPart;
    };
    let down = null;
    canvas.addEventListener("pointerdown", (event) => {
        down = event.isPrimary && event.button === 0 ? {id: event.pointerId, x: event.clientX, y: event.clientY} : null;
    });
    canvas.addEventListener("pointermove", (event) => {
        if (!event.buttons && event.pointerType === "mouse") canvas.style.cursor = partAt(event) ? "pointer" : "grab";
        if (down && Math.hypot(event.clientX - down.x, event.clientY - down.y) > 6) down = null;
    });
    canvas.addEventListener("pointercancel", () => { down = null; });
    canvas.addEventListener("pointerup", (event) => {
        const clicked = down?.id === event.pointerId && Math.hypot(event.clientX - down.x, event.clientY - down.y) <= 6;
        down = null;
        const selected = clicked ? partAt(event) : null;
        if (selected) pick(selected);
    });
    const lost = (event) => { event.preventDefault(); unavailable(); };
    if (renderer.isWebGLRenderer) canvas.addEventListener("webglcontextlost", lost);
    let texture = null, currentSlot = null, currentArtwork = null;
    return {
        mode: renderer.isWebGLRenderer ? "webgl" : "canvas",
        show(slot, art) {
            if (slot === currentSlot && art === currentArtwork) return;
            const changedSlot = slot !== currentSlot;
            currentSlot = slot;
            currentArtwork = art;
            texture?.dispose();
            const surface = surfaces.find(({key}) => key === slot);
            texture = surface && art ? artworkTexture(art, parts.get(slot).aspect, surface.color.getStyle()) : null;
            for (const {key, material, color, map, emissive} of surfaces) {
                const selected = key === slot;
                material.map = (selected && texture) || map;
                material.color.copy(color);
                if (selected && texture) material.color.set(0xffffff);
                if (emissive) {
                    material.emissive.copy(emissive);
                    if (selected && !texture) material.emissive.set(0x35234b);
                }
                material.needsUpdate = true;
            }
            if (changedSlot && slot && model.parts.some((part) => part.view === "underside")) {
                const sign = parts.get(slot)?.view === "underside" ? -1 : 1;
                camera.position.y = center.y + sign * Math.max(Math.abs(camera.position.y - center.y), span * .6);
                controls.update();
            }
            render();
        },
        dispose() {
            observer.disconnect();
            if (frame !== null) cancelAnimationFrame(frame);
            canvas.removeEventListener("webglcontextlost", lost);
            controls.dispose();
            texture?.dispose();
            releaseModel();
            key.shadow.dispose();
            renderer.dispose?.();
            renderer.forceContextLoss?.();
        },
    };
}
