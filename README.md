# web-assets-extractor

Desktop app locale in Python per estrarre font, palette colori, copy e asset digitali da una pagina web pubblica.

## Stack

- Python 3.12
- PySide6
- Playwright
- BeautifulSoup4
- requests
- Pillow

## Avvio

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
python -m web_assets_extractor.main
```

## Build Desktop

Per generare la cartella `dist/` con un bundle desktop PySide6:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
./scripts/build.sh
```

Lo script:

- installa le dipendenze di build
- genera `dist/web-assets-extractor/`
- produce anche `dist/web-assets-extractor.app` su macOS

Il binario finale viene creato in:

```bash
dist/web-assets-extractor.app
```

## Output

Ogni analisi crea una cartella dedicata in `analysis_runs/` con:

- `report.md`
- `report.json`
- `assets/`
- `assets.zip` se richiesto
