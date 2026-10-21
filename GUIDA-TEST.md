# Guida alla prima fase di test

## Le sezioni del progetto

| Cartella | Cosa contiene |
| --- | --- |
| `orchestratore/` | Il server centrale: divide i lavori, distribuisce le cellette, ricompone, verifica, memoria centrale, miele, pannello web |
| `nodo-ape/` | Il programma da installare su ogni PC che presta potenza, con il suo pannello locale |
| `moduli-sorgenti/` | I sorgenti dei moduli di calcolo WebAssembly (già compilati in `orchestratore/modules/`) |
| `scripts/` | Installazione, avvio del server, avvio della prova completa |
| `esempi/` | 2.000 recensioni di esempio (con doppioni, maiuscole, spazi e alcuni dati personali da togliere) |
| `docs/ROADMAP.md` | Differenze dalla specifica e passi successivi |

## 1. Prova completa su un solo computer

Dalla cartella principale del progetto:

```sh
sh scripts/installa-server.sh
.venv/bin/python scripts/prova_completa.py --esempio
```

Si avviano l'orchestratore, 2 api di prova e un lavoro di esempio. Poi apri:

- **Pannello web:** `http://localhost:8000`, utente `prova`, password `provaprova`
- **Pannelli delle api:** `http://127.0.0.1:7771` e `http://127.0.0.1:7772`

Con `--api 3` (fino a 5) avvii più api. Ctrl+C ferma tutto. Per ricominciare da zero si cancella la cartella `data-prova`.

## 2. Cosa provare

- [ ] **Iscrizione:** crea un nuovo utente dal pannello web e controlla i 100 miele di benvenuto.
- [ ] **Lavoro:** in "Chiedi un lavoro" carica `esempi/recensioni-esempio.txt`. Prima dell'invio deve comparire quanti dati personali sono stati tolti.
- [ ] **Avanzamento:** la barra delle cellette cresce fino a "Pronto".
- [ ] **Risultati:** scarica il file e controlla testi puliti, doppioni segnalati e dati personali sostituiti.
- [ ] **Prova del nove:** deve risultare "20 su 20 corretti".
- [ ] **Riuso:** invia di nuovo lo stesso file; deve costare molto meno e mostrare "riusati risultati già presenti".
- [ ] **Ricerca:** in "Cerca" prova per esempio "lavatrice rumorosa".
- [ ] **Pannello dell'ape:** pausa e ripresa (la pausa deve scattare entro 2 secondi), intensità, orari colorati, costo stimato della corrente.
- [ ] **Ape spenta:** durante un lavoro grande ferma un'ape; il lavoro deve finire lo stesso con le altre.
- [ ] **Gruppo di amici:** crea un gruppo, entra con un altro utente usando il codice, guarda la classifica.
- [ ] **Medaglie e livello:** dopo il lavoro delle api, accedi come `proprietario1` e controlla miele, livello e medaglie.

## 3. Prova con PC veri

1. Avvia il server su una macchina raggiungibile: `sh scripts/avvia-server.sh`. In un Codespace rendi pubblica la porta 8000 dalla scheda "Porte".
2. Su ogni PC da collegare copia la cartella `nodo-ape` (serve Node.js 20 o più recente), poi:
   ```sh
   node alveare-node.js setup --server https://indirizzo-del-server --user nome_utente
   node alveare-node.js start
   ```
3. Su ogni PC apri `http://127.0.0.1:7777` e scegli orari e intensità.

Il Codespace va bene per i test, ma si spegne quando resta inattivo: per una rete sempre accesa serve un server stabile.

## 4. Collaudo automatico

```sh
cd nodo-ape && node --test test/modules.test.js
cd ../orchestratore && ../.venv/bin/python -m unittest discover -s tests -v
```
