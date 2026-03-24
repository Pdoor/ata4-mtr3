# -*- coding: utf-8 -*-
"""
Scanner Dataroom ATA4 MTR3 2026-2027
Gira su GitHub Actions, analizza ogni dataroom, classifica i documenti,
traccia la storia dei caricamenti, e produce dashboard.json.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
import zipfile
import tempfile
from datetime import datetime, timezone
from io import BytesIO

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException

def log_debug(msg):
    """Funzione per stampare log immediati su GitHub Actions"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {msg}")
    sys.stdout.flush()

# ═══════════════════════════════════════════════════════════
#  CONFIGURAZIONE SCADENZE
# ═══════════════════════════════════════════════════════════
# Modifica queste date quando vengono definite
SCADENZE = {
    "termine_arera_mtr3": "2026-07-31",    # Scadenza circolare ARERA MTR3
    "termine_ata_gestori": "2026-05-31",   # Scadenza ATA per i gestori
    "termine_ata_comuni": "2026-06-15",    # Scadenza ATA per i comuni
}

# ═══════════════════════════════════════════════════════════
#  CLASSIFICAZIONE DOCUMENTI
# ═══════════════════════════════════════════════════════════
# Pattern regex per classificare i file trovati nello zip.
# Ogni file viene confrontato con questi pattern (case-insensitive).
# Il primo match vince. "source" indica se è del gestore o del comune.

DOC_PATTERNS = [
    # GESTORE
    {"key": "tool_mtr3",       "source": "gestore", "patterns": [
        r"tool.*mtr", r"appendice.*1.*gest", r"app.*1.*gest",
        r"pef.*grezzo.*gest", r"mtr3?.*gest.*tool", r"gest.*tool",
        r"gest.*appendice.*1", r"gest.*app.*1",
    ]},
    {"key": "relazione",       "source": "gestore", "patterns": [
        r"relazione.*gest", r"gest.*relazione", r"appendice.*2.*gest",
        r"app.*2.*gest", r"gest.*appendice.*2", r"gest.*app.*2",
    ]},
    {"key": "dich_veridicita", "source": "gestore", "patterns": [
        r"dich.*veridic.*gest", r"gest.*dich.*veridic",
        r"appendice.*3.*gest", r"app.*3.*gest",
        r"gest.*appendice.*3", r"gest.*app.*3", r"veridicit.*gest",
    ]},
    {"key": "altre_com",       "source": "gestore", "patterns": [
        r"comunicazion.*gest", r"gest.*comunicazion", r"gest.*altr",
        r"format.*gest", r"gest.*format", r"dati.*gest",
    ]},
    # COMUNE
    {"key": "tool_mtr3_c",       "source": "comune", "patterns": [
        r"tool.*mtr.*comun", r"comun.*tool", r"appendice.*1.*comun",
        r"app.*1.*comun", r"comun.*appendice.*1", r"comun.*app.*1",
        r"pef.*grezzo.*comun", r"mtr3?.*comun.*tool",
    ]},
    {"key": "relazione_c",       "source": "comune", "patterns": [
        r"relazione.*comun", r"comun.*relazione", r"appendice.*2.*comun",
        r"app.*2.*comun", r"comun.*appendice.*2", r"comun.*app.*2",
    ]},
    {"key": "dich_veridicita_c", "source": "comune", "patterns": [
        r"dich.*veridic.*comun", r"comun.*dich.*veridic",
        r"appendice.*3.*comun", r"app.*3.*comun",
        r"comun.*appendice.*3", r"comun.*app.*3", r"veridicit.*comun",
    ]},
    {"key": "altre_com_c",       "source": "comune", "patterns": [
        r"comunicazion.*comun", r"comun.*comunicazion", r"comun.*altr",
        r"format.*comun", r"comun.*format", r"dati.*comun",
    ]},
]

# Fallback: se il file non matcha nessun pattern specifico,
# prova a capire almeno se è gestore o comune dalla struttura cartelle
FOLDER_HINTS = {
    "gestore": [r"gest", r"gestore", r"operatore"],
    "comune":  [r"comun", r"ente", r"municipio"],
}


