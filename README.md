# Nx Caller

**Nx sta per Nexiquar.**

Chiama, invia SMS, rispondi alle chiamate e guarda lo schermo del telefono
**dal PC**, usando il telefono che hai già in tasca.

Nx Caller è un'app per Ubuntu/GNOME scritta in Python con **GTK4 +
libadwaita**, con l'interfaccia **Liquid Glass**. Non usa alcun server:
tutto resta in locale.

---

## Cosa fa

| Funzione | Come | Requisiti sul telefono |
|---|---|---|
| **Invio SMS** | KDE Connect (GSConnect) → azione D-Bus `sendSms` | App KDE Connect + plugin SMS |
| **IP del telefono in automatico** | letto da KDE Connect (mDNS `_kdeconnect._udp`) | app KDE Connect collegata |
| **Chiamare / rispondere / chiudere** | `adb shell am start/accept/decline` | Android 11+ con **Debug wireless** |
| **Registro, contatti, SMS in arrivo** | `adb shell content query` | idem |
| **Audio della chiamata dalle casse del PC** | profilo Bluetooth **HFP** (BlueZ + oFono + PipeWire) | Bluetooth attivo |
| **Specchio dello schermo** | scrcpy ottimizzato: h264, 60 fps, 8 Mbit/s | Debug wireless |

> KDE Connect **non** supporta le chiamate: per quello serve ADB wireless —
> ma è proprio KDE Connect a fornire l'IP, quindi non scrivi mai indirizzi.

---

## Installazione

```bash
git clone https://github.com/<USERNAME>/nx-caller.git
cd nx-caller
./install.sh
```

Lo script fa **tutto da solo**:

1. installa le dipendenze che mancano (`adb`, `scrcpy`, `avahi-utils`,
   componenti GTK4) — chiede la password di root **una volta sola**;
2. copia il programma in `~/.local/share/nxcaller`;
3. crea il comando `nx-caller`;
4. crea la voce di menu con icona;
5. **mette l'icona sul Desktop**.

Punto da'icona sul Desktop o dal menu delle app, oppure `nx-caller` da
terminale. Per provare senza installare: `./run.sh`.

Per disinstallare: `./install.sh --uninstall`.

### Installazione automatica delle dipendenze

Anche se installi solo con `./run.sh` (senza `install.sh`), al primo avvio
l'app **installa da sola** ciò che manca, con una richiesta di password
tramite PolicyKit. Niente terminale, niente `sudo apt install` manuale.

Le dipendenze sono: `python3-gi`, `gir1.2-gtk-4.0`, `gir1.2-adw-1`,
`adb`, `scrcpy`, `avahi-utils`. Se preferisci che l'app chieda prima di
installare (invece di farlo subito), disattiva «installazione automatica»
dal file `~/.config/nxcaller/config.json` (`"auto_install_deps": false`).

---

## Configurazione (una tantum)

### 1. KDE Connect / GSConnect — SMS e IP automatico

1. Sul telefono installa **KDE Connect** (F-Droid e Play Store).
2. Su Ubuntu apri **Estensioni** → attiva **Connetti KDE (GSConnect)**.
3. Abbina il telefono e attiva il plugin **«Invio messaggio SMS»**.

Da questo momento l'IP del telefono è conosciuto dal PC: Nx Caller lo usa
per connettere ADB senza scrivere nulla (vedi sotto).

### 2. ADB wireless — chiamate, registro e contatti

Sul telefono: **Impostazioni → Informazioni sul telefono → «Versione
build»** → tocca 7 volte → **Opzioni sviluppatore** → attiva **«Debug
wireless»** → **«Abbina dispositivo con codice di associazione»**.

Poi su Nx Caller, **Impostazioni → Accesso al telefono**:

* **Usa IP di KDE Connect**: l'IP arriva da KDE Connect e la porta la
  trova adb da solo (mDNS). Se il telefono non è ancora accoppiato, ti
  prepara l'indirizzo di pairing: inserisci il **codice a 6 cifre** e
  premi **Abbina**.
* **Scopri / Connetti**: il metodo manuale, se preferisci.
* **Prepara il telefono**: concede ad `adb` i permessi di lettura SMS,
  contatti e registro.

Con **«Usa l'IP di KDE Connect in automatico»** attivo (default), all'avvio
l'app si collega da sola al telefono.

> L'IP di KDE Connect funziona anche se il firewall blocca il mDNS di
> Android: la connessione KDE Connect è già aperta, e da lì si legge l'IP.

### 3. Audio della chiamata via Bluetooth (HFP)

Vuoi parlare col microfono del PC e sentire dalle casse? Nx Caller può
trasformare il computer in un **auricolare Bluetooth** del telefono.

1. **Impostazioni → Chiamate via Bluetooth (HFP) → Configura audio
   Bluetooth** (serve la password di root una volta): installa `ofono` +
   `ofono-phonesim` e abilita HSP/HFP in BlueZ.
2. Abbina il telefono al PC da **Impostazioni Bluetooth di GNOME** e attiva
   **«Audio per chiamate»** sul dispositivo appaiato.
