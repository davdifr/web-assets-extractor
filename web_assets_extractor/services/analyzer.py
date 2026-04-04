from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Callable
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from urllib3 import disable_warnings
from urllib3.exceptions import InsecureRequestWarning

from web_assets_extractor.models import (
    AnalysisOptions,
    AnalysisPaths,
    AnalysisResult,
    AssetRecord,
    CTARecord,
    ColorRecord,
    FontRecord,
    ProgressUpdate,
    TextSnippet,
)
from web_assets_extractor.services.exporter import ReportExporter
from web_assets_extractor.utils.colors import extract_color_tokens, normalize_css_color
from web_assets_extractor.utils.css import (
    extract_font_families,
    extract_google_font_families,
    extract_url_tokens,
    iter_css_declarations,
)
from web_assets_extractor.utils.files import make_analysis_paths, sanitize_filename
from web_assets_extractor.utils.urls import (
    absolutize_url,
    extract_urls_from_srcset,
    guess_filename_from_url,
    normalize_url,
)

ProgressCallback = Callable[[ProgressUpdate], None] | None

disable_warnings(InsecureRequestWarning)


@dataclass(slots=True)
class StylesheetContent:
    label: str
    content: str
    source_type: str
    url: str | None = None


class WebAnalyzer:
    MAX_STYLESHEETS = 12
    MAX_STYLESHEET_CHARS = 500_000

    def __init__(self, exporter: ReportExporter) -> None:
        self._exporter = exporter
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            }
        )

    def analyze(
        self,
        url: str,
        options: AnalysisOptions,
        output_root: Path,
        progress_callback: ProgressCallback = None,
    ) -> AnalysisResult:
        source_url = normalize_url(url)
        paths = AnalysisPaths(*make_analysis_paths(output_root, source_url))
        notes: list[str] = []
        start_time = perf_counter()
        timeout_seconds = max(5, int(options.timeout_ms / 1000))

        self._progress(progress_callback, "Fetching page HTML")
        response, used_insecure_ssl = self._fetch_response(source_url, timeout_seconds)
        html = response.text or ""
        final_url = response.url or source_url
        status_code = response.status_code

        if used_insecure_ssl:
            notes.append(
                "SSL certificate verification failed for the main request. "
                "Extraction continued without certificate verification on this device."
            )
        if status_code >= 400:
            notes.append(
                f"The server returned HTTP {status_code}. Extraction continued using the available HTML response."
            )

        page_soup = BeautifulSoup(html, "html.parser")
        stylesheet_soup = BeautifulSoup(html, "html.parser")
        analysis_soup = BeautifulSoup(html, "html.parser")
        page_title = self._extract_page_title(page_soup)
        page_description = self._extract_page_description(page_soup)

        stylesheets: list[StylesheetContent] = []
        if options.analyze_fonts or options.analyze_colors or options.analyze_assets:
            self._progress(progress_callback, "Collecting inline and linked stylesheets")
            stylesheets = self._collect_stylesheets(
                stylesheet_soup,
                final_url,
                timeout_seconds,
                progress_callback,
                notes,
            )

        self._remove_non_content_nodes(analysis_soup)
        word_count = self._count_words(analysis_soup)

        self._progress(progress_callback, "Extracting requested data")
        fonts = self._build_fonts(analysis_soup, stylesheets) if options.analyze_fonts else []
        colors = self._build_colors(analysis_soup, stylesheets) if options.analyze_colors else []
        headlines, ctas, copy_blocks = (
            self._build_copy_sections(analysis_soup, final_url)
            if options.analyze_copy
            else ([], [], [])
        )
        assets = self._extract_assets(analysis_soup, final_url, stylesheets) if options.analyze_assets else []

        notes.append("Analysis completed with direct HTTP extraction; no rendered browser snapshot was used.")
        if stylesheets:
            external_stylesheet_count = sum(1 for sheet in stylesheets if sheet.source_type == "external")
            inline_stylesheet_count = sum(1 for sheet in stylesheets if sheet.source_type == "inline")
            notes.append(
                f"Stylesheets inspected: {inline_stylesheet_count} inline, {external_stylesheet_count} external."
            )
        notes.append(f"Initial reports written to {paths.root_dir}")

        duration_ms = int((perf_counter() - start_time) * 1000)
        result = AnalysisResult(
            source_url=source_url,
            final_url=final_url,
            page_title=page_title,
            page_description=page_description,
            status_code=status_code,
            analysed_at=self._timestamp(),
            duration_ms=duration_ms,
            word_count=word_count,
            options=options,
            paths=paths,
            fonts=fonts,
            colors=colors,
            headlines=headlines,
            ctas=ctas,
            copy_blocks=copy_blocks,
            assets=assets,
            notes=notes,
        )

        self._progress(progress_callback, "Writing reports")
        self._exporter.write_session_reports(result)
        return result

    def _fetch_response(self, url: str, timeout_seconds: int) -> tuple[requests.Response, bool]:
        response, used_insecure_ssl = self._request_url(url, timeout_seconds)
        if not (response.text or "").strip():
            raise ValueError(f"The page returned an empty HTML response: {url}")
        return response, used_insecure_ssl

    def _request_url(self, url: str, timeout_seconds: int) -> tuple[requests.Response, bool]:
        try:
            response = self._session.get(url, timeout=timeout_seconds, allow_redirects=True)
            return response, False
        except requests.exceptions.SSLError:
            try:
                response = self._session.get(
                    url,
                    timeout=timeout_seconds,
                    allow_redirects=True,
                    verify=False,
                )
                return response, True
            except requests.RequestException as exc:
                raise ValueError(f"Could not fetch {url}: {exc}") from exc
        except requests.RequestException as exc:
            raise ValueError(f"Could not fetch {url}: {exc}") from exc

    def _collect_stylesheets(
        self,
        soup: BeautifulSoup,
        base_url: str,
        timeout_seconds: int,
        progress_callback: ProgressCallback,
        notes: list[str],
    ) -> list[StylesheetContent]:
        stylesheets: list[StylesheetContent] = []

        for index, style_tag in enumerate(soup.find_all("style"), start=1):
            content = style_tag.get_text("\n", strip=True)
            if content:
                stylesheets.append(
                    StylesheetContent(
                        label=f"Inline stylesheet {index}",
                        content=content,
                        source_type="inline",
                    )
                )

        external_urls: list[str] = []
        seen_urls: set[str] = set()
        for link_tag in soup.find_all("link", href=True):
            rel_values = {value.lower() for value in link_tag.get("rel", [])}
            href = link_tag.get("href", "")
            if "stylesheet" not in rel_values and not href.lower().endswith(".css"):
                continue
            resolved_url = absolutize_url(base_url, href)
            if not resolved_url or resolved_url in seen_urls:
                continue
            seen_urls.add(resolved_url)
            external_urls.append(resolved_url)

        if external_urls:
            self._progress(progress_callback, "Downloading linked stylesheets")

        for index, stylesheet_url in enumerate(external_urls[: self.MAX_STYLESHEETS], start=1):
            try:
                response, used_insecure_ssl = self._request_url(stylesheet_url, timeout_seconds)
            except ValueError as exc:
                notes.append(f"Could not download stylesheet {stylesheet_url}: {exc}")
                continue

            if used_insecure_ssl:
                notes.append(
                    f"SSL certificate verification failed for stylesheet {stylesheet_url}. "
                    "The file was fetched without certificate verification."
                )
            if not response.ok and not (response.text or "").strip():
                notes.append(
                    f"Skipped stylesheet {stylesheet_url} because the server returned HTTP {response.status_code}."
                )
                continue

            content = response.text or ""
            if not content.strip():
                continue

            if len(content) > self.MAX_STYLESHEET_CHARS:
                content = content[: self.MAX_STYLESHEET_CHARS]
                notes.append(
                    f"Trimmed stylesheet {response.url} to {self.MAX_STYLESHEET_CHARS} characters for analysis."
                )

            stylesheets.append(
                StylesheetContent(
                    label=f"External stylesheet {index}",
                    content=content,
                    source_type="external",
                    url=response.url,
                )
            )

        skipped_stylesheets = max(0, len(external_urls) - self.MAX_STYLESHEETS)
        if skipped_stylesheets:
            notes.append(
                f"Skipped {skipped_stylesheets} additional linked stylesheets after reaching the analysis limit."
            )
        return stylesheets

    def _build_fonts(
        self,
        soup: BeautifulSoup,
        stylesheets: list[StylesheetContent],
    ) -> list[FontRecord]:
        counts: Counter[str] = Counter()

        for stylesheet in stylesheets:
            counts.update(extract_font_families(stylesheet.content))
            if stylesheet.url:
                counts.update(extract_google_font_families(stylesheet.url))

        for element in soup.select("[style]"):
            style_value = element.get("style")
            if style_value:
                counts.update(extract_font_families(style_value))

        return [
            FontRecord(family=family, occurrences=occurrences)
            for family, occurrences in counts.most_common(25)
        ]

    def _build_colors(
        self,
        soup: BeautifulSoup,
        stylesheets: list[StylesheetContent],
    ) -> list[ColorRecord]:
        counts: dict[tuple[str, str], int] = defaultdict(int)

        for stylesheet in stylesheets:
            source_label = "external CSS" if stylesheet.source_type == "external" else "inline CSS"
            self._add_colors_from_text(stylesheet.content, source_label, counts)

        for element in soup.select("[style]"):
            style_value = element.get("style")
            if style_value:
                self._add_colors_from_text(style_value, "inline style", counts)

        for meta_name in ("theme-color", "msapplication-TileColor"):
            meta_tag = soup.select_one(f"meta[name='{meta_name}']")
            if meta_tag and meta_tag.get("content"):
                normalized = normalize_css_color(meta_tag.get("content"))
                if normalized:
                    counts[(normalized, f"meta {meta_name}")] += 1

        sorted_colors = sorted(
            [
                ColorRecord(value=color, source=source, occurrences=count)
                for (color, source), count in counts.items()
            ],
            key=lambda item: (-item.occurrences, item.value, item.source),
        )
        return sorted_colors[:30]

    def _add_colors_from_text(
        self,
        text: str,
        source_label: str,
        counts: dict[tuple[str, str], int],
    ) -> None:
        for _property_name, value in iter_css_declarations(text):
            for token in extract_color_tokens(value):
                normalized = normalize_css_color(token)
                if normalized:
                    counts[(normalized, source_label)] += 1

    def _build_copy_sections(
        self,
        soup: BeautifulSoup,
        final_url: str,
    ) -> tuple[list[TextSnippet], list[CTARecord], list[TextSnippet]]:
        headlines: list[TextSnippet] = []
        ctas: list[CTARecord] = []
        copy_blocks: list[TextSnippet] = []
        seen_headlines: set[str] = set()
        seen_ctas: set[tuple[str, str, str | None]] = set()
        seen_copy_blocks: set[tuple[str, str]] = set()

        for element in soup.select("h1, h2, h3, h4, h5, h6"):
            text = self._normalize_text(element.get_text(" ", strip=True))
            if not text or text in seen_headlines:
                continue
            seen_headlines.add(text)
            headlines.append(TextSnippet(tag=element.name or "h1", text=text))
            if len(headlines) >= 20:
                break

        for element in soup.select(
            "a[href], button, [role='button'], input[type='button'], input[type='submit']"
        ):
            text = self._normalize_text(
                element.get_text(" ", strip=True) or element.get("value", "") or element.get("aria-label", "")
            )
            if not text or len(text) > 120:
                continue

            href = absolutize_url(final_url, element.get("href")) if element.name == "a" else None
            key = (element.name or "button", text, href)
            if key in seen_ctas:
                continue

            seen_ctas.add(key)
            ctas.append(CTARecord(text=text, url=href, tag=element.name or "button"))
            if len(ctas) >= 30:
                break

        for element in soup.select("p, li, blockquote, figcaption"):
            text = self._normalize_text(element.get_text(" ", strip=True))
            if len(text) < 40 or len(text) > 1200:
                continue

            key = (element.name or "p", text)
            if key in seen_copy_blocks:
                continue

            seen_copy_blocks.add(key)
            copy_blocks.append(TextSnippet(tag=element.name or "p", text=text))
            if len(copy_blocks) >= 60:
                break

        return headlines, ctas, copy_blocks

    def _extract_assets(
        self,
        soup: BeautifulSoup,
        base_url: str,
        stylesheets: list[StylesheetContent],
    ) -> list[AssetRecord]:
        assets: list[AssetRecord] = []
        seen: set[str] = set()
        inline_svg_counter = 1

        def add_asset(
            kind: str | None,
            url: str | None,
            origin: str,
            *,
            reference_url: str | None = None,
            alt_text: str | None = None,
            mime_type: str | None = None,
            inline_content: str | None = None,
        ) -> None:
            nonlocal inline_svg_counter

            resolved_url: str | None = None
            filename: str
            if inline_content is not None:
                digest = hashlib.sha1(inline_content.encode("utf-8")).hexdigest()
                dedupe_key = f"inline:{digest}"
                if dedupe_key in seen:
                    return
                seen.add(dedupe_key)
                inferred_kind = kind or "svg"
                filename = f"inline-svg-{inline_svg_counter:03d}.svg"
                inline_svg_counter += 1
            else:
                resolved_url = absolutize_url(reference_url or base_url, url)
                if not resolved_url:
                    return
                dedupe_key = f"url:{resolved_url}"
                if dedupe_key in seen:
                    return
                inferred_kind = self._infer_asset_kind(kind, origin, resolved_url)
                if inferred_kind is None:
                    return
                seen.add(dedupe_key)
                fallback_name = f"{inferred_kind}-{len(assets) + 1:03d}"
                filename = sanitize_filename(
                    guess_filename_from_url(resolved_url, fallback_name),
                    default=fallback_name,
                )
                if "." not in Path(filename).name and inferred_kind == "svg":
                    filename = f"{filename}.svg"

            assets.append(
                AssetRecord(
                    asset_id=f"asset-{len(assets) + 1:03d}",
                    kind=inferred_kind,
                    filename=filename,
                    origin=origin,
                    url=resolved_url,
                    mime_type=mime_type,
                    alt_text=alt_text,
                    inline_content=inline_content,
                )
            )

        for image in soup.select("img"):
            alt_text = image.get("alt")
            for attribute, origin in (
                ("src", "img[src]"),
                ("data-src", "img[data-src]"),
                ("data-lazy-src", "img[data-lazy-src]"),
            ):
                add_asset("image", image.get(attribute), origin, alt_text=alt_text)

            srcset = image.get("srcset")
            if srcset:
                for item in extract_urls_from_srcset(srcset, base_url):
                    add_asset("image", item, "img[srcset]", alt_text=alt_text)

        for source in soup.select("picture source[srcset]"):
            srcset = source.get("srcset")
            if srcset:
                for item in extract_urls_from_srcset(srcset, base_url):
                    add_asset("image", item, "picture source[srcset]")

        for video in soup.select("video"):
            add_asset("video", video.get("src"), "video[src]")
            add_asset("image", video.get("poster"), "video[poster]")

        for source in soup.select("video source[src], source[src]"):
            add_asset(None, source.get("src"), f"{source.name}[src]", mime_type=source.get("type"))

        for link_tag in soup.select('link[rel~="icon"], link[rel="apple-touch-icon"]'):
            add_asset("icon", link_tag.get("href"), "link[rel=icon]")

        for element in soup.find_all(True):
            for attribute_name, kind in (
                ("data-mp4", "video"),
                ("data-webm", "video"),
                ("data-ogv", "video"),
                ("data-ogg", "video"),
                ("data-poster", "image"),
                ("data-image", "image"),
                ("data-thumb", "image"),
                ("data-thumb-src", "image"),
                ("data-bg", "image"),
                ("data-bgimage", "image"),
                ("data-lazyload", "image"),
            ):
                attribute_value = element.get(attribute_name)
                if isinstance(attribute_value, str) and attribute_value.strip():
                    add_asset(kind, attribute_value, f"{element.name}[{attribute_name}]")

        for element in soup.select("[style]"):
            style_value = element.get("style")
            if not style_value:
                continue
            for candidate_url in extract_url_tokens(style_value):
                add_asset("image", candidate_url, "inline style URL")

        for stylesheet in stylesheets:
            origin = "external CSS URL" if stylesheet.source_type == "external" else "inline CSS URL"
            reference_url = stylesheet.url or base_url
            for candidate_url in extract_url_tokens(stylesheet.content):
                if stylesheet.source_type == "external":
                    resolved_candidate = absolutize_url(reference_url, candidate_url)
                    if not resolved_candidate or not self._should_include_external_css_asset(resolved_candidate):
                        continue
                add_asset(None, candidate_url, origin, reference_url=reference_url)

        for svg in soup.find_all("svg"):
            add_asset(
                "svg",
                None,
                "inline svg",
                mime_type="image/svg+xml",
                inline_content=str(svg),
            )

        return assets

    def _infer_asset_kind(
        self,
        default_kind: str | None,
        origin: str,
        resolved_url: str,
    ) -> str | None:
        path = urlparse(resolved_url).path.lower()

        if self._is_ignored_asset_url(path):
            return None

        if "icon" in origin or path.endswith((".ico", ".icns")):
            return "icon"
        if path.endswith(".svg"):
            if "/fonts/" in path or "/font/" in path:
                return None
            return "svg"
        if path.endswith((".mp4", ".mov", ".webm", ".m3u8", ".ogg")):
            return "video"
        if path.endswith((".woff", ".woff2", ".ttf", ".otf", ".eot")):
            return None
        if path.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".avif", ".tif", ".tiff")):
            return "image"
        return default_kind

    def _should_include_external_css_asset(self, resolved_url: str) -> bool:
        path = urlparse(resolved_url).path.lower()
        return "/uploads/" in path

    def _is_ignored_asset_url(self, path: str) -> bool:
        return any(
            token in path
            for token in (
                "/revslider/public/assets/assets/transparent.png",
                "/smile_fonts/",
            )
        )

    def _extract_page_title(self, soup: BeautifulSoup) -> str:
        if soup.title and soup.title.get_text(strip=True):
            return soup.title.get_text(strip=True)
        return "Untitled page"

    def _extract_page_description(self, soup: BeautifulSoup) -> str | None:
        description = soup.select_one("meta[name='description']")
        if description and description.get("content"):
            return description.get("content", "").strip()
        return None

    def _remove_non_content_nodes(self, soup: BeautifulSoup) -> None:
        for node in soup.select("script, style, noscript, template"):
            node.decompose()

    def _count_words(self, soup: BeautifulSoup) -> int:
        root = soup.body or soup
        text = self._normalize_text(root.get_text(" ", strip=True))
        return len(text.split()) if text else 0

    def _normalize_text(self, value: str) -> str:
        return " ".join(value.split())

    def _timestamp(self) -> str:
        from datetime import datetime

        return datetime.now().isoformat(timespec="seconds")

    def _progress(self, callback: ProgressCallback, message: str) -> None:
        if callback:
            callback(ProgressUpdate(message=message, indeterminate=True))
