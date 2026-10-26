"""
Backend audio Bluetooth: le chiamate GSM passano dalle casse e dal
microfono del PC (profilo HFP, «auricolare» del telefono).

Per farlo serve trasformare il computer in un auricolare Bluetooth:
  * BlueZ con HSP/HFP in modalità sperimentale,
  * oFono con un modem emulato (ofono-phonesim) — su un desktop non ce
    n'è uno vero,
  * PipeWire/WirePlumber che porta l'audio SCO a casse e microfono.

Lo script `setup-hfp.sh` fa tutto questo richiedendo root una volta;
questo modulo ne espone stato in tempo reale e avvio con pkexec.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import gi
gi.require_version("GLib", "2.0")
from gi.repository import GLib, GObject  # noqa: E402

_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="bt")


def _log(msg: str) -> None:
    print(f"[bt] {msg}", file=sys.stderr, flush=True)


def setup_script() -> str | None:
    """Percorso di setup-hfp.sh (dai sorgenti oppure da installazione)."""
    pkg_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    share_dir = os.path.dirname(pkg_dir)          # ~/.local/share/nxcaller
    for cand in (os.path.join(os.path.dirname(pkg_dir), "setup-hfp.sh"),
                 os.path.join(share_dir, "setup-hfp.sh")):
        if os.path.isfile(cand):
            return cand
    return None


def _run(cmd: list[str], timeout: float = 15.0) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, check=False)
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except FileNotFoundError:
        return 127, "comando assente"
    except subprocess.TimeoutExpired:
        return 124, "timeout"


def _pgrep(name: str) -> bool:
    return subprocess.run(["pgrep", "-x", name], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode == 0


class AudioBackend(GObject.Object):
    """Stato e configurazione dell'audio Bluetooth per le chiamate."""

    __gsignals__ = {
        "status-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self) -> None:
        super().__init__()
        self.info: dict = {}

    # ------------------------------------------------------------- stato

    @staticmethod
    def _paired_devices() -> list[tuple[str, str]]:
        rc, out = _run(["bluetoothctl", "devices", "Paired"])
        if rc != 0 or not out.strip():
            rc, out = _run(["bluetoothctl", "paired-devices"])
        devices = []
        for line in out.splitlines():
            parts = line.split(None, 2)      # Device AA:BB:CC… Nome
            if len(parts) >= 3 and parts[0] == "Device":
                devices.append((parts[1], parts[2]))
        return devices

    def status(self) -> dict:
        """Stato di adattatore, ofono, phonesim e percorso audio HFP."""
        info = {"adapter": "", "powered": False, "discoverable": False,
                "paired": [], "ofono": False, "phonesim": False,
                "hfp_audio": False, "script": setup_script() or "",
                "available": False}

        rc, out = _run(["bluetoothctl", "show"])
        if rc == 0:
            info["available"] = True
            for line in out.splitlines():
                line = line.strip()
                low = line.lower()
                if low.startswith("name:"):
                    info["adapter"] = line.split(":", 1)[1].strip()
                elif low.startswith("powered:"):
                    info["powered"] = low.split(":", 1)[1].strip() == "yes"
                elif low.startswith("discoverable:"):
                    info["discoverable"] = \
                        low.split(":", 1)[1].strip() == "yes"

        info["paired"] = self._paired_devices()
        info["ofono"] = _pgrep("ofono")
        info["phonesim"] = _pgrep("phonesim")

        # Il percorso audio HFP compare tra sorgenti e uscite PipeWire.
        _rc, sources = _run(["pactl", "list", "short", "sources"])
        _rc2, sinks = _run(["pactl", "list", "short", "sinks"])
        info["hfp_audio"] = bool(
            re.search(r"hfp|handsfree|bluez", sources + sinks, re.I))
        return info

    def refresh(self, on_done=None) -> None:
        """Rilegge lo stato in background e notifica «status-changed»."""

        def task():
            info = self.status()

            def deliver():
                self.info = info
                self.emit("status-changed")
                if on_done is not None:
                    on_done(info)
                return False

            GLib.idle_add(deliver)

        _POOL.submit(task)

    # ------------------------------------------------------------- setup

    def run_setup(self, on_done) -> None:
        """
        Esegue setup-hfp.sh con i permessi di root (pkexec).
        `on_done` riceve ("ok" | "ko", testo dell'output).
        """
        script = setup_script()
        if not script:
            GLib.idle_add(on_done, "ko", "setup-hfp.sh non trovato")
            return
        if not (shutil.which("pkexec") or os.geteuid() == 0):
            GLib.idle_add(on_done, "ko", "Servono i permessi di root")
            return

        def task():
            cmd = ["pkexec", "/usr/bin/env", script] \
                if shutil.which("pkexec") and os.geteuid() != 0 \
                else [script]
            _log(f"avvio setup HFP: {' '.join(cmd)}")
            try:
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True)
                out, _ = proc.communicate(timeout=300)
                kind = "ok" if proc.returncode == 0 else "ko"
                text = (out or "")[-3000:]
            except (OSError, subprocess.TimeoutExpired) as exc:
                kind, text = "ko", str(exc)
            GLib.idle_add(on_done, kind, text)

        _POOL.submit(task)
