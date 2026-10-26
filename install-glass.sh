#!/usr/bin/env bash
#
# install-glass.sh — Liquid Glass REALE per Nx Caller (glass-lib).
#
# Compila da sorgente glass-lib <https://github.com/ryohsuke1231/glass-lib>,
# la libreria dell'autore dell'estensione Liquid Glass per GNOME: aggiunge
# alla finestra di Nx Caller la vera rifrazione, il blur e la luce di
# bordo del Liquid Glass, dentro l'app (funziona su ogni compositore).
#
# Requisiti: GNOME 50 / GTK ≥ 4.22 e libadwaita ≥ 1.9 (Ubuntu 26.04 ok).
# Senza questa libreria Nx Caller resta comunque identico, con il tema
# Liquid Glass fatto in CSS.
#
# Serve la password di root una volta sola (installazione in /usr/local).
#
set -euo pipefail

PREFIX=/usr/local
SRC=/tmp/glass-lib-nxcaller
REPO=https://github.com/ryohsuke1231/glass-lib

CYAN=$'\033[36m'; GRN=$'\033[32m'; YLW=$'\033[33m'; BLD=$'\033[1m'; RST=$'\033[0m'
step() { echo; echo "${BLD}==> $*${RST}"; }
ok()   { echo "  ${GRN}✓${RST} $*"; }
warn() { echo "  ${YLW}!${RST} $*"; }

sudo_run() {
    if [ "$(id -u)" -eq 0 ]; then "$@"
    elif command -v pkexec >/dev/null 2>&1; then pkexec "$@"
    else sudo "$@"
    fi
}

# --------------------------------------------------------- requisiti GTK
step "1/4  Verifica versioni"
python3 - <<'PY' || { echo "Serve GTK ≥ 4.22 e libadwaita ≥ 1.9 (GNOME 50)."; exit 1; }
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw
gv = Gtk.get_major_version() * 100 + Gtk.get_minor_version()
av = Adw.get_major_version() * 100 + Adw.get_minor_version()
assert gv >= 422, f"GTK {Gtk.get_major_version()}.{Gtk.get_minor_version()} < 4.22"
assert av >= 109, f"libadwaita {Adw.get_major_version()}.{Adw.get_minor_version()} < 1.9"
print(f"  GTK {Gtk.get_major_version()}.{Gtk.get_minor_version()}"
      f" · libadwaita {Adw.get_major_version()}.{Adw.get_minor_version()}")
PY

# ------------------------------------------------------------- pacchetti
step "2/4  Pacchetti di compilazione"
PKGS=(meson ninja-build build-essential git pkg-config
      libgtk-4-dev libadwaita-1-dev libepoxy-dev
      gobject-introspection libgirepository1.0-dev)
MISSING=()
for p in "${PKGS[@]}"; do
    dpkg -s "$p" >/dev/null 2>&1 || MISSING+=("$p")
done
if [ ${#MISSING[@]} -gt 0 ]; then
    echo "  installo: ${MISSING[*]}"
    sudo_run env DEBIAN_FRONTEND=noninteractive \
        /usr/bin/apt-get install -y "${MISSING[@]}"
    ok "installati"
else
    ok "tutto presente"
fi

# -------------------------------------------------------------- compilazione
step "3/4  Compilazione di glass-lib"
rm -rf "$SRC"
git clone --depth 1 "$REPO" "$SRC"
cd "$SRC"
meson setup build \
    --prefix="$PREFIX" \
    -Dintrospection=true -Dexamples=false -Dtests=false -Ddocumentation=false \
    -Ddemos='[]'
meson compile -C build
ok "compilato"

# ------------------------------------------------------------- installazione
step "4/4  Installazione in $PREFIX"
sudo_run meson install -C build
sudo_run ldconfig
ok "installato"

echo
echo "${BLD}Fatto.${RST} Riavvia Nx Caller: la barra del titolo e i pannelli"
echo "avraanno il vetro rifrangente reale del Liquid Glass."
