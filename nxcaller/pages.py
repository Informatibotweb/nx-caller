"""Pagine dell'app: Chiamate, SMS, Impostazioni."""

from __future__ import annotations

import sys
import time

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import __version__, glass
from .backends import scrcpy
from .backends.adb import find_adb
from .models import (CallState, Conversation, Message, format_day,
                     numbers_match)
from .widgets import (CallRow, ConversationRow, DaySeparator, DialPad,
                      MessageBubble, make_avatar, toast)


def _clear(box: Gtk.Box) -> None:
    """Svuota un Gtk.Box."""
    while (child := box.get_first_child()) is not None:
        box.remove(child)


def _header(title: str = "") -> Adw.HeaderBar:
    """
    Barra del titolo per una pagina interna.

    `set_decoration_layout("")` toglie i pulsanti della finestra: altrimenti
    ogni pannello di un NavigationSplitView aggiunge la propria X e i
    pulsanti si moltiplicano.
    """
    bar = glass.header_bar()
    try:
        bar.set_decoration_layout("")
    except (AttributeError, TypeError):
        pass   # widget del vetro reale senza questo metodo
    if title:
        bar.set_title_widget(
            Gtk.Label(label=title, css_classes=["nx-section-title"]))
    return bar


def _log(msg: str) -> None:
    print(f"[ui] {msg}", file=sys.stderr, flush=True)


def _clear_list(lst: Gtk.ListBox) -> None:
    """Svuota una Gtk.ListBox."""
    while (row := lst.get_row_at_index(0)) is not None:
        lst.remove(row)


# =============================================================== CHIAMATE

