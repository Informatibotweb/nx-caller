#!/usr/bin/env bash
#
# Nx Caller — avvio con installazione automatica delle dipendenze.
#
# Se al primo avvio mancano i componenti GTK (o adb / scrcpy / avahi),
# vengono installati da soli: pkexec chiederà la password una volta.
#
set -uo pipefail

HERE="$(cd "$(dirname "$(readlink -rf "${BASH_SOURCE[0]}")")" && pwd)"

# ------------------------------------------------------- dipendenze obbligatorie
GUI_DEPS=(python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1)
# adb        -> chiamate, registro, contatti, lettura SMS
# scrcpy     -> specchio dello schermo
# avahi-utils-> scopre l'IP del telefono via KDE Connect
OPT_DEPS=(adb scrcpy avahi-utils)

gtk_ok() {
    command -v python3 >/dev/null 2>&1 || return 1
    python3 - "$HERE" <<'PY' 2>/dev/null
import sys
sys.path.insert(0, sys.argv[1])
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gdk, Gio
import nxcaller
PY
}

have_pkg() { dpkg -s "$1" >/dev/null 2>&1; }

install_pkgs() {
    echo "Installo: $*"
    if command -v pkexec >/dev/null 2>&1; then
        pkexec /usr/bin/apt-get update -qq >/dev/null 2>&1
        pkexec env DEBIAN_FRONTEND=noninteractive /usr/bin/apt-get install -y "$@"
        return $?
    fi
    if command -v apt-get >/dev/null 2>&1; then
        sudo /usr/bin/apt-get install -y "$@"
        return $?
    fi
    return 1
}

# --- GUI: senza non c'è proprio l'app
if ! gtk_ok; then
    echo "!! Mancano i componenti GUI (GTK4 / libadwaita)."
    if ! install_pkgs "${GUI_DEPS[@]}"; then
        echo "Installazione non riuscita. Esegui a mano:"
        echo "    sudo apt install ${GUI_DEPS[*]}"
        exit 1
    fi
    gtk_ok || { echo "Componenti installati ma non funzionanti."; exit 1; }
fi

# --- opzionali ma utili: si installano da soli al primo avvio
MISSING=()
for opt in "${OPT_DEPS[@]}"; do
    command -v "$opt" >/dev/null 2>&1 && continue
    case "$opt" in
        avahi-browse) have_pkg avahi-utils || MISSING+=(avahi-utils) ;;
        *)            have_pkg "$opt" || MISSING+=("$opt") ;;
    esac
done
if [ ${#MISSING[@]} -gt 0 ]; then
    echo "Dipendenze mancanti, le installo: ${MISSING[*]}"
    install_pkgs "${MISSING[@]}" || \
        echo "nota: alcune non installate — l'app le proporrà comunque"
fi

# --- glass-lib (Liquid Glass reale): opzionale, se c'à l'app la usa
for _d in /usr/local/lib/*/girepository-1.0 /usr/local/lib/girepository-1.0; do
    [ -d "$_d" ] && GI_TYPELIB_PATH="${GI_TYPELIB_PATH:+$GI_TYPELIB_PATH:}$_d"
done
for _d in /usr/local/lib/* /usr/local/lib; do
    [ -d "$_d" ] && LD_LIBRARY_PATH="${LD_LIBRARY_PATH:+$LD_LIBRARY_PATH:}$_d"
done
export GI_TYPELIB_PATH LD_LIBRARY_PATH

cd "$HERE"
exec python3 -m nxcaller "$@"
