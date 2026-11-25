import {BackSide, FrontSide, Matrix3, Matrix4, Vector3} from "three";

const light = new Vector3(-.42, .83, .36).normalize();
const edge = (a, b, x, y) => (b.sx - a.sx) * (y - a.sy) - (b.sy - a.sy) * (x - a.sx);

function project(vertex, width, height) {
    vertex.inverse = 1 / vertex.w;
    vertex.sx = (.5 + .5 * vertex.x * vertex.inverse) * width;
    vertex.sy = (.5 - .5 * vertex.y * vertex.inverse) * height;
    return vertex;
}

function clipNear(vertices, near, width, height) {
    if (vertices.every(({w}) => w >= near)) return vertices;
    const visible = [];
    for (let i = 0; i < vertices.length; i++) {
        const a = vertices[i];
        const b = vertices[(i + 1) % vertices.length];
        if (a.w >= near) visible.push(a);
        if ((a.w >= near) === (b.w >= near)) continue;
        const fraction = (near - a.w) / (b.w - a.w);
        const crossing = {w: near};
        for (const key of ["x", "y", "u", "v", "shade"]) crossing[key] = a[key] + (b[key] - a[key]) * fraction;
        visible.push(project(crossing, width, height));
    }
    return visible;
}

function drawTriangle(a, b, c, material, texture, frame, depth) {
    const area = edge(a, b, c.sx, c.sy);
    if (Math.abs(area) < .0001 || (area > 0 && material.side === FrontSide)
        || (area < 0 && material.side === BackSide)) return;
    const {width, height, data} = frame;
    const left = Math.max(0, Math.floor(Math.min(a.sx, b.sx, c.sx)));
    const top = Math.max(0, Math.floor(Math.min(a.sy, b.sy, c.sy)));
    const right = Math.min(width - 1, Math.ceil(Math.max(a.sx, b.sx, c.sx)));
    const bottom = Math.min(height - 1, Math.ceil(Math.max(a.sy, b.sy, c.sy)));
    const color = material.color.getHex();
    const red = color >> 16, green = (color >> 8) & 255, blue = color & 255;
    const highlighted = material.emissive?.getHex() || 0;
    const inverseArea = 1 / area;

    for (let y = top; y <= bottom; y++) {
        for (let x = left; x <= right; x++) {
            const xCenter = x + .5, yCenter = y + .5;
            const alpha = edge(b, c, xCenter, yCenter) * inverseArea;
            const beta = edge(c, a, xCenter, yCenter) * inverseArea;
            const gamma = 1 - alpha - beta;
            if (alpha < -1e-7 || beta < -1e-7 || gamma < -1e-7) continue;
            const wa = alpha * a.inverse, wb = beta * b.inverse, wc = gamma * c.inverse;
            const distance = wa + wb + wc;
            const pixel = y * width + x;
            if (distance <= depth[pixel]) continue;
            const shade = (wa * a.shade + wb * b.shade + wc * c.shade) / distance;
            let r = red, g = green, bChannel = blue;
            if (texture) {
                const u = (wa * a.u + wb * b.u + wc * c.u) / distance;
                const v = (wa * a.v + wb * b.v + wc * c.v) / distance;
                const tx = Math.max(0, Math.min(texture.width - 1, Math.floor(u * texture.width)));
                const ty = Math.max(0, Math.min(texture.height - 1, Math.floor(v * texture.height)));
                const index = (ty * texture.width + tx) * 4;
                if (texture.data[index + 3] < 128) continue;
                r *= texture.data[index] / 255;
                g *= texture.data[index + 1] / 255;
                bChannel *= texture.data[index + 2] / 255;
            }
            depth[pixel] = distance;
            const offset = pixel * 4;
            data[offset] = r * shade + .35 * (highlighted >> 16);
            data[offset + 1] = g * shade + .35 * ((highlighted >> 8) & 255);
            data[offset + 2] = bChannel * shade + .35 * (highlighted & 255);
            data[offset + 3] = 255;
        }
    }
}

