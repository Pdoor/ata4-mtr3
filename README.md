# ATA4 — Cruscotto MTR3 2026-2027

Sistema automatico di monitoraggio dataroom per l'ATA4 della Provincia di Fermo.

## Architettura

```
┌──────────────────────────────────────────────────────────────┐
│                    GitHub Repository                         │
│                                                              │
│  ┌─────────────┐    ┌──────────────┐    ┌────────────────┐  │
│  │  GitHub      │───▸│ scripts/     │───▸│ data/          │  │
│  │  Actions     │    │ scanner.py   │    │ dashboard.json │  │
│  │  (cron 6h)  │    └──────────────┘    └───────┬────────┘  │
│  └─────────────┘                                │            │
│                                                  │            │
│  ┌──────────────────────────────────────────────▼──────────┐ │
│  │  docs/index.html  (GitHub Pages - Cruscotto)            │ │
│  │  Legge dashboard.json → mostra stato in tempo reale     │ │
│  └─────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

### Flusso

1. **GitHub Actions** lancia `scanner.py` ogni 6 ore (o manualmente)
2. Lo script accede a ciascuna dataroom della Provincia, scarica lo zip
3. Analizza il contenuto: classifica i file per tipo documento (Tool MTR3, Relazione, Dichiarazione, ecc.)
4. Confronta con lo stato precedente (hash), traccia primo caricamento e sostituzioni
5. Scrive `data/dashboard.json` con lo stato aggiornato
6. Fa commit+push automatico del JSON
7. **GitHub Pages** serve `docs/index.html` che legge il JSON → cruscotto sempre aggiornato

### Scadenze

Il cruscotto mostra automaticamente le scadenze:
- **Termine ARERA MTR3**: scadenza normativa della circolare
- **Termine ATA**: scadenza impostata dall'ATA per comuni e gestori
- Evidenzia in rosso i ritardi e in giallo le scadenze prossime

## Setup

### 1. Fork/Clone questo repository

### 2. Configura i Secrets di GitHub

Vai in **Settings → Secrets and variables → Actions** e aggiungi:

| Secret | Valore |
|--------|--------|
| `CREDENTIALS_JSON` | Il contenuto del file `data/credentials.json` (vedi sotto) |

### 3. Prepara le credenziali

Crea un file `data/credentials.json` locale (NON committare!) con le credenziali:

```json
[
  {"id": 1, "comune": "Altidona", "url": "https://...", "pwd": "xxx"},
  ...
]
```

Poi codificalo come secret: `cat data/credentials.json | base64` e metti il risultato nel secret.

### 4. Abilita GitHub Pages

**Settings → Pages → Source: Deploy from a branch → Branch: main, folder: /docs**

### 5. Lancia manualmente la prima scansione

**Actions → Scansione Dataroom → Run workflow**

## File

| File | Descrizione |
|------|-------------|
| `scripts/scanner.py` | Script di scansione (gira su GitHub Actions) |
| `data/dashboard.json` | Database JSON aggiornato dallo scanner |
| `data/credentials.json` | Credenziali (solo locale, MAI committare) |
| `docs/index.html` | Cruscotto web (GitHub Pages) |
| `.github/workflows/scan.yml` | Workflow automatico |