def classify_file(filepath):
    """
    Classifica un file in base al suo nome e percorso.
    Restituisce (doc_key, source) oppure (None, source_guess).
    """
    name_lower = filepath.lower().replace("\\", "/")
    basename = os.path.basename(name_lower)

    # Ignora file di sistema, thumbs, desktop.ini, ecc.
    if basename.startswith(".") or basename in ("thumbs.db", "desktop.ini", ".ds_store"):
        return None, None

    # Prova pattern specifici
    for doc in DOC_PATTERNS:
        for pat in doc["patterns"]:
            if re.search(pat, name_lower):
                return doc["key"], doc["source"]

    # Fallback: cerca indizi nel path per capire gestore vs comune
    source_guess = "sconosciuto"
    for src, hints in FOLDER_HINTS.items():
        for hint in hints:
            if re.search(hint, name_lower):
                source_guess = src
                break

    return None, source_guess


def file_hash(data):
    """SHA-256 di bytes."""
    return hashlib.sha256(data).hexdigest()


# ═══════════════════════════════════════════════════════════
#  SELENIUM DOWNLOAD
# ═══════════════════════════════════════════════════════════

def create_driver(download_dir):
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--allow-running-insecure-content")
    opts.add_argument("--unsafely-treat-insecure-origin-as-secure=http://provincia.fm.it")
    opts.add_experimental_option("prefs", {
        "download.default_directory": download_dir,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True,
    })
    return webdriver.Chrome(options=opts)


MAX_RETRIES = 2          # Tentativi extra per ogni comune fallito
PAGE_LOAD_TIMEOUT = 40  # Timeout caricamento pagina (secondi)


def download_zip(url, password, download_dir, timeout=120):
    """Scarica lo zip dalla dataroom. Restituisce path dello zip o None."""
    # Pulisci directory
    for f in os.listdir(download_dir):
        fp = os.path.join(download_dir, f)
        if os.path.isfile(fp):
            os.unlink(fp)

    driver = create_driver(download_dir)
    try:
        driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
        log_debug(f"Navigazione: {url}")
        try:
            driver.get(url)
        except TimeoutException:
            log_debug(f"ERRORE: Timeout caricamento pagina ({PAGE_LOAD_TIMEOUT}s)")
            return None
        time.sleep(3)

        # Password
        try:
            pwd_input = WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, 'input[name="password"]'))
            )
            pwd_input.send_keys(password + Keys.ENTER)
            log_debug("Password inserita")
        except TimeoutException:
            log_debug("Campo password non trovato")

        time.sleep(3)

        # Controlla se la dataroom è vuota prima di tentare il download
        try:
            src = driver.page_source
            if "Nessun files in questa pagina" in src or "No files in this page" in src:
                log_debug("Dataroom vuota: nessun file al livello radice")
                return "EMPTY_DATAROOM"
        except Exception:
            pass

        # Download
        btn_found = False
        for label in ["Scarica tutto", "Download all"]:
            try:
                btn = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, f"//button[contains(text(), '{label}')]"))
                )
                btn.click()
                log_debug(f"Click '{label}'")
                btn_found = True
                break
            except TimeoutException:
                continue

        if not btn_found:
            log_debug("ERRORE: Pulsante download non trovato")
            return None

        # Attendi completamento
        start = time.time()
        while time.time() - start < timeout:
            time.sleep(2)
            files = os.listdir(download_dir)
            crdowns = [f for f in files if f.endswith(".crdownload")]
            zips = [f for f in files if f.endswith(".zip")]
            if not crdowns and zips:
                time.sleep(1)
                log_debug(f"File scaricato: {zips[0]}")
                return os.path.join(download_dir, zips[0])

        # Ultimo controllo: potrebbe essere diventato vuoto dopo il click
        try:
            if "Nessun files in questa pagina" in driver.page_source:
                log_debug("Dataroom vuota rilevata post-click")
                return "EMPTY_DATAROOM"
        except Exception:
            pass

        log_debug("ERRORE: Timeout download")
        return None

    except WebDriverException as e:
        log_debug(f"ERRORE Selenium: {e}")
        return None
    finally:
        driver.quit()


# ═══════════════════════════════════════════════════════════
#  ANALISI ZIP
# ═══════════════════════════════════════════════════════════

