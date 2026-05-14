# pf_manager — Specifica Tecnica

Documento prescrittivo, autosufficiente per rigenerare l'applicativo da zero. Linguaggio target: **Python 3 stdlib + `psutil`**. Sistema target primario: **Windows 10/11** (Linux/macOS supportati con fallback).

---

## 1. Scopo

GUI desktop per gestire `kubectl port-forward` verso microservizi in un cluster Kubernetes. L'utente deve poter avviare, fermare, modificare, eliminare e ispezionare port-forward configurati, con i comandi `kubectl` eseguiti in background (nessuna console visibile) e l'output dirottato su file di log per riga.

---

## 2. Requisiti funzionali

### 2.1 Lista port-forward

- Una finestra principale mostra una **tabella di port-forward**, una riga per microservizio.
- Ogni riga espone in ordine da sinistra a destra:
  1. **Indicatore ON/OFF** colorato: testo `ON` verde se il port-forward è attivo, `OFF` rosso se non attivo.
  2. **Nome microservizio** (etichetta utente).
  3. **Porta**: stringa `<local>:<remote>`.
  4. **Namespace** kubernetes.
  5. **Stato testuale**: `Attivo (PID <pid>[,<pid>…])` o `Non attivo`.
  6. Pulsante **"Avvia portforwarding"**.
  7. Pulsante **"Chiudi portforwarding"**.
  8. Pulsante **"Modifica"**.
  9. Pulsante **"Log"**.
  10. Pulsante **"Elimina"**.

### 2.2 Toolbar globale

- Pulsante **"Aggiungi"**: apre un dialog per creare un nuovo port-forward.
- Pulsante **"Aggiorna stato"**: forza un refresh manuale.
- Pulsante **"Chiudi tutti"**: termina tutti i port-forward attivi (con conferma).

### 2.3 Comportamento pulsanti per riga

| Stato port-forward | Avvia | Chiudi | Modifica | Log | Elimina |
|--------------------|-------|--------|----------|-----|---------|
| Attivo (ON)        | disabled | enabled | disabled | enabled | disabled |
| Non attivo (OFF)   | enabled | disabled | enabled | enabled | enabled |

### 2.4 Avvio port-forward

- Lancia `kubectl port-forward <type>/<name> <local>:<remote> -n <ns>` come processo in **background, senza finestra console**.
- `stdout` e `stderr` di kubectl vanno scritti in un file di log dedicato alla riga: `logs/<sanitized_name>_<local_port>.log` (sovrascritto a ogni avvio).
- Dopo ~800 ms verifica se kubectl è ancora vivo. Se no, mostra un popup con il contenuto del log.

### 2.5 Chiusura port-forward

- "Chiudi portforwarding" deve **trovare i processi kubectl di sistema** corrispondenti alla riga e terminarli (`terminate()` + fallback `kill()` dopo 3 s di timeout).
- Il riconoscimento avviene per **matching della command line**: il processo è considerato pertinente alla riga se la sua `cmdline` contiene tutti questi token (case-insensitive):
  - `kubectl` e `port-forward`
  - `<resource_type>/<resource_name>` **oppure** equivalente `svc/` ↔ `service/`
  - `<local>:<remote>` (port spec esatta)
  - `<namespace>`
- L'ordine degli argomenti non conta.

### 2.6 Aggiunta / Modifica / Eliminazione

- **Aggiungi** apre un dialog con i campi: `name`, `resource_type` (combobox: `svc`/`service`/`pod`/`deployment`/`statefulset`), `resource_name`, `namespace`, `local_port`, `remote_port`. Tutti obbligatori. Le porte devono essere interi.
- **Modifica** apre lo stesso dialog popolato con i valori correnti. **Abilitato solo se il port-forward non è attivo**.
- **Elimina** chiede conferma e rimuove la configurazione. **Abilitato solo se il port-forward non è attivo**.
- Ogni modifica persiste subito sul file JSON.

### 2.7 Visualizzazione log

