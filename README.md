# web-assets-extractor

Local Python desktop app for analyzing a public website and collecting the context needed for a redesign: fonts, color palette, copy, digital assets, and the brand's main routes.

## What It Does

- analyzes a single public page or explores the main internal routes with the optional `Brand Scan` mode
- extracts fonts, color palettes, headlines, CTA candidates, copy blocks, and digital assets
- uses the rendered DOM for websites that load content through JavaScript or Shadow DOM
- keeps page-level context for copy and assets, so you know which route each item comes from
- lets you select and download only the assets you actually want
- supports chunked media streams and can combine audio + video into a final `MP4` through muxing
- handles YouTube videos through `yt-dlp`

## Stack

- Python 3.12
- PySide6
- Playwright
- BeautifulSoup4
- requests
- Pillow
- yt-dlp

## Local Run

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
python -m web_assets_extractor.main
```

## Optional Dependencies

- `ffmpeg`
  Required for downloading chunked media streams and combining audio + video into a final `MP4` via muxing.

- Chromium browser for Playwright
  The app uses Playwright to inspect the rendered DOM on websites that mount content through JavaScript. If the Playwright browser is not available in the bundle, the analysis automatically tries to use an installed browser such as Chrome, Edge, Chromium, or Brave.

- `yt-dlp`
  Already included as a project dependency. For YouTube videos, if `ffmpeg` is available the app downloads and combines the best audio/video pair; otherwise it falls back to a progressive `MP4` when possible.

## How To Use

1. Enter a public URL.
2. Choose what to extract: fonts, colors, copy, and assets.
3. Optionally enable `Brand Scan` to explore the main routes on the same domain.
4. Run the analysis and review the results in the app tabs.
5. Select only the assets you need and download them.

`Brand Scan` is designed for redesign work: it enriches the brand context, but it is slower than scanning a single page.

## Desktop Build

To generate the `dist/` folder with a PySide6 desktop bundle:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
./scripts/build.sh
```

The script:

- installs the build dependencies
- generates `dist/web-assets-extractor/`
- also produces `dist/web-assets-extractor.app` on macOS

The final app bundle is created in:

```bash
dist/web-assets-extractor.app
```

## Output

Each analysis creates a dedicated folder in `analysis_runs/` with:

- `report.md`
- `report.json`
- `assets/`
- `assets.zip` when requested

Reports include:

- analysis overview
- explored pages
- detected fonts and colors
- headlines, CTA candidates, and copy blocks
- assets with source context
- operational notes about fallbacks, browser rendering, and limitations encountered

## Notes

- analysis works best on public, non-authenticated content
- SPA websites or highly dynamic pages may take longer because the app also uses a headless browser
- temporary media URLs, especially on YouTube, can expire: if preview or download fails after a while, rerun the analysis