def analyze_zip(zip_path):
    """
    Analizza il contenuto dello zip.
    Restituisce:
    - zip_hash: hash dello zip intero
    - files: lista di {name, path, hash, size, doc_key, source, classified}
    """
    zip_hash = file_hash(open(zip_path, "rb").read())
    files = []

    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            data = zf.read(info.filename)
            fhash = file_hash(data)
            doc_key, source = classify_file(info.filename)

            files.append({
                "name": os.path.basename(info.filename),
                "path": info.filename,
                "hash": fhash,
                "size": info.file_size,
                "doc_key": doc_key,
                "source": source,
                "classified": doc_key is not None,
            })

    return zip_hash, files


# ═══════════════════════════════════════════════════════════
#  AGGIORNAMENTO STATO CON STORICIZZAZIONE
# ═══════════════════════════════════════════════════════════

def update_comune_state(old_state, zip_hash, files, scan_time):
    """
    Aggiorna lo stato di un comune confrontando con lo stato precedente.
    Gestisce:
    - Primo caricamento di un documento
    - Sostituzione (nuovo hash per stesso doc_key)
    - Rimozione (doc_key presente prima ma non ora)
    - File multipli per stesso doc_key
    - Storicizzazione date
    """
    state = old_state.copy() if old_state else {}

    # Fingerprint sul contenuto reale dei file, indipendente dai metadati
    # dello zip (timestamp, ecc.) che cambiano ad ogni generazione dinamica.
    sorted_file_hashes = sorted((f["name"], f["hash"]) for f in files)
    content_fp = hashlib.sha256(json.dumps(sorted_file_hashes).encode()).hexdigest()
    old_fp = state.get("content_fingerprint", "")
    old_zip_hash = old_state.get("zip_hash") if old_state else None
    state["zip_hash"] = zip_hash                  # conservato per storico/debug
    state["content_fingerprint"] = content_fp
    state["last_scan"] = scan_time
    state["zip_changed"] = (content_fp != old_fp)

    if "docs" not in state:
        state["docs"] = {}
    if "all_files" not in state:
        state["all_files"] = []
    if "scan_history" not in state:
        state["scan_history"] = []

    # Registro scan
    state["scan_history"].append({
        "time": scan_time,
        "zip_hash": zip_hash,
        "changed": state["zip_changed"],
        "file_count": len(files),
    })
    # Tieni solo ultime 100 scansioni
    state["scan_history"] = state["scan_history"][-100:]

    if not state["zip_changed"] and old_zip_hash is not None:
        # Nessun cambiamento, non aggiornare i dettagli
        return state

    # ── Aggiorna documenti classificati ──
    old_docs = state.get("docs", {})
    new_docs = {}

    # Mappa doc_key -> lista file trovati in questo scan
    classified_now = {}
    for f in files:
        if f["doc_key"]:
            classified_now.setdefault(f["doc_key"], []).append(f)

    # Per ogni tipo di documento possibile
    all_doc_keys = [d["key"] for d in DOC_PATTERNS]
    for dk in all_doc_keys:
        old_doc = old_docs.get(dk, {})
        found_files = classified_now.get(dk, [])

        if found_files:
            # Prendi il file principale (il più grande, tipicamente il documento vero)
            main_file = max(found_files, key=lambda x: x["size"])
            old_hash = old_doc.get("current_hash")
            new_hash = main_file["hash"]

            doc = {
                "status": "received",
                "current_hash": new_hash,
                "current_file": main_file["name"],
                "current_size": main_file["size"],
                "file_count": len(found_files),
                "all_files": [{"name": f["name"], "hash": f["hash"], "size": f["size"]} for f in found_files],
            }

            if not old_doc or old_doc.get("status") == "missing":
                # Primo caricamento
                doc["first_upload"] = scan_time
                doc["last_upload"] = scan_time
                doc["replaced"] = False
                doc["upload_history"] = [{"time": scan_time, "hash": new_hash, "file": main_file["name"], "event": "primo_caricamento"}]
            elif new_hash != old_hash:
                # Sostituzione
                doc["first_upload"] = old_doc.get("first_upload", scan_time)
                doc["last_upload"] = scan_time
                doc["replaced"] = True
                history = old_doc.get("upload_history", [])
                history.append({"time": scan_time, "hash": new_hash, "file": main_file["name"], "event": "sostituzione"})
                doc["upload_history"] = history[-50:]
            else:
                # Invariato
                doc["first_upload"] = old_doc.get("first_upload", scan_time)
                doc["last_upload"] = old_doc.get("last_upload", scan_time)
                doc["replaced"] = old_doc.get("replaced", False)
                doc["upload_history"] = old_doc.get("upload_history", [])

            new_docs[dk] = doc
        else:
            # Documento non trovato in questo scan
            if old_doc and old_doc.get("status") == "received":
                # Era presente prima, ora rimosso
                doc = old_doc.copy()
                doc["status"] = "removed"
                doc["removed_at"] = scan_time
                history = doc.get("upload_history", [])
                history.append({"time": scan_time, "hash": None, "file": None, "event": "rimosso"})
                doc["upload_history"] = history[-50:]
                new_docs[dk] = doc
            else:
                new_docs[dk] = {"status": "missing"}

    state["docs"] = new_docs

    # ── File non classificati ──
    state["unclassified_files"] = [
        {"name": f["name"], "path": f["path"], "source": f["source"], "size": f["size"]}
        for f in files if not f["classified"]
    ]

    # ── Lista completa file ──
    state["all_files"] = [
        {"name": f["name"], "path": f["path"], "hash": f["hash"],
         "size": f["size"], "doc_key": f["doc_key"], "source": f["source"]}
        for f in files
    ]

    return state