- Il pulsante **"Log"** apre una `Toplevel` dedicata che mostra il contenuto del file `logs/<name>_<port>.log`.
- La finestra ha sfondo scuro stile terminale (`#1e1e1e` fg `#dcdcdc`, font monospace).
- Si **auto-aggiorna ogni 1 secondo** rileggendo il file (skip se la dimensione non è cambiata).
- Mantiene l'autoscroll in fondo se l'utente non ha scrollato.
- Pulsanti **Aggiorna** (forza refresh) e **Pulisci** (tronca il log).
- Il pulsante Log è **sempre abilitato**, anche quando il port-forward è inattivo (utile per consultare errori passati).

### 2.8 Persistenza configurazione

- File JSON `portforward_config.json` accanto allo script.
- Letto all'avvio; se assente, viene creato con i default (vedi §6.1).
- Riscritto a ogni Aggiungi/Modifica/Elimina.
- Schema di una voce:
  ```json
  {
    "name": "<string>",
    "resource_type": "<string>",
    "resource_name": "<string>",
    "namespace": "<string>",
    "local_port": "<string>",
    "remote_port": "<string>"
  }
  ```
  Le porte sono **stringhe** (semplifica I/O da widget).

### 2.9 Rilevamento orfani all'avvio

- All'avvio, ~100 ms dopo la costruzione della GUI, scansionare i processi di sistema per port-forward kubectl che combaciano con voci in configurazione (cioè avviati da un'esecuzione precedente terminata male).
- Se trovati, mostrare un dialog yes/no che elenca i match (nome, port spec, namespace, PID) e offre di terminarli o lasciarli vivi.

### 2.10 Refresh periodico

- Ogni **2 secondi** ricalcolare lo stato ON/OFF di tutte le righe interrogando psutil.
- Aggiornare indicatore + stato testuale + abilitazione pulsanti.

---

## 3. Struttura progetto

```
pfutils/
  pf_manager.py              # applicativo principale (un singolo file)
  portforward_config.json    # generato al primo avvio
  logs/                      # creata on-demand, un .log per port-forward
  run.bat                    # launcher Windows che usa il venv
  venv/                      # virtualenv con psutil
  _test_pf_manager.py        # suite di test
  SPEC.md                    # questo documento
```

---

## 4. Specifica delle classi

Tutto in `pf_manager.py`. Quattro classi + `main()`. Costanti modulo:

```python
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "portforward_config.json")
LOG_DIR = os.path.join(SCRIPT_DIR, "logs")
REFRESH_INTERVAL_MS = 2000
HAS_PSUTIL = True/False     # tentato import di psutil
```

### 4.1 `PortForward`

Modello dati per una singola configurazione di port-forward.

**Costruttore**: `__init__(self, name, resource_type, resource_name, namespace, local_port, remote_port)` — `local_port` e `remote_port` vengono coerciti a `str`.

**Metodi**:

- `to_dict() -> dict`: serializzazione per JSON (6 campi, vedi §2.8).
- `from_dict(d: dict) -> PortForward` (classmethod): deserializzazione tollerante (default `resource_type="svc"`, `namespace="default"`).
- `kubectl_args() -> list[str]`: ritorna gli argomenti per `subprocess.Popen` nell'ordine:
  ```python
  ["kubectl", "port-forward",
   f"{resource_type}/{resource_name}",
   f"{local_port}:{remote_port}",
   "-n", namespace]
  ```
- `label_str() -> str`: ritorna `f"PF-MANAGER:{name}@{local_port}"`. Identificatore univoco usato come fallback / legacy match.
- `_legacy_match(cmdline_list) -> bool`: vedi §2.5. Lower-casa, verifica presenza di tutti i token richiesti.
- `find_processes() -> list[psutil.Process]`: scansiona tutti i processi via `psutil.process_iter(['pid','name','cmdline'])`; include processi la cui cmdline contiene la `label_str()` **oppure** combacia con `_legacy_match()`. Per i match per label, raccoglie anche i figli `kubectl` (per compatibilità con versioni che lanciavano via wrapper `cmd.exe`). Deduplica per PID. Gestisce `NoSuchProcess`/`AccessDenied`/`ZombieProcess`.
- `kubectl_processes() -> list[psutil.Process]`: filtra `find_processes()` ai soli processi il cui nome contiene `kubectl`. **È questo il metodo da usare per decidere lo stato ON/OFF.**

