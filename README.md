# Alveare — rete di calcolo distribuito (V1)

Una rete di PC privati che, quando non sono usati, elaborano insieme testi e tabelle.
Chi presta il PC guadagna **miele** (crediti interni) e lo spende per far elaborare i propri testi.
Il server conserva i risultati elaborati e li **riusa**: un lavoro già fatto non si rifà.

Nome provvisorio: **Alveare** (si cambia in `orchestratore/web/index.html` e `nodo-ape/ui/index.html`).

**Per provare tutto subito su un solo computer leggi `GUIDA-TEST.md`.**

## Cosa fa la V1

| Parte | Cosa fa |
| --- | --- |
| **Orchestratore** (`orchestratore/`) | Divide i lavori in cellette, le cifra, le assegna con prestito a tempo, le ricompone, verifica a campione, riusa i risultati, gestisce il libro del miele, la memoria centrale e la ricerca |
| **Ape / nodo** (`nodo-ape/`) | Decide da sola quando lavorare (orari, intensità, batteria, uso del PC), esegue le cellette in una sandbox WebAssembly, consegna risultati sigillati per il server. Pannello locale su `http://127.0.0.1:7777` |
| **Moduli** (`moduli-sorgenti/` → `orchestratore/modules/`) | Pulizia testi, rimozione dati personali, statistiche, mappa dei significati (embedding). WebAssembly senza accesso a file, rete o schermo, firmati Ed25519 |
| **Pannello web** (`orchestratore/web/`) | Iscrizione, livelli e medaglie, api collegate, invio lavori con pulizia dei dati personali nel browser, prova del nove, ricerca, miele, gruppi di amici |

## Requisiti

- **Server:** Python 3.10 o più recente (le dipendenze vanno in un ambiente isolato `.venv` nella cartella del progetto).
- **Ape:** Node.js 20 o più recente. Nessuna libreria da installare.
- **Ricompilare i moduli** (facoltativo, sono già compilati): `clang` con supporto `wasm32`.

## Avvio rapido

Dalla cartella principale del progetto:

```sh
sh scripts/installa-server.sh      # una volta: crea .venv e installa le dipendenze
sh scripts/avvia-server.sh         # avvia il server su http://0.0.0.0:8000
```

Apri il pannello web, iscriviti, poi su ogni PC che deve lavorare (cartella `nodo-ape`):

```sh
node alveare-node.js setup --server https://indirizzo-del-server --user tuo_nome
node alveare-node.js start
```

e apri `http://127.0.0.1:7777` per scegliere orari e intensità.

## Collaudo

```sh
cd nodo-ape && node --test test/modules.test.js                              # moduli e crittografia
cd ../orchestratore && ../.venv/bin/python -m unittest discover -s tests -v  # rete completa (serve Node.js)
```

Il collaudo completo avvia server e nodi veri e verifica:
1. 10.000 testi con un nodo spento a metà: nessuna perdita, risultato **identico** al calcolo su un solo PC, originali cancellati, libro del miele a somma zero; lo stesso lavoro inviato di nuovo viene servito dalla memoria senza distribuire nulla.
2. Consegne duplicate, reinvii, sigilli manomessi e prestiti scaduti: rifiutati, niente doppi pagamenti.
3. Un nodo che bara viene scoperto dalla verifica incrociata e penalizzato; nessun nodo onesto penalizzato.
4. Prova del nove: il richiedente segnala un errore, due api lo confermano, il risultato viene corretto e il colpevole penalizzato; una segnalazione infondata non penalizza nessuno.
5. La copia Python della mappa dei significati (usata per la ricerca) coincide con il modulo WebAssembly.
6. Abusi bloccati: un utente non può scollegare l'ape di un altro, non può contestare con un originale inventato, non vede i testi altrui nella ricerca; richieste enormi e tentativi di accesso ripetuti vengono respinti.

Il collaudo completo dura circa 1-2 minuti.

## Impostazioni del server

Variabili d'ambiente con prefisso `ALVEARE_` (vedi `orchestratore/alveare_server/config.py`). Le principali:

| Variabile | Predefinito | Significato |
| --- | --- | --- |
| `ALVEARE_DATA_DIR` | `./data` | Database e chiavi del server (da salvare nei backup) |
| `ALVEARE_CHUNK_MAX_BYTES` | 65536 | Dimensione massima di una celletta |
| `ALVEARE_LEASE_MIN_S` / `ALVEARE_LEASE_MAX_S` | 30 / 1800 | Limiti del prestito a tempo |
| `ALVEARE_VERIFY_P_NEW` / `_NORMAL` / `_RELIABLE` | 0.30 / 0.10 / 0.05 | Probabilità di verifica incrociata |
| `ALVEARE_WELCOME_MIELE` | 100 | Miele di benvenuto |
| `ALVEARE_DAILY_CAP` / `ALVEARE_DAILY_CAP_NEW` | 2000 / 200 | Tetto giornaliero di miele per utente |
| `ALVEARE_STAGING_TTL_S` | 604800 | Tempo massimo di conservazione degli originali (7 giorni) |
| `ALVEARE_RATE_AUTH` / `_NODE` / `_API` | 10 / 1200 / 300 | Richieste al minuto: accessi per IP, protocollo per ape, pannello per utente |

**Importante:** la cartella `data/keys` contiene le chiavi del server. Se si perde, tutte le api vanno configurate di nuovo.
In produzione il server va messo dietro HTTPS (per esempio con Caddy, gratuito, che ottiene il certificato da solo).

## Struttura

```
GUIDA-TEST.md       come provare tutto (prima fase di test)
esempi/             testi di esempio
scripts/            installa-server.sh, avvia-server.sh, prova_completa.py
moduli-sorgenti/    sorgenti C dei moduli + build.sh
orchestratore/
  alveare_server/   hive.py (orchestratore), app.py (API), db.py, crypto.py, ledger.py, auth.py, modules.py, embed_py.py
  modules/          moduli .wasm compilati
  web/              pannello web
  tests/            collaudo completo
nodo-ape/
  alveare-node.js   comandi setup / start / status
  lib/              bee.js (logica dell'ape), sandbox, crittografia, monitoraggio locale, pannello locale
  ui/               pannello locale dell'ape
  tools/            run-module.js
  test/             test dei moduli + nodo imbroglione per il collaudo
docs/ROADMAP.md     differenze dalla specifica e passi successivi
```
