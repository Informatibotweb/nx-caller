"""
Widget riusati di Nx Caller.

Tutti usano classi CSS con prefisso `nx-` definite in style.css e si
adattano al tema chiaro/scuro di sistema.
"""

from __future__ import annotations

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402
from gi.repository import Pango  # noqa: E402

from .models import (  # noqa: E402
    CallType, avatar_color, format_duration, format_ts, initials,
)

END = Pango.EllipsizeMode.END


def make_avatar(name: str, number: str = "", size: int = 40,
                large: bool = False) -> Gtk.Label:
    """Cerchio colorato con le iniziali del contatto (o le ultime 2 cifre)."""
    if name:
        text = initials(name)
    elif number:
        text = number[-2:]
    else:
        text = "?"
    classes = ["nx-avatar", f"avatar-{avatar_color(name or number)}"]
    if large:
        classes.append("nx-avatar-lg")
    lbl = Gtk.Label(label=text, width_request=size, height_request=size,
                    xalign=0.5, yalign=0.5, css_classes=classes)
    return lbl


class DaySeparator(Gtk.ListBoxRow):
    """Etichetta «Oggi» / «Ieri» / «12 ottobre» tra due giorni di chat."""

    def __init__(self, text: str) -> None:
        super().__init__(selectable=False, activatable=False,
                         css_classes=["nx-day-row"])
        box = Gtk.Box(halign=Gtk.Align.CENTER)
        box.append(Gtk.Label(label=text, css_classes=["nx-day-pill"]))
        self.set_child(box)


class MessageBubble(Gtk.ListBoxRow):
    """Riga di una conversazione: bolla a sinistra (ricevuto) o a destra."""

    def __init__(self, body: str, outgoing: bool, timestamp: float,
                 sender: str = "", pending: bool = False,
                 failed: bool = False) -> None:
        super().__init__(selectable=False, activatable=False,
                         css_classes=["nx-bubble-row"])

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                        halign=(Gtk.Align.END if outgoing else Gtk.Align.START))
        outer.add_css_class("nx-bubble")
        outer.add_css_class("nx-bubble-out" if outgoing else "nx-bubble-in")

        if not outgoing and sender:
            who = Gtk.Label(label=sender, xalign=0)
            who.add_css_class("nx-bubble-sender")
            outer.append(who)

        lbl = Gtk.Label(label=body, xalign=0, yalign=0, wrap=True,
                        wrap_mode=Gtk.WrapMode.WORD_CHAR, selectable=True,
                        max_width_chars=46, width_chars=1)
        lbl.add_css_class("nx-bubble-text")
        outer.append(lbl)

        meta = format_ts(timestamp)
        if pending:
            meta = "invio…"
        elif failed:
            meta = "non inviato"
        if meta:
            time_lbl = Gtk.Label(label=meta, xalign=1)
            time_lbl.add_css_class("nx-bubble-time")
            outer.append(time_lbl)

        self.set_child(outer)


class ConversationRow(Gtk.ListBoxRow):
    """Riga nella lista delle conversazioni SMS."""

    def __init__(self, title: str, preview: str, timestamp: float,
                 unread: int = 0, number: str = "") -> None:
        super().__init__(css_classes=["nx-conv-row"])
        self.title = title
        self.address = number
        self.last_ts = timestamp

        box = Gtk.Box(spacing=12, margin_top=7, margin_bottom=7,
                      margin_start=10, margin_end=10)
        box.append(make_avatar(title, number, 44))

        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                        hexpand=True, valign=Gtk.Align.CENTER)

        top = Gtk.Box(spacing=8)
        name = Gtk.Label(label=title or number or "Sconosciuto", xalign=0,
                         hexpand=True, ellipsize=END)
        name.add_css_class("nx-name")
        if unread:
            name.add_css_class("nx-name-unread")
        top.append(name)
        if timestamp:
            time_lbl = Gtk.Label(label=format_ts(timestamp), xalign=1,
                                 ellipsize=END)
            time_lbl.add_css_class("dim-label")
            time_lbl.add_css_class("caption")
            top.append(time_lbl)
        texts.append(top)

        prev = Gtk.Label(label=preview or "Nessun messaggio", xalign=0,
                         ellipsize=END, hexpand=True)
        prev.add_css_class("dim-label")
        prev.add_css_class("caption")
        if unread:
            prev.add_css_class("nx-preview-unread")
        texts.append(prev)
        box.append(texts)

        if unread:
            badge = Gtk.Label(label=str(unread) if unread < 100 else "99+",
                              valign=Gtk.Align.CENTER, css_classes=["nx-badge"])
            box.append(badge)

        self.set_child(box)


