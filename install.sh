#!/usr/bin/env bash
#
# Nx Caller — installazione completa in un colpo solo.
#
#   ./install.sh
#
# Fa tutto da solo:
#   1. installa le dipendenze di sistema che mancano (chiederà la password
#      di root una volta sola, via pkexec);
#   2. copia il programma in ~/.local/share/nxcaller;
#   3. crea il comando `nx-caller` in ~/.local/bin;
#   4. crea la voce di menu con icona;
#   5. mette l'icona sul Desktop.
#
# Per disinstallare:  ./install.sh --uninstall
#
set -euo pipefail

SRC="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
PREFIX="${PREFIX:-$HOME/.local}"
SHARE="$PREFIX/share/nxcaller"
BIN="$PREFIX/bin"
APPS="$PREFIX/share/applications"
ICONS="$PREFIX/share/icons/hicolor/scalable/apps"
DESKTOP_DIR="$HOME/Desktop"
[ -d "$DESKTOP_DIR" ] || DESKTOP_DIR="$HOME/Scrivorio"

CYAN=$'\033[36m'; GRN=$'\033[32m'; YLW=$'\033[33m'; BLD=$'\033[1m'; RST=$'\033[0m'
step() { echo; echo "${BLD}==> $*${RST}"; }
ok()   { echo "  ${GRN}✓${RST} $*"; }
warn() { echo "  ${YLW}!${RST} $*"; }

# ------------------------------------------------------------- disinstalla
if [ "${1:-}" = "--uninstall" ]; then
    step "Disinstallazione"
    rm -rf "$SHARE" "$BIN/nx-caller" \
           "$APPS/org.nxcaller.NxCaller.desktop" \
           "$ICONS/org.nxcaller.NxCaller.svg" \
           "$DESKTOP_DIR/Nx Caller.desktop" 2>/dev/null || true
    ok "rimosso (la configurazione in ~/.config/nxcaller resta)"
    exit 0
fi

# ------------------------------------------------------------ dipendenze
step "1/6  Dipendenze di sistema"
DEPS=(python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1
      adb scrcpy avahi-utils)
MISSING=()
for p in "${DEPS[@]}"; do
    if dpkg -s "$p" >/dev/null 2>&1; then
        ok "$p già installato"
    else
        MISSING+=("$p")
    fi
done
if [ ${#MISSING[@]} -gt 0 ]; then
    warn "mancano: ${MISSING[*]}"
    echo "  le installo ora (serve la password di root una volta)…"
    if command -v pkexec >/dev/null 2>&1; then
        pkexec /usr/bin/apt-get update -qq >/dev/null 2>&1 || true
        pkexec env DEBIAN_FRONTEND=noninteractive \
              /usr/bin/apt-get install -y "${MISSING[@]}"
    else
        sudo /usr/bin/apt-get install -y "${MISSING[@]}"
    fi
    ok "dipendenze installate"
fi

# ------------------------------------------------------------------ codice
step "2/6  Programma"
mkdir -p "$SHARE"
rm -rf "$SHARE/nxcaller"
cp -r "$SRC/nxcaller" "$SHARE/"
cp "$SRC/setup-hfp.sh" "$SHARE/setup-hfp.sh"
find "$SHARE" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
ok "codice -> $SHARE"

# -------------------------------------------------------------- lanciatore
step "3/6  Comando nx-caller"
mkdir -p "$BIN"
cat > "$BIN/nx-caller" <<LAUNCH
#!/bin/sh
# glass-lib (Liquid Glass reale) si installa in /usr/local: se c'è, la
# troviamo senza tocare il resto del sistema.
for _d in /usr/local/lib/*/girepository-1.0 /usr/local/lib/girepository-1.0; do
    [ -d "\$_d" ] && GI_TYPELIB_PATH="\${GI_TYPELIB_PATH:+\$GI_TYPELIB_PATH:}\$_d"
done
for _d in /usr/local/lib/* /usr/local/lib; do
    [ -d "\$_d" ] && LD_LIBRARY_PATH="\${LD_LIBRARY_PATH:+\$LD_LIBRARY_PATH:}\$_d"
done
export GI_TYPELIB_PATH LD_LIBRARY_PATH
PYTHONPATH="$SHARE\${PYTHONPATH:+:\$PYTHONPATH}" exec python3 -m nxcaller "\$@"
LAUNCH
chmod +x "$BIN/nx-caller"
ok "comando -> $BIN/nx-caller"

# ------------------------------------------------- menu e icona nel desktop
step "4/6  Menu di sistema e icone"
mkdir -p "$APPS" "$ICONS"
sed -e "s|^Exec=.*|Exec=$BIN/nx-caller|" \
    "$SRC/data/org.nxcaller.NxCaller.desktop" \
    > "$APPS/org.nxcaller.NxCaller.desktop"
cp "$SRC/data/org.nxcaller.NxCaller.svg" "$ICONS/org.nxcaller.NxCaller.svg"
ok "voce di menu con icona"

command -v update-desktop-database >/dev/null 2>&1 && \
    update-desktop-database "$APPS" 2>/dev/null || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && \
    gtk-update-icon-cache -f "$PREFIX/share/icons/hicolor" 2>/dev/null || true

# ------------------------------------------------------------ icona Desktop
step "5/6  Icona sul Desktop"
if [ -d "$DESKTOP_DIR" ]; then
    cat > "$DESKTOP_DIR/Nx Caller.desktop" <<DESK
[Desktop Entry]
Version=1.0
Type=Application
Name=Nx Caller
Name[it_IT]=Nx Caller
GenericName=Phone on desktop
GenericName[it_IT]=Telefono dal desktop
Comment=Call, SMS and screen mirroring through KDE Connect and ADB
Comment[it_IT]=Chiama, scrivi SMS e vedi lo schermo del telefono dal PC
Exec=$BIN/nx-caller
Path=$SHARE
Icon=$ICONS/org.nxcaller.NxCaller.svg
Terminal=false
StartupNotify=true
Categories=Network;Telephony;GTK;GNOME;
Keywords=phone;sms;call;kdeconnect;adb;scrcpy;telefono;chiamate;
StartupWMClass=nx-caller
DESK
    chmod +x "$DESKTOP_DIR/Nx Caller.desktop"
    ok "icona -> $DESKTOP_DIR/Nx Caller.desktop"
else
    warn "nessuna cartella Desktop trovata: salto l'icona"
fi

# ------------------------------------------------------------------- PATH
step "6/6  PATH"
case ":$PATH:" in
    *":$BIN:"*) ok "$BIN è già nel PATH" ;;
    *)  warn "$BIN non è nel PATH; aggiungi al ~/.bashrc:"
        echo "        export PATH=\"$BIN:\$PATH\"" ;;
esac

echo
echo "  ${BLD}Fatto.${RST} Avvia Nx Caller con:"
echo "     • l'icona sul ${BLD}Desktop${RST} o nel ${BLD}menu delle app${RST}"
echo "     • oppure da terminale:  ${BLD}nx-caller${RST}"
echo
echo "  Primo avvio: attiva il ${BLD}Debug wireless${RST} sul telefono e premi"
echo "  «Usa IP di KDE Connect» nelle Impostazioni: l'IP lo trova da solo."
