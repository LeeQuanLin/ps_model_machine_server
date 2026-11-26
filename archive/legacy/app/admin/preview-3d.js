import {
    Box3, CanvasTexture, DirectionalLight, HemisphereLight, PCFShadowMap, PerspectiveCamera, Raycaster, Scene,
    SRGBColorSpace, Vector2, Vector3, WebGLRenderer,
} from "three";
import {GLTFLoader} from "three/addons/loaders/GLTFLoader.js";
import {CanvasPreviewRenderer} from "./preview-canvas.js";

function surfaceTexture(image, aspect, background) {
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(1024 * Math.min(aspect, 1)));
    canvas.height = Math.max(1, Math.round(1024 / Math.max(aspect, 1)));
    const context = canvas.getContext("2d");
    const {width: w, height: h} = canvas;
    context.fillStyle = background ? "#f2f3f4" : "#fafafa";
    context.fillRect(0, 0, canvas.width, canvas.height);
    if (image) {
        const width = Math.min(image.naturalWidth, image.naturalHeight * aspect);
        const height = Math.min(image.naturalHeight, image.naturalWidth / aspect);
        context.drawImage(image, (image.naturalWidth - width) / 2, (image.naturalHeight - height) / 2,
            width, height, 0, 0, w, h);
    } else if (!background) {
        // UV isolines follow the folds of the mesh without exposing its triangle diagonals.
        const spacing = Math.max(w, h) / 10;
        context.beginPath();
        for (let x = spacing; x < w; x += spacing) { context.moveTo(x, 0); context.lineTo(x, h); }
        for (let y = spacing; y < h; y += spacing) { context.moveTo(0, y); context.lineTo(w, y); }
        context.strokeStyle = "#737985";
        context.lineWidth = 2.7;
        context.stroke();
    }
    context.strokeStyle = background ? "#b6bac1" : "#363b46";
    context.lineWidth = background ? 3 : 8;
    context.strokeRect(0, 0, w, h);
    if (!background) {
        context.lineWidth = 2;
        context.setLineDash([9, 6]);
        context.strokeRect(12, 12, w - 24, h - 24);
    }
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
    // Display both sides of a reversible model in the same fixed view.
    if (model.parts.some((part) => part.view === "underside")) {
        const size = new Box3().setFromObject(object).getSize(new Vector3());
        object.traverse((mesh) => {
            if (!mesh.isMesh) return;
            const part = parts.get(mesh.userData.previewPart || mesh.name);
            if (!part) return;
            const underside = part.view === "underside";
            if (underside) mesh.rotateX(Math.PI);
            mesh.position.z += (underside ? 1 : -1) * size.z * .58;
        });
    }
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
            copy.color.set(0xffffff);
            surfaces.push({key, material: copy});
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
    const fill = new HemisphereLight(0xffffff, 0xc8cbd2, 1.55);
    const key = new DirectionalLight(0xffffff, 1.5);
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
    const viewOffset = new Vector3(.64, 1.46, 1.18).multiplyScalar(span);

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
    const observer = new ResizeObserver(() => {
        const {clientWidth: width, clientHeight: height} = host;
        if (!width || !height) return;
        renderer.setSize(width, height, false);
        camera.aspect = width / height;
        camera.position.copy(center).addScaledVector(viewOffset, Math.max(1, 1 / camera.aspect));
        camera.lookAt(center);
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
        if (!event.buttons && event.pointerType === "mouse") canvas.style.cursor = partAt(event) ? "pointer" : "default";
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
    const artworkTextures = new Map();
    return {
        mode: renderer.isWebGLRenderer ? "webgl" : "canvas",
        show(slot, artworks) {
            for (const {key, material} of surfaces) {
                const selected = key === slot;
                const image = artworks.get(key)?.image;
                const art = image?.naturalWidth ? image : null;
                let cached = artworkTextures.get(key);
                if (!cached || cached.image !== art) {
                    cached?.texture.dispose();
                    cached = {image: art, texture: surfaceTexture(art, parts.get(key).aspect, key === "background")};
                    artworkTextures.set(key, cached);
                }
                if (material.map !== cached.texture) {
                    material.map = cached.texture;
                    material.needsUpdate = true;
                }
                material.color.set(selected && !art ? 0xdedcf0 : 0xffffff);
            }
            render();
        },
        dispose() {
            observer.disconnect();
            if (frame !== null) cancelAnimationFrame(frame);
            canvas.removeEventListener("webglcontextlost", lost);
            artworkTextures.forEach(({texture}) => texture.dispose());
            releaseModel();
            key.shadow.dispose();
            renderer.dispose?.();
            renderer.forceContextLoss?.();
        },
    };
}
