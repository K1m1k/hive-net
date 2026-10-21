# Differenze dalla specifica V1 e passi successivi

## Scelte diverse dalla specifica (e perché)

| Specifica | Questa versione | Motivo | Passo successivo |
| --- | --- | --- | --- |
| Client nodo in Rust | Client in Node.js | Nell'ambiente di sviluppo non si potevano scaricare le librerie Rust; Node ha WebAssembly e crittografia integrati, zero dipendenze | Port in Rust per un client più difficile da manomettere e un eseguibile unico |
| Embedding con modello neurale (multilingual-e5-small) | Embedding per hashing delle caratteristiche (256 dimensioni) | Nessun download di modelli possibile durante lo sviluppo | Nuovo modulo `embed_onnx` con modello quantizzato; stessa interfaccia, nuova versione dell'operazione |
| PostgreSQL + pgvector | SQLite (WAL) + ricerca vettoriale in memoria con numpy + FTS5 | Zero installazioni, adatto alla V1 | Migrazione a PostgreSQL + pgvector (`halfvec`) oltre circa 1 milione di testi o con più server |
| MessagePack | JSON (compresso gzip dal server) | Nessuna libreria esterna | MessagePack quando il traffico lo giustifica |
| Pannello web in Next.js | HTML + JavaScript senza librerie, servito dal server | Niente build e niente Node sul server | Next.js se il pannello cresce molto |

## Non ancora presenti

- Estrazione di entità e relazioni (mappa della conoscenza) e catalogo per argomenti automatico.
- Interfaccia per il ricorso contro le penalità (il registro delle prove esiste già nella tabella `penalties`).
- Protezione da iscrizioni automatiche in massa (per esempio un controllo anti-robot); il limite di frequenza c'è già.
- Limite di frequenza condiviso tra più server (oggi è in memoria, adatto a un solo server).
- Rilevamento dei nomi di persona nella pulizia dei dati personali (serve un modello IA).
- Temperatura del processore nel semaforo del nodo (non leggibile in modo uniforme tra i sistemi).
- Aggiornamento automatico e firma del client del nodo.
- V2: immagini, livelli di sensibilità con aree protette hardware e attestazione, pagamenti.

## Taratura da fare con dati reali

- Valore delle unità di lavoro (`bytes_per_unit` in `orchestratore/alveare_server/modules.py`): oggi stimato, da ricalibrare con benchmark su PC di riferimento.
- Consumo per core nel pannello del nodo (predefinito 10 W) e prezzo dell'elettricità (0,30 €/kWh): modificabili dall'utente.
- Probabilità di verifica e tetti giornalieri in base al comportamento reale della rete.

## Da dimostrare con i benchmark prima di ampliare

- Che distribuire convenga davvero rispetto a un solo PC: tempo totale, traffico di rete, consumo energetico, tempi di attesa, percentuale di risultati riusati.
- Le operazioni della V1 (pulizia, conteggi, embedding a hashing) sono leggere: il vantaggio della rete si vedrà soprattutto con operazioni pesanti (modelli neurali, V2-V3) e con il riuso dei risultati.

## Correzioni di sicurezza già fatte dopo la revisione

- Scollegare un'ape: si verifica prima la proprietà, poi si tocca qualsiasi cosa.
- Contestazioni: l'originale indicato deve avere la stessa impronta (SHA-256) di quello inviato con il lavoro.
- Ricerca: mostra solo i testi dei lavori dell'utente.
- Limite di dimensione delle richieste controllato prima e durante la lettura; limiti di frequenza su accesso, protocollo delle api e pannello.

## Da non dimenticare: la visione completa (prossime versioni)

Ciò che la V1 non copre ancora dell'idea originale:

- [ ] **Addestramento su richiesta**: ogni utente chiede l'addestramento della propria IA, anche su immagini (V2 immagini, V3 fine-tuning distribuito).
- [ ] **Il server come "IA suprema"**: la memoria centrale alimenta modelli addestrati dalla rete, non solo un archivio di risultati (V2 chatbot con memoria, V3-V4 modelli propri).
- [ ] **Utenti paganti**: chi non presta potenza paga e ottiene priorità e dati in base al pagamento.
- [ ] **Assegnazione ai nodi più stabili**: i pezzi vanno preferibilmente alle api più affidabili, non solo alla prima libera.
- [ ] **Uso della GPU** dei nodi che ce l'hanno.
- [ ] **Dati sensibili** gestiti solo da nodi con area protetta hardware e attestazione.
- [ ] **Risultati consegnati nel formato richiesto** dall'utente (oggi solo JSONL).
