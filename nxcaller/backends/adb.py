"""
Backend ADB wireless: chiamate (fai/rispondi/rifiuta/chiudi), lettura SMS,
contatti e registro chiamate.

Requisiti sul telefono (Android 11+, senza root):
  * «Debug wireless» attivo (Impostazioni → Sviluppatore)
  * i permessi dati vengono concessi al pacchetto shell con `pm grant`
    (bottoncino «Prepara il telefono» nelle impostazioni dell'app)

Tutte le operazioni ADB sono eseguite in thread separati; i risultati
tornano sul loop principale GTK con GLib.idle_add.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import gi
gi.require_version("GLib", "2.0")
from gi.repository import GLib, GObject  # noqa: E402

from ..models import (  # noqa: E402
    CallRecord, CallState, CallType, Contact, Message,
    _CALL_LOG_TYPE_MAP, parse_call_state_dump, parse_content_query,
    numbers_match,
)

_POOL = ThreadPoolExecutor(max_workers=3, thread_name_prefix="adb")

_ADB_CANDIDATES = (
    "adb",
    "/usr/lib/android-sdk/platform-tools/adb",
    "/usr/lib/android-sdk/platform-tools/adb.exe",
    str(Path.home() / "Android" / "Sdk" / "platform-tools" / "adb"),
    str(Path.home() / "platform-tools" / "adb"),
)

# Permessi serviti a `com.android.shell` per leggere/agire sul telefono.
_GRANTS = (
    "android.permission.READ_SMS",
    "android.permission.READ_CONTACTS",
    "android.permission.READ_CALL_LOG",
    "android.permission.CALL_PHONE",
)


def _log(msg: str) -> None:
    print(f"[adb] {msg}", file=sys.stderr, flush=True)


def find_adb() -> str | None:
    """Percorso dell'installazione di adb, se presente."""
    for cand in _ADB_CANDIDATES:
        if "/" in cand:
            if os.path.isfile(cand) and os.access(cand, os.X_OK):
                return cand
        else:
            found = shutil.which(cand)
            if found:
                return found
    return None


