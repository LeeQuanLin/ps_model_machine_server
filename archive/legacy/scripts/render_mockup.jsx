(function () {
    var dialogMode = app.displayDialogs;
    var originalDocument = app.documents.length ? app.activeDocument : null;
    var work = null, child = null, art = null;
    var baselinePublished = false;
    var report = new File(job.report);
    report.encoding = "UTF-8";

    function collectSmartObjects(layers, prefix, parentVisible, found) {
        for (var i = 0; i < layers.length; i++) {
            var layer = layers[i];
            var path = prefix + layer.name;
            var visible = parentVisible && layer.visible;
            if (layer.typename === "LayerSet") {
                collectSmartObjects(layer.layers, path + " / ", visible, found);
            } else if (layer.kind === LayerKind.SMARTOBJECT) {
                found.push({layer: layer, path: path, visible: visible});
            }
        }
    }

    function selectSmartObject(doc, selector) {
        var found = [], matches = [], descriptions = [];
        collectSmartObjects(doc.layers, "", true, found);
        for (var i = 0; i < found.length; i++) {
            var item = found[i];
            descriptions.push(item.path + " [ID " + item.layer.id + "]" + (item.visible ? "" : " (hidden)"));
            if (selector === null ? item.visible : selector === item.layer.id || selector === item.layer.name) {
                matches.push(item);
            }
        }
        if (matches.length !== 1) {
            throw Error("Choose one visible Smart Object by name or ID with --layer. Candidates: "
                + (descriptions.length ? descriptions.join("; ") : "none"));
        }
        if (!matches[0].visible) throw Error("The selected Smart Object is hidden: " + matches[0].path);
        return matches[0].layer;
    }

    function requireEmbeddedObject(layer) {
        var smartObjectKey = stringIDToTypeID("smartObject");
        var ref = new ActionReference();
        ref.putProperty(stringIDToTypeID("property"), smartObjectKey);
        ref.putIdentifier(stringIDToTypeID("layer"), layer.id);
        var info = executeActionGet(ref).getObjectValue(smartObjectKey);
        var linkedKey = stringIDToTypeID("linked");
        if (info.hasKey(linkedKey) && info.getBoolean(linkedKey)) {
            throw Error("Embed the selected Smart Object in the template before rendering: " + layer.name);
        }
        var fileKey = stringIDToTypeID("fileReference");
        if (info.hasKey(fileKey) && /\.(ai|eps|pdf|svg)$/i.test(info.getString(fileKey))) {
            throw Error("The selected Smart Object requires an external editor: " + layer.name);
        }
    }

    function close(doc) {
        if (doc) doc.close(SaveOptions.DONOTSAVECHANGES);
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

    function stageComposite(doc, destination) {
        var options;
        var copy = null;
        try {
            if (destination.ext === ".png") {
                options = new PNGSaveOptions();
                if (doc.mode !== DocumentMode.RGB && doc.mode !== DocumentMode.GRAYSCALE
                    && doc.mode !== DocumentMode.INDEXEDCOLOR) {
                    copy = doc.duplicate("Mockup PNG", true);
                    copy.changeMode(ChangeMode.RGB);
                    doc = copy;
                }
            } else {
                options = new JPEGSaveOptions();
                options.quality = 11;
                options.embedColorProfile = true;
            }
            doc.saveAs(new File(destination.staged), options, true, Extension.LOWERCASE);
        } finally {
            close(copy);
        }
    }

    function publish(destination) {
        var file = new File(destination.staged);
        var finalFile = new File(destination.path);
        if (finalFile.exists || !file.rename(File.decode(finalFile.name))) {
            throw Error("Could not write destination: " + destination.path);
        }
    }

    function removeFile(path) {
        var file = new File(path);
        if (file.exists) file.remove();
    }

    var result, stage = "初始化";
    try {
        app.displayDialogs = DialogModes.NO;
        var openDocuments = {};
        for (var i = 0; i < app.documents.length; i++) openDocuments[app.documents[i].id] = true;
        stage = "打开样机模板 " + job.template_name;
        work = openInput(job.template);
        openDocuments[work.id] = true;
        stage = "选择替换图层";
        var target = selectSmartObject(work, job.layer);
        requireEmbeddedObject(target);
        if (job.baseline) {
            stage = "导出原样机";
            stageComposite(work, job.baseline);
        }
        stage = "打开智能对象内容";
        work.activeLayer = target;
        executeAction(stringIDToTypeID("placedLayerEditContents"), undefined, DialogModes.NO);
        if (openDocuments[app.activeDocument.id]) throw Error("Photoshop did not open a separate Smart Object document.");
        child = app.activeDocument;

        stage = "打开印花图片";
        art = openInput(job.artwork);
        stage = "替换智能对象内容";
        fitArtwork(art, child.width.as("px"), child.height.as("px"), child.resolution);
        if (/\.ps[bd]$/i.test(child.name)) {
            insertArtwork(child, art);
        } else {
            // A JPEG or PNG Smart Object cannot save Photoshop layers in its original format.
            art.saveAs(new File(job.replacement), new PhotoshopSaveOptions(), true, Extension.LOWERCASE);
            close(child); child = null;
            app.activeDocument = work;
            work.activeLayer = target;
            var replacement = new ActionDescriptor();
            replacement.putPath(charIDToTypeID("null"), new File(job.replacement));
            executeAction(stringIDToTypeID("placedLayerReplaceContents"), replacement, DialogModes.NO);
        }
        close(art); art = null;
        close(child); child = null;
        stage = "导出生成图";
        app.activeDocument = work;
        stageComposite(work, job.output);
        if (job.baseline) {
            publish(job.baseline);
            baselinePublished = true;
        }
        publish(job.output);
        result = "OK Photoshop " + app.version + " " + work.width.as("px") + "x" + work.height.as("px");
    } catch (error) {
        if (baselinePublished) removeFile(job.baseline.path);
        result = "ERROR " + stage + "：" + error + " (Photoshop " + app.version + ", line " + error.line + ")";
    } finally {
        try { close(art); } catch (ignored) {}
        try { close(child); } catch (ignored) {}
        try { close(work); } catch (ignored) {}
        try { if (originalDocument) app.activeDocument = originalDocument; } catch (ignored) {}
        removeFile(job.output.staged);
        if (job.baseline) removeFile(job.baseline.staged);
        app.displayDialogs = dialogMode;
        if (report.open("w")) { report.write(result); report.close(); }
    }
    return result;
}());