3. Quando arriva una chiamata, scegli il PC come audio oppure premi
   **Rispondi** in Nx Caller.

Lo stato (adattatore, ofono, audio HFP) è mostrato in tempo reale nella
stessa sezione. Se l'audio non parte, un riavvio del PC Completa il
configurazione.

### 4. Specchio dello schermo (ottimizzato)

Il pulsante 🖥️ nell'header avvia **scrcpy** con il preset fluido:

* codec **h264** (il più veloce), **60 fps**, **8 Mbit/s**;
* renderer **OpenGL** hardware;
* audio del telefono inoltrato sul PC;
* schermo del telefono **spento** e telefono **sveglio** mentre lo guardi.

Bitrate (fino a 24 Mbit/s), frame rate (fino a 120), codec (h265/av1) e
audio si regolano da **Impostazioni → Schermo del telefono**.

### 5. Liquid Glass

L'interfaccia usa il tema **Liquid Glass**: pannelli traslucidi su fondo
aurora, luce di bordo, ombre morbide.

Per la **rifrazione reale** (lo stesso effetto dell'estensione Liquid Glass
per GNOME, dentro la finestra dell'app) installa la libreria
[glass-lib](https://github.com/ryohsuke1231/glass-lib) dell'autore di
quell'estensione:

```bash
./install-glass.sh          # compila da sorgente, serve GNOME 50+
```

Nx Caller la rileva da sola all'avvio e usa il vetro rifrangente per barre
e pannelli. Senza la libreria l'app resta identica, con il tema CSS.

---

## Risoluzione dei problemi

| Sintomo | Causa probabile | Soluzione |
|---|---|---|
| Banner «GSConnect non è in esecuzione» | Estensione disattiva | Attiva **Connetti KDE** da Estensioni di GNOME |
| «Il plugin SMS non è attivo» | Plugin SMS spento | Impostazioni GSConnect → dispositivo → plugin **SMS** |
| «Telefono non trovato in rete» | KDE Connect non collegato | Controlla che il telefono appaia in GSConnect |
| Connessione ADB fallita | Debug wireless spento | Riapri **Debug wireless** sul telefono e riprova |
| «Telefono da autorizzare» | Accoppiamento mancante | Inserisci il codice a 6 cifre e premi **Abbina** |
| «Non autorizzato» | Il telefono ha negato il debug | Conferma «Consenti debug USB?» sullo schermo |
| Registro/contatti vuoti | Permessi non concessi | Premi **Prepara il telefono** in Impostazioni |
| Audio chiamata non passa dal PC | HFP non configurato o telefono non appaiato | **Configura audio Bluetooth** + attiva «Audio per chiamate» |
| Schermo scuro / niente specchio | scrcpy non installato | Si installa da solo; oppure `sudo apt install scrcpy` |

---

## Scorciatoie

| Tasto | Effetto |
|---|---|
| `Invio` | Chiama (pagina Chiamate) o invia il messaggio (pagina SMS) |
| `Maiusc + Invio` | A capo nel compositore |
| `Ctrl + Q` | Esci |

---

## Struttura del progetto

```
nxcaller/
├── main.py                 App, CSS, notifiche, azioni D-Bus, avvio auto
├── window.py               Finestra + selettore pagine + specchio scrcpy
├── pages.py                Chiamate · SMS · Impostazioni
├── widgets.py              Avatar, bolle, righe elenco, tastierino
├── models.py               Dati e parsing dell'output di adb
├── config.py               Persistenza delle impostazioni
├── glass.py                Integrazione opzionale con glass-lib
├── style.css               Tema Liquid Glass (chiaro e scuro)
└── backends/
    ├── kdeconnect.py       Dispositivi, SMS via D-Bus, IP via mDNS
    ├── adb.py              Chiamate, registro, contatti, SMS, pairing
    ├── audio.py            Audio Bluetooth HFP (chiamate dal PC)
    └── scrcpy.py           Specchio dello schermo ottimizzato
install.sh                  Installazione completa (menu + Desktop)
install-glass.sh            Compila glass-lib (Liquid Glass reale)
setup-hfp.sh                Trasforma il PC in auricolare Bluetooth
run.sh                      Avvio con installazione automatica dipendenze
```

---

## Sviluppo

```bash
./run.sh            # avvia dai sorgenti
```

Il codice è in `nxcaller/`: modifichi, rilanci, vedi.

---

## Licenza e crediti

**GPL-3.0** — vedi [LICENSE](LICENSE).

* «Nx» sta per **Nexiquar** — autore e nome del progetto.
* [scrcpy](https://github.com/Genymobile/scrcpy) — specchio dello schermo.
* [GSConnect](https://github.com/GSConnect/gsconnect) — KDE Connect su GNOME.
* [glass-lib](https://github.com/ryohsuke1231/glass-lib) e
  [Liquid Glass](https://github.com/ryohsuke1231/liquid-glass) di
  ryohsuke1231 — l'ispirazione e la libreria del vetro (MIT).