export class CanvasPreviewRenderer {
    constructor() {
        this.domElement = document.createElement("canvas");
        this.context = this.domElement.getContext("2d");
        if (!this.context) throw Error("无法创建 3D 预览画布。");
        this.textures = new WeakMap();
        this.projection = new Matrix4();
        this.transform = new Matrix4();
        this.normals = new Matrix3();
        this.normal = new Vector3();
    }

    setSize(width, height) {
        const scale = Math.min(1, 640 / Math.max(width, height));
        this.domElement.width = Math.max(1, Math.round(width * scale));
        this.domElement.height = Math.max(1, Math.round(height * scale));
        this.frame = this.context.createImageData(this.domElement.width, this.domElement.height);
        this.depth = new Float32Array(this.frame.width * this.frame.height);
    }

    getTexture(map) {
        if (!map?.image) return null;
        if (this.textures.has(map)) return this.textures.get(map);
        const canvas = document.createElement("canvas");
        const {width, height} = map.image;
        const scale = Math.min(1, 1024 / Math.max(width, height));
        canvas.width = Math.max(1, Math.round(width * scale));
        canvas.height = Math.max(1, Math.round(height * scale));
        const context = canvas.getContext("2d", {willReadFrequently: true});
        let data = null;
        try {
            context.drawImage(map.image, 0, 0, canvas.width, canvas.height);
            data = context.getImageData(0, 0, canvas.width, canvas.height);
        } catch {
            // The GLB surface color is still usable when an optional texture cannot be read.
        }
        this.textures.set(map, data);
        return data;
    }

    render(scene, camera) {
        if (!this.frame) return;
        this.frame.data.fill(0);
        this.depth.fill(0);
        scene.updateMatrixWorld();
        camera.updateMatrixWorld();
        this.projection.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
        scene.traverseVisible((mesh) => {
            if (mesh.isMesh) this.drawMesh(mesh, camera.near);
        });
        this.context.putImageData(this.frame, 0, 0);
    }

    drawMesh(mesh, near) {
        const {geometry} = mesh;
        const position = geometry.getAttribute("position");
        if (!position) return;
        const normal = geometry.getAttribute("normal");
        const uv = geometry.getAttribute("uv");
        const matrix = this.transform.multiplyMatrices(this.projection, mesh.matrixWorld).elements;
        this.normals.getNormalMatrix(mesh.matrixWorld);
        const {width, height} = this.frame;
        const vertices = [];
        for (let i = 0; i < position.count; i++) {
            const x = position.getX(i), y = position.getY(i), z = position.getZ(i);
            const shade = normal ? .64 + .36 * Math.max(0, this.normal.fromBufferAttribute(normal, i)
                .applyNormalMatrix(this.normals).dot(light)) : .85;
            const vertex = {
                x: matrix[0] * x + matrix[4] * y + matrix[8] * z + matrix[12],
                y: matrix[1] * x + matrix[5] * y + matrix[9] * z + matrix[13],
                w: matrix[3] * x + matrix[7] * y + matrix[11] * z + matrix[15],
                u: uv?.getX(i) ?? 0, v: uv?.getY(i) ?? 0, shade,
            };
            vertices.push(vertex.w >= near ? project(vertex, width, height) : vertex);
        }
        const index = geometry.getIndex();
        const groups = Array.isArray(mesh.material) ? geometry.groups :
            [{start: 0, count: index?.count ?? position.count}];
        for (const {start, count, materialIndex} of groups) {
            const material = materialIndex === undefined ? mesh.material : mesh.material[materialIndex];
            if (!material?.visible || !material.color) continue;
            const texture = this.getTexture(material.map);
            for (let i = start; i + 2 < start + count; i += 3) {
                const triangle = [i, i + 1, i + 2].map((offset) => vertices[index ? index.getX(offset) : offset]);
                const clipped = clipNear(triangle, near, width, height);
                for (let j = 1; j + 1 < clipped.length; j++) {
                    drawTriangle(clipped[0], clipped[j], clipped[j + 1], material, texture, this.frame, this.depth);
                }
            }
        }
    }
}
