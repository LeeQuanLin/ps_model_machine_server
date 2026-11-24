(function () {
    var original = app.documents.length ? app.activeDocument : null;
    var mode = app.displayDialogs;
    var input = null, preview = null, ownsInput = false;
    var report = new File(job.report);
    report.encoding = "UTF-8";
    try {
        app.displayDialogs = DialogModes.NO;
        var openIds = {};
        for (var i = 0; i < app.documents.length; i++) openIds[app.documents[i].id] = true;
        var source = new File(job.template);
        for (var j = 0; j < app.documents.length; j++) {
            var doc = app.documents[j];
            if (doc.saved && doc.fullName.fsName.toLowerCase() === source.fsName.toLowerCase()) {
                input = doc;
                break;
            }
        }
        if (!input) input = app.open(source);
        ownsInput = !openIds[input.id];
        if (!input.saved) throw Error("Save the open template before exporting a preview.");
        app.activeDocument = input;
        preview = input.duplicate("Template preview", true);
        if (ownsInput) {
            input.close(SaveOptions.DONOTSAVECHANGES);
            ownsInput = false;
        }
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
    } catch (error) {
        if (report.open("w")) {
            report.write(String(error));
            report.close();
        }
    } finally {
        try { if (preview) preview.close(SaveOptions.DONOTSAVECHANGES); } catch (ignored) {}
        try { if (input && ownsInput) input.close(SaveOptions.DONOTSAVECHANGES); } catch (ignored) {}
        try { if (original) app.activeDocument = original; } catch (ignored) {}
        app.displayDialogs = mode;
    }
}());
