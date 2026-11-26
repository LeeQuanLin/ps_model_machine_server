import json
from pathlib import Path


DEFAULT_PREVIEW_MODEL = "bedding"
PREVIEW_MODELS = json.loads(Path(__file__).with_suffix(".json").read_text(encoding="utf-8"))
