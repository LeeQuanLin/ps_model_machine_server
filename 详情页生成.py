"""顺序替换 Photoshop 详情页模板中的智能对象，按指定高度导出 PNG 切片。"""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


PHOTOSHOP_SCRIPT = r"""
(function () {
    var original = app.documents.length ? app.activeDocument : null;
    var dialogs = app.displayDialogs, units = app.preferences.rulerUnits;
    var work = null, art = null, page = null, stage = "打开详情页模板";
    var key = stringIDToTypeID;

    function close(doc) {
        if (doc) doc.close(SaveOptions.DONOTSAVECHANGES);
    }

    function findGroups(groups, matches) {
        for (var i = 0; i < groups.length; i++) {
            if (groups[i].name === "替换区") matches.push(groups[i]);
            findGroups(groups[i].layerSets, matches);
        }
    }

    function collectSlots(layers, slots) {
        for (var i = 0; i < layers.length; i++) {
            var layer = layers[i];
            if (layer.typename === "LayerSet") collectSlots(layer.layers, slots);
            else if (layer.kind === LayerKind.SMARTOBJECT) slots.push(layer);
        }
    }

    function smartInfo(layer) {
        var ref = new ActionReference();
        ref.putProperty(key("property"), key("smartObjectMore"));
        ref.putIdentifier(key("layer"), layer.id);
        return executeActionGet(ref).getObjectValue(key("smartObjectMore"));
    }

    function number(desc, name) {
        var field = key(name);
        if (desc.getType(field) === DescValueType.UNITDOUBLE) return desc.getUnitDoubleValue(field);
        return desc.getDouble(field);
    }

    function px(n) { return new UnitValue(n, "px"); }

    function replaceSlot(layer, info, input, index, shared) {
        var source = new File(input);
        if (!source.exists) throw Error("找不到输入图片：" + input);
        var ext = source.name.match(/\.[^.]+$/);
        var copy = new File(job.temp + "/art-" + index + (ext ? ext[0] : ""));
        if (!source.copy(copy)) throw Error("无法读取输入图片：" + input);
        art = app.open(copy);
        var size = info.getObjectValue(key("size"));
        var w = Math.round(number(size, "width")), h = Math.round(number(size, "height"));
        var aw = art.width.as("px"), ah = art.height.as("px");
        var scale = Math.min(aw / w, ah / h);
        var cw = Math.max(1, Math.round(w * scale)), ch = Math.max(1, Math.round(h * scale));
        var x = Math.floor((aw - cw) / 2), y = Math.floor((ah - ch) / 2);
        if (cw !== aw || ch !== ah) art.crop([px(x), px(y), px(x + cw), px(y + ch)]);
        art.resizeImage(px(w), px(h), number(info, "resolution"), ResampleMethod.BICUBIC);
        var replacement = new File(job.temp + "/replacement-" + index + ".psd");
        art.saveAs(replacement, new PhotoshopSaveOptions(), true, Extension.LOWERCASE);
        close(art); art = null;

        app.activeDocument = work;
        work.activeLayer = layer;
        if (shared) {
            // A shared slot needs independent contents so later or unused slots keep theirs.
            var name = layer.name, visible = layer.visible;
            executeAction(key("placedLayerMakeCopy"), undefined, DialogModes.NO);
            var independent = work.activeLayer;
            if (independent.id === layer.id) throw Error("无法建立独立的智能对象");
            layer.remove();
            independent.name = name;
            independent.visible = visible;
            work.activeLayer = independent;
        }
        var desc = new ActionDescriptor();
        desc.putPath(charIDToTypeID("null"), replacement);
        executeAction(key("placedLayerReplaceContents"), desc, DialogModes.NO);
    }

    function exportSlices(doc, height) {
        var width = Math.round(doc.width.as("px")), total = Math.round(doc.height.as("px"));
        var whole = doc.activeHistoryState, count = 0;
        var options = new PNGSaveOptions();
        options.compression = 9;
        for (var top = 0; top < total; top += height) {
            count++;
            stage = "导出第 " + count + " 张切片";
            doc.crop([px(0), px(top), px(width), px(Math.min(top + height, total))]);
            var name = String(count);
            while (name.length < 3) name = "0" + name;
            doc.saveAs(new File(job.output + "/" + name + ".png"), options, true, Extension.LOWERCASE);
            if (top + height < total) doc.activeHistoryState = whole;
        }
        return count;
    }

    try {
        app.displayDialogs = DialogModes.NO;
        app.preferences.rulerUnits = Units.PIXELS;
        work = app.open(new File(job.template));
        var groups = [], slots = [];
        findGroups(work.layerSets, groups);
        if (groups.length !== 1) throw Error("需要唯一的「替换区」分组，实际找到 " + groups.length + " 个");
        collectSlots(groups[0].layers, slots);
        if (!slots.length) throw Error("「替换区」中没有智能对象");
        var infos = [], ids = [], remaining = {};
        for (var i = 0; i < slots.length; i++) {
            var info = smartInfo(slots[i]);
            var id = info.hasKey(key("ID")) ? info.getString(key("ID")) : String(slots[i].id);
            infos.push(info);
            ids.push(id);
            remaining[id] = (remaining[id] || 0) + 1;
        }
        var used = Math.min(slots.length, job.images.length);
        for (var n = 0; n < used; n++) {
            stage = "替换第 " + (n + 1) + " 个智能对象「" + slots[n].name + "」";
            remaining[ids[n]]--;
            replaceSlot(slots[n], infos[n], job.images[n], n, remaining[ids[n]] > 0);
        }
        stage = "合成详情页";
        app.activeDocument = work;
        page = work.duplicate("Detail page slices", true);
        close(work); work = null;
        app.activeDocument = page;
        if (page.mode !== DocumentMode.RGB && page.mode !== DocumentMode.GRAYSCALE)
            page.changeMode(ChangeMode.RGB);
        if (page.bitsPerChannel === BitsPerChannelType.THIRTYTWO)
            page.bitsPerChannel = BitsPerChannelType.SIXTEEN;
        return "OK:" + used + ":" + slots.length + ":" + exportSlices(page, job.height);
    } catch (e) {
        return "ERROR " + stage + "：" + e.toString() + " (line " + e.line + ")";
    } finally {
        try { close(art); } catch (ignored) {}
        try { close(page); } catch (ignored) {}
        try { close(work); } catch (ignored) {}
        try { if (original) app.activeDocument = original; } catch (ignored) {}
        app.preferences.rulerUnits = units;
        app.displayDialogs = dialogs;
    }
}());
"""

