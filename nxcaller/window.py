"""Finestra principale di Nx Caller."""

from __future__ import annotations

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import APP_NAME  # noqa: E402
from .backends import scrcpy  # noqa: E402
from . import glass  # noqa: E402
from .pages import CallsPage, SettingsPage, SmsPage  # noqa: E402
from .widgets import toast  # noqa: E402


class MainWindow(Adw.ApplicationWindow):
    """Finestra unica: pagine Chiamate / SMS / Impostazioni."""

    def __init__(self, app) -> None:
        super().__init__(application=app, title=APP_NAME,
                         default_width=1100, default_height=760)
        self.set_size_request(780, 560)
        self.app = app
        # Sfondo «aurora»: i pannelli di vetro sopra ci guadagnano profondità.
        self.add_css_class("nx-aurora")
        self.set_content(self._build())

        app.adb.connect("alert", self._on_alert)
        app.adb.connect("status", self._on_status)
        app.kdeconnect.connect("devices-changed", self._on_devices)
        app.kdeconnect.connect("sms-sent", self._on_sms_sent)

        self._update_banner()

    # ------------------------------------------------------------ costruzione

    def _build(self) -> Gtk.Widget:
        toolbar = glass.toolbar_view()

        self.banner = Adw.Banner(button_label="Apri impostazioni")
        self.banner.connect("button-clicked",
                            lambda *a: self.show_page("settings"))
        self.banner.set_revealed(False)
        toolbar.add_top_bar(self.banner)

        self.header = glass.header_bar()

        self.stack = Adw.ViewStack(vexpand=True)
        self.stack.connect("notify::visible-child", lambda *a: self._update_banner())

        self.page_calls = CallsPage(self)
        self.page_sms = SmsPage(self)
        self.page_settings = SettingsPage(self)

        self.stack.add_titled(self.page_calls, "calls", "Chiamate")
        self.stack.add_titled(self.page_sms, "sms", "SMS")
        self.stack.add_titled(self.page_settings, "settings", "Impostazioni")
        for name, icon in (("calls", "phone-symbolic"),
                           ("sms", "chat-bubble-text-symbolic"),
                           ("settings", "emblem-system-symbolic")):
            page = self.stack.get_page(self.stack.get_child_by_name(name))
            page.set_icon_name(icon)

        switcher = Adw.ViewSwitcher(stack=self.stack,
                                    policy=Adw.ViewSwitcherPolicy.WIDE)
        self.header.set_title_widget(switcher)

        self.status_btn = Gtk.Button(css_classes=["flat", "nx-status"])
        self.status_btn.connect("clicked",
                                lambda *a: self.show_page("settings"))
        box = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
        self.status_dot = Gtk.Box(css_classes=["status-dot", "off"],
                                  width_request=9, height_request=9,
                                  halign=Gtk.Align.CENTER,
                                  valign=Gtk.Align.CENTER)
        self.status_label = Gtk.Label(label="—", css_classes=["caption"])
        box.append(self.status_dot)
        box.append(self.status_label)
        self.status_btn.set_child(box)
        self.header.pack_end(self.status_btn)

        # specchio dello schermo (scrcpy)
        self.mirror_btn = Gtk.Button(
            icon_name="video-display-symbolic", css_classes=["flat"],
            tooltip_text="Specchio dello schermo del telefono (scrcpy)")
        self.mirror_btn.connect("clicked", self._mirror)
        self.header.pack_end(self.mirror_btn)
        toolbar.add_top_bar(self.header)

        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_child(self.stack)
        toolbar.set_content(self.toast_overlay)
        return toolbar

    # ------------------------------------------------------------- pagine

    def show_page(self, name: str) -> None:
        self.stack.set_visible_child_name(name)

    def open_conversation(self, address: str, title: str = "") -> None:
        self.page_sms.open(address, title)
        self.show_page("sms")

    # ----------------------------------------------------------- specchio

    def _mirror(self, *_a) -> None:
        """Apre lo schermo del telefono sul PC con scrcpy ottimizzato."""
        if not scrcpy.supported():
            toast(self, "scrcpy non installato",
                  "si installa da solo al primo avvio, oppure: "
                  "sudo apt install scrcpy")
            return
        serial = self.app.adb.serial
        if not serial:
            toast(self, "Collega prima il telefono via ADB",
                  "Impostazioni → Connetti")
            return
        ok, msg = scrcpy.launch(self.app.config, serial)
        if not ok:
            toast(self, "Impossibile avviare scrcpy", msg)
            return
        cfg = self.app.config
        toast(self, "Schermo del telefono in riproduzione",
              f"{cfg['mirror_fps']} fps · {cfg['mirror_bitrate']}bit/s")

    # ------------------------------------------------------------- backend

    def _on_alert(self, _adb, title: str, body: str, kind: str) -> None:
        if kind == "call":
            self.show_page("calls")
            self.present()
        toast(self, title, body)

    def _on_status(self, _adb, kind: str, note: str) -> None:
        if kind == "unauthorized":
            toast(self, "Telefono non autorizzato",
                  "conferma il debug wireless sullo schermo")
        elif kind == "disconnected":
            toast(self, "Telefono scollegato")
        self._update_banner()

    def _on_devices(self, _kde) -> None:
        self._update_banner()
        self.page_settings.refresh_status()
        self.page_sms.refresh()

    def _on_sms_sent(self, _kde, ok: bool, message: str) -> None:
        if ok:
            toast(self, "SMS inviato", message)
        else:
            toast(self, "Invio SMS fallito", message)
        self.page_sms.on_sms_sent(ok, message)

    def _update_banner(self) -> None:
        """Banner in alto: spiega cosa non va, se qualcosa non va."""
        if not hasattr(self, "status_dot"):
            return   # ancora in costruzione

        text = ""
        if not self.app.adb.adb:
            text = ("adb non è installato: senza non puoi chiamare né "
                    "leggere contatti e registro (sudo apt install adb)")
        elif not self.app.kdeconnect.available and \
                self.app.kdeconnect.last_error:
            text = self.app.kdeconnect.last_error
        elif self.app.adb.serial and not self.app.adb.contacts:
            text = ("Premi «Prepara il telefono» in Impostazioni per "
                    "leggere contatti, registro e SMS")
        elif not self.app.adb.serial and not self.app.kdeconnect.devices:
            text = "Nessun telefono collegato: abbina da Impostazioni"

        if text:
            self.banner.set_title(text)
            self.banner.set_revealed(True)
        else:
            self.banner.set_revealed(False)

        # pill di stato nell'header
        adb_ok = bool(self.app.adb.serial)
        kde_ok = any(d.connected for d in self.app.kdeconnect.devices)
        for cls in ("ok", "warn", "err", "off"):
            self.status_dot.remove_css_class(cls)
        if adb_ok and kde_ok:
            self.status_dot.add_css_class("ok")
            self.status_label.set_text("Connesso")
        elif adb_ok or kde_ok:
            self.status_dot.add_css_class("warn")
            self.status_label.set_text("Parziale")
        else:
            self.status_dot.add_css_class("off")
            self.status_label.set_text("Offline")

    def refresh_all(self) -> None:
        """Aggiorna dati e stato (usato all'avvio)."""
        self.page_calls.refresh_log()
        self.page_sms.refresh()
        self.page_settings.refresh_status()
        self._update_banner()
        GLib.timeout_add_seconds(3, self._slow_refresh)

    def _slow_refresh(self) -> bool:
        self.page_settings.refresh_status()
        self._update_banner()
        return True
