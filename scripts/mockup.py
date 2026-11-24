"""Apply an image to a Photoshop mockup and render the visible composite."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import PROJECT_ROOT, load_environment, resolve_template_dir

DEFAULT_TEMPLATE = "898(1).psd"
A_SIDE_LAYER = 21
JSX = Path(__file__).with_name("render_mockup.jsx")
DATA = PROJECT_ROOT / "data"
OUTPUT_EXTENSIONS = {".png", ".jpg", ".jpeg"}


def prepare_job(folder, template, artwork, output, baseline, layer):
    template_copy = folder / f"template{template.suffix.lower()}"
    artwork_copy = folder / f"artwork{artwork.suffix.lower()}"
    shutil.copyfile(template, template_copy)
    shutil.copyfile(artwork, artwork_copy)

    def destination(path, role):
        if path is None:
            return None
        suffix = ".png" if path.suffix.lower() == ".png" else ".jpg"
        staged = path.with_name(f"{folder.name}_{role}{suffix}")
        return {"path": str(path), "staged": str(staged), "ext": path.suffix.lower()}

    job = {
        "template": str(template_copy),
        "artwork": str(artwork_copy),
        "replacement": str(folder / "replacement.psd"),
        "output": destination(output, "output"),
        "baseline": destination(baseline, "baseline"),
        "report": str(folder / "result.txt"),
        "layer": layer,
    }
    script = folder / "run_mockup.jsx"
    script.write_text(
        "#target photoshop\nvar job = " + json.dumps(job) + ";\n"
        + JSX.read_text(encoding="utf-8"),
        encoding="utf-8-sig",
    )
    return script


def run_in_photoshop(script):
    command = (
        "$ErrorActionPreference = 'Stop'; "
        "$photoshop = New-Object -ComObject Photoshop.Application; "
        "$null = $photoshop.DoJavaScriptFile($env:MOCKUP_JSX_PATH)"
    )
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        env={**os.environ, "MOCKUP_JSX_PATH": str(script)},
        capture_output=True,
        text=True,
        errors="replace",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path,
                        help="PSD or PSB path, or a filename in PHOTOSHOP_TEMPLATE_DIR. Defaults to 898(1).psd.")
    parser.add_argument("--art", required=True, type=Path, help="Artwork image.")
    parser.add_argument("--out", type=Path, help="Destination. Defaults to data/<art>_<template>.png.")
    parser.add_argument("--layer", help="Smart Object layer name or ID; optional for a single visible Smart Object.")
    parser.add_argument("--baseline", type=Path, help="Optional rendering of the original mockup.")
    parser.add_argument("--prepare-only", action="store_true",
                        help="Prepare JSX for File > Scripts > Browse in Photoshop.")
    args = parser.parse_args()

    template = args.template
    if template is None or (not template.is_absolute() and not template.is_file()):
        template_dir = resolve_template_dir(load_environment())
        template = template_dir / (template or DEFAULT_TEMPLATE)
    template = template.resolve()
    artwork = args.art.resolve()
    output = (args.out or DATA / f"{artwork.stem}_{template.stem}.png").resolve()
    baseline = args.baseline.resolve() if args.baseline else None
    layer = args.layer
    if layer is None and args.template is None:
        layer = A_SIDE_LAYER
    elif layer is not None and layer.isascii() and layer.isdecimal():
        layer = int(layer)

    if not template.is_file() or template.suffix.lower() not in {".psd", ".psb"}:
        parser.error(f"Provide an existing .psd or .psb template: {template}")
    with template.open("rb") as file:
        header = file.read(6)
    version = b"\x00\x01" if template.suffix.lower() == ".psd" else b"\x00\x02"
    if header != b"8BPS" + version:
        parser.error(f"Template contents do not match the file extension: {template}")
    if not artwork.is_file() or not artwork.suffix:
        parser.error(f"Provide an existing image with a file extension: {artwork}")
    if args.out is None:
        DATA.mkdir(exist_ok=True)
    for path in (output, baseline):
        if path is not None and (path.exists() or path.suffix.lower() not in OUTPUT_EXTENSIONS):
            parser.error(f"Provide a new .png, .jpg or .jpeg destination: {path}")
        if path is not None and not path.parent.is_dir():
            parser.error(f"Destination directory does not exist: {path.parent}")
    if baseline == output:
        parser.error("Baseline and output must be different.")
    if not args.prepare_only and os.name != "nt":
        parser.error("Automatic rendering requires Windows Photoshop. Use --prepare-only to run the JSX in Photoshop.")

    folder = Path(tempfile.mkdtemp(prefix="mockup_", dir=output.parent))
    keep_script = False
    try:
        script = prepare_job(folder, template, artwork, output, baseline, layer)
        if args.prepare_only:
            keep_script = True
            print(script)
            return

        process = run_in_photoshop(script)
        report = folder / "result.txt"
        if not report.is_file():
            keep_script = True
            parser.exit(1, f"Photoshop did not run the script. {process.stderr.strip()}\n"
                           f"Run it in Photoshop via File > Scripts > Browse: {script}\n")
        result = report.read_text(encoding="utf-8-sig").strip()
        if not result.startswith("OK ") or not output.is_file() or (baseline and not baseline.is_file()):
            parser.exit(1, f"{result or 'No render was produced.'}\n")
        print(result)
        print(output)
    finally:
        if not keep_script:
            shutil.rmtree(folder)


if __name__ == "__main__":
    main()
