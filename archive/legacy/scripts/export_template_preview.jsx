(function () {
    var original = app.documents.length ? app.activeDocument : null;
    var mode = app.displayDialogs;
    var input = null, preview = null, ownsInput = false;
    var report = new File(job.report);
    report.encoding = "UTF-8";

    function collect(layers, prefix, rows) {
        for (var i = 0; i < layers.length; i++) {
            var layer = layers[i];
            if (!layer.visible || layer.opacity <= 0) continue;
            var path = prefix + layer.name;
            if (layer.typename === "LayerSet") {
                collect(layer.layers, path + " / ", rows);
            } else if (layer.kind === LayerKind.SMARTOBJECT) {
                var key = stringIDToTypeID("smartObject");
                var ref = new ActionReference();
                ref.putProperty(stringIDToTypeID("property"), key);
                ref.putIdentifier(stringIDToTypeID("layer"), layer.id);
                var info = executeActionGet(ref).getObjectValue(key);
                var linked = stringIDToTypeID("linked"), file = stringIDToTypeID("fileReference");
                if ((info.hasKey(linked) && info.getBoolean(linked)) ||
                    (info.hasKey(file) && /\.(ai|eps|pdf|svg)$/i.test(info.getString(file)))) continue;
                var b = layer.bounds;
                rows.push([layer.id, encodeURIComponent(layer.name), encodeURIComponent(path),
                    Math.floor(b[1].as("px")), Math.floor(b[0].as("px")),
                    Math.ceil(b[3].as("px")), Math.ceil(b[2].as("px"))].join("\t"));
            }
        }
    }

    try {
        app.displayDialogs = DialogModes.NO;
        var openIds = {};
        for (var i = 0; i < app.documents.length; i++) openIds[app.documents[i].id] = true;
        var source = new File(job.template);
        for (var j = 0; j < app.documents.length; j++) {
            var doc = app.documents[j];
            try {
                if (doc.fullName.fsName.toLowerCase() === source.fsName.toLowerCase()) {
                    input = doc;
                    break;
                }
            } catch (ignored) {}
        }
        if (!input) input = app.open(source);
        ownsInput = !openIds[input.id];
        if (!input.saved) throw Error("Save the open template before exporting a preview.");
        app.activeDocument = input;
        app.activeDocument = input;
        var rows = ["OK"];
        collect(input.layers, "", rows);
        if (job.output) {
            preview = input.duplicate("Template preview", true);
            app.activeDocument = preview;
            if (preview.mode !== DocumentMode.RGB && preview.mode !== DocumentMode.GRAYSCALE) {
                preview.changeMode(ChangeMode.RGB);
            }
            if (preview.bitsPerChannel === BitsPerChannelType.THIRTYTWO) {
                preview.bitsPerChannel = BitsPerChannelType.SIXTEEN;
            }
            var width = preview.width.as("px"), height = preview.height.as("px");
            var scale = job.short_edge / Math.min(width, height);
            preview.resizeImage(new UnitValue(Math.round(width * scale), "px"),
                new UnitValue(Math.round(height * scale), "px"), preview.resolution, ResampleMethod.BICUBIC);
            preview.saveAs(new File(job.output), new PNGSaveOptions(), true, Extension.LOWERCASE);
        }
        if (report.open("w")) {
            report.write(rows.join("\n"));
            report.close();
        }
    } catch (error) {
        if (report.open("w")) {
            report.write("ERROR " + error + " (Photoshop " + app.version + ", line " + error.line + ")");
            report.close();
        }
    } finally {
        try { if (preview) preview.close(SaveOptions.DONOTSAVECHANGES); } catch (ignored) {}
        try { if (input && ownsInput) input.close(SaveOptions.DONOTSAVECHANGES); } catch (ignored) {}
        try { if (original) app.activeDocument = original; } catch (ignored) {}
        app.displayDialogs = mode;
    }
}());
