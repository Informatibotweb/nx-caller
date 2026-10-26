"""
Backend KDE Connect.

Su GNOME/Ubuntu il protocollo KDE Connect è implementato dall'estensione
GSConnect, che espone D-Bus:

  bus:    org.gnome.Shell.Extensions.GSConnect
  root:   /org/gnome/Shell/Extensions/GSConnect        (ObjectManager)
  device: /org/gnome/Shell/Extensions/GSConnect/Device/<id>
          -> org.gnome.Shell.Extensions.GSConnect.Device  (proprietà)
          -> org.gtk.Actions                               (azioni plugin)

L'invio di SMS usa l'azione `sendSms` del plugin SMS del dispositivo
(parametri: telefono, testo). La ricezione degli SMS non è esposta su
D-Bus da GSConnect: per quella usiamo il backend ADB (pages/messaging).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import gi
gi.require_version("Gio", "2.0")
from gi.repository import GLib, GObject, Gio  # noqa: E402

from ..models import Device  # noqa: E402

BUS = "org.gnome.Shell.Extensions.GSConnect"
ROOT = "/org/gnome/Shell/Extensions/GSConnect"
IFACE_DEVICE = "org.gnome.Shell.Extensions.GSConnect.Device"
IFACE_PROPS = "org.freedesktop.DBus.Properties"
IFACE_ACTIONS = "org.gtk.Actions"
IFACE_OM = "org.freedesktop.DBus.ObjectManager"

# Timeout per la chiamata D-Bus di invio: senza, se GSConnect non risponde
# la bolla resta in «invio…» per sempre.
SMS_TIMEOUT_MS = 15000

# Tipo di servizio mDNS pubblicato da KDE Connect (protocollo ufficiale:
# l'istanza si chiama come il device id, e il TXT contiene "id=<device id>").
MDNS_TYPE = "_kdeconnect._udp"

_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="kdeconnect")

# Porte TCP usate dal protocollo KDE Connect (1714–1764).
_KDE_PORTS = range(1714, 1765)


def _log(msg: str) -> None:
    print(f"[kdeconnect] {msg}", file=sys.stderr, flush=True)


def device_path(device_id: str) -> str:
    """Percorso D-Bus del dispositivo (i caratteri non alfanumerici diventano _)."""
    return f"{ROOT}/Device/{re.sub(r'[^A-Za-z0-9_]', '_', device_id)}"


def _session_bus() -> Gio.DBusConnection | None:
    try:
        return Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except GLib.Error:
        return None


def _describe_error(err: GLib.Error) -> str:
    msg = err.message or str(err)
    if "SERVICE_UNKNOWN" in msg or "No such name" in msg:
        return ("GSConnect non è in esecuzione: attiva l'estensione "
                "«Connetti KDE» da Estensioni di GNOME.")
    if "No such action" in msg:
        return ("Il plugin SMS non è attivo su questo dispositivo: aprilo "
                "nelle impostazioni di GSConnect.")
    return msg


# ------------------------------------------------------------------ IP

# "= eth0 IPv4 <nome> _kdeconnect._udp local <host> <ip> <porta> "id=…;""
_AVAHI_RE = re.compile(
    r'^=\s+\S+\s+\S+\s+(?P<name>.+?)\s+' + re.escape(MDNS_TYPE) +
    r'\s+\S+\s+(?P<host>\S+)\s+(?P<addr>\S+)\s+(?P<port>\d+)\s+"(?P<txt>.*)"')


def _hex_ip(hexaddr: str, v6: bool) -> str:
    """Converte un indirizzo esadecimale di /proc/net/tcp[6] in IPv4."""
    try:
        raw = bytes.fromhex(hexaddr)
    except ValueError:
        return ""
    if not v6:
        return ".".join(str(b) for b in raw[::-1]) if len(raw) == 4 else ""
    # tcp6: indirizzi IPv4 mappati ::ffff:a.b.c.d
    if len(raw) == 16 and raw[:12] == b"\x00" * 10 + b"\xff\xff":
        return ".".join(str(b) for b in raw[12:16][::-1])
    return ""


def _ip_from_proc() -> list[str]:
    """
    IP dei telefoni con cui KDE Connect ha già una connessione TCP aperta.

    Fallback senza dipendenze: legge /proc/net/tcp e cerca connessioni
    stabilite sulle porte del protocollo (1714–1764).
    """
    found: list[str] = []
    for path, v6 in (("/proc/net/tcp", False), ("/proc/net/tcp6", True)):
        try:
            with open(path, encoding="ascii") as fh:
                lines = fh.read().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            parts = line.split()
            if len(parts) < 4 or parts[3] != "01":     # 01 = ESTABLISHED
                continue
            try:
                port = int(parts[2].split(":")[1], 16)
                ip = _hex_ip(parts[2].split(":")[0], v6)
            except (ValueError, IndexError):
                continue
            if port in _KDE_PORTS and ip and ip not in found:
                found.append(ip)
    return found


class KdeConnectBackend(GObject.Object):
    """Elenco dispositivi KDE Connect + invio SMS via D-Bus."""

    __gsignals__ = {
        "devices-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "sms-sent": (GObject.SignalFlags.RUN_FIRST, None, (bool, str)),
    }

    def __init__(self) -> None:
        super().__init__()
        self.devices: list[Device] = []
        self.available = False
        self.last_error: str = ""
        self.bus = _session_bus()
        if self.bus is None:
            self.last_error = "Bus D-Bus di sessione non disponibile."
            return
        self._watch_id = self.bus.signal_subscribe(
            BUS, IFACE_PROPS, "PropertiesChanged", None, None,
            Gio.DBusSignalFlags.NONE, self._on_props_changed)
        self.refresh()

    # ------------------------------------------------------------ stato

    def refresh(self) -> None:
        if self.bus is None:
            return
        self.bus.call(
            BUS, ROOT, IFACE_OM, "GetManagedObjects", None,
            GLib.VariantType("(a{oa{sa{sv}}})"),
            Gio.DBusCallFlags.NONE, -1, None, self._on_managed_objects)

    def _on_managed_objects(self, conn, res) -> None:
        try:
            result = conn.call_finish(res)
        except GLib.Error as err:
            self.available = False
            self.last_error = _describe_error(err)
            self.devices = []
            self.emit("devices-changed")
            return

        self.available = True
        self.last_error = ""
        objects = result.unpack()[0]
        devices: list[Device] = []
        for path, ifaces in objects.items():
            props = ifaces.get(IFACE_DEVICE)
            if not props:
                continue
            dev_id = str(props.get("Id", ""))
            if not dev_id:
                continue
            devices.append(Device(
                id=dev_id,
                name=str(props.get("Name") or dev_id),
                source="gsconnect",
                reachable=bool(props.get("Connected")),
                paired=bool(props.get("Paired")),
            ))
        self.devices = devices
        self.emit("devices-changed")

    def _on_props_changed(self, _conn, _sender, path, _iface,
                          _member, _sig, params) -> None:
        changed = params.unpack()[1] if params else {}
        if not path.startswith(f"{ROOT}/Device/"):
            return
        for dev in self.devices:
            if device_path(dev.id) == path:
                if "Connected" in changed:
                    dev.reachable = bool(changed["Connected"])
                if "Paired" in changed:
                    dev.paired = bool(changed["Paired"])
                self.emit("devices-changed")
                return

    # ------------------------------------------------------------ IP telefono

    @staticmethod
    def _mdns_ip(device_id: str | None) -> str:
        """
        IP del telefono pubblicato su mDNS come `_kdeconnect._udp`.

        KDE Connect usa l'id del device come nome del servizio e lo ripete
        nel TXT ("id=…"), per cui il telefono abbinaato è individuabile
        senza scrivere nulla. Serve `avahi-browse` (pacchetto avahi-utils).
        """
        if not shutil.which("avahi-browse"):
            return ""
        try:
            proc = subprocess.run(
                ["avahi-browse", "-rptf", MDNS_TYPE],
                capture_output=True, text=True, timeout=8, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return ""
        wanted = (device_id or "").strip()
        fallback = ""
        for line in (proc.stdout or "").splitlines():
            m = _AVAHI_RE.match(line.strip())
            if not m:
                continue
            addr = m.group("addr")
            if "." not in addr:          # preferisci IPv4
                continue
            txt, name = m.group("txt"), m.group("name").strip()
            if wanted:
                if wanted in txt or name == wanted:
                    return addr
            else:
                fallback = fallback or addr
        return fallback

    def resolve_ip(self, device_id: str | None, on_done) -> None:
        """
        Trova l'IP del telefono noto a KDE Connect e lo consegna a `on_done`.

        Due strade, entrambe senza chiedere nulla all'utente:
          1. mDNS: il record `_kdeconnect._udp` contiene l'id del device;
          2. fallback: la connessione TCP che GSConnect ha già aperto
             (lettura di /proc/net/tcp, nessuna dipendenza).
        """

        def task():
            ip = self._mdns_ip(device_id)
            if ip:
                _log(f"IP {ip} trovato via mDNS per {device_id or 'auto'}")
                return ip
            ips = _ip_from_proc()
            if ips:
                _log(f"IP {ips[0]} trovato dalla connessione attiva")
                return ips[0]
            _log("nessun IP trovato per il telefono")
            return ""

        _POOL.submit(lambda: GLib.idle_add(on_done, task()))

    # ------------------------------------------------------------ uso

    def get_device(self, device_id: str | None) -> Device | None:
        """Dispositivo scelto, oppure il primo collegato e associato."""
        if device_id:
            for dev in self.devices:
                if dev.id == device_id:
                    return dev
        for dev in self.devices:
            if dev.connected:
                return dev
        return None

    def send_sms(self, number: str, body: str,
                 device_id: str | None = None) -> None:
        """Invia un SMS tramite il plugin SMS di KDE Connect (asincrono)."""
        dev = self.get_device(device_id)
        if dev is None:
            self.emit("sms-sent", False,
                      "Nessun telefono collegato via KDE Connect.")
            return
        if not number.strip() or not body.strip():
            self.emit("sms-sent", False, "Numero o messaggio vuoto.")
            return

        _log(f"send_sms → {number!r} su {dev.name!r} "
             f"(device_id={device_id or 'auto'})")
        params = GLib.Variant(
            "(sava{sv})",
            ["sendSms", [GLib.Variant("(ss)", (number.strip(), body))], {}])
        if self.bus is None:
            _log("bus non disponibile")
            self.emit("sms-sent", False, "Bus D-Bus non disponibile.")
            return
        self.bus.call(
            BUS, device_path(dev.id), IFACE_ACTIONS, "Activate", params,
            None, Gio.DBusCallFlags.NONE, SMS_TIMEOUT_MS, None,
            lambda conn, res: self._on_send_sms(conn, res, number))

    def _on_send_sms(self, conn, res, number: str) -> None:
        try:
            conn.call_finish(res)
            _log(f"risposta D-Bus OK per {number!r}")
            self.emit("sms-sent", True, f"SMS inviato a {number}.")
        except GLib.Error as err:
            _log(f"errore D-Bus per {number!r}: {err.message}")
            self.emit("sms-sent", False, _describe_error(err))
