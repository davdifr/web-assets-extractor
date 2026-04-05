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

Per scaricare stream media chunked e combinare audio + video in un file finale `MP4` tramite muxing, `ffmpeg` deve essere installato e disponibile nel `PATH`.

Per i video YouTube, l'app usa `yt-dlp`: se `ffmpeg` e disponibile scarica e combina la miglior coppia audio/video, altrimenti ripiega automaticamente su un `MP4` progressivo.

Per i siti che montano gli asset via JavaScript, l'app usa anche Playwright per leggere il DOM renderizzato. Se il browser Playwright non e presente nel bundle, l'analisi prova automaticamente a usare Chrome o Edge installati sul computer.

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
