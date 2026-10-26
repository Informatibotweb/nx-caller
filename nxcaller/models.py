"""Modelli dati condivisi per Nx Caller."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


# ---------------------------------------------------------------- utilità

def normalize_number(number: str | None) -> str:
    """Toglie tutti i caratteri tranne cifre e '+', per confrontare numeri."""
    if not number:
        return ""
    out = []
    for ch in number.strip():
        if ch.isdigit() or (ch == "+" and not out):
            out.append(ch)
    return "".join(out)


def numbers_match(a: str, b: str) -> bool:
    """True se i due numeri sembrano lo stesso contatto (suffisso uguale)."""
    na, nb = normalize_number(a), normalize_number(b)
    if not na or not nb:
        return False
    return na == nb or na.endswith(nb) or nb.endswith(na)


def initials(name: str) -> str:
    """Iniziali da un nome (massimo 2 lettere)."""
    parts = [p for p in (name or "").split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[1][0]).upper()


_AVATAR_COLORS = [
    "blue", "green", "orange", "purple", "teal", "red", "slate", "violet",
]


def avatar_color(name: str) -> str:
    """Colore stabile (per nome) da usare nelle classi CSS dell'avatar."""
    if not name:
        return "slate"
    return _AVATAR_COLORS[sum(ord(c) for c in name) % len(_AVATAR_COLORS)]


def format_ts(ts: float) -> str:
    """Data/ora leggibile: oggi -> ora, ieri -> 'ieri HH:MM', poi data."""
    if not ts:
        return ""
    now = time.time()
    diff = now - ts
    local = time.localtime(ts)
    if diff < 60:
        return "adesso"
    if time.localtime(now).tm_yday == local.tm_yday and \
            time.localtime(now).tm_year == local.tm_year:
        return time.strftime("%H:%M", local)
    if diff < 7 * 86400:
        return "ieri " + time.strftime("%H:%M", local)
    return time.strftime("%d/%m/%Y", local)


_MONTHS = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
           "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]


def format_day(ts: float) -> str:
    """Etichetta del separatore di giorno: «Oggi», «Ieri», «12 ottobre»."""
    if not ts:
        return ""
    local = time.localtime(ts)
    now = time.localtime()

    def same_day(a: time.struct_time, b: time.struct_time) -> bool:
        return (a.tm_year, a.tm_yday) == (b.tm_year, b.tm_yday)

    if same_day(local, now):
        return "Oggi"
    if same_day(local, time.localtime(time.time() - 86400)):
        return "Ieri"
    text = f"{local.tm_mday} {_MONTHS[local.tm_mon - 1]}"
    if local.tm_year != now.tm_year:
        text += f" {local.tm_year}"
    return text


def format_duration(seconds: float) -> str:
    """Durata chiamata in mm:ss o hh:mm:ss."""
    s = int(max(0, seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


# ---------------------------------------------------------------- enum

class CallType(Enum):
    INCOMING = "in"
    OUTGOING = "out"
    MISSED = "missed"
    UNKNOWN = "unknown"


class CallState(Enum):
    IDLE = "idle"
    RINGING = "ringing"
    ACTIVE = "active"


# ---------------------------------------------------------------- dati

@dataclass
class Device:
    """Un telefono accoppiato (via KDE Connect e/o ADB)."""
    id: str
    name: str
    source: str = "kdeconnect"   # "kdeconnect" | "gsconnect"
    reachable: bool = False
    paired: bool = False

    @property
    def connected(self) -> bool:
        return self.reachable and self.paired


@dataclass
class Contact:
    name: str
    number: str

    @property
    def display(self) -> str:
        return self.name or self.number


@dataclass
class CallRecord:
    number: str
    type: CallType = CallType.UNKNOWN
    timestamp: float = 0.0
    duration: int = 0
    name: str = ""


@dataclass
class Message:
    """Singolo SMS."""
    body: str
    address: str = ""
    timestamp: float = 0.0
    outgoing: bool = False
    read: bool = True
    contact_name: str = ""
    msg_id: str = ""
    pending: bool = False    # inviato ma non ancora confermato
    failed: bool = False


@dataclass
class Conversation:
    """Thread di conversazione (thread_id sul telefono)."""
    thread_id: str
    address: str = ""
    contact_name: str = ""
    messages: list[Message] = field(default_factory=list)

    @property
    def title(self) -> str:
        return self.contact_name or self.address or "Sconosciuto"

    @property
    def last_message(self) -> Message | None:
        return self.messages[-1] if self.messages else None

    @property
    def last_ts(self) -> float:
        return self.messages[-1].timestamp if self.messages else 0.0

    @property
    def unread(self) -> int:
        return sum(1 for m in self.messages if not m.outgoing and not m.read)

    def sorted_messages(self) -> list[Message]:
        return sorted(self.messages, key=lambda m: m.timestamp)


# ---------------------------------------------------------------- parsing ADB

def parse_call_state_dump(dump: str) -> tuple[CallState, str]:
    """
    Estra stato chiamata e numero chiamante da `dumpsys telephony.registry`.
    Ritorna (CallState, numero).
    """
    state = CallState.IDLE
    number = ""
    current = 0
    for line in dump.splitlines():
        line = line.strip()
        if line.startswith("mCallState="):
            try:
                current = int(line.split("=", 1)[1].strip())
            except ValueError:
                current = 0
            if current == 1:
                state = CallState.RINGING
            elif current == 2 and state != CallState.RINGING:
                state = CallState.ACTIVE
        elif line.startswith("mCallIncomingNumber=") and current in (1, 2):
            val = line.split("=", 1)[1].strip()
            if val and not number:
                number = val
    return state, number


_CALL_LOG_TYPE_MAP = {
    "1": CallType.INCOMING,
    "2": CallType.OUTGOING,
    "3": CallType.MISSED,
    "4": CallType.MISSED,   # voicemail entrante
    "5": CallType.OUTGOING, # rifiutata in rete / outgoing (in alcune ROM)
    "6": CallType.MISSED,   # rifiutata
}


def parse_content_query(output: str) -> list[dict[str, str]]:
    """
    Converte l'output di `adb shell content query` in liste di dict.
    Esempio di riga: `Row: 0 number=+39..., type=1, date=1700000000000`
    """
    rows: list[dict[str, str]] = []
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith("Row:"):
            continue
        # Taglia "Row: N "
        _, _, rest = line.partition(" ")
        _, _, rest = partition_first_token(rest)
        row: dict[str, str] = {}
        # I campi possono contenere '=' e ',' nei valori: splitto per
        # ", chiave=" cercando chiavi note; strategia semplice: split su
        # una virgola seguita da una parola e un '='.
        import re
        for match in re.finditer(r"(?:^|,\s*)(\w+)=", rest):
            key = match.group(1)
            start = match.end()
            # il valore finisce alla prossima ", chiave=" oppure a fine riga
            nxt = re.search(r",\s*\w+=", rest[start:])
            end = start + nxt.start() if nxt else len(rest)
            row[key] = rest[start:end].strip()
        if row:
            rows.append(row)
    return rows


def partition_first_token(text: str) -> tuple[str, str, str]:
    """Serve a parse_content_query: separa il primo token (numero di riga)."""
    parts = text.split(" ", 1)
    if len(parts) == 1:
        return parts[0], "", ""
    return parts[0], " ", parts[1]