_CALL_TYPE_ICONS = {
    CallType.INCOMING: ("call-incoming-symbolic", "nx-dir-in"),
    CallType.OUTGOING: ("call-outgoing-symbolic", "nx-dir-out"),
    CallType.MISSED: ("call-missed-symbolic", "nx-dir-missed"),
    CallType.UNKNOWN: ("call-start-symbolic", "nx-dir-out"),
}


class CallRow(Gtk.ListBoxRow):
    """Riga del registro chiamate."""

    def __init__(self, name: str, number: str, call_type: CallType,
                 timestamp: float, duration: int) -> None:
        super().__init__(css_classes=["nx-call-row"])
        self.number = number

        box = Gtk.Box(spacing=12, margin_top=7, margin_bottom=7,
                      margin_start=10, margin_end=10)
        box.append(make_avatar(name, number, 40))

        icon_name, css = _CALL_TYPE_ICONS.get(
            call_type, _CALL_TYPE_ICONS[CallType.UNKNOWN])
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(15)
        icon.add_css_class(css)
        icon.set_valign(Gtk.Align.CENTER)

        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3,
                        hexpand=True, valign=Gtk.Align.CENTER)
        title = Gtk.Label(label=name or number or "Sconosciuto", xalign=0,
                          ellipsize=END)
        title.add_css_class("nx-name")
        if call_type == CallType.MISSED:
            title.add_css_class("nx-missed")
        texts.append(title)

        sub = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        sub.append(icon)
        detail = Gtk.Label(label=number, xalign=0, ellipsize=END)
        detail.add_css_class("dim-label")
        detail.add_css_class("caption")
        sub.append(detail)
        texts.append(sub)
        box.append(texts)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3,
                        halign=Gtk.Align.END, valign=Gtk.Align.CENTER)
        right.append(Gtk.Label(label=format_ts(timestamp), xalign=1,
                               css_classes=["caption", "dim-label"]))
        if call_type in (CallType.INCOMING, CallType.OUTGOING) and duration:
            right.append(Gtk.Label(label=format_duration(duration), xalign=1,
                                   css_classes=["caption", "dim-label"]))
        box.append(right)

        self.set_child(box)


class DialPad(Gtk.Grid):
    """Tastierino numerico 4x3."""

    def __init__(self, on_digit=None, compact: bool = False) -> None:
        super().__init__(row_spacing=8, column_spacing=8,
                         halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self._on_digit = on_digit
        keys = [
            ("1", ""), ("2", "ABC"), ("3", "DEF"),
            ("4", "GHI"), ("5", "JKL"), ("6", "MNO"),
            ("7", "PQRS"), ("8", "TUV"), ("9", "WXYZ"),
            ("*", ""), ("0", "+"), ("#", ""),
        ]
        self.buttons: dict[str, Gtk.Button] = {}
        for i, (digit, letters) in enumerate(keys):
            btn = Gtk.Button(css_classes=["nx-key"])
            btn.set_tooltip_text(digit)
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            num = Gtk.Label(label=digit)
            num.add_css_class("nx-key-digit")
            inner.append(num)
            if letters:
                let = Gtk.Label(label=letters)
                let.add_css_class("nx-key-letters")
                inner.append(let)
            btn.set_child(inner)
            btn.connect("clicked", self._on_clicked, digit)
            self.attach(btn, i % 3, i // 3, 1, 1)
            self.buttons[digit] = btn
        if compact:
            for btn in self.buttons.values():
                btn.add_css_class("flat")

    def _on_clicked(self, _btn: Gtk.Button, digit: str) -> None:
        if self._on_digit:
            self._on_digit(digit)


def toast(window, title: str, body: str = "") -> None:
    """Messaggio effimero (toast) sopra il contenuto della finestra."""
    label = title if not body else f"{title} — {body}"
    window.toast_overlay.add_toast(Adw.Toast(title=label, timeout=4))
