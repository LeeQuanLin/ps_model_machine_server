"""把图片贴到 PSD / PSB 样机，导出合成 PNG 和 800×800 的主体透明图。"""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


# Photoshop uses ExtendScript (ES3). Keep it embedded so this file is portable.
RENDER_SCRIPT = r"""
(function () {
    var original = app.documents.length ? app.activeDocument : null;
    var dialogs = app.displayDialogs;
    var rulerUnits = app.preferences.rulerUnits;
    var work = null, art = null, child = null;
    var stage = "打开样机";

    function collect(layers, prefix, objects) {
        for (var i = 0; i < layers.length; i++) {
            var layer = layers[i];
            var path = prefix + layer.name;
            if (layer.typename === "LayerSet") {
                collect(layer.layers, path + "/", objects);
            } else if (layer.kind === LayerKind.SMARTOBJECT || layer.kind === LayerKind.SOLIDFILL ||
                       layer.kind === LayerKind.NORMAL) {
                objects.push({layer: layer, path: path});
            }
        }
    }

    function findTarget(objects, pair) {
        var name = pair.name;
        var matches = [], choices = [], paths = [];
        for (var i = 0; i < objects.length; i++) {
            var item = objects[i];
            if (!pair.color && item.layer.kind !== LayerKind.SMARTOBJECT) continue;
            if (item.layer.name === name || item.path === name) {
                matches.push(item);
                paths.push(item.path);
            }
            choices.push(item.path);
        }
        if (!matches.length && job.skipMissing && name === "枕套")
            return findTarget(objects, {name: "枕头"});
        if (!matches.length && job.skipMissing) return null;
        if (!matches.length) throw Error("找不到智能对象。可选图层：" + choices.join("、"));
        if (matches.length > 1 && job.skipMissing) {
            // Prefer the template author's placeholder over the base background.
            var preferred = [];
            for (var j = 0; j < matches.length; j++)
                if (("/" + matches[j].path).indexOf("/贴图区/") >= 0) preferred.push(matches[j]);
            if (preferred.length) matches = preferred;
            if (matches.length > 1 && pair.color) {
                var kinds = [LayerKind.SMARTOBJECT, LayerKind.SOLIDFILL, LayerKind.NORMAL];
                for (var k = 0; k < kinds.length; k++) {
                    preferred = [];
                    for (var n = 0; n < matches.length; n++)
                        if (matches[n].layer.kind === kinds[k]) preferred.push(matches[n]);
                    if (preferred.length) { matches = preferred; break; }
                }
            }
        }
        if (matches.length > 1) throw Error("存在同名智能对象，请使用唯一的完整路径：" + paths.join("、"));
        return matches[0].layer;
    }

    function sourceId(layer) {
        var key = stringIDToTypeID("smartObject");
        var ref = new ActionReference();
        ref.putProperty(stringIDToTypeID("property"), key);
        ref.putIdentifier(stringIDToTypeID("layer"), layer.id);
        var info = executeActionGet(ref).getObjectValue(key);
        var linked = stringIDToTypeID("linked"), file = stringIDToTypeID("fileReference");
        if (info.hasKey(linked) && info.getBoolean(linked)) throw Error("请先将链接的智能对象嵌入样机");
        if (info.hasKey(file) && /\.(ai|eps|pdf|svg)$/i.test(info.getString(file)))
            throw Error("该智能对象需要外部编辑器，请先转为 Photoshop 可编辑的智能对象");
        var detailsKey = stringIDToTypeID("smartObjectMore");
        var detailsRef = new ActionReference();
        detailsRef.putProperty(stringIDToTypeID("property"), detailsKey);
        detailsRef.putIdentifier(stringIDToTypeID("layer"), layer.id);
        var details = executeActionGet(detailsRef).getObjectValue(detailsKey);
        var id = stringIDToTypeID("ID");
        return details.hasKey(id) ? details.getString(id) : "layer-" + layer.id;
    }

    function close(doc) {
        if (doc) doc.close(SaveOptions.DONOTSAVECHANGES);
    }

    function findSubject(groups, path, matches) {
        for (var i = 0; i < groups.length; i++) {
            var group = groups[i], branch = path.concat([group]);
            if (group.name === "主体") matches.push(branch);
            findSubject(group.layerSets, branch, matches);
        }
    }

    function isolateSubject(doc, branch) {
        var parent = doc;
        for (var i = 0; i < branch.length; i++) {
            var group = branch[i];
            group.visible = true;
            for (var j = 0; j < parent.layers.length; j++) {
                var sibling = parent.layers[j];
                if (sibling.id !== group.id && sibling.visible) sibling.visible = false;
            }
            parent = group;
        }
    }

    function squareCrop(doc) {
        var width = doc.width.as("px"), height = doc.height.as("px");
        var size = new UnitValue(800, "px");
        doc.resizeImage(width <= height ? size : undefined, height < width ? size : undefined,
                        undefined, ResampleMethod.BICUBIC);
        var left = Math.floor((Math.round(doc.width.as("px")) - 800) / 2);
        var top = Math.floor((Math.round(doc.height.as("px")) - 800) / 2);
        doc.crop([new UnitValue(left, "px"), new UnitValue(top, "px"),
                  new UnitValue(left + 800, "px"), new UnitValue(top + 800, "px")]);
    }

    function savePng(doc, path) {
        // Mode and bit-depth conversions may flatten layers. Convert a disposable copy.
        var convert = doc.mode !== DocumentMode.RGB && doc.mode !== DocumentMode.GRAYSCALE &&
                      doc.mode !== DocumentMode.INDEXEDCOLOR;
        var copy = null;
        try {
            if (convert || doc.bitsPerChannel === BitsPerChannelType.THIRTYTWO) {
                copy = doc.duplicate("PNG export", true);
                doc = copy;
                if (convert) doc.changeMode(ChangeMode.RGB);
                if (doc.bitsPerChannel === BitsPerChannelType.THIRTYTWO)
                    doc.bitsPerChannel = BitsPerChannelType.SIXTEEN;
            }
            doc.saveAs(new File(path), new PNGSaveOptions(), true, Extension.LOWERCASE);
        } finally {
            close(copy);
        }
    }

    function openInput(path) {
        var source = new File(path);
        if (!source.exists) throw Error("任务文件不存在或不可读取。");
        try {
            return app.open(source);
        } catch (error) {
            if (!/\.psd$/i.test(path)) throw error;
            try {
                return app.open(source, OpenDocumentType.PHOTOSHOP);
            } catch (retryError) {
                throw Error(error + "；按 PSD 格式重试失败：" + retryError);
            }
        }
    }

    function fitArtwork(doc, width, height, resolution) {
        var sourceWidth = doc.width.as("px"), sourceHeight = doc.height.as("px");
        var scale = Math.min(sourceWidth / width, sourceHeight / height);
        var cropWidth = Math.max(1, Math.round(width * scale));
        var cropHeight = Math.max(1, Math.round(height * scale));
        if (cropWidth !== sourceWidth || cropHeight !== sourceHeight) {
            var left = Math.floor((sourceWidth - cropWidth) / 2);
            var top = Math.floor((sourceHeight - cropHeight) / 2);
            doc.crop([new UnitValue(left, "px"), new UnitValue(top, "px"),
                new UnitValue(left + cropWidth, "px"), new UnitValue(top + cropHeight, "px")]);
        }
        if (doc.width.as("px") !== width || doc.height.as("px") !== height || doc.resolution !== resolution) {
            doc.resizeImage(new UnitValue(width, "px"), new UnitValue(height, "px"), resolution,
                ResampleMethod.BICUBIC);
        }
        if (doc.layers.length > 1) doc.mergeVisibleLayers();
    }

    function insertArtwork(doc, artwork) {
        app.activeDocument = doc;
        for (var i = 0; i < doc.layers.length; i++) doc.layers[i].visible = false;

        app.activeDocument = artwork;
        var sourceLayer = artwork.activeLayer;
        var sourceBounds = sourceLayer.bounds;
        var placed = sourceLayer.duplicate(doc, ElementPlacement.PLACEATBEGINNING);

        app.activeDocument = doc;
        placed.name = "Mockup artwork";
        placed.visible = true;
        var placedBounds = placed.bounds;
        var dx = sourceBounds[0].as("px") - placedBounds[0].as("px");
        var dy = sourceBounds[1].as("px") - placedBounds[1].as("px");
        if (dx || dy) placed.translate(new UnitValue(dx, "px"), new UnitValue(dy, "px"));
        doc.save();
    }

    function replaceArtwork(target, input, replacement) {
        app.activeDocument = work;
        work.activeLayer = target;
        var openIds = {};
        for (var i = 0; i < app.documents.length; i++) openIds[app.documents[i].id] = true;
        executeAction(stringIDToTypeID("placedLayerEditContents"), undefined, DialogModes.NO);
        if (openIds[app.activeDocument.id]) throw Error("智能对象未在独立的新文档中打开");
        child = app.activeDocument;
        art = openInput(input);
        fitArtwork(art, child.width.as("px"), child.height.as("px"), child.resolution);
        if (/\.ps[bd]$/i.test(child.name)) {
            insertArtwork(child, art);
        } else {
            art.saveAs(new File(replacement), new PhotoshopSaveOptions(), true, Extension.LOWERCASE);
            close(child); child = null;
            app.activeDocument = work;
            work.activeLayer = target;
            var desc = new ActionDescriptor();
            desc.putPath(charIDToTypeID("null"), new File(replacement));
            executeAction(stringIDToTypeID("placedLayerReplaceContents"), desc, DialogModes.NO);
        }
        close(art); art = null;
        close(child); child = null;
    }

    function fillColor(layer, hex) {
        app.activeDocument = work;
        work.activeLayer = layer;
        var color = new SolidColor();
        color.rgb.hexValue = hex.substring(1);
        if (layer.kind === LayerKind.SOLIDFILL) {
            var ref = new ActionReference(), set = new ActionDescriptor();
            ref.putEnumerated(stringIDToTypeID("contentLayer"), charIDToTypeID("Ordn"), charIDToTypeID("Trgt"));
            set.putReference(charIDToTypeID("null"), ref);
            var rgb = new ActionDescriptor(), fill = new ActionDescriptor();
            rgb.putDouble(charIDToTypeID("Rd  "), color.rgb.red);
            rgb.putDouble(charIDToTypeID("Grn "), color.rgb.green);
            rgb.putDouble(charIDToTypeID("Bl  "), color.rgb.blue);
            fill.putObject(charIDToTypeID("Clr "), charIDToTypeID("RGBC"), rgb);
            set.putObject(charIDToTypeID("T   "), stringIDToTypeID("solidColorLayer"), fill);
            executeAction(charIDToTypeID("setd"), set, DialogModes.NO);
        } else {
            work.selection.selectAll();
            work.selection.fill(color, ColorBlendMode.NORMAL, 100, true);
            work.selection.deselect();
        }
    }

    try {
        app.displayDialogs = DialogModes.NO;
        app.preferences.rulerUnits = Units.PIXELS;
        work = openInput(job.template);
        var objects = [], targets = [], assigned = {}, skipped = [];
        collect(work.layers, "", objects);
        for (var i = 0; i < job.pairs.length; i++) {
            var pair = job.pairs[i];
            stage = "查找图层「" + pair.name + "」";
            var layer = findTarget(objects, pair);
            if (!layer) { skipped.push(i); continue; }
            var key = layer.kind === LayerKind.SMARTOBJECT ? "source-" + sourceId(layer) : "layer-" + layer.id;
            if (assigned[key]) {
                if (assigned[key].input !== pair.input)
                    throw Error("与「" + assigned[key].name + "」共用同一智能对象，无法使用不同图片");
                continue;
            }
            assigned[key] = pair;
            targets.push({layer: layer, pair: pair});
        }
        for (var t = 0; t < targets.length; t++) {
            var task = targets[t];
            stage = "替换图层「" + task.pair.name + "」";
            if (task.layer.kind === LayerKind.SMARTOBJECT)
                replaceArtwork(task.layer, task.pair.input, task.pair.replacement);
            else
                fillColor(task.layer, task.pair.color);
        }
        if (!targets.length) return "SKIP";
        app.activeDocument = work;
        if (job.exportMockup) {
            stage = "导出 PNG";
            savePng(work, job.output);
        }
        if (!job.exportSubject) return "OK:" + skipped.join(",");
        stage = "导出主体透明图";
        var subjects = [];
        findSubject(work.layerSets, [], subjects);
        if (!subjects.length) return "NO_SUBJECT:" + skipped.join(",");
        if (subjects.length > 1) throw Error("存在多个「主体」分组，无法确定透明图的主体");
        app.activeDocument = work;
        isolateSubject(work, subjects[0]);
        squareCrop(work);
        savePng(work, job.transparentOutput);
        return "OK:" + skipped.join(",");
    } catch (error) {
        return "ERROR " + stage + "：" + error.toString() + " (line " + error.line + ")";
    } finally {
        try { close(art); } catch (ignored) {}
        try { close(child); } catch (ignored) {}
        try { close(work); } catch (ignored) {}
        try { if (original) app.activeDocument = original; } catch (ignored) {}
        app.preferences.rulerUnits = rulerUnits;
        app.displayDialogs = dialogs;
    }
}());
"""


