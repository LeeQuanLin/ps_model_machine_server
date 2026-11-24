import json
import logging
import os
import subprocess
from pathlib import Path
from threading import Lock
from typing import Literal

from app.schemas.photoshop import PhotoshopState


COM_HELPER = Path(__file__).with_name("photoshop_com.ps1")
COM_TIMEOUT = 90
logger = logging.getLogger(__name__)


class PhotoshopControlError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code


class PhotoshopService:
    def __init__(self, photoshop_exe: Path):
        self.photoshop_exe = photoshop_exe
        self.lock = Lock()

    def control(self, action: Literal["start", "stop"]) -> PhotoshopState:
        return PhotoshopState.model_validate(self._run(action))

    def render(self, script: Path) -> None:
        self._run("render", script)

    def _run(self, action: Literal["start", "stop", "render"], script: Path | None = None) -> dict:
        if os.name != "nt":
            raise PhotoshopControlError(503, "Photoshop COM requires Windows.")

        powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        env = {**os.environ, "PHOTOSHOP_EXE": str(self.photoshop_exe)}
        env.pop("PHOTOSHOP_API_TOKEN", None)
        if script is not None:
            env["MOCKUP_JSX_PATH"] = str(script)
        try:
            with self.lock:
                process = subprocess.run(
                    [str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "RemoteSigned",
                     "-File", str(COM_HELPER), "-Operation", action],
                    env=env, capture_output=True, encoding="utf-8", errors="replace",
                    timeout=300 if script is not None else COM_TIMEOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW, check=False,
                )
        except subprocess.TimeoutExpired as error:
            raise PhotoshopControlError(504, "Photoshop COM did not respond in time.") from error
        except OSError as error:
            logger.exception("Could not run the Photoshop COM helper")
            raise PhotoshopControlError(503, "Could not launch the Windows PowerShell COM helper.") from error

        try:
            result = json.loads(process.stdout.strip())
            if not isinstance(result, dict):
                raise ValueError("Expected a JSON object from Photoshop COM helper")
            if process.returncode == 0:
                return result
            raise PhotoshopControlError(result["status_code"], result["detail"])
        except (ValueError, TypeError, KeyError) as error:
            logger.error("Photoshop COM helper returned exit code %s: %s", process.returncode, process.stderr.strip())
            raise PhotoshopControlError(502, "The Photoshop COM helper failed. Check the server log.") from error