# ═══════════════════════════════════════════════════════════
#  CALCOLO PROCESSABILITÀ
# ═══════════════════════════════════════════════════════════

def compute_processabilita(docs):
    """
    Determina la processabilità in base ai documenti presenti.
    - SI: tutti i documenti obbligatori ricevuti (tool + relazione + dich per entrambi)
    - SI_RISERVA: tool + relazione ok ma manca dichiarazione veridicità
    - NO: manca tool o relazione
    """
    obbligatori = ["tool_mtr3", "relazione", "tool_mtr3_c", "relazione_c"]
    dich = ["dich_veridicita", "dich_veridicita_c"]

    all_obb = all(docs.get(k, {}).get("status") == "received" for k in obbligatori)
    all_dich = all(docs.get(k, {}).get("status") == "received" for k in dich)

    if all_obb and all_dich:
        return "si"
    elif all_obb:
        return "si_riserva"
    else:
        return "no"


# ═══════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════

import subprocess

COMMIT_EVERY = 5  # Commit intermedio ogni N comuni


def save_dashboard(dashboard, output_path, scan_time, results, total_comuni):
    """
    Salva il dashboard.json in modo atomico (write su file .tmp poi rename).
    Aggiorna il meta con lo stato corrente prima di salvare.
    Può essere chiamato dopo ogni comune per salvataggi incrementali.
    """
    dashboard["meta"] = {
        "last_scan": scan_time,
        "results": results,
        "total_comuni": total_comuni,
        "scadenze": SCADENZE,
        "scan_in_progress": results["scansionati"] + results["errori"] < total_comuni,
    }
    tmp_path = output_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(dashboard, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, output_path)


