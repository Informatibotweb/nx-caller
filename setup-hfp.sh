#!/usr/bin/env bash
#
# setup-hfp.sh — trasforma questo PC in un "auricolare Bluetooth" (profilo
# HFP Hands-Free) per il telefono, così la voce delle chiamate GSM passa
# dalle casse e dal microfono del PC.
#
# Perché serve tutto questo:
#   * BlueZ espone il profilo HFP solo in modalità "experiment"
#   * oFono implementa il lato HF (Hands-Free) e richiede un modem: su un
#     desktop non ce n'è uno, quindi usiamo phonesim come emulatore
#   * PipeWire/WirePlumber porta l'audio SCO alle casse e al microfono
#
# Da eseguire come root:  sudo ./setup-hfp.sh     (oppure pkexec)
#
set -uo pipefail

RED=$'\033[31m'; GRN=$'\033[32m'; YLW=$'\033[33m'; BLD=$'\033[1m'; RST=$'\033[0m'
step() { echo; echo "${BLD}==> $*${RST}"; }
ok()   { echo "  ${GRN}✓${RST} $*"; }
warn() { echo "  ${YLW}!${RST} $*"; }
fail() { echo "  ${RED}✗${RST} $*"; }

if [ "$(id -u)" -ne 0 ]; then
    echo "Serve root:  sudo $0   (o pkexec $0)"
    exit 1
fi

# --------------------------------------------------------------- pacchetti
step "1/6  Pacchetti necessari"
PKGS=(ofono ofono-phonesim bluez pipewire wireplumber pipewire-pulse pipewire-audio-client-libraries)
MISSING=()
for p in "${PKGS[@]}"; do
    if dpkg -s "$p" >/dev/null 2>&1; then ok "$p già installato"; else MISSING+=("$p"); fi
done
if [ ${#MISSING[@]} -gt 0 ]; then
    echo "  installo: ${MISSING[*]}"
    if apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y "${MISSING[@]}"; then
        ok "installati"
    else
        fail "installazione fallita — esegui manualmente:  apt install ${MISSING[*]}"
        exit 1
    fi
fi

# ------------------------------------------------- flag "experimental" BlueZ
step "2/6  Abilita HSP/HFP sperimentale in BlueZ"
CONF=/etc/bluetooth/main.conf
[ -f "$CONF" ] || { echo "[General]" > "$CONF"; ok "creato $CONF"; }
cp -n "$CONF" "$CONF.nxcaller.bak" >/dev/null 2>&1
if grep -qiE '^[[:space:]]*#?[[:space:]]*Experimental[[:space:]]*=' "$CONF"; then
    sed -i -E 's/^[[:space:]]*#?[[:space:]]*Experimental[[:space:]]*=.*/Experimental = true/' "$CONF"
    ok "Experimental = true (modificato)"
else
    if grep -qiE '^\[General\]' "$CONF"; then
        sed -i -E '/^\[General\]/a Experimental = true' "$CONF"
    else
        printf '\n[General]\nExperimental = true\n' >> "$CONF"
    fi
    ok "Experimental = true (aggiunto)"
fi
grep -i "Experimental" "$CONF" | head -1 | sed 's/^/    /'

# ------------------------------------------------------- phonesim + ofono
step "3/6  Configura l'emulatore di modem (phonesim)"
PHONESIM=/etc/ofono/phonesim.conf
mkdir -p /etc/ofono
if [ -f "$PHONESIM" ] && grep -q "12345" "$PHONESIM" 2>/dev/null; then
    ok "$PHONESIM già presente"
else
    cat > "$PHONESIM" <<'EOF'
[phonesim]
Driver=phonesim
Address=127.0.0.1
Port=12345
EOF
    ok "scritto $PHONESIM"
fi

# ---------------------------------------------------------------- riavvio
step "4/6  Riavvio Bluetooth e avvio i servizi"
systemctl restart bluetooth.service 2>/dev/null && ok "bluetooth riavviato" \
    || fail "riavvio bluetooth fallito"
sleep 1

pkill -f "phonesim -p 12345" >/dev/null 2>&1
if command -v phonesim >/dev/null 2>&1; then
    nohup phonesim -p 12345 /usr/share/phonesim/default.xml \
        >/var/log/nxcaller-phonesim.log 2>&1 &
    sleep 1.5
    if pgrep -f "phonesim -p 12345" >/dev/null; then ok "phonesim avviato (porta 12345)"
    else fail "phonesim non parte — log: /var/log/nxcaller-phonesim.log"; fi
else
    fail "binario phonesim non trovato (pacchetto ofono-phonesim)"
fi

if systemctl restart ofono.service 2>/dev/null; then
    ok "ofono riavviato"
else
    warn "nessun unit systemd per ofono — lo avvio a mano"
    pkill ofono >/dev/null 2>&1; sleep 0.5
    nohup ofono >/var/log/nxcaller-ofono.log 2>&1 &
    sleep 1.5
    pgrep -x ofono >/dev/null && ok "ofono avviato" || fail "ofono non parte"
fi

# ---------------------------------------------------------------- verifica
step "5/6  Verifica"
if pgrep -f "phonesim -p 12345" >/dev/null; then ok "phonesim in esecuzione"; else fail "phonesim assente"; fi
if pgrep -x ofono >/dev/null; then ok "ofono in esecuzione"; else fail "ofono assente"; fi
if bluetoothctl show 2>/dev/null | grep -qi "Name:"; then ok "controller Bluetooth visibile"; else fail "nessun controller Bluetooth"; fi

# ------------------------------------------------------ rendi il PC scopribile
step "6/6  Rendi il PC agganciabile dal telefono"
bluetoothctl power on >/dev/null 2>&1
bluetoothctl discoverable on >/dev/null 2>&1
bluetoothctl pairable on >/dev/null 2>&1
bluetoothctl agent NoInputNoOutput >/dev/null 2>&1
bluetoothctl default-agent >/dev/null 2>&1
ok "accendibile, scopribile e agganciabile"

echo
echo "${BLD}Fatto.${RST} Ora:"
echo "  1. Sul telefono: ${BLD}Impostazioni → Bluetooth${RST} → cerca il PC"
echo "     ($(hostname)) e abbinalo. Accetta le richieste su entrambi."
echo "  2. Nelle impostazioni del telefono, sul dispositivo appaiato:"
echo "     attiva ${BLD}Audio per chiamate${RST} / ${BLD}Contatti${RST}."
echo "  3. Quando arriva una chiamata, scegli il PC come dispositivo audio"
echo "     oppure premi ${BLD}Rispondi${RST} in Nx Caller."
echo
echo "  Se l'audio non passa, verifica con:   pactl list short sources"
echo "  (deve comparire una sorgente hfp/handsfree)"