POWERSHELL = """
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$ps = New-Object -ComObject Photoshop.Application
Write-Output ($ps.DoJavaScriptFile($env:DETAIL_PAGE_JSX))
"""


def generate(template, output, images, slice_height=960, *, on_processed=None):
    """在新目录中一次性发布全部切片，返回替换数、模板槽位数、切片数。"""
    template, output = Path(template).resolve(), Path(output).resolve()
    images = [str(Path(image).absolute()) for image in images]
    if not template.is_file() or template.suffix.lower() not in {".psd", ".psb"}:
        raise ValueError(f"请指定已有的 PSD 或 PSB 模板：{template}")
    if not images or slice_height < 1:
        raise ValueError("至少提供一张输入图片，切片高度必须为正整数")
    if output.exists():
        raise FileExistsError(f"输出目录已存在，请指定新目录：{output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="detail_", dir=output.parent) as temp:
        folder = Path(temp)
        private = folder / ("template" + template.suffix.lower())
        shutil.copyfile(template, private)
        staged = folder / "slices"
        staged.mkdir()
        job = {"template": str(private), "temp": temp, "output": str(staged),
               "images": images, "height": slice_height}
        script = folder / "detail.jsx"
        script.write_text("#target photoshop\nvar job = " + json.dumps(job) + ";\n" + PHOTOSHOP_SCRIPT,
                          encoding="utf-8-sig")
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", POWERSHELL],
            env={**os.environ, "DETAIL_PAGE_JSX": str(script)}, capture_output=True,
            encoding="utf-8", errors="replace",
        )
        message = result.stdout.strip()
        if on_processed and message.startswith(("OK:", "ERROR ")):
            on_processed()
        if result.returncode or not message.startswith("OK:"):
            raise RuntimeError(result.stderr.strip() or message or "Photoshop 未返回生成结果")
        used, slots, count = map(int, message[3:].split(":"))
        if count < 1 or any(not (staged / f"{i:03d}.png").is_file() for i in range(1, count + 1)):
            raise RuntimeError("Photoshop 导出的切片不完整")
        staged.rename(output)
    return used, slots, count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("template", type=Path, metavar="详情模板.psb")
    parser.add_argument("-i", required=True, nargs="+", type=Path, metavar="图片", help="按图层面板从上到下排列")
    parser.add_argument("-o", required=True, type=Path, metavar="输出目录", help="为本次切片创建的新目录")
    parser.add_argument("--slice-height", type=int, default=960, metavar="像素", help="每张切片的高度，默认 960")
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("请在已安装 Photoshop 的 Windows 桌面会话中运行")
    try:
        used, slots, count = generate(args.template, args.o, args.i, args.slice_height)
    except (OSError, RuntimeError, ValueError) as exc:
        parser.exit(1, f"生成失败：{exc}\n")
    print(f"替换 {used}/{slots} 个智能对象，忽略 {len(args.i) - used} 张多余图片。"
          f"已导出 {count} 张切片到 {args.o.resolve()}")


if __name__ == "__main__":
    main()
