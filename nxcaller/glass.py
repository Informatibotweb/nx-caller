"""
Integrazione opzionale con glass-lib — il Liquid Glass «vero».

L'autore dell'estensione Liquid Glass per GNOME ha scritto anche
`glass-lib` <https://github.com/ryohsuke1231/glass-lib>: una libreria
GTK4/libadwaita che disegna il vetro *dentro* la finestra dell'app, con
rifrazione, blur e luce di bordo — lo stesso effetto dell'estensione,
indipendente dal compositore.

Nx Caller funziona sempre e comunque:

  * senza la libreria → tema Liquid Glass in CSS (pannelli traslucidi,
    riflesso, luce di bordo, ombre morbide);
  * con la libreria  → barre e pannelli principali diventano vetro
    rifrangente reale (serve GNOME 50 / GTK ≥ 4.22).

Per installarla:  ./install-glass.sh
"""

from __future__ import annotations

import sys

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

Glass = None
_ready = False


def _log(msg: str) -> None:
    print(f"[glass] {msg}", file=sys.stderr, flush=True)


def available() -> bool:
    """True se glass-lib è installata e funzionante."""
    return _ready


def init() -> bool:
    """Prova ad attivare glass-lib. Ritorna True se è riuscita."""
    global Glass, _ready
    if _ready:
        return True
    try:
        gi.require_version("Glass", "1")
        from gi.repository import Glass as _glass
    except (ValueError, ImportError):
        return False
    try:
        _glass.init()
        Glass = _glass
        _ready = True
        _log("glass-lib attiva: vetro con rifrazione reale")
        try:
            ctx = _glass.Context.get_default()
            ctx.set_param(_glass.PARAM_BLUR_RADIUS, 3)
        except Exception:  # noqa: BLE001 — l'API può cambiare (0.0.x)
            pass
    except Exception as exc:  # noqa: BLE001
        _log(f"glass-lib presente ma inutilizzabile: {exc}")
        _ready = False
    return _ready


def toolbar_view(content: Gtk.Widget | None = None):
    """Adw.ToolbarView, oppure il Glass.ToolbarView equivalente."""
    if _ready:
        try:
            view = Glass.ToolbarView()
            if content is not None:
                view.set_content(content)
            return view
        except Exception as exc:  # noqa: BLE001
            _log(f"Glass.ToolbarView non disponibile: {exc}")
    return Adw.ToolbarView(content=content)


def header_bar() -> Adw.HeaderBar:
    """Adw.HeaderBar, oppure la GlassHeaderBar equivalente."""
    if _ready:
        try:
            return Glass.HeaderBar()
        except Exception as exc:  # noqa: BLE001
            _log(f"Glass.HeaderBar non disponibile: {exc}")
    return Adw.HeaderBar()
