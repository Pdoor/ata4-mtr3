# -*- coding: utf-8 -*-
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

# Funzione per forzare i log su GitHub Actions
def log_debug(msg):
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {msg}")
    sys.stdout.flush()

# --- CONFIGURAZIONE ---
SCADENZE = {
    "termine_arera_mtr3": "2026-07-31",
    "termine_ata_gestori": "2026-05-31",
    "termine_ata_comuni": "2026-06-15",
}

DOC_PATTERNS = [
    {"key": "tool_mtr3", "source": "gestore", "patterns": [r"tool.*mtr", r"appendice.*1.*gest", r"app.*1.*gest", r"pef.*grezzo.*gest", r"mtr3?.*gest.*tool", r"gest.*tool", r"gest.*appendice.*1", r"gest.*app.*1"]},
    {"key": "relazione", "source": "gestore", "patterns": [r"relazione.*gest", r"gest.*relazione", r"appendice.*2.*gest", r"app.*2.*gest", r"gest.*appendice.*2", r"gest.*app.*2"]},
    {"key": "dich_veridicita", "source": "gestore", "patterns": [r"dich.*veridic.*gest", r"gest.*dich.*veridic", r"appendice.*3.*gest", r"app.*3.*gest", r"gest.*appendice.*3", r"gest.*app.*3", r"veridicit.*gest"]},
    {"key": "altre_com", "source": "gestore", "patterns": [r"comunicazion.*gest", r"gest.*comunicazion", r"gest.*altr", r"format.*gest", r"gest.*format", r"dati.*gest"]},
    {"key": "tool_mtr3_c", "source": "comune", "patterns": [r"tool.*mtr.*comun", r"comun.*tool", r"appendice.*1.*comun", r"app.*1.*comun", r"comun.*appendice.*1", r"comun.*app.*1", r"pef.*grezzo.*comun", r"mtr3?.*comun.*tool"]},
    {"key": "relazione_c", "source": "comune", "patterns": [r"relazione.*comun", r"comun.*relazione", r"appendice.*2.*comun", r"app.*2.*comun", r"comun.*appendice.*2", r"comun.*app.*2"]},
    {"key": "dich_veridicita_c", "source": "comune", "patterns": [r"dich.*veridic.*comun", r"comun.*dich.*veridic", r"appendice.*3.*comun", r"app.*3.*comun", r"comun.*appendice.*3", r"comun.*app.*3", r"veridicit.*comun"]},
    {"key": "altre_com_c", "source": "comune", "patterns": [r"comunicazion.*comun", r"comun.*comunicazion", r"comun.*altr", r"format.*comun", r"comun.*format", r"dati.*comun"]},
]

def classify_file(filepath):
    name_lower = filepath.lower().replace("\\", "/")
    basename = os.path.basename(name_lower)
    if basename.startswith(".") or basename in ("thumbs.db", "desktop.ini", ".ds_store"):
        return None, None
    for doc in DOC_PATTERNS:
        for pat in doc["patterns"]:
            if re.search(pat, name_lower):
                return doc["key"], doc["source"]
    return None, "sconosciuto"

def file_hash(data):
    return hashlib.sha256(data).hexdigest()

def create_driver(download_dir):
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1920,1080")
    opts.add_experimental_option("prefs", {
        "download.default_directory": download_dir,
        "download.prompt_for_download": False,
    })
    return webdriver.Chrome(options=opts)

def download_zip(url, password, download_dir, timeout=120):
    log_debug(f"Avvio browser per: {url}")
    driver = create_driver(download_dir)
    try:
        driver.get(url)
        log_debug("Pagina caricata, attesa 5s...")
        time.sleep(5)
        try:
            pwd_input = WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[name="password"]')))
            pwd_input.send_keys(password + Keys.ENTER)
            log_debug("Password inserita, attesa login...")
        except TimeoutException:
            log_debug("Campo password non trovato, procedo...")
        time.sleep(5)
        btn_found = False
        for label in ["Scarica tutto", "Download all"]:
            try:
                btn = driver.find_element(By.XPATH, f"//button[contains(text(), '{label}')]")
                btn.click()
                log_debug(f"Click su '{label}' effettuato.")
                btn_found = True
                break
            except:
                continue
        if not btn_found:
            log_debug("ERRORE: Pulsante download non trovato!")
            return None
        log_debug("Download in corso, attesa file ZIP...")
        start = time.time()
        while time.time() - start < timeout:
            zips = [f for f in os.listdir(download_dir) if f.endswith(".zip")]
            if zips:
                log_debug(f"File ZIP scaricato: {zips[0]}")
                return os.path.join(download_dir, zips[0])
            time.sleep(3)
        log_debug("ERRORE: Timeout download.")
        return None
    finally:
        driver.quit()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--credentials", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    log_debug("=== AVVIO SCANSIONE DATAROOM ===")
    with open(args.credentials, "r", encoding="utf-8") as f:
        comuni = json.load(f)
    download_dir = tempfile.mkdtemp()
    for i, cred in enumerate(comuni, 1):
        log_debug(f"Elaborazione Comune {i}/{len(comuni)}: {cred['comune']}")
        download_zip(cred["url"], cred["pwd"], download_dir)
    log_debug("=== FINE SCANSIONE ===")

if __name__ == "__main__":
    main()