### 4.2 `LogViewer`

`Toplevel` per visualizzare un file di log con auto-refresh.

**Costruttore**: `__init__(self, parent, title, log_path)`. Crea la finestra (900×500), una toolbar con "Aggiorna" e "Pulisci", un `tk.Text` scuro non-editable con scrollbar.

**Metodi**:

- `_refresh()`: legge il file; se la dimensione è invariata, salta. Se il file non esiste, mostra un messaggio placeholder. Preserva l'autoscroll-in-fondo se l'utente era in fondo.
- `_tick()`: chiamato ogni `REFRESH_MS = 1000` ms via `after`. Si autoplanifica.
- `_clear()`: tronca il log a 0 byte.
- `_on_close()`: cancella il timer e distrugge la finestra. Bound su `WM_DELETE_WINDOW`.

### 4.3 `PortForwardDialog`

`Toplevel` modale per Aggiungi/Modifica.

**Costruttore**: `__init__(self, parent, title, initial=None)`. Crea i 6 campi (vedi §2.6). `resource_type` è una `Combobox` readonly.

**Stato**: l'attributo `result` è `None` finché l'utente non clicca **OK**; in caso di OK, `result` è un dict con i 6 campi (validati: nessun vuoto, porte intere).

Il chiamante usa `parent.wait_window(dlg.top)` e poi controlla `dlg.result`.

### 4.4 `PortForwardManagerApp`

Applicazione principale.

**Costante di classe**:
```python
DEFAULT_ENTRIES = [
    ("documents",     "newrecap-documents",     8081),
    ("etb-cout",      "newrecap-etb-cout",      8082),
    ("etb-longterm",  "newrecap-etb-longterm",  8083),
    ("etb-spot",      "newrecap-etb-spot",      8084),
    ("frontend",      "newrecap-frontend",      8085),
    ("identity",      "newrecap-identity",      8086),
    ("integrations",  "newrecap-integrations",  8087),
    ("lng-cin",       "newrecap-lng-cin",       8088),
    ("lng-cout",      "newrecap-lng-cout",      8089),
    ("notifications", "newrecap-notifications", 8090),
    ("registry",      "newrecap-registry",      8091),
]
DEFAULT_NAMESPACE = "sd-newrecap"
```
Tutte le voci di default hanno `resource_type="svc"`, `remote_port=80`.

**Costruttore**: `__init__(self, root)` esegue in ordine:
1. `_load_config()` (crea default e salva se file assente).
2. `_build_ui()`.
3. `root.after(100, self._handle_orphans_on_startup)`.
4. `_schedule_refresh()`.

**Metodi principali**:

