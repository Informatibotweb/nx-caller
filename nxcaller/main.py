"""Punto d'ingresso di Nx Caller: applicazione libadwaita + CSS."""

from __future__ import annotations

import shutil
import subprocess
import sys

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import APP_ID, APP_NAME, DEVELOPER_NAME, REPO_URL, __version__  # noqa: E402
from .backends.adb import AdbManager, find_adb  # noqa: E402
from .backends.audio import AudioBackend  # noqa: E402
from .backends.kdeconnect import KdeConnectBackend  # noqa: E402
from .config import Config  # noqa: E402
from . import glass  # noqa: E402
from .widgets import toast  # noqa: E402
from .window import MainWindow  # noqa: E402

CSS_PATH = __file__.rsplit("/", 1)[0] + "/style.css"

# Dipendenze opzionali: (comando, pacchetto apt, a cosa serve)
OPTIONAL_DEPS: list[tuple[str, str, str]] = [
    ("adb", "adb", "chiamate, contatti, registro e lettura SMS"),
    ("scrcpy", "scrcpy", "specchio dello schermo del telefono"),
    ("avahi-browse", "avahi-utils",
     "scoperta dell'IP del telefono via KDE Connect"),
]


def load_css() -> None:
    provider = Gtk.CssProvider()
    try:
        with open(CSS_PATH, "rb") as fh:
            provider.load_from_data(fh.read())
    except OSError:
        return
    display = Gdk.Display.get_default()
    if display is None:
        return
    Gtk.StyleContext.add_provider_for_display(
        display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


class NxApplication(Adw.Application):
    """Applicazione: gestisce backend, azioni globali e la finestra."""

    def __init__(self) -> None:
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.config = Config()
        self.adb = AdbManager()
        self.kdeconnect = KdeConnectBackend()
        self.audio = AudioBackend()
        self.kde_ip: str = ""      # ultimo IP del telefono preso da KDE Connect
        self.window: MainWindow | None = None

        self.connect("startup", self._on_startup)
        self.connect("activate", self._on_activate)

        # azioni globali
        for name, handler in (
            ("quit", lambda *a: self.quit()),
            ("about", self._on_about),
            ("open-settings", lambda *a: self._show_page("settings")),
            ("dial", self._on_dial_action),
            ("new-sms", self._on_new_sms_action),
        ):
            action = Gio.SimpleAction.new(
                name, GLib.VariantType("s") if name in
                ("dial", "new-sms") else None)
            action.connect("activate", handler)
            self.add_action(action)

        self.set_accels_for_action("app.quit", ["<Control>q"])

    # ------------------------------------------------------------ ciclo vita

    def _on_startup(self, *_a) -> None:
        load_css()
        glass.init()          # Liquid Glass reale, se glass-lib è installata
        self.adb.start()

        # tentativo di connessione automatica ADB
        addr = self.config["adb_address"]
        if addr and self.config["auto_connect_adb"] and find_adb():
            self.adb.address = addr
            self.adb.connect_device(addr, lambda _r: None)

        # notifiche desktop per chiamate in arrivo / nuovi SMS
        self.adb.connect("alert", self._on_alert)

    def _on_activate(self, *_a) -> None:
        if self.window is None:
            self.window = MainWindow(self)
            self.window.refresh_all()
        self.window.present()
        # dopo che la finestra è mappata: dipendenze e connessione automatica
        GLib.timeout_add_seconds(2, self._check_dependencies)
        GLib.timeout_add_seconds(4, self._auto_connect_kde_ip)

    # -------------------------------------------------------- dipendenze

    def missing_deps(self) -> list[tuple[str, str, str]]:
        """Dipendenze opzionali non presenti: (comando, pacchetto, uso)."""
        return [d for d in OPTIONAL_DEPS if shutil.which(d[0]) is None]

    def _check_dependencies(self) -> bool:
        missing = self.missing_deps()
        if not missing or self.window is None:
            return False

        # Modalità «automatica» (default): si installano da sole all'avvio,
        # pkexec chiederà la password una sola volta.
        if self.config["auto_install_deps"]:
            self._start_dep_install(missing)
            return False

        body = "Servono per alcune funzioni:\n\n"
        for cmd, _pkg, use in missing:
            body += f"  • {cmd} — {use}\n"
        body += "\nLe installo adesso?"

        dlg = Adw.MessageDialog(
            transient_for=self.window,
            heading="Mancano delle dipendenze",
            body=body)
        dlg.add_response("skip", "Non ora")
        dlg.add_response("install", "Installa")
        dlg.set_response_appearance("install",
                                    Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("install")
        dlg.set_close_response("skip")
        dlg.connect("response", self._on_dep_response, missing)
        dlg.present()
        return False

    def _on_dep_response(self, _dlg, response, missing) -> None:
        if response != "install":
            return
        self._start_dep_install(missing)

    def _start_dep_install(self, missing) -> None:
        pkgs = [pkg for _cmd, pkg, _use in missing]
        toast(self.window, "Installo le dipendenze…", " ".join(pkgs))
        try:
            subprocess.Popen(
                ["pkexec", "/usr/bin/env", "DEBIAN_FRONTEND=noninteractive",
                 "/usr/bin/apt-get", "install", "-y"] + pkgs,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
        except OSError as exc:
            toast(self.window, "Installazione non riuscita", str(exc)[:80])
            return
        # Ricontrolla finché apt non ha finito (max ~3 minuti).
        self._dep_tries = 0
        GLib.timeout_add_seconds(3, self._poll_dependencies)

    def _poll_dependencies(self) -> bool:
        self._dep_tries = getattr(self, "_dep_tries", 0) + 1
        if not self.missing_deps():
            # Il backend ADB cacherà il percorso: lo aggiorniamo.
            self.adb.adb = find_adb()
            toast(self.window, "Dipendenze installate",
                  "funzioni ora disponibili")
            if self.window:
                self.window._update_banner()
                self.window.page_settings.refresh_status()
            return False
        if self._dep_tries > 60:
            toast(self.window, "Dipendenze ancora mancanti",
                  "prova: sudo apt install adb scrcpy avahi-utils")
            return False
        return True

    # ----------------------------------------------- connessione da KDE IP

    def _auto_connect_kde_ip(self) -> bool:
        """All'avvio, se ADB non è connesso, usa l'IP di KDE Connect."""
        if not (self.config["auto_connect_kde_ip"] and find_adb()):
            return False
        if self.adb.serial:            # già connesso: niente da fare
            return False
        if not (self.kdeconnect.available and self.kdeconnect.devices):
            return False
        self.auto_connect_via_kdeconnect(quiet=True)
        return False

    def auto_connect_via_kdeconnect(self, quiet: bool = False,
                                    on_done=None) -> None:
        """
        Collega ADB all'IP del telefono che KDE Connect conosce già.

        Nessun indirizzo da scrivere: si legge l'IP dal record mDNS del
        dispositivo (o dalla connessione che GSConnect ha già aperto) e la
        porta wireless la trova adb stesso.
        """
        dev = self.kdeconnect.get_device(self.config["kdeconnect_device_id"])
        dev_id = dev.id if dev else self.config["kdeconnect_device_id"]

        def got_ip(ip: str) -> None:
            if not ip:
                if not quiet:
                    toast(self.window, "Telefono non trovato in rete",
                          "KDE Connect non conosce ancora l'IP")
                if on_done is not None:
                    on_done(False)
                return
            if not quiet:
                toast(self.window, "Telefono trovato", f"IP {ip}")
            self.kde_ip = ip
            self.adb.connect_ip(ip, self._on_kde_ip_connect)

        self.kdeconnect.resolve_ip(dev_id, got_ip)

    def _on_kde_ip_connect(self, result) -> None:
        kind, target, pair, text = (result if isinstance(result, tuple)
                                    else ("ko", "", "", str(result)))
        win = self.window
        if kind == "ok":
            self.config["adb_address"] = target
            self.adb.address = target
            toast(win, "Connesso via KDE Connect", target)
            self.adb.refresh_all()
            if win:
                win.page_settings.refresh_status()
                win._update_banner()
        elif kind == "unauthorized":
            # Il telefono va accoppiato: prepariamo l'indirizzo di pairing
            # così basta digitare il codice e premere «Abbina».
            if pair:
                self.config["adb_pair_address"] = pair
                if win:
                    win.page_settings.addr_entry.set_text(pair)
            toast(win, "Telefono da autorizzare",
                  "inserisci il codice mostrato sullo schermo")
        else:
            toast(win, "Connessione ADB fallita", text[:100])

    # ------------------------------------------------------------ notifiche

    def _on_alert(self, _adb, title: str, body: str, kind: str) -> None:
        note = Gio.Notification(title)
        if body:
            note.set_body(body)
        note.set_priority(Gio.NotificationPriority.URGENT)
        if kind == "call":
            note.set_default_action("app.open-settings")
        elif kind == "sms":
            note.set_default_action("app.new-sms")
        self.send_notification(f"nx-{kind}", note)

    # ------------------------------------------------------------ azioni

    def _show_page(self, name: str) -> None:
        if self.window is None:
            self._on_activate()
        if self.window:
            self.window.show_page(name)
            self.window.present()

    def _on_dial_action(self, _action, param) -> None:
        number = param.get_string() if param else ""
        if not number:
            return
        self._show_page("calls")
        if self.window:
            self.window.page_calls.number_entry.set_text(number)
            self.window.page_calls.dial()

    def _on_new_sms_action(self, _action, param) -> None:
        self._show_page("sms")

    def _on_about(self, _action, _param) -> None:
        Adw.AboutWindow(
            transient_for=self.window,
            application_name=APP_NAME,
            application_icon="org.nxcaller.NxCaller",
            developer_name=DEVELOPER_NAME,
            developers=[DEVELOPER_NAME],
            version=__version__,
            license_type=Gtk.License.GPL_3_0,
            copyright="© 2026 Nexiquar",
            comments="Nx sta per Nexiquar. Chiama, invia e ricevi SMS, "
                     "rispondi alle chiamate e vedi lo schermo del telefono "
                     "direttamente da Ubuntu grazie a KDE Connect "
                     "(invio SMS e IP in automatico) e ADB wireless "
                     "(chiamate, dati e specchio).",
            website=REPO_URL,
            issue_url=f"{REPO_URL}/issues",
        ).present()

    # ------------------------------------------------------------ avvio

    def run(self, argv=None) -> int:
        return super().run(argv if argv is not None else sys.argv)


def main() -> int:
    app = NxApplication()
    return app.run()
