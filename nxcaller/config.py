"""Configurazione persistente di Nx Caller (~/.config/nxcaller/config.json)."""

from __future__ import annotations

import json
import os
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "nxcaller"
CONFIG_FILE = CONFIG_DIR / "config.json"

_DEFAULTS = {
    # --- connessione al telefono ----------------------------------------
    "kdeconnect_device_id": None,   # id dispositivo KDE Connect scelto
    "adb_address": None,            # es. "192.168.1.20:37123"
    "adb_pair_address": None,       # indirizzo di accoppiamento mostrato dal telefono
    "auto_connect_adb": True,
    # Usa l'IP che KDE Connect/GSConnect conosce già per connettere ADB,
    # senza dover scrivere nulla: il telefono è lo stesso.
    "auto_connect_kde_ip": True,
    "poll_sms_seconds": 12,

    # --- dipendenze ------------------------------------------------------
    # Al primo avvio, se mancano adb/scrcpy/avahi, l'installa da sola.
    "auto_install_deps": True,

    # --- specchio dello schermo (scrcpy) ---------------------------------
    "mirror_bitrate": "8M",         # 1M · 2M · 4M · 8M · 12M · 16M · 24M
    "mirror_fps": 60,               # 30 · 60 · 90 · 120
    "mirror_codec": "h264",         # h264 · h265 · av1
    "mirror_audio": True,           # audio del telefono sul PC (scrcpy ≥ 2)
    "mirror_turn_screen_off": True, # spegne lo schermo del telefono
    "mirror_stay_awake": True,      # il telefono non si spegne mentre lo guardi
}


class Config:
    def __init__(self) -> None:
        self._data = dict(_DEFAULTS)
        self._load()

    def _load(self) -> None:
        try:
            if CONFIG_FILE.exists():
                saved = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                if isinstance(saved, dict):
                    self._data.update({k: v for k, v in saved.items()
                                       if k in _DEFAULTS})
        except (OSError, ValueError):
            pass

    def save(self) -> None:
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(
                json.dumps(self._data, indent=2, ensure_ascii=False),
                encoding="utf-8")
        except OSError:
            pass

    def __getitem__(self, key: str):
        return self._data.get(key)

    def __setitem__(self, key: str, value) -> None:
        if key in _DEFAULTS:
            self._data[key] = value
            self.save()
