#target photoshop
(function () {
  var data = new Folder(new File($.fileName).parent.parent.fsName + "/data");
  if (!data.exists && !data.create()) throw Error("Could not create the data directory.");
  var log = new File(data.fsName + "/mockup_inspect.txt");
  log.encoding = "UTF8";
  if (!log.open("w")) throw Error("Could not write the mockup inspection report.");
  var mode=app.displayDialogs, doc;
  function walk(layers, prefix) {
    for(var i=0;i<layers.length;i++){
      var l=layers[i], path=prefix+"/"+l.name;
      log.writeln([path,l.typename,l.typename==="ArtLayer"?l.kind:"",l.id,l.visible].join("\t"));
      if(l.typename==="LayerSet") walk(l.layers,path);
    }
  }
  try {
    app.displayDialogs=DialogModes.NO;
    var input=new File("E:\\Users\\lee\\A0-WorkData\\2.店铺文件\\7.棉创U选旗舰店\\样机\\898(1).psd");
    for(var i=0;i<app.documents.length;i++) {
      try { if(app.documents[i].fullName.fsName===input.fsName) doc=app.documents[i]; }catch(e){}
    }
    var owned=!doc;
    if(owned) doc=app.open(input);
    log.writeln("Photoshop "+app.version+" "+doc.width.as("px")+"x"+doc.height.as("px")+" "+doc.resolution);
    walk(doc.layers,"");
    if(owned) doc.close(SaveOptions.DONOTSAVECHANGES);
  }catch(e){log.writeln("ERROR "+e+" line "+e.line);}
  finally {app.displayDialogs=mode;log.close();}
}());