class CallsPage(Gtk.Box):
    """Registro + tastierino + interfaccia in-chiamata."""

    def __init__(self, window) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.window = window
        self.app = window.app
        self.adb = self.app.adb
        self._timer_id = 0
        self._call_started = 0.0
        self._muted = False

        self.stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE,
            transition_duration=200, vexpand=True)
        self.stack.add_named(self._build_pad(), "pad")
        self.stack.add_named(self._build_call(), "call")
        self.append(self.stack)

        self.adb.connect("call-state", self._on_call_state)
        self.adb.connect("log-updated", lambda *a: self.refresh_log())

    # ------------------------------------------------------------ tastierino

    def _build_pad(self) -> Gtk.Widget:
        split = Adw.NavigationSplitView(
            min_sidebar_width=260, max_sidebar_width=360)
        split.set_vexpand(True)
        split.set_sidebar(
            Adw.NavigationPage(title="Recenti",
                               child=self._build_sidebar()))
        split.set_content(
            Adw.NavigationPage(title="Chiama",
                               child=self._build_dial()))
        return split

    def _build_sidebar(self) -> Gtk.Widget:
        view = Adw.ToolbarView()
        view.add_css_class("nx-glass-panel")
        bar = _header("Registro chiamate")
        refresh = Gtk.Button(icon_name="view-refresh-symbolic",
                             tooltip_text="Aggiorna il registro",
                             css_classes=["flat"])
        refresh.connect("clicked", lambda *a: self.adb.refresh_all())
        bar.pack_end(refresh)
        view.add_top_bar(bar)

        self.log_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE,
                                    css_classes=["navigation-sidebar"])
        self.log_list.connect("row-activated", self._on_log_row)
        scroll = Gtk.ScrolledWindow(child=self.log_list, vexpand=True,
                                    hscrollbar_policy=Gtk.PolicyType.NEVER)

        self.log_stack = Gtk.Stack(vexpand=True,
                                   transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.log_stack.add_named(scroll, "list")
        self.log_stack.add_named(Adw.StatusPage(
            icon_name="call-start-symbolic",
            title="Nessuna chiamata",
            description="Collega il telefono via ADB per vedere "
                        "il registro."), "empty")
        view.set_content(self.log_stack)
        return view

    def _build_dial(self) -> Gtk.Widget:
        """Colonna centrale: numero, tastierino, azioni."""
        view = Adw.ToolbarView()
        view.add_css_class("nx-glass-panel")
        view.add_top_bar(_header())
        view.set_content(self._build_dial_content())
        return view

    def _build_dial_content(self) -> Gtk.Widget:
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16,
                      valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER,
                      margin_start=28, margin_end=28)

        self.number_entry = Gtk.Entry(
            halign=Gtk.Align.CENTER, hexpand=False, width_chars=22,
            placeholder_text="Numero di telefono",
            css_classes=["nx-number-entry"], xalign=0.5)
        self.number_entry.set_icon_from_icon_name(
            Gtk.EntryIconPosition.SECONDARY, "edit-delete-symbolic")
        self.number_entry.set_icon_tooltip_text(
            Gtk.EntryIconPosition.SECONDARY, "Cancella")
        self.number_entry.connect("icon-press",
                                  lambda e, _pos: e.set_text(""))
        self.number_entry.connect("changed", self._on_number_changed)
        self.number_entry.connect("activate", lambda *a: self.dial())
        col.append(self.number_entry)

        self.contact_hint = Gtk.Label(halign=Gtk.Align.CENTER)
        self.contact_hint.add_css_class("nx-contact-hint")
        col.append(self.contact_hint)

        col.append(DialPad(on_digit=self._on_digit))

        actions = Gtk.Box(spacing=20, halign=Gtk.Align.CENTER)

        paste = Gtk.Button(icon_name="edit-paste-symbolic",
                           tooltip_text="Incolla",
                           css_classes=["nx-call-small", "flat"])
        paste.connect("clicked", self._paste)
        actions.append(paste)

        call_btn = Gtk.Button(icon_name="call-start-symbolic",
                              css_classes=["nx-call-round", "nx-call-answer"],
                              tooltip_text="Chiama (Invio)")
        call_btn.connect("clicked", lambda *a: self.dial())
        actions.append(call_btn)

        del_btn = Gtk.Button(icon_name="edit-delete-symbolic",
                             tooltip_text="Cancella l'ultimo numero",
                             css_classes=["nx-call-small", "flat"])
        del_btn.connect("clicked", self._backspace)
        actions.append(del_btn)
        col.append(actions)

        hint = Gtk.Label(label="Invio per chiamare · durante la chiamata "
                               "il tastierino compone i DTMF",
                         halign=Gtk.Align.CENTER, wrap=True)
        hint.add_css_class("nx-hint")
        col.append(hint)
        return col

    def _on_digit(self, digit: str) -> None:
        if self.stack.get_visible_child_name() == "call" and \
                self.adb.call_state == CallState.ACTIVE:
            self.adb.send_dtmf(digit)
            self.dtmf_label.set_text(self.dtmf_label.get_text() + digit)
            return
        entry = self.number_entry
        entry.set_text(entry.get_text() + digit)
        entry.set_position(entry.get_text_length())

    def _backspace(self, *_a) -> None:
        text = self.number_entry.get_text()
        self.number_entry.set_text(text[:-1])

    def _paste(self, *_a) -> None:
        clip = self.number_entry.get_display().get_clipboard()

        def got_text(_src, result):
            try:
                text = clip.read_text_finish(result)
            except Exception:
                return
            if text:
                self.number_entry.set_text(text.strip())

        clip.read_text_async(None, got_text)

    def _on_number_changed(self, entry: Gtk.Entry) -> None:
        text = entry.get_text().strip()
        name = ""
        if text:
            name = self.adb.lookup_name(text)
            if not name:
                for c in self.adb.contacts:
                    if numbers_match(c.number, text):
                        name = c.name
                        break
        self.contact_hint.set_text(name)

    def _on_log_row(self, _list: Gtk.ListBox, row) -> None:
        if row is not None and getattr(row, "number", ""):
            self.number_entry.set_text(row.number)
            self.stack.set_visible_child_name("pad")

    def refresh_log(self) -> None:
        if not hasattr(self, "log_list"):
            return
        _clear_list(self.log_list)
        for rec in self.adb.call_log[:200]:
            name = rec.name or self.adb.lookup_name(rec.number)
            self.log_list.append(
                CallRow(name, rec.number, rec.type, rec.timestamp, rec.duration))
        self.log_stack.set_visible_child_name(
            "list" if self.adb.call_log else "empty")

    def dial(self) -> None:
        number = self.number_entry.get_text().strip()
        if not number:
            toast(self.window, "Inserisci un numero")
            return
        _log(f"dial() {number!r}")
        self.adb.dial(number)
        toast(self.window, "Chiamata avviata sul telefono", number)

    # ---------------------------------------------------------- in-chiamata

    def _build_call(self) -> Gtk.Widget:
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=22,
                      valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER,
                      margin_start=32, margin_end=32,
                      margin_top=24, margin_bottom=24)
        col.add_css_class("nx-glass-panel")
        col.add_css_class("nx-glass-card")

        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                       halign=Gtk.Align.CENTER)
        self.call_state_lbl = Gtk.Label(label="CHIAMATA IN CORSO")
        self.call_state_lbl.add_css_class("nx-call-state")
        head.append(self.call_state_lbl)

        self.call_name_lbl = Gtk.Label(label="")
        self.call_name_lbl.add_css_class("nx-call-name")
        head.append(self.call_name_lbl)

        self.call_number_lbl = Gtk.Label(label="")
        self.call_number_lbl.add_css_class("nx-call-number")
        head.append(self.call_number_lbl)

        self.call_timer_lbl = Gtk.Label(label="")
        self.call_timer_lbl.add_css_class("nx-call-timer")
        head.append(self.call_timer_lbl)

        self.dtmf_label = Gtk.Label(label="")
        self.dtmf_label.add_css_class("nx-dtmf-label")
        head.append(self.dtmf_label)
        col.append(head)

        # rispondi / rifiuta (chiamata in arrivo)
        self.answer_row = Gtk.Box(spacing=34, halign=Gtk.Align.CENTER)
        reject = Gtk.Button(icon_name="call-stop-symbolic",
                            css_classes=["nx-call-round", "nx-call-hangup"],
                            tooltip_text="Rifiuta la chiamata")
        reject.connect("clicked", lambda *a: self.adb.hangup())
        self.answer_row.append(reject)
        answer = Gtk.Button(icon_name="call-start-symbolic",
                            css_classes=["nx-call-round", "nx-call-answer"],
                            tooltip_text="Rispondi")
        answer.connect("clicked", lambda *a: self.adb.answer())
        self.answer_row.append(answer)
        col.append(self.answer_row)

        # comandi (chiamata attiva)
        self.cmd_row = Gtk.Box(spacing=18, halign=Gtk.Align.CENTER)
        self.mute_btn = Gtk.Button(label="Mute",
                                   css_classes=["nx-call-small"])
        self.mute_btn.connect("clicked", self._toggle_mute)
        self.cmd_row.append(self.mute_btn)

        keypad = Gtk.Button(icon_name="view-grid-symbolic",
                            css_classes=["nx-call-small"],
                            tooltip_text="Tastierino DTMF")
        keypad.connect("clicked", self._show_dtmf_pad)
        self.cmd_row.append(keypad)

        hang = Gtk.Button(icon_name="call-stop-symbolic",
                          css_classes=["nx-call-round", "nx-call-hangup"],
                          tooltip_text="Chiudi la chiamata")
        hang.connect("clicked", lambda *a: self.adb.hangup())
        self.cmd_row.append(hang)
        col.append(self.cmd_row)

        self.dtmf_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_UP,
            reveal_child=False)
        self.dtmf_revealer.set_child(
            DialPad(on_digit=self._on_digit, compact=True))
        col.append(self.dtmf_revealer)
        return col

    def _toggle_mute(self, *_a) -> None:
        self._muted = not self._muted
        if self._muted:
            self.mute_btn.add_css_class("active")
        else:
            self.mute_btn.remove_css_class("active")
        self.mute_btn.set_label("Mute ✓" if self._muted else "Mute")
        self.adb.toggle_mute()

    def _show_dtmf_pad(self, *_a) -> None:
        revealed = not self.dtmf_revealer.get_reveal_child()
        self.dtmf_revealer.set_reveal_child(revealed)

    def _on_call_state(self, _adb, state_str: str, number: str) -> None:
        if state_str == "idle":
            self._stop_timer()
            self._muted = False
            self.mute_btn.remove_css_class("active")
            self.mute_btn.set_label("Mute")
            self.dtmf_label.set_text("")
            self.dtmf_revealer.set_reveal_child(False)
            self.stack.set_visible_child_name("pad")
            self.refresh_log()
            return

        self.stack.set_visible_child_name("call")
        name = self.adb.lookup_name(number)
        self.call_number_lbl.set_text(number or "Numero privato")
        self.call_name_lbl.set_text(name)

        if state_str == "ringing":
            self.call_state_lbl.set_text("Chiamata in arrivo")
            self.answer_row.set_visible(True)
            self.cmd_row.set_visible(False)
            self.dtmf_label.set_text("")
            self.call_timer_lbl.set_text("")
        else:
            self.call_state_lbl.set_text("Chiamata in corso")
            self.answer_row.set_visible(False)
            self.cmd_row.set_visible(True)
            if not self._timer_id:
                self._call_started = time.time()
                self._timer_id = GLib.timeout_add_seconds(
                    1, self._update_timer)
                self._update_timer()

    def _update_timer(self) -> bool:
        self.call_timer_lbl.set_text(
            self._fmt(int(time.time() - self._call_started)))
        return True

    def _stop_timer(self) -> None:
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        self.call_timer_lbl.set_text("")

    @staticmethod
    def _fmt(seconds: int) -> str:
        m, s = divmod(max(0, seconds), 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


# ===================================================================== SMS

class SmsPage(Gtk.Box):
    """Lista conversazioni + thread + compositore."""

    def __init__(self, window) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.window = window
        self.app = window.app
        self.adb = self.app.adb
        self.current_address: str | None = None
        self.current_title = ""
        self._search = ""
        self._optimistic: dict[str, list[Message]] = {}
        self._read_override: set[str] = set()

        self.split = Adw.NavigationSplitView(
            min_sidebar_width=260, max_sidebar_width=360)
        # vexpand: senza questo il pannello resta alto quanto il contenuto
        # e sotto resta uno spazio morto.
        self.split.set_vexpand(True)
        self.split.set_sidebar(
            Adw.NavigationPage(title="Conversazioni",
                               child=self._build_sidebar()))
        self.split.set_content(
            Adw.NavigationPage(title="Conversazione",
                               child=self._build_thread()))
        self.append(self.split)

        self.adb.connect("sms-updated", self._on_sms_updated)

    # ------------------------------------------------------------ sidebar

    def _build_sidebar(self) -> Gtk.Widget:
        view = Adw.ToolbarView()
        view.add_css_class("nx-glass-panel")

        bar = _header("Messaggi")
        new_btn = Gtk.Button(icon_name="document-new-symbolic",
                             tooltip_text="Nuovo messaggio",
                             css_classes=["flat"])
        new_btn.connect("clicked", self._new_message_dialog)
        bar.pack_end(new_btn)
        view.add_top_bar(bar)

        self.search = Gtk.SearchEntry(placeholder_text="Cerca…",
                                      margin_start=8, margin_end=8)
        self.search.connect("search-changed", self._on_search)
        view.add_top_bar(self.search)

        self.conv_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE,
                                     css_classes=["navigation-sidebar"])
        self.conv_list.set_filter_func(self._filter_rows)
        self.conv_list.set_sort_func(self._sort_rows)
        self.conv_list.connect("row-activated", self._on_conv_activated)
        scroll = Gtk.ScrolledWindow(child=self.conv_list, vexpand=True,
                                    hscrollbar_policy=Gtk.PolicyType.NEVER)

        self.list_stack = Gtk.Stack(vexpand=True,
                                    transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.list_stack.add_named(scroll, "list")

        empty_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                            spacing=16, valign=Gtk.Align.CENTER,
                            halign=Gtk.Align.CENTER, margin_start=24,
                            margin_end=24)
        empty_box.append(Adw.StatusPage(
            icon_name="chat-bubble-text-symbolic",
            title="Nessun messaggio",
            description="Collega il telefono via ADB per leggere "
                        "i messaggi in arrivo."))
        compose = Gtk.Button(label="Nuovo messaggio",
                             css_classes=["suggested-action", "pill"],
                             halign=Gtk.Align.CENTER)
        compose.connect("clicked", self._new_message_dialog)
        empty_box.append(compose)
        self.list_stack.add_named(empty_box, "empty")
        self.list_empty = empty_box

        view.set_content(self.list_stack)
        return view

    def _on_search(self, entry: Gtk.SearchEntry) -> None:
        self._search = entry.get_text().strip().lower()
        self.conv_list.invalidate_filter()

    def _filter_rows(self, row) -> bool:
        if not self._search:
            return True
        title = getattr(row, "title", "").lower()
        address = getattr(row, "address", "")
        from .models import normalize_number
        return self._search in title or \
            self._search in normalize_number(address)

    def _sort_rows(self, row1, row2) -> int:
        t1 = getattr(row1, "last_ts", 0)
        t2 = getattr(row2, "last_ts", 0)
        return (t2 > t1) - (t2 < t1)

    def _on_conv_activated(self, _list, row) -> None:
        if row is not None:
            self.open(getattr(row, "address", ""),
                      getattr(row, "title", ""))

    # ------------------------------------------------------------- thread

    def _build_thread(self) -> Gtk.Widget:
        view = Adw.ToolbarView()
        view.add_css_class("nx-glass-panel")

        # --- barra del titolo con avatar + nome + numero
        self.header_box = Gtk.Box(spacing=10, margin_start=6, margin_end=6)
        self.header_box.append(Gtk.Label(
            label="Conversazione", xalign=0, css_classes=["heading"]))
        self.thread_bar = _header()
        self.thread_bar.set_title_widget(self.header_box)

        self.call_btn = Gtk.Button(icon_name="call-start-symbolic",
                                   css_classes=["flat"], sensitive=False,
                                   tooltip_text="Chiama questo contatto")
        self.call_btn.connect("clicked", self._call_current)
        self.thread_bar.pack_end(self.call_btn)
        view.add_top_bar(self.thread_bar)

        # --- area messaggi
        self.bubbles = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.NONE,
            css_classes=["transparent-list", "nx-thread-list"])
        self.bubble_scroll = Gtk.ScrolledWindow(
            child=self.bubbles, vexpand=True,
            hscrollbar_policy=Gtk.PolicyType.NEVER)

        self.empty_thread = Adw.StatusPage(
            icon_name="chat-bubble-text-symbolic",
            title="Nessuna conversazione",
            description="Scegli una conversazione a sinistra oppure "
                        "componi un nuovo messaggio.")

        self.thread_stack = Gtk.Stack(vexpand=True)
        self.thread_stack.add_named(self.empty_thread, "empty")
        self.thread_stack.add_named(self.bubble_scroll, "thread")
        view.set_content(self.thread_stack)

        # --- compositore (sempre in basso)
        composer_box = Gtk.Box(margin_start=14, margin_end=14,
                               margin_top=6, margin_bottom=10)
        self.composer = Gtk.Box(css_classes=["nx-composer"], hexpand=True)
        self.textview = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                     accepts_tab=False, top_margin=7,
                                     bottom_margin=7, right_margin=46,
                                     hexpand=True)
        self.textview.set_vexpand(False)
        self.text_scroll = Gtk.ScrolledWindow(
            child=self.textview, hexpand=True,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            min_content_height=34, max_content_height=120,
            propagate_natural_height=True)
        self.composer.append(self.text_scroll)

        self.send_btn = Gtk.Button(icon_name="mail-send-symbolic",
                                   css_classes=["nx-send"],
                                   tooltip_text="Invia (Invio)",
                                   valign=Gtk.Align.CENTER)
        self.send_btn.connect("clicked", lambda *a: self.send())
        self.composer.append(self.send_btn)
        composer_box.append(self.composer)
        view.add_bottom_bar(composer_box)

        controller = Gtk.EventControllerKey()
        controller.connect("key-pressed", self._on_key)
        self.textview.add_controller(controller)
        return view

    def _on_key(self, _ctrl, keyval, _code, state) -> bool:
        from gi.repository import Gdk
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if state & Gdk.ModifierType.SHIFT_MASK:
                return False   # a capo
            self.send()
            return True
        return False

    def _call_current(self, *_a) -> None:
        if not self.current_address:
            return
        self.adb.dial(self.current_address)
        toast(self.window, "Chiamata avviata", self.current_title)

    # ------------------------------------------------------------ dati

    def _merged_messages(self, address: str) -> list[Message]:
        phone = list(self.adb.sms_threads.get(address, []))
        bodies = {m.body for m in phone}
        extra = [m for m in self._optimistic.get(address, [])
                 if m.body not in bodies]
        return sorted(phone + extra, key=lambda m: m.timestamp)

    def conversations(self) -> list[Conversation]:
        addrs = set(self.adb.sms_threads) | set(self._optimistic)
        out = []
        for addr in addrs:
            msgs = self._merged_messages(addr)
            if not msgs:
                continue
            out.append(Conversation(
                thread_id=addr, address=addr,
                contact_name=self.adb.lookup_name(addr), messages=msgs))
        out.sort(key=lambda c: c.last_ts, reverse=True)
        return out

    def refresh(self) -> None:
        """Ricostruisce la lista conversazioni."""
        if not hasattr(self, "conv_list"):
            return
        rows = self.conversations()
        _clear_list(self.conv_list)
        for conv in rows:
            unread = 0 if conv.address in self._read_override else conv.unread
            last = conv.last_message
            preview = (("Tu: " if last and last.outgoing else "") +
                       (last.body if last else ""))
            self.conv_list.append(ConversationRow(
                title=conv.title, preview=preview,
                timestamp=conv.last_ts, unread=unread,
                number=conv.address))
        self.list_stack.set_visible_child_name(
            "list" if rows else "empty")

        # apre la prima conversazione se non ce n'è una aperta
        if rows and not self.current_address:
            self.open(rows[0].address, rows[0].title)
        elif self.current_address:
            self._rebuild_bubbles()

    def _on_sms_updated(self, _adb, _count) -> None:
        self.refresh()

    def open(self, address: str, title: str = "") -> None:
        if not address:
            return
        self.current_address = address
        self.current_title = title or self.adb.lookup_name(address) or address
        self._read_override.add(address)

        _clear(self.header_box)
        self.header_box.append(
            make_avatar(self.current_title, address, 32))
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        t = Gtk.Label(label=self.current_title, xalign=0)
        t.add_css_class("heading")
        texts.append(t)
        if self.current_title != address:
            sub = Gtk.Label(label=address, xalign=0)
            sub.add_css_class("caption")
            sub.add_css_class("dim-label")
            texts.append(sub)
        self.header_box.append(texts)
        self.header_texts = texts

        self.call_btn.set_sensitive(True)
        self._rebuild_bubbles()
        self.thread_stack.set_visible_child_name("thread")
        self.split.set_show_content(True)
        self.refresh()

    def _rebuild_bubbles(self) -> None:
        _clear_list(self.bubbles)
        if not self.current_address:
            self.thread_stack.set_visible_child_name("empty")
            return
        msgs = self._merged_messages(self.current_address)
        if not msgs:
            self.thread_stack.set_visible_child_name("empty")
            return

        prev_day = None
        for m in msgs:
            day = format_day(m.timestamp)
            if day != prev_day:
                self.bubbles.append(DaySeparator(day))
                prev_day = day
            self.bubbles.append(MessageBubble(
                body=m.body, outgoing=m.outgoing, timestamp=m.timestamp,
                sender=("" if m.outgoing else self.current_title),
                pending=m.pending, failed=m.failed))
        self.thread_stack.set_visible_child_name("thread")
        self._scroll_bottom()

    def _scroll_bottom(self) -> None:
        def do_scroll():
            adj = self.bubble_scroll.get_vadjustment()
            adj.set_value(max(0, adj.get_upper() - adj.get_page_size()))
            return False
        GLib.idle_add(do_scroll)
        GLib.timeout_add(80, do_scroll)

    # ------------------------------------------------------------ invio

    def send(self) -> None:
        address = self.current_address
        if not address:
            toast(self.window, "Seleziona o crea una conversazione")
            return
        buf = self.textview.get_buffer()
        start, end = buf.get_bounds()
        text = buf.get_text(start, end, True).strip()
        if not text:
            return
        buf.set_text("")

        msg = Message(body=text, address=address, timestamp=time.time(),
                      outgoing=True, read=True, pending=True)
        self._optimistic.setdefault(address, []).append(msg)
        self._rebuild_bubbles()
        _log(f"invio SMS a {address!r} (device="
             f"{self.app.config['kdeconnect_device_id']})")
        self.app.kdeconnect.send_sms(
            address, text, self.app.config["kdeconnect_device_id"])

    def on_sms_sent(self, ok: bool, msg: str) -> None:
        """Callback dal backend KDE Connect."""
        _log(f"on_sms_sent ok={ok} msg={msg!r} "
             f"current={self.current_address!r}")
        if not self.current_address:
            return
        changed = 0
        for m in self._optimistic.get(self.current_address, []):
            if m.pending:
                m.pending = False
                m.failed = not ok
                changed += 1
        _log(f"bolle aggiornate: {changed}")
        self._rebuild_bubbles()

    def _new_message_dialog(self, *_a) -> None:
        dialog = Adw.MessageDialog(transient_for=self.window,
                                   heading="Nuovo messaggio",
                                   body="Numero e testo del messaggio.")
        grid = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                       margin_top=6)
        entry = Gtk.Entry(placeholder_text="Numero di telefono", hexpand=True)
        entry.set_activates_default(True)
        grid.append(entry)
        view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=6,
                            bottom_margin=6, left_margin=8, right_margin=8)
        scroll = Gtk.ScrolledWindow(child=view, min_content_height=90,
                                    max_content_height=180,
                                    hscrollbar_policy=Gtk.PolicyType.NEVER,
                                    propagate_natural_height=True)
        grid.append(scroll)
        dialog.set_extra_child(grid)

        dialog.add_response("cancel", "Annulla")
        dialog.add_response("send", "Invia")
        dialog.set_response_appearance("send", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("send")
        dialog.set_close_response("cancel")

        def on_response(_dlg, response):
            if response != "send":
                return
            number = entry.get_text().strip()
            if not number:
                toast(self.window, "Numero mancante")
                return
            s, e = view.get_buffer().get_bounds()
            body = view.get_buffer().get_text(s, e, True).strip()
            if not body:
                toast(self.window, "Messaggio vuoto")
                return
            msg = Message(body=body, address=number, timestamp=time.time(),
                          outgoing=True, read=True, pending=True)
            self._optimistic.setdefault(number, []).append(msg)
            self.open(number, self.adb.lookup_name(number) or number)
            self.app.kdeconnect.send_sms(
                number, body, self.app.config["kdeconnect_device_id"])

        dialog.connect("response", on_response)
        dialog.present()


# ============================================================ IMPOSTAZIONI

class SettingsPage(Adw.PreferencesPage):
    """Stato dispositivi, accoppiamento ADB, permessi, info."""

    def __init__(self, window) -> None:
        super().__init__()
        self.window = window
        self.app = window.app
        self.adb = self.app.adb
        self.kde = self.app.kdeconnect
        self._combo_guard = False

        # ---- KDE Connect
        self.kde_group = Adw.PreferencesGroup(
            title="KDE Connect",
            description="Invio SMS: il telefono invia il messaggio tramite "
                        "l'app KDE Connect (plugin SMS).")
        self.kde_status = Adw.ActionRow(title="Stato")
        self.kde_dot = Gtk.Box(css_classes=["status-dot", "off"],
                               width_request=9, height_request=9,
                               valign=Gtk.Align.CENTER)
        self.kde_status.add_suffix(self.kde_dot)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic",
                             css_classes=["flat", "circular"],
                             tooltip_text="Rileggi dispositivi")
        refresh.connect("clicked", lambda *a: self.kde.refresh())
        self.kde_status.add_suffix(refresh)
        self.kde_group.add(self.kde_status)

        self.device_row = Adw.ComboRow(title="Telefono")
        self.device_model = Gtk.StringList()
        self.device_row.set_model(self.device_model)
        self.device_row.connect("notify::selected", self._on_device_selected)
        self.kde_group.add(self.device_row)
        self.add(self.kde_group)

        # ---- ADB (chiamate)
        self.adb_group = Adw.PreferencesGroup(
            title="Chiamate via ADB wireless",
            description="Android 11+ con «Debug wireless» attivo: serve per "
                        "chiamare, rispondere e leggere registro/contatti/SMS.")
        self.adb_status = Adw.ActionRow(title="Stato")
        self.adb_dot = Gtk.Box(css_classes=["status-dot", "off"],
                               width_request=9, height_request=9,
                               valign=Gtk.Align.CENTER)
        self.adb_status.add_suffix(self.adb_dot)
        self.adb_group.add(self.adb_status)

        self.addr_row = Adw.ActionRow(title="Indirizzo (IP:porta)")
        self.addr_entry = Gtk.Entry(placeholder_text="192.168.1.20:37123",
                                    width_chars=18)
        self.addr_entry.set_text(self.app.config["adb_address"] or "")
        self.addr_row.add_suffix(self.addr_entry)
        discover = Gtk.Button(label="Scopri", css_classes=["pill"])
        discover.connect("clicked", self._discover)
        self.addr_row.add_suffix(discover)
        connect_btn = Gtk.Button(label="Connetti",
                                 css_classes=["pill", "suggested-action"])
        connect_btn.connect("clicked", self._connect)
        self.addr_row.add_suffix(connect_btn)
        self.adb_group.add(self.addr_row)

        # IP preso da KDE Connect: niente indirizzi da scrivere.
        self.ip_row = Adw.ActionRow(
            title="IP del telefono da KDE Connect",
            subtitle="Lo legge dal dispositivo già abbinaato: nessun "
                     "indirizzo da scrivere, la porta la trova adb")
        self.ip_label = Gtk.Label(label="—", css_classes=["dim-label"])
        self.ip_row.add_suffix(self.ip_label)
        kde_ip_btn = Gtk.Button(label="Usa IP di KDE Connect",
                                css_classes=["pill", "suggested-action"])
        kde_ip_btn.connect("clicked", self._connect_via_kde)
        self.ip_row.add_suffix(kde_ip_btn)
        self.adb_group.add(self.ip_row)

        pair_row = Adw.ActionRow(
            title="Accoppiamento",
            subtitle="Telefono → Impostazioni → Sistema → Opzioni "
                     "sviluppatore → Debug wireless → «Abbina con codice»")
        self.pair_entry = Gtk.Entry(placeholder_text="Codice a 6 cifre",
                                    width_chars=8, max_length=10)
        pair_row.add_suffix(self.pair_entry)
        pair_btn = Gtk.Button(label="Abbina", css_classes=["pill"])
        pair_btn.connect("clicked", self._pair)
        pair_row.add_suffix(pair_btn)
        self.adb_group.add(pair_row)

        perm_row = Adw.ActionRow(
            title="Permessi del telefono",
            subtitle="Concede ad adb letture SMS, contatti e registro")
        perm_btn = Gtk.Button(label="Prepara il telefono",
                              css_classes=["pill", "suggested-action"])
        perm_btn.connect("clicked", self._prepare)
        perm_row.add_suffix(perm_btn)
        perm_row.set_activatable_widget(perm_btn)
        self.adb_group.add(perm_row)

        self.auto_row = Adw.SwitchRow(
            title="Connessione automatica all'avvio",
            active=bool(self.app.config["auto_connect_adb"]))
        self.auto_row.connect("notify::active", self._on_auto)
        self.adb_group.add(self.auto_row)

        self.auto_kde_row = Adw.SwitchRow(
            title="Usa l'IP di KDE Connect in automatico",
            subtitle="All'avvio, se ADB non è connesso, prende l'IP del "
                     "telefono da KDE Connect e si collega da solo",
            active=bool(self.app.config["auto_connect_kde_ip"]))
        self.auto_kde_row.connect("notify::active", self._on_auto_kde)
        self.adb_group.add(self.auto_kde_row)

        refresh_row = Adw.ActionRow(title="Aggiorna ora i dati",
                                    subtitle="SMS, registro, contatti")
        refresh_all = Gtk.Button(icon_name="view-refresh-symbolic",
                                 css_classes=["flat", "circular"])
        refresh_all.connect("clicked", lambda *a: self.adb.refresh_all())
        refresh_row.add_suffix(refresh_all)
        refresh_row.set_activatable_widget(refresh_all)
        self.adb_group.add(refresh_row)
        self.add(self.adb_group)

        # ---- Schermo (scrcpy)
        mirror_group = Adw.PreferencesGroup(
            title="Schermo del telefono (scrcpy)",
            description="Preset di default pensato per la massima fluidità: "
                        "codec h264, 60 fps e bitrate alto. Più bitrate "
                        "aumenta la nitidezza in movimento.")
        mirror_group.add(self._combo_row(
            "Bitrate video", "Alt = nitido, ma serve Wi-Fi veloce",
            ["2M", "4M", "8M", "12M", "16M", "24M"],
            self.app.config["mirror_bitrate"],
            lambda v: self.app.config.__setitem__("mirror_bitrate", v)))
        mirror_group.add(self._combo_row(
            "Frame per secondo", "60 è il punto d'oro, 120 su Wi-Fi 5/6",
            ["30", "60", "90", "120"], str(self.app.config["mirror_fps"]),
            lambda v: self.app.config.__setitem__("mirror_fps", int(v))))
        mirror_group.add(self._combo_row(
            "Codec video", "h264 è il più veloce; h265/av1 più efficienti",
            ["h264", "h265", "av1"], self.app.config["mirror_codec"],
            lambda v: self.app.config.__setitem__("mirror_codec", v)))
        mirror_group.add(self._switch_row(
            "Audio del telefono sul PC", "",
            "mirror_audio"))
        mirror_group.add(self._switch_row(
            "Spegni lo schermo del telefono",
            "Il telefono si scarica meno mentre lo guardi",
            "mirror_turn_screen_off"))
        mirror_group.add(self._switch_row(
            "Tieni il telefono sveglio", "", "mirror_stay_awake"))
        self.add(mirror_group)

        # ---- Audio Bluetooth (chiamate)
        bt_group = Adw.PreferencesGroup(
            title="Chiamate via Bluetooth (HFP)",
            description="Trasforma il PC in un auricolare del telefono: la "
                        "voce della chiamata GSM esce dalle casse del PC e "
                        "parli col microfono del computer.")
        self.bt_status = Adw.ActionRow(title="Stato")
        self.bt_dot = Gtk.Box(css_classes=["status-dot", "off"],
                              width_request=9, height_request=9,
                              valign=Gtk.Align.CENTER)
        self.bt_status.add_suffix(self.bt_dot)
        bt_refresh = Gtk.Button(icon_name="view-refresh-symbolic",
                                css_classes=["flat", "circular"],
                                tooltip_text="Rileggi lo stato Bluetooth")
        bt_refresh.connect("clicked", lambda *a: self.app.audio.refresh())
        self.bt_status.add_suffix(bt_refresh)
        bt_group.add(self.bt_status)

        self.bt_devices = Adw.ActionRow(title="Telefono appaiato",
                                        subtitle="…")
        bt_group.add(self.bt_devices)

        bt_setup_row = Adw.ActionRow(
            title="Configurazione",
            subtitle="Installa ofono + phonesim e abilita HFP in BlueZ "
                     "(serve la password di root una volta)")
        bt_setup = Gtk.Button(label="Configura audio Bluetooth",
                              css_classes=["pill", "suggested-action"])
        bt_setup.connect("clicked", self._bt_setup)
        bt_setup_row.add_suffix(bt_setup)
        bt_setup_row.set_activatable_widget(bt_setup)
        bt_group.add(bt_setup_row)
        self.add(bt_group)

        # ---- Info
        info = Adw.PreferencesGroup(title="Informazioni")
        info.add(Adw.ActionRow(title="Versione", subtitle=__version__))
        info.add(Adw.ActionRow(
            title="Nx sta per Nexiquar",
            subtitle="Progetto di Nexiquar · GPL-3.0"))
        info.add(Adw.ActionRow(
            title="Come funziona",
            subtitle="SMS inviati tramite KDE Connect/GSConnect · IP del "
                     "telefono letto da KDE Connect · chiamate e lettura "
                     "dati tramite ADB wireless · audio chiamate via "
                     "Bluetooth HFP · nessun server, tutto in locale."))
        self.add(info)

        self.kde.connect("devices-changed", lambda *a: self.refresh_status())
        self.adb.connect("status", lambda *a: self.refresh_status())
        self.app.audio.connect("status-changed", self._on_audio_status)
        self.app.audio.refresh()
        self.refresh_status()

    # ------------------------------------------------------------- segnali

    def _on_device_selected(self, row, *_a) -> None:
        if self._combo_guard:
            return
        idx = row.get_selected()
        if idx >= 1 and idx - 1 < len(self.kde.devices):
            self.app.config["kdeconnect_device_id"] = self.kde.devices[idx - 1].id
        else:
            self.app.config["kdeconnect_device_id"] = None

    def _on_auto(self, row, *_a) -> None:
        self.app.config["auto_connect_adb"] = row.get_active()

    def _on_auto_kde(self, row, *_a) -> None:
        self.app.config["auto_connect_kde_ip"] = row.get_active()

    # ----------------------------------------------------------- widget util

    @staticmethod
    def _combo_row(title: str, subtitle: str, options: list[str],
                   current, on_pick) -> Adw.ComboRow:
        """Riga con tendina: la scelta viene salvata via on_pick(valore)."""
        row = Adw.ComboRow(title=title, subtitle=subtitle)
        row.set_model(Gtk.StringList.new([str(o) for o in options]))
        index = 0
        for i, opt in enumerate(options):
            if str(opt) == str(current):
                index = i
                break
        row.set_selected(index)
        row.connect("notify::selected",
                    lambda r, *_a: on_pick(str(options[r.get_selected()])))
        return row

    def _switch_row(self, title: str, subtitle: str, key: str) -> Adw.SwitchRow:
        """Interruttore legato direttamente a una chiave di configurazione."""
        row = Adw.SwitchRow(title=title, subtitle=subtitle,
                            active=bool(self.app.config[key]))
        row.connect("notify::active",
                    lambda r, *_a: self.app.config.__setitem__(
                        key, r.get_active()))
        return row

    # -------------------------------------------------------------- azioni

    def _connect_via_kde(self, *_a) -> None:
        toast(self.window, "Cerco il telefono…", "via KDE Connect")
        self.app.auto_connect_via_kdeconnect()

    def _bt_setup(self, *_a) -> None:
        toast(self.window, "Configuro l'audio Bluetooth…",
              "servirà la password di root una volta")

        def done(kind: str, text: str) -> None:
            if kind == "ok":
                toast(self.window, "Audio Bluetooth configurato",
                      "riavvia il PC se l'audio non parte")
            else:
                toast(self.window, "Configurazione non riuscita",
                      text.strip()[-100:] or "errore sconosciuto")
            self.app.audio.refresh()

        self.app.audio.run_setup(done)

    # ------------------------------------------------------------- audio BT

    def _on_audio_status(self, _audio, *_a) -> None:
        info = self.app.audio.info or {}
        if not info:
            return
        names = ", ".join(n for _addr, n in info.get("paired", []))
        self.bt_devices.set_subtitle(
            names or "nessuno: abbina il telefono dalle impostazioni "
                     "Bluetooth di GNOME")

        if not info.get("available"):
            sub, kind = "Nessun adattatore Bluetooth trovato", "err"
        elif not info.get("powered"):
            sub, kind = ("Bluetooth spento: accendilo dalle impostazioni "
                         "di GNOME", "warn")
        elif not info.get("ofono") or not info.get("phonesim"):
            sub, kind = ("Servizi audio non configurati: premi "
                         "«Configura audio Bluetooth»", "warn")
        elif not info.get("hfp_audio"):
            sub, kind = ("Configurato: attiva «Audio per chiamate» sul "
                         "telefono appaiato", "warn")
        else:
            sub, kind = "Attivo · la voce della chiamata passa dal PC", "ok"
        self.bt_status.set_subtitle(sub)
        self._set_dot(self.bt_dot, kind)

    # -------------------------------------------------------------- azioni

    def _discover(self, *_a) -> None:
        def done(result):
            addrs = result if isinstance(result, list) else []
            if addrs:
                self.addr_entry.set_text(addrs[0])
                toast(self.window, "Trovati dispositivi",
                      ", ".join(addrs[:3]))
            else:
                toast(self.window, "Nessun dispositivo trovato",
                      "attiva il Debug wireless sul telefono")
        self.adb.mdns_discover(done)

    def _connect(self, *_a) -> None:
        address = self.addr_entry.get_text().strip()
        if not address:
            toast(self.window, "Inserisci IP:porta")
            return

        def done(result):
            kind, text = result if isinstance(result, tuple) else ("ko", "")
            if kind == "ok":
                self.app.config["adb_address"] = address
                self.adb.address = address
                toast(self.window, "Connesso", address)
                self.adb.refresh_all()
            else:
                toast(self.window, "Connessione fallita", text[:120])
            self.refresh_status()
        self.adb.connect_device(address, done)

    def _pair(self, *_a) -> None:
        address = self.addr_entry.get_text().strip()
        code = self.pair_entry.get_text().strip()
        if not address or not code:
            toast(self.window, "Servono indirizzo e codice")
            return

        def done(result):
            kind, text = result if isinstance(result, tuple) else ("ko", "")
            if kind == "ok":
                toast(self.window, "Telefono accoppiato", "ora premi Connetti")
                self.pair_entry.set_text("")
            else:
                toast(self.window, "Accoppiamento fallito", text[:120])
            self.refresh_status()
        self.adb.pair(address, code, done)

    def _prepare(self, *_a) -> None:
        if not self.adb.serial:
            toast(self.window, "Connetti prima il telefono")
            return

        def done(results):
            if not isinstance(results, list):
                return
            ok = sum(1 for _n, good, _o in results if good)
            failed = [n for n, good, o in results if not good]
            if ok == len(results):
                toast(self.window, "Permessi concessi", f"{ok}/{ok}")
            else:
                toast(self.window, f"Permessi: {ok}/{len(results)}",
                      ", ".join(failed)[:80])
            self.adb.refresh_all()
            self.refresh_status()
        self.adb.prepare_phone(done)

    # ------------------------------------------------------------- stato

    def refresh_status(self) -> None:
        # KDE Connect
        err = self.kde.last_error
        n_conn = sum(1 for d in self.kde.devices if d.connected)
        if err:
            self.kde_status.set_subtitle(err)
            self._set_dot(self.kde_dot, "err")
        elif not self.kde.available:
            self.kde_status.set_subtitle("In attesa di GSConnect…")
            self._set_dot(self.kde_dot, "warn")
        elif not self.kde.devices:
            self.kde_status.set_subtitle(
                "Nessun dispositivo: abbina il telefono con l'app "
                "KDE Connect")
            self._set_dot(self.kde_dot, "warn")
        else:
            self.kde_status.set_subtitle(
                f"{len(self.kde.devices)} dispositivi, {n_conn} collegati")
            self._set_dot(self.kde_dot, "ok" if n_conn else "warn")

        # combo dispositivi
        names = ["(automatico)"] + [
            f"{d.name}{' · collegato' if d.connected else ''}"
            for d in self.kde.devices]
        self._combo_guard = True
        selected = 0
        wanted = self.app.config["kdeconnect_device_id"]
        for i, d in enumerate(self.kde.devices, start=1):
            if wanted and d.id == wanted:
                selected = i
        new_model = Gtk.StringList.new(names)
        self.device_row.set_model(new_model)
        self.device_model = new_model
        if len(names) > 1:
            self.device_row.set_selected(selected)
        self._combo_guard = False

        # ADB
        if not find_adb():
            self.adb_status.set_subtitle(
                "adb non installato (si installa da solo all'avvio)")
            self._set_dot(self.adb_dot, "err")
        elif self.adb.serial:
            self.adb_status.set_subtitle(f"Connesso · {self.adb.serial}")
            self._set_dot(self.adb_dot, "ok")
        elif self.adb.last_error == "unauthorized":
            self.adb_status.set_subtitle(
                "Non autorizzato: conferma la finestra sul telefono")
            self._set_dot(self.adb_dot, "warn")
        else:
            self.adb_status.set_subtitle(
                "Non connesso — apri il Debug wireless sul telefono")
            self._set_dot(self.adb_dot, "off")

        # IP del telefono conosciuto da KDE Connect / ADB
        ip = (getattr(self.app, "kde_ip", "") or
              (self.app.config["adb_address"] or "").rsplit(":", 1)[0])
        self.ip_label.set_text(ip or "—")

        # Stato Bluetooth: bluetoothctl + pactl sono lenti, un rilevamento
        # ogni ~15 secondi basta e avanza.
        self._bt_tick = getattr(self, "_bt_tick", 0) + 1
        if self._bt_tick % 5 == 1:
            self.app.audio.refresh()

    @staticmethod
    def _set_dot(box: Gtk.Box, kind: str) -> None:
        for cls in ("ok", "warn", "err", "off"):
            box.remove_css_class(cls)
        box.add_css_class(kind)