def merge_remote_dashboard(output_path, dashboard):
    """
    Legge il dashboard.json dal remote (via git fetch + git show) e lo fonde
    semanticamente con quello in memoria: per ogni comune vince la versione
    con last_scan più recente. Così nessun job sovrascrive i dati degli altri.
    Aggiorna anche il file su disco se c'è stato almeno un merge.
    """
    try:
        subprocess.run(["git", "fetch", "origin", "--quiet"], check=True, capture_output=True)
        result = subprocess.run(
            ["git", "show", f"origin/HEAD:{output_path}"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            return  # File non ancora presente sul remote, nulla da fondare
        remote = json.loads(result.stdout)
        merged = False
        for cid, remote_state in remote.get("comuni", {}).items():
            local_state = dashboard.get("comuni", {}).get(cid, {})
            if remote_state.get("last_scan", "") > local_state.get("last_scan", ""):
                dashboard.setdefault("comuni", {})[cid] = remote_state
                merged = True
        if merged:
            tmp = output_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(dashboard, f, indent=2, ensure_ascii=False)
            os.replace(tmp, output_path)
            log_debug("Merge semantico con remote eseguito")
    except Exception as e:
        log_debug(f"WARN: merge remote fallito ({e}), continuo con versione locale")


def git_commit_push(output_path, processed, total, dashboard):
    """
    Esegue git add + commit + push del JSON aggiornato.
    Prima di ogni commit esegue un merge semantico con la versione remota
    per evitare di sovrascrivere dati di job concorrenti.
    Funziona solo in ambiente GitHub Actions (GITHUB_ACTIONS=true).
    """
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return  # In locale non fa nulla

    try:
        # Merge semantico con il remote prima di committare
        merge_remote_dashboard(output_path, dashboard)

        subprocess.run(["git", "add", output_path], check=True)
        # Controlla se c'è qualcosa da committare
        diff = subprocess.run(["git", "diff", "--cached", "--quiet"])
        if diff.returncode == 0:
            log_debug("Git: nessun cambiamento da committare")
            return

        msg = f"Scansione in corso [{processed}/{total} comuni]"
        subprocess.run(["git", "commit", "-m", msg], check=True)
        log_debug(f"Git commit: {msg}")

        # Push con retry in caso di conflitto
        for attempt in range(3):
            result = subprocess.run(["git", "push"], capture_output=True, text=True)
            if result.returncode == 0:
                log_debug("Git push OK")
                return
            # Conflitto remoto: reintegra la storia git, poi rifai il merge semantico
            log_debug(f"Git push fallito (tentativo {attempt+1}/3), remerge e riprova...")
            subprocess.run(["git", "pull", "--rebase"], capture_output=True)
            merge_remote_dashboard(output_path, dashboard)
            subprocess.run(["git", "add", output_path], check=True)
            subprocess.run(["git", "commit", "--amend", "--no-edit"], check=True)

        log_debug("WARN: git push fallito dopo 3 tentativi, continuo comunque")
    except subprocess.CalledProcessError as e:
        log_debug(f"WARN: git error: {e} — continuo comunque")


def main():
    parser = argparse.ArgumentParser(description="Scanner Dataroom ATA4 MTR3")
    parser.add_argument("--credentials", required=True, help="Path al file credentials.json")
    parser.add_argument("--output", required=True, help="Path output dashboard.json")
    parser.add_argument("--filter", default="", help="Filtra per nome comune (parziale)")
    args = parser.parse_args()

    # Carica credenziali
    with open(args.credentials, "r", encoding="utf-8") as f:
        credentials = json.load(f)

    # Carica stato precedente
    dashboard = {"comuni": {}, "meta": {}, "scadenze": SCADENZE}
    if os.path.exists(args.output):
        with open(args.output, "r", encoding="utf-8") as f:
            dashboard = json.load(f)

    scan_time = datetime.now(timezone.utc).isoformat()
    download_dir = tempfile.mkdtemp(prefix="ata4_")

    # Filtra comuni se richiesto
    comuni = credentials
    if args.filter:
        comuni = [c for c in comuni if args.filter.lower() in c["comune"].lower()]

    log_debug("═══ Scanner ATA4 MTR3 ═══")
    log_debug(f"Data: {scan_time}")
    log_debug(f"Comuni da scansionare: {len(comuni)}")

    results = {"scansionati": 0, "aggiornati": 0, "invariati": 0, "errori": 0, "vuoti": 0}

    for i, cred in enumerate(comuni, 1):
        cid = str(cred["id"])
        nome = cred["comune"]
        log_debug(f"[{i}/{len(comuni)}] === {nome} ===")

        old_state = dashboard.get("comuni", {}).get(cid, {})

        # Download (con retry automatico; non ritenta le dataroom vuote)
        zip_path = None
        for attempt in range(1 + MAX_RETRIES):
            if attempt > 0:
                log_debug(f"    Retry {attempt}/{MAX_RETRIES} per {nome}...")
                time.sleep(15)
            zip_path = download_zip(cred["url"], cred["pwd"], download_dir)
            if zip_path:  # path reale o "EMPTY_DATAROOM": esci comunque
                break

        if zip_path == "EMPTY_DATAROOM":
            # Dataroom raggiungibile ma priva di file: non è un vero errore
            results["vuoti"] += 1
            dashboard.setdefault("comuni", {})[cid] = {
                **dashboard.get("comuni", {}).get(cid, {}),
                "last_scan": scan_time,
                "last_scan_error": False,
                "last_scan_empty": True,
                "info": {
                    "comune": nome,
                    "gestore": cred.get("gestore", ""),
                    "url": cred["url"],
                },
            }
            log_debug(f"    VUOTO: dataroom senza file ({nome})")
            processed = results["scansionati"] + results["errori"] + results["vuoti"]
            save_dashboard(dashboard, args.output, scan_time, results, len(credentials))
            if processed % COMMIT_EVERY == 0:
                git_commit_push(args.output, processed, len(comuni), dashboard)
            continue

        if not zip_path:
            results["errori"] += 1
            # Aggiorna almeno la data dell'ultimo tentativo
            if cid not in dashboard.get("comuni", {}):
                dashboard.setdefault("comuni", {})[cid] = {}
            dashboard["comuni"][cid]["last_scan"] = scan_time
            dashboard["comuni"][cid]["last_scan_error"] = True
            dashboard["comuni"][cid]["last_scan_empty"] = False
            dashboard["comuni"][cid].setdefault("info", {})
            dashboard["comuni"][cid]["info"]["comune"] = nome
            dashboard["comuni"][cid]["info"]["gestore"] = cred.get("gestore", "")
            dashboard["comuni"][cid]["info"]["url"] = cred["url"]
            log_debug(f"    ERRORE su {nome} (dopo {1+MAX_RETRIES} tentativi)")
            # ── Salvataggio incrementale dopo errore ──
            processed = results["scansionati"] + results["errori"] + results["vuoti"]
            save_dashboard(dashboard, args.output, scan_time, results, len(credentials))
            if processed % COMMIT_EVERY == 0:
                git_commit_push(args.output, processed, len(comuni), dashboard)
            continue

        # Analizza
        zip_hash, files = analyze_zip(zip_path)
        log_debug(f"    File nello zip: {len(files)}")

        # Aggiorna stato
        new_state = update_comune_state(old_state, zip_hash, files, scan_time)
        new_state["info"] = {
            "comune": nome,
            "gestore": cred.get("gestore", ""),
            "advisor": cred.get("advisor", ""),
            "url": cred["url"],
            "id": cred["id"],
        }
        new_state["last_scan_error"] = False

        # Processabilità automatica
        new_state["processabile"] = compute_processabilita(new_state.get("docs", {}))

        dashboard.setdefault("comuni", {})[cid] = new_state
        results["scansionati"] += 1

        if new_state.get("zip_changed"):
            results["aggiornati"] += 1
            classified = sum(1 for f in files if f["classified"])
            unclassified = len(files) - classified
            log_debug(f"    AGGIORNATO! {classified} classificati, {unclassified} non classificati")
        else:
            results["invariati"] += 1
            log_debug("    Invariato")

        # Pulizia
        os.unlink(zip_path)

        # ── Salvataggio incrementale dopo ogni comune ──
        processed = results["scansionati"] + results["errori"]
        save_dashboard(dashboard, args.output, scan_time, results, len(credentials))
        if processed % COMMIT_EVERY == 0:
            git_commit_push(args.output, processed, len(comuni), dashboard)

    # ── Salvataggio finale (marca scan_in_progress = False) ──
    # ── Note manuali: preserva quelle esistenti ──
    # Le note manuali sono in dashboard["notes"][cid] e non vengono toccate dallo scanner
    save_dashboard(dashboard, args.output, scan_time, results, len(credentials))
    git_commit_push(args.output, len(comuni), len(comuni), dashboard)

    log_debug("═══ REPORT FINALE ═══")
    log_debug(f"Scansionati: {results['scansionati']}")
    log_debug(f"Aggiornati:  {results['aggiornati']}")
    log_debug(f"Invariati:   {results['invariati']}")
    log_debug(f"Errori:      {results['errori']}")


if __name__ == "__main__":
    main()