- `_load_config()` / `_save_config()`: I/O del JSON. In caso di errore di lettura, `messagebox.showerror` e lista vuota.
- `_build_ui()`: toolbar + header + canvas scrollabile contenente `rows_frame`.
- `_render_rows()`: distrugge i widget esistenti e richiama `_render_row` per ogni entry.
- `_render_row(idx, entry)`: layout in `grid` su una colonna per cella, indici 0..9 (vedi §2.1). L'indicatore ON/OFF deve essere `tk.Label` (non `ttk.Label`, perché su Windows il foreground dei `ttk.Label` non è sempre rispettato dal tema nativo).
- `_schedule_refresh()`: chiama `_refresh_status()` e si autoplanifica con `root.after(REFRESH_INTERVAL_MS, ...)`.
- `_refresh_status()`: per ogni entry, usa `entry.kubectl_processes()`. Se non vuoto: `state="ON"`, fg verde, `status="Attivo (PID …)"`, disabilita Avvia/Modifica/Elimina, abilita Chiudi. Altrimenti viceversa.
- `_handle_orphans_on_startup()`: se `HAS_PSUTIL` e ci sono match, mostra `askyesno` con la lista; se Sì, chiama `_terminate_processes(...)`.
- `_terminate_processes(procs)`: per ognuno: `terminate()`; `wait(timeout=3)`; fallback `kill()` su `TimeoutExpired`; raccoglie e mostra eventuali errori.
- `_log_path_for(entry) -> str`: crea `LOG_DIR` se manca; ritorna `logs/<sanitized>_<local_port>.log`, dove `sanitized` rimpiazza i caratteri non `isalnum()`/`-_.` con `_`.
- `_on_add()` / `_on_edit(idx)` / `_on_delete(idx)`: aprono dialog/conferma e salvano la config.
- `_on_show_log(idx)`: crea un `LogViewer` per la riga `idx`.
- `_on_start(idx)`:
  1. Se `kubectl_processes()` non vuoto, mostra info "già attivo" e return.
  2. Termina eventuali processi residui non-kubectl trovati da `find_processes()` (cleanup wrapper di versioni precedenti).
  3. Apre `log_file = open(self._log_path_for(entry), "wb")`.
  4. Lancia `subprocess.Popen(entry.kubectl_args(), stdin=DEVNULL, stdout=log_file, stderr=STDOUT, creationflags=CREATE_NO_WINDOW)` su Windows; su Unix usa `start_new_session=True` invece di `creationflags`.
  5. Chiude `log_file` lato GUI in `finally` (l'handle è duplicato nel figlio).
  6. Schedula `root.after(800, self._post_start_check, idx, log_path)`.
- `_post_start_check(idx, log_path)`: refresh e, se `kubectl_processes()` è ancora vuoto, popup `showwarning` col contenuto del log.
- `_on_stop(idx)` / `_on_stop_all()`: trovano i processi via `find_processes()` (non solo `kubectl_processes`, per chiudere anche eventuali wrapper) e li terminano.

### 4.5 `main()`

```python
def main():
    root = tk.Tk()
    try:
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
    except Exception:
        pass
    PortForwardManagerApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
```

---

## 5. Gotcha rilevanti (errori da non rifare)

1. **Non usare `rem` come marker in cmd.exe**: `rem` commenta tutto il resto della riga, **inclusi gli operatori `&&` e i comandi successivi**. `cmd /c "title X && rem X && kubectl ..."` esegue solo `title` (e poi `rem` mangia il resto). Se serve un marker nella cmdline di cmd.exe, usare il `title`: la stringa del titolo finisce comunque nella cmdline del processo `cmd.exe`.

2. **Backslash in path su Bash su Windows**: i comandi tipo `python -m venv c:\Users\foo\venv` lanciati via shell Bash di Git for Windows interpretano `\U`, `\D`, `\f` come escape e creano una directory con nome corrotto. Sempre usare PowerShell o quotare i path nella Bash con apici singoli.

3. **`ttk.Label` foreground su Windows**: su alcuni temi nativi, `ttk.Label.config(foreground=...)` viene ignorato. L'indicatore ON/OFF colorato deve usare `tk.Label`, non `ttk.Label`.

4. **`subprocess.CREATE_NO_WINDOW` vs `DETACHED_PROCESS`**: mutuamente esclusivi su Windows. Per nascondere la console, basta `CREATE_NO_WINDOW`.

5. **Handle del file di log**: passandolo come `stdout=` a `Popen`, l'OS duplica il descrittore nel figlio. Lato genitore va chiuso (in `finally`) per evitare leak; il figlio mantiene la sua copia.

6. **`psutil.Process.environ()` su Windows**: può sollevare `AccessDenied` su processi di altra sessione. Non affidarsi a variabili d'ambiente come marker — preferire matching su cmdline.

7. **`pythonw.exe` nel venv crea 2 processi**: il launcher del venv su Windows spawna sé stesso + l'interprete reale. Entrambi mostrano la stessa cmdline `pythonw.exe pf_manager.py`. Considerare normale.

---

## 6. Default e onboarding

### 6.1 Voci di default

Al primo avvio, se `portforward_config.json` non esiste, lo script lo crea con le 11 voci di `DEFAULT_ENTRIES`, tutte:
- `resource_type = "svc"`
- `resource_name = "newrecap-<nome>"`
- `namespace = "sd-newrecap"`
- `local_port` come da tabella (8081…8091)
- `remote_port = "80"`

Esempio comando lanciato per `registry`:
```
kubectl port-forward svc/newrecap-registry 8091:80 -n sd-newrecap
```

### 6.2 Launcher `run.bat`

Trova il venv accanto al file (`%~dp0venv\Scripts\pythonw.exe`, fallback a `python.exe`), lancia lo script in modalità detached con `start ""`. Se il venv non esiste, stampa istruzioni di setup e `pause`.

### 6.3 Setup venv (manuale, una tantum)

```bat
python -m venv venv
venv\Scripts\python.exe -m pip install psutil
```

---

## 7. Test

Suite eseguibile in `_test_pf_manager.py`. **26 test** (modello di check semplice: print di `[OK]`/`[FAIL]`, `sys.exit(1)` se almeno uno fallisce). Coprono:

1. `kubectl_args()` ordine argomenti coincide con esempio utente.
2. `label_str()` formato.
3. `_legacy_match`: 8 casi (match positivi, riordino argomenti, equivalenza svc/service, e 4 non-match: porta, servizio, namespace diversi, cmdline non-kubectl, cmdline vuota).
4. Riconoscimento di label in cmdline simulata.
5. JSON roundtrip `to_dict` / `from_dict`.
6. Default config: 11 voci con nomi e porte attesi, namespace `sd-newrecap`.
7. `find_processes()` ritorna lista (numero variabile a runtime).
8. `CONFIG_FILE` punta accanto allo script.
9. `DEFAULT_ENTRIES` ha tipi `(str, str, int)`.
10. `LOG_DIR` punta accanto allo script.
11. Smoke test GUI con `root.withdraw()`:
    - App istanziata senza eccezioni
    - JSON creato al primo avvio con 11 voci, prima `documents`, ultima `registry`, namespace `sd-newrecap`
    - 11 righe renderizzate
    - Indicatore iniziale = "OFF", Avvia abilitato, Chiudi disabilitato, Modifica abilitato
    - `_log_path_for()` ritorna path `.log` dentro `logs/` con nome+porta

> **Nota**: il test 11 può fallire transitoriamente se al momento del run è in esecuzione un vero `kubectl port-forward` matchabile (es. perché l'utente lo ha avviato dalla GUI). Il run "a freddo" (nessun kubectl attivo) passa sempre.

---

## 8. Dipendenze

- Python ≥ 3.10 (testato su 3.13).
- `psutil` ≥ 5.x.
- `tkinter` (stdlib; incluso nell'installer ufficiale Python su Windows).
- `kubectl` nel `PATH`.

`psutil` è **opzionale** in import (`HAS_PSUTIL`). Se manca, la toolbar mostra un avviso rosso e tutte le funzioni che dipendono dal process scan diventano no-op (`find_processes` ritorna `[]`). Per supporto pieno installarlo.

---

## 9. Convenzioni di codice

- Stile PEP 8; nomi metodi snake_case; metodi privati con underscore.
- Italiano nei messaggi utente (titoli dialog, errori, label).
- Errori utente sempre via `tkinter.messagebox.show{error,warning,info}` (no `print`).
- Cattura di `psutil.NoSuchProcess`, `psutil.AccessDenied`, `psutil.ZombieProcess` ovunque si itera su `process_iter`/`children`.
- Geometria GUI fissa via `geometry("WxH")`. Nessun layout responsive.