class AdbManager(GObject.Object):
    """Gestisce la connessione ADB wireless e le azioni sul telefono."""

    __gsignals__ = {
        # stato: "connected" | "disconnected" | "unauthorized" | ...
        "status": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
        # (nuovo_stato, numero_chiamante)
        "call-state": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
        "sms-updated": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        "contacts-updated": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        "log-updated": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        # (titolo, corpo, kind: "sms" | "call" | "info" | "error")
        # NB: chiamato "alert" e non "notify": quest'ultimo è il segnale
        # standard delle proprietà di GObject.
        "alert": (GObject.SignalFlags.RUN_FIRST, None, (str, str, str)),
    }

    def __init__(self) -> None:
        super().__init__()
        self.adb = find_adb()
        self.serial: str | None = None
        self.address: str | None = None
        self.call_state: CallState = CallState.IDLE
        self.active_number: str = ""
        self.sms_threads: dict[str, list[Message]] = {}
        self.contacts: list[Contact] = []
        self.call_log: list[CallRecord] = []
        self.last_error: str = ""
        self._tick = 0
        self._busy = False
        self._poll_id = 0

    # ------------------------------------------------------------ utilità

    def _cmd(self, *args: str, timeout: float = 20.0):
        """Esegue un comando adb. Ritorna (ok, stdout, stderr)."""
        if not self.adb:
            return False, "", "adb non installato"
        base = [self.adb]
        if self.serial:
            base += ["-s", self.serial]
        try:
            proc = subprocess.run(
                base + list(args), capture_output=True, text=True,
                timeout=timeout, check=False)
            return proc.returncode == 0, proc.stdout, proc.stderr
        except FileNotFoundError:
            return False, "", "adb non trovato"
        except subprocess.TimeoutExpired:
            return False, "", "timeout adb"

    def _sh(self, command: str, timeout: float = 25.0):
        """Esegue `adb shell <command>`."""
        return self._cmd("shell", command, timeout=timeout)

    def _run_async(self, fn, on_done=None, *args):
        """Esegue fn in background e consegna il risultato sul main loop."""

        def task():
            try:
                result = fn(*args)
            except Exception as exc:  # noqa: BLE001 — mostriamo l'errore in UI
                result = exc
            GLib.idle_add(self._deliver, result, on_done)

        _POOL.submit(task)

    @staticmethod
    def _deliver(result, on_done):
        if on_done is not None:
            on_done(result)
        return False

    # ------------------------------------------------------------ stato

    def start(self) -> None:
        """Avvia il polling periodico dello stato."""
        if self._poll_id:
            return
        self._poll_id = GLib.timeout_add(1500, self._poll)

    def stop(self) -> None:
        if self._poll_id:
            GLib.source_remove(self._poll_id)
            self._poll_id = 0

    def _poll(self) -> bool:
        self._tick += 1
        if not self.adb:
            return True
        if self._busy:
            return True

        # Verifica periodica che il dispositivo sia ancora collegato.
        if self._tick % 7 == 1:
            self._run_async(self._scan_devices, self._on_scan)

        if self.serial is None:
            return True

        # Stato chiamata: è il cuore dell'app, lo chiediamo a ogni tick.
        if not self._busy:
            self._busy = True
            self._run_async(self._query_call_state, self._on_call_state)

        if self._tick % 8 == 0:
            self._run_async(self._query_sms, self._on_sms)
        if self._tick % 40 == 1:
            self._run_async(self._query_contacts, self._on_contacts)
        if self._tick % 80 == 3:
            self._run_async(self._query_call_log, self._on_log)
        return True

    # ---------------------------------------------------- scansione device

    def _scan_devices(self):
        ok, out, err = self._cmd("devices")
        if not ok:
            return ("error", err.strip() or "errore adb", "")
        devices = []
        unauthorized = False
        for line in out.splitlines()[1:]:
            line = line.strip()
            if not line or line.startswith("*"):
                continue
            parts = line.split()
            if len(parts) >= 2:
                if parts[1] == "device":
                    devices.append(parts[0])
                elif parts[1] == "unauthorized":
                    unauthorized = True
        if len(devices) == 1:
            return ("connected", devices[0], "")
        if len(devices) > 1:
            return ("connected", devices[0], f"{len(devices)} dispositivi")
        if unauthorized:
            return ("unauthorized", "", "")
        return ("disconnected", "", "")

    def _on_scan(self, result) -> None:
        kind, serial, note = result if isinstance(result, tuple) \
            else ("error", "", str(result))
        if kind == "connected" and serial != self.serial:
            self.serial = serial
            self.last_error = ""
            self.emit("status", "connected", note or serial)
            self.refresh_all()
        elif kind == "connected":
            self.last_error = ""
        elif kind != "connected" and self.serial is not None:
            self.serial = None
            self.last_error = kind
            self.emit("status", kind, note)
        elif kind in ("unauthorized", "error"):
            self.last_error = kind
            self.emit("status", kind, note)

    def refresh_all(self) -> None:
        self._run_async(self._query_contacts, self._on_contacts)
        self._run_async(self._query_sms, self._on_sms)
        self._run_async(self._query_call_log, self._on_log)

    # ---------------------------------------------------- accoppiamento

    def pair(self, address: str, code: str, on_done) -> None:
        address, code = address.strip(), code.strip()

        def task():
            ok, out, err = self._cmd("pair", f"{address}", code, timeout=45)
            text = (out + "\n" + err).strip()
            if "Successfully paired" in text or "paired" in text.lower():
                return ("ok", text)
            return ("ko", text or "accoppiamento non riuscito")

        _POOL.submit(lambda: GLib.idle_add(on_done, task()))

    def connect_device(self, address: str, on_done) -> None:
        """Connette adb all'indirizzo wireless (ip:porta)."""
        address = address.strip()

        def task():
            ok, out, err = self._cmd("connect", address, timeout=25)
            text = (out + "\n" + err).strip()
            if "connected to" in text.lower() or "already connected" in text.lower():
                return ("ok", text)
            return ("ko", text or "connessione non riuscita")

        _POOL.submit(lambda: GLib.idle_add(on_done, task()))

    def disconnect_device(self) -> None:
        """Disconnette adb dall'indirizzo wireless."""
        if self.address:
            self._run_async(lambda: self._cmd("disconnect", self.address))
        self.serial = None
        self.emit("status", "disconnected", "")

    def mdns_discover(self, on_done) -> None:
        """Cerca debug wireless sulla rete locale (adb mdns services)."""

        def task():
            ok, out, _ = self._cmd("mdns", "services", timeout=10)
            addrs = re.findall(r"(\d{1,3}(?:\.\d{1,3}){3}:\d+)", out or "")
            seen, uniq = set(), []
            for a in addrs:
                if a not in seen:
                    seen.add(a)
                    uniq.append(a)
            return uniq

        _POOL.submit(lambda: GLib.idle_add(on_done, task()))

    # -------------------------------------------------- porta wireless (mDNS)

    def _mdns_services(self) -> dict[str, str]:
        """
        Servizi mDNS dichiarati dal telefono: {"adb-tls-connect": "ip:porta",
        "adb-tls-pairing": "ip:porta"}.

        Le porte del debug wireless cambiano a ogni riavvio: è l'unico modo
        saperle senza chiederle all'utente.
        """
        ok, out, _ = self._cmd("mdns", "services", timeout=10)
        services: dict[str, str] = {}
        if not ok:
            return services
        for line in (out or "").splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0].startswith("adb-"):
                services[parts[0].split("._")[0]] = parts[1]
        return services

    def mdns_services(self, on_done) -> None:
        _POOL.submit(lambda: GLib.idle_add(on_done, self._mdns_services()))

    def connect_ip(self, ip: str, on_done) -> None:
        """
        Connette adb a `ip` senza scrivere la porta.

        La porta giusta (quella del debug wireless) viene letta da
        `adb mdns services`; se non c'è si prova la 5555 classica.
        Ritorna (kind, indirizzo, indirizzo_pairing, testo) con
        kind in "ok" | "unauthorized" | "ko".
        """
        ip = (ip or "").strip()

        def task():
            services = self._mdns_services()
            target = pair = ""
            for kind, addr in services.items():
                if not addr.startswith(ip + ":"):
                    continue
                if kind == "adb-tls-connect":
                    target = addr
                elif kind == "adb-tls-pairing":
                    pair = addr
            target = target or f"{ip}:5555"
            ok, out, err = self._cmd("connect", target, timeout=25)
            text = (out + "\n" + err).strip()
            low = text.lower()
            if "connected to" in low or "already connected" in low:
                self.address = target
                return ("ok", target, pair, text)
            if "failed to authenticate" in low or "authoriz" in low:
                return ("unauthorized", target, pair, text)
            return ("ko", target, pair, text or "connessione non riuscita")

        _POOL.submit(lambda: GLib.idle_add(on_done, task()))

    def prepare_phone(self, on_done) -> None:
        """Concede a shell i permessi per SMS, contatti, registro e chiamate."""

        def task():
            results = []
            for perm in _GRANTS:
                ok, out, err = self._sh(f"pm grant com.android.shell {perm}",
                                        timeout=15)
                name = perm.rsplit(".", 1)[-1]
                results.append((name, ok, (out + err).strip()))
            return results

        _POOL.submit(lambda: GLib.idle_add(on_done, task()))

    # ---------------------------------------------------- chiamate

    def _query_call_state(self):
        ok, out, _ = self._sh(
            'dumpsys telephony.registry 2>/dev/null | '
            'grep -E "mCallState=|mCallIncomingNumber="')
        state, number = parse_call_state_dump(out or "")
        if not ok or not out.strip():
            # Fallback su alcune ROM/Android recenti.
            ok2, out2, _ = self._sh(
                'dumpsys telecom 2>/dev/null | grep -E '
                '"CallState:|mAddress=" | head -20')
            if ok2 and out2.strip():
                low = out2.lower()
                if "ringing" in low:
                    state = CallState.RINGING
                elif "active" in low or "offhook" in low:
                    state = CallState.ACTIVE
                m = re.search(r"mAddress=([^\s,]+)", out2)
                if m:
                    number = m.group(1).strip('"')
        return state, number

    def _on_call_state(self, result) -> None:
        self._busy = False
        if isinstance(result, Exception):
            return
        state, number = result
        if state == CallState.IDLE:
            number = ""   # numeri "fantasma" restano nel dump dopo la chiamata
        old, old_number = self.call_state, self.active_number
        if state == old and number == old_number:
            return
        self.call_state = state
        self.active_number = number if state != CallState.IDLE else ""
        self.emit("call-state", state.value, self.active_number)
        if state == CallState.IDLE and old != CallState.IDLE:
            # Chiamata finita: aggiorna registro e SMS.
            self._run_async(self._query_call_log, self._on_log)
            self._run_async(self._query_sms, self._on_sms)
        elif state == CallState.RINGING and old != CallState.RINGING:
            name = self.lookup_name(number)
            self.emit("alert", "Chiamata in arrivo",
                      f"{name or number} · {number}".strip(" ·"),
                      "call")

    def dial(self, number: str) -> None:
        number = re.sub(r"[^\d+*#]", "", number or "")
        if not number:
            return

        def task():
            ok, out, err = self._sh(
                f"am start -a android.intent.action.CALL -d 'tel:{number}'")
            text = (out + err)
            _log(f"CALL {number!r} -> ok={ok} out={out.strip()!r} "
                 f"err={err.strip()!r}")
            if ok and "Error" not in text and "Exception" not in text:
                _log("chiamata avviata direttamente")
                return ("ok", "")
            # Fallback: apri il dialer del telefono (nessun permesso serve).
            ok2, out2, err2 = self._sh(
                f"am start -a android.intent.action.DIAL -d 'tel:{number}'")
            _log(f"DIAL fallback -> ok={ok2} out={out2.strip()!r} "
                 f"err={err2.strip()!r}")
            return ("dialer", "Aperto il tastierino sul telefono: "
                              "premi Chiama per confermare.")

        _POOL.submit(lambda: GLib.idle_add(self._on_dial, task()))

    def _on_dial(self, result) -> None:
        kind, msg = result
        _log(f"_on_dial kind={kind!r} msg={msg!r}")
        if kind == "dialer" and msg:
            self.emit("alert", "Chiamata", msg, "info")

    def answer(self) -> None:
        # KEYCODE_CALL (5) risponde alla chiamata in arrivo.
        _log("answer() -> keyevent 5")
        self._run_async(lambda: self._sh("input keyevent 5"))

    def hangup(self) -> None:
        # KEYCODE_ENDCALL (6) termina/rimbalza la chiamata corrente.
        _log("hangup() -> keyevent 6")
        self._run_async(lambda: self._sh("input keyevent 6"))

    def toggle_mute(self) -> None:
        # KEYCODE_MUTE (91) funziona durante una chiamata attiva.
        self._run_async(lambda: self._sh("input keyevent 91"))

    def send_dtmf(self, char: str) -> None:
        names = {"*": "KEYCODE_STAR", "#": "KEYCODE_POUND"}
        code = names.get(char, f"KEYCODE_{char.upper()}" if char.isdigit()
                         else None)
        if code:
            self._run_async(lambda: self._sh(f"input keyevent {code}"))

    # ---------------------------------------------------- SMS (lettura)

    def _query_sms(self):
        # Ultimi ~90 giorni, per non scaricare l'intero archivio.
        since_ms = int((GLib.get_real_time() / 1_000_000) - 90 * 86400) * 1000
        ok, out, _ = self._sh(
            "content query --uri content://sms "
            f"--where \"date>{since_ms}\" "
            "--projection _id:address:body:date:type:read")
        if not ok:
            return None
        threads: dict[str, list[Message]] = {}
        for row in parse_content_query(out or ""):
            mtype = row.get("type", "")
            if mtype in ("3",):      # bozze: ignorate
                continue
            outgoing = mtype in ("2", "4", "5", "6")
            try:
                ts = int(row.get("date", "0")) / 1000.0
            except ValueError:
                ts = 0.0
            msg = Message(
                body=row.get("body", ""),
                address=row.get("address", ""),
                timestamp=ts,
                outgoing=outgoing,
                read=row.get("read", "1") == "1",
                msg_id=row.get("_id", ""),
            )
            if not msg.address:
                continue
            threads.setdefault(msg.address, []).append(msg)
        for msgs in threads.values():
            msgs.sort(key=lambda m: m.timestamp)
        return threads

    def _on_sms(self, result) -> None:
        if result is None or isinstance(result, Exception):
            return
        old_last = {addr: (msgs[-1].msg_id if msgs else "")
                    for addr, msgs in self.sms_threads.items()}
        self.sms_threads = result
        total = sum(len(m) for m in result.values())
        self.emit("sms-updated", total)
        # Notifica solo per messaggi realmente nuovi.
        for addr, msgs in result.items():
            if not msgs:
                continue
            last = msgs[-1]
            if (not last.outgoing and old_last.get(addr) not in (None, "")
                    and last.msg_id and last.msg_id != old_last.get(addr)):
                name = self.lookup_name(addr)
                self.emit("alert", f"SMS · {name or addr}",
                          last.body or "(vuoto)", "sms")

    # ---------------------------------------------------- contatti

    def _query_contacts(self):
        ok, out, _ = self._sh(
            "content query --uri content://com.android.contacts/data/phones "
            "--projection display_name:data1", timeout=60)
        if not ok:
            return []
        contacts = []
        for row in parse_content_query(out or ""):
            number = row.get("data1", "").strip()
            name = row.get("display_name", "").strip()
            if number:
                contacts.append(Contact(name=name, number=number))
        return contacts

    def _on_contacts(self, result) -> None:
        if isinstance(result, Exception) or result is None:
            return
        self.contacts = result
        self.emit("contacts-updated", len(result))

    def lookup_name(self, number: str) -> str:
        for c in self.contacts:
            if numbers_match(c.number, number):
                return c.name
        return ""

    # ---------------------------------------------------- registro

    def _query_call_log(self):
        ok, out, _ = self._sh(
            "content query --uri content://call_log/calls "
            "--projection number:type:date:duration", timeout=40)
        if not ok:
            return []
        records = []
        for row in parse_content_query(out or ""):
            try:
                ts = int(row.get("date", "0")) / 1000.0
            except ValueError:
                ts = 0.0
            records.append(CallRecord(
                number=row.get("number", ""),
                type=_CALL_LOG_TYPE_MAP.get(row.get("type", ""),
                                            CallType.UNKNOWN),
                timestamp=ts,
                duration=int(row.get("duration", "0") or 0),
                name=self.lookup_name(row.get("number", "")),
            ))
        records.sort(key=lambda r: r.timestamp, reverse=True)
        return records

    def _on_log(self, result) -> None:
        if isinstance(result, Exception) or result is None:
            return
        self.call_log = result
        self.emit("log-updated", len(result))