def transparent_path(output):
    return output.with_name(output.stem + "-透明图.png")


def render(template, output, pairs, *, colors=None, skip_missing=False, on_processed=None,
           on_photoshop_start=None, export_mockup=True, export_subject=True):
    # Photoshop opens private working copies even if the originals are already open.
    with tempfile.TemporaryDirectory(prefix="mockup_", dir=output.parent) as temp:
        folder = Path(temp)
        template_copy = folder / ("template" + template.suffix.lower())
        shutil.copyfile(template, template_copy)
        staged = folder / "result.png"
        staged_subject = folder / "subject.png"
        inputs = {}
        tasks = []
        for index, (name, image) in enumerate(pairs):
            if image not in inputs:
                inputs[image] = folder / f"art{index}{image.suffix.lower()}"
                shutil.copyfile(image, inputs[image])
            tasks.append({"name": name, "input": str(inputs[image]), "color": (colors or {}).get(name),
                          "replacement": str(folder / f"replacement{index}.psd")})

        job = {"template": str(template_copy), "pairs": tasks, "output": str(staged),
               "transparentOutput": str(staged_subject), "skipMissing": skip_missing,
               "exportMockup": export_mockup, "exportSubject": export_subject}
        script = folder / "render.jsx"
        script.write_text("#target photoshop\nvar job = " + json.dumps(job) + ";\n" + RENDER_SCRIPT,
                          encoding="utf-8-sig")
        command = """
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$ps = New-Object -ComObject Photoshop.Application
$result = $ps.DoJavaScriptFile($env:MOCKUP_JSX_PATH)
Write-Output $result
"""
        if on_photoshop_start:
            on_photoshop_start()
        process = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            env={**os.environ, "MOCKUP_JSX_PATH": str(script)},
            capture_output=True, encoding="utf-8", errors="replace",
        )
        result = process.stdout.strip()
        # A JSX response confirms Photoshop actually processed this template.
        if on_processed and (result == "SKIP" or result.startswith(("OK:", "NO_SUBJECT:", "ERROR "))):
            on_processed()
        if not process.returncode and result == "SKIP":
            return [name for name, _ in pairs]
        status, _, indices = result.partition(":")
        if process.returncode or not result.startswith(("OK:", "NO_SUBJECT:")):
            raise RuntimeError(process.stderr.strip() or result or "Photoshop 未生成 PNG")
        if export_mockup and not staged.is_file():
            raise RuntimeError("Photoshop 未生成样机合成图")
        if not export_mockup and status == "NO_SUBJECT":
            raise RuntimeError("样机缺少「主体」分组，无法生成透明图")
        skipped = [pairs[int(index)][0] for index in indices.split(",") if index]
        # On Windows rename publishes a completed file and refuses an existing destination.
        if status == "OK" and export_subject:
            subject = transparent_path(output)
            staged_subject.rename(subject)
        if export_mockup:
            try:
                staged.rename(output)
            except OSError:
                if status == "OK" and export_subject:
                    subject.unlink()
                raise
        return skipped


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("template", type=Path, metavar="样机.psb", help="PSD 或 PSB 文件")
    parser.add_argument("-o", required=True, type=Path, metavar="输出.png", help="新 PNG 文件的路径")
    parser.add_argument("-l", required=True, action="append", nargs="+", metavar="图层名或图片",
                        help="按「图层名 替换图」成对填写，重名时可用「组名/图层名」")
    args = parser.parse_args()
    if any(len(group) % 2 for group in args.l):
        parser.error("-l 后必须成对填写图层名和替换图，最后一个图层缺少图片")
    template = args.template.resolve()
    output = args.o.resolve()
    if not template.is_file() or template.suffix.lower() not in {".psd", ".psb"}:
        parser.error(f"请指定已有的 PSD 或 PSB 样机：{template}")
    if output.suffix.lower() != ".png" or output.exists():
        parser.error(f"请指定新的 PNG 输出文件：{output}")
    if transparent_path(output).exists():
        parser.error(f"透明图输出文件已存在：{transparent_path(output)}")
    if not output.parent.is_dir():
        parser.error(f"输出目录不存在：{output.parent}")
    pairs = []
    seen = set()
    tokens = [token for group in args.l for token in group]
    for name, filename in zip(tokens[::2], tokens[1::2]):
        image = Path(filename).resolve()
        if not name.strip() or name in seen:
            parser.error(f"图层名为空或重复：{name!r}")
        if not image.is_file() or not image.suffix:
            parser.error(f"图层「{name}」的替换图不存在或缺少扩展名：{image}")
        seen.add(name)
        pairs.append((name, image))
    if os.name != "nt":
        parser.error("请在已安装 Photoshop 的 Windows 桌面会话中运行")
    try:
        render(template, output, pairs)
    except (OSError, RuntimeError) as error:
        parser.exit(1, f"生成失败：{error}\n")
    print(output)
    if transparent_path(output).is_file():
        print(transparent_path(output))
    else:
        print("模板缺少「主体」分组，仅生成合成图。", file=sys.stderr)


if __name__ == "__main__":
    main()
