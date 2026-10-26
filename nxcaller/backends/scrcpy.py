"""
Specchio dello schermo ottimizzato al massimo (scrcpy).

Preset «fluidità» di default:
  * codec h264: il più veloce a codificare sul telefono e a decodificare
    sul PC, quindi il frame rate più alto per la stessa banda;
  * 60 fps e bitrate alto (8 Mbit/s, fino a 24): niente compressioni
    visibili anche in movimento;
  * renderer OpenGL hardware;
  * audio del telefono inoltrato sul PC (scrcpy ≥ 2);
  * schermo del telefono spento e PC sempre sveglio (-S -w): il telefono
    non si consuma la batteria proiettando.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys

from ..config import Config  # noqa: F401  (solo per riferimento)


def _log(msg: str) -> None:
    print(f"[scrcpy] {msg}", file=sys.stderr, flush=True)


def find() -> str | None:
    """Percorso di scrcpy, se installato."""
    return shutil.which("scrcpy")


def version() -> tuple[int, int]:
    """Versione (maggiore, minore) di scrcpy; (0, 0) se assente."""
    exe = find()
    if not exe:
        return (0, 0)
    try:
        proc = subprocess.run([exe, "--version"], capture_output=True,
                              text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return (0, 0)
    m = re.search(r"scrcpy\s+(\d+)\.(\d+)", (proc.stdout or "") +
                  (proc.stderr or ""))
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def supported() -> bool:
    return find() is not None


def build_args(config, serial: str) -> list[str]:
    """Linea di comando di scrcpy costruita dalle impostazioni."""
    exe = find()
    if not exe or not serial:
        return []
    ver = version()
    args = [exe, "--serial", serial,
            "--window-title", "Nx Caller — schermo"]
    args += ["--video-codec", str(config["mirror_codec"] or "h264")]
    args += ["--video-bit-rate", str(config["mirror_bitrate"] or "8M")]
    args += ["--max-fps", str(int(config["mirror_fps"] or 60))]
    args += ["--render-driver", "opengl"]
    if config["mirror_stay_awake"]:
        args.append("--stay-awake")
    if config["mirror_turn_screen_off"]:
        args.append("--turn-screen-off")
    if ver >= (2, 0):
        # Con scrcpy ≥ 2 l'audio è inoltrato di default: --no-audio lo spegne.
        if not config["mirror_audio"]:
            args.append("--no-audio")
    return args


def launch(config, serial: str) -> tuple[bool, str]:
    """Avvia lo specchio. Ritorna (ok, messaggio)."""
    args = build_args(config, serial)
    if not args:
        return False, "scrcpy non installato o telefono non collegato"
    _log("avvio: " + " ".join(args))
    try:
        subprocess.Popen(args, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as exc:
        return False, str(exc)[:120]
    return True, ""
