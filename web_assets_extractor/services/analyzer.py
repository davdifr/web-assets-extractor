from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any, Callable
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


@dataclass(slots=True)
class RenderedMediaResponse:
    url: str
    content_type: str | None


@dataclass(slots=True)
class RenderedPageSnapshot:
    html: str
    final_url: str
    media_responses: list[RenderedMediaResponse]
    headlines: list[TextSnippet] = field(default_factory=list)
    ctas: list[CTARecord] = field(default_factory=list)
    copy_blocks: list[TextSnippet] = field(default_factory=list)


class WebAnalyzer:
    MAX_STYLESHEETS = 12
    MAX_STYLESHEET_CHARS = 500_000
    UI_NOISE_TEXTS = {
        "x",
        "×",
        "refresh",
        "open main menu",
        "close",
        "chiudi",
        "cookie policy",
        "privacy policy",
    }
    COOKIE_TEXT_MARKERS = (
        "cookie",
        "consenso",
        "privacy policy",
        "cookie policy",
        "terze parti selezionate",
        "finalita tecniche",
        "finalità tecniche",
    )
    NOISE_ATTRIBUTE_TOKENS = (
        "cookie",
        "consent",
        "iubenda",
        "gdpr",
        "onetrust",
        "trustarc",
        "modal",
        "overlay",
        "popup",
        "toast",
        "drawer",
    )
    NAVIGATION_ATTRIBUTE_TOKENS = (
        "menu",
        "navbar",
        "sidenav",
        "drawer",
    )

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
        rendered_snapshot: RenderedPageSnapshot | None = None
        rendered_soup: BeautifulSoup | None = None
        rendered_analysis_soup: BeautifulSoup | None = None
        rendered_snapshot_used = False
        rendered_usage_labels: list[str] = []

        if options.analyze_assets or options.analyze_copy:
            rendered_snapshot = self._collect_rendered_asset_snapshot(
                final_url,
                options.timeout_ms,
                progress_callback,
                notes,
            )
            if rendered_snapshot is not None:
                rendered_snapshot_used = True
                rendered_soup = BeautifulSoup(rendered_snapshot.html, "html.parser")
                rendered_analysis_soup = BeautifulSoup(rendered_snapshot.html, "html.parser")
                self._remove_non_content_nodes(rendered_analysis_soup)

        self._progress(progress_callback, "Extracting requested data")
        fonts = self._build_fonts(analysis_soup, stylesheets) if options.analyze_fonts else []
        colors = self._build_colors(analysis_soup, stylesheets) if options.analyze_colors else []
        headlines, ctas, copy_blocks = (
            self._build_copy_sections(analysis_soup, final_url)
            if options.analyze_copy
            else ([], [], [])
        )
        if options.analyze_copy and rendered_analysis_soup is not None and rendered_snapshot is not None:
            rendered_copy_sections = (
                list(rendered_snapshot.headlines),
                list(rendered_snapshot.ctas),
                list(rendered_snapshot.copy_blocks),
            )
            if not any(rendered_copy_sections):
                rendered_copy_sections = self._build_copy_sections(
                    rendered_analysis_soup,
                    rendered_snapshot.final_url,
                )
            merged_headlines = self._merge_text_snippets(headlines, rendered_copy_sections[0])
            merged_ctas = self._merge_cta_records(ctas, rendered_copy_sections[1])
            merged_copy_blocks = self._merge_text_snippets(copy_blocks, rendered_copy_sections[2])
            additional_headlines = max(0, len(merged_headlines) - len(headlines))
            additional_ctas = max(0, len(merged_ctas) - len(ctas))
            additional_copy_blocks = max(0, len(merged_copy_blocks) - len(copy_blocks))
            headlines, ctas, copy_blocks = merged_headlines, merged_ctas, merged_copy_blocks
            if additional_headlines or additional_ctas or additional_copy_blocks:
                rendered_usage_labels.append("copy")
                notes.append(
                    "Rendered browser snapshot inspected for copy extraction. "
                    f"Added {additional_headlines} headlines, {additional_ctas} CTA candidates, "
                    f"and {additional_copy_blocks} copy blocks."
                )
        assets: list[AssetRecord] = []
        if options.analyze_assets:
            static_assets = self._extract_assets(analysis_soup, html, final_url, stylesheets)
            assets = list(static_assets)
            if rendered_snapshot is not None and rendered_soup is not None:
                rendered_assets = self._extract_assets(
                    rendered_soup,
                    rendered_snapshot.html,
                    rendered_snapshot.final_url,
                    stylesheets,
                )
                network_assets = self._extract_network_assets(
                    rendered_snapshot.media_responses,
                    rendered_snapshot.final_url,
                )
                assets = self._merge_assets(rendered_assets, network_assets, static_assets)

                additional_assets = max(0, len(assets) - len(static_assets))
                notes.append(
                    "Rendered browser snapshot inspected for asset extraction. "
                    f"Added {additional_assets} rendered/network asset candidates."
                )
                rendered_usage_labels.append("assets")
        if rendered_snapshot_used:
            usage_summary = ", ".join(rendered_usage_labels) if rendered_usage_labels else "rendered content"
            notes.append(
                "Analysis completed with HTTP extraction plus rendered browser inspection "
                f"for {usage_summary}."
            )
        else:
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
            if self._is_noise_element(element, include_navigation=False):
                continue
            text = self._clean_extracted_text(element.get_text(" ", strip=True))
            if not text or self._is_low_signal_heading(text) or text in seen_headlines:
                continue
            seen_headlines.add(text)
            headlines.append(TextSnippet(tag=element.name or "h1", text=text))
            if len(headlines) >= 20:
                break

        for element in soup.select(
            "a[href], button, [role='button'], input[type='button'], input[type='submit']"
        ):
            if self._is_noise_element(element, include_navigation=True):
                continue
            text = self._clean_extracted_text(
                element.get_text(" ", strip=True) or element.get("value", "") or element.get("aria-label", "")
            )
            if self._is_low_signal_cta_text(text):
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
            if self._is_noise_element(element, include_navigation=False):
                continue
            text = self._clean_extracted_text(element.get_text(" ", strip=True))
            if self._is_low_signal_copy_text(text):
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
        html: str,
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
            filename_hint: str | None = None,
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
                inferred_kind = self._infer_asset_kind(
                    kind,
                    origin,
                    resolved_url,
                    mime_type=mime_type,
                )
                if inferred_kind is None:
                    return
                seen.add(dedupe_key)
                fallback_name = f"{inferred_kind}-{len(assets) + 1:03d}"
                filename_source = filename_hint or guess_filename_from_url(resolved_url, fallback_name)
                filename = sanitize_filename(
                    filename_source,
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

        for audio in soup.select("audio"):
            add_asset("audio", audio.get("src"), "audio[src]")

        for source in soup.select("source[src]"):
            parent_name = source.parent.name if source.parent else source.name
            origin = f"{parent_name} source[src]" if parent_name else f"{source.name}[src]"
            add_asset(None, source.get("src"), origin, mime_type=source.get("type"))

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

        self._extract_embedded_media_assets(html, base_url, add_asset)
        return assets

    def _extract_embedded_media_assets(
        self,
        html: str,
        page_url: str,
        add_asset: Callable[..., None],
    ) -> None:
        self._extract_youtube_player_assets(html, page_url, add_asset)

    def _extract_youtube_player_assets(
        self,
        html: str,
        page_url: str,
        add_asset: Callable[..., None],
    ) -> None:
        player_response = self._extract_json_assignment(html, "ytInitialPlayerResponse")
        if not isinstance(player_response, dict):
            return

        video_details = player_response.get("videoDetails")
        video_id = (
            video_details.get("videoId")
            if isinstance(video_details, dict) and isinstance(video_details.get("videoId"), str)
            else "youtube"
        )
        title = (
            video_details.get("title")
            if isinstance(video_details, dict) and isinstance(video_details.get("title"), str)
            else None
        )
        add_asset(
            "video",
            page_url,
            "yt-dlp[youtube-best]",
            filename_hint=f"{video_id}-youtube-best.mp4",
            mime_type="video/mp4",
            alt_text=title,
        )

    def _extract_json_assignment(self, html: str, variable_name: str) -> dict[str, object] | None:
        pattern = re.compile(rf"(?:var\s+)?{re.escape(variable_name)}\s*=\s*\{{", re.MULTILINE)
        match = pattern.search(html)
        if not match:
            return None

        brace_start = html.find("{", match.start())
        if brace_start < 0:
            return None

        depth = 0
        in_string = False
        escaping = False
        for index in range(brace_start, len(html)):
            char = html[index]
            if in_string:
                if escaping:
                    escaping = False
                elif char == "\\":
                    escaping = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
                continue
            if char == "{":
                depth += 1
                continue
            if char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        payload = json.loads(html[brace_start : index + 1])
                    except json.JSONDecodeError:
                        return None
                    return payload if isinstance(payload, dict) else None
        return None

    def _collect_rendered_asset_snapshot(
        self,
        url: str,
        timeout_ms: int,
        progress_callback: ProgressCallback,
        notes: list[str],
    ) -> RenderedPageSnapshot | None:
        self._progress(progress_callback, "Capturing rendered browser snapshot")
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            notes.append(f"Rendered browser snapshot unavailable because Playwright could not be loaded: {exc}")
            return None

        media_responses: list[RenderedMediaResponse] = []
        seen_response_urls: set[str] = set()
        render_timeout_ms = max(8_000, min(60_000, timeout_ms))
        networkidle_timeout_ms = min(render_timeout_ms, 15_000)

        try:
            with sync_playwright() as playwright:
                browser = self._launch_playwright_browser(playwright, notes)
                try:
                    page = browser.new_page(
                        user_agent=self._session.headers.get("User-Agent", ""),
                    )

                    def on_response(response: object) -> None:
                        response_url = getattr(response, "url", None)
                        if not isinstance(response_url, str) or response_url in seen_response_urls:
                            return

                        try:
                            status = getattr(response, "status", None)
                            headers = getattr(response, "headers", None) or {}
                        except Exception:
                            return

                        if isinstance(status, int) and status >= 400:
                            return

                        content_type = ""
                        if isinstance(headers, dict):
                            content_type = str(headers.get("content-type", "") or "").lower()
                        if not self._is_media_response_url(response_url, content_type):
                            return

                        seen_response_urls.add(response_url)
                        media_responses.append(
                            RenderedMediaResponse(url=response_url, content_type=content_type or None)
                        )

                    page.on("response", on_response)
                    page.goto(url, wait_until="domcontentloaded", timeout=render_timeout_ms)
                    try:
                        page.wait_for_load_state("networkidle", timeout=networkidle_timeout_ms)
                    except PlaywrightTimeoutError:
                        notes.append(
                            "Rendered browser snapshot reached the network-idle timeout; captured the partial rendered page instead."
                        )
                    page.wait_for_timeout(1200)
                    rendered_copy_sections = self._extract_rendered_copy_sections(page, page.url)
                    return RenderedPageSnapshot(
                        html=page.content(),
                        final_url=page.url,
                        media_responses=list(media_responses),
                        headlines=rendered_copy_sections[0],
                        ctas=rendered_copy_sections[1],
                        copy_blocks=rendered_copy_sections[2],
                    )
                finally:
                    browser.close()
        except Exception as exc:
            notes.append(f"Rendered browser snapshot failed: {exc}")
            return None

    def _launch_playwright_browser(self, playwright: Any, notes: list[str]) -> Any:
        launch_errors: list[str] = []
        for label, launch_kwargs in self._iter_playwright_launch_candidates():
            try:
                browser = playwright.chromium.launch(**launch_kwargs)
                if label != "bundled Playwright Chromium":
                    notes.append(f"Rendered asset snapshot used fallback browser: {label}.")
                return browser
            except Exception as exc:
                error_line = str(exc).splitlines()[0].strip() or repr(exc)
                launch_errors.append(f"{label}: {error_line}")
        attempts = ", ".join(label for label, _ in self._iter_playwright_launch_candidates())
        details = " | ".join(launch_errors[:3])
        raise RuntimeError(
            "No compatible Chromium browser was available for rendered asset extraction. "
            f"Tried {attempts}. {details}"
        )

    def _iter_playwright_launch_candidates(self) -> list[tuple[str, dict[str, Any]]]:
        candidates: list[tuple[str, dict[str, Any]]] = [
            ("bundled Playwright Chromium", {"headless": True}),
            ("Google Chrome channel", {"headless": True, "channel": "chrome"}),
            ("Microsoft Edge channel", {"headless": True, "channel": "msedge"}),
        ]
        seen_paths: set[str] = set()
        for executable_path in self._candidate_browser_paths():
            if executable_path in seen_paths:
                continue
            seen_paths.add(executable_path)
            candidates.append(
                (
                    f"installed browser at {executable_path}",
                    {"headless": True, "executable_path": executable_path},
                )
            )
        return candidates

    def _candidate_browser_paths(self) -> list[str]:
        if sys.platform == "darwin":
            search_roots = [Path("/Applications"), Path.home() / "Applications"]
            app_names = (
                ("Google Chrome.app", "Contents/MacOS/Google Chrome"),
                ("Google Chrome for Testing.app", "Contents/MacOS/Google Chrome for Testing"),
                ("Chromium.app", "Contents/MacOS/Chromium"),
                ("Microsoft Edge.app", "Contents/MacOS/Microsoft Edge"),
                ("Brave Browser.app", "Contents/MacOS/Brave Browser"),
            )
            return [
                str(root / app_name / executable_suffix)
                for root in search_roots
                for app_name, executable_suffix in app_names
                if (root / app_name / executable_suffix).exists()
            ]
        if sys.platform.startswith("linux"):
            return [
                path
                for path in (
                    "/usr/bin/google-chrome",
                    "/usr/bin/google-chrome-stable",
                    "/usr/bin/chromium",
                    "/usr/bin/chromium-browser",
                    "/snap/bin/chromium",
                    "/usr/bin/microsoft-edge",
                    "/usr/bin/brave-browser",
                )
                if Path(path).exists()
            ]
        if sys.platform.startswith("win"):
            program_files = [
                Path.home() / "AppData/Local/Google/Chrome/Application/chrome.exe",
                Path.home() / "AppData/Local/Microsoft/Edge/Application/msedge.exe",
                Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
                Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
                Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
                Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
            ]
            return [str(path) for path in program_files if path.exists()]
        return []

    def _is_media_response_url(self, url: str, content_type: str) -> bool:
        lowered_url = url.lower()
        if any(token in content_type for token in ("image/", "video/", "audio/", "svg")):
            return True
        return lowered_url.endswith(
            (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".avif", ".svg", ".mp4", ".webm", ".mov", ".m3u8")
        )

    def _extract_network_assets(
        self,
        responses: list[RenderedMediaResponse],
        base_url: str,
    ) -> list[AssetRecord]:
        assets: list[AssetRecord] = []
        seen_urls: set[str] = set()

        for response in responses:
            resolved_url = absolutize_url(base_url, response.url)
            if not resolved_url or resolved_url in seen_urls:
                continue
            inferred_kind = self._infer_asset_kind(
                None,
                "rendered network response",
                resolved_url,
                mime_type=response.content_type,
            )
            if inferred_kind is None:
                continue

            seen_urls.add(resolved_url)
            fallback_name = f"{inferred_kind}-{len(assets) + 1:03d}"
            filename = sanitize_filename(
                guess_filename_from_url(resolved_url, fallback_name),
                default=fallback_name,
            )
            assets.append(
                AssetRecord(
                    asset_id=f"asset-{len(assets) + 1:03d}",
                    kind=inferred_kind,
                    filename=filename,
                    origin="rendered network response",
                    url=resolved_url,
                    mime_type=response.content_type,
                )
            )
        return assets

    def _extract_rendered_copy_sections(
        self,
        page: object,
        final_url: str,
    ) -> tuple[list[TextSnippet], list[CTARecord], list[TextSnippet]]:
        raw_sections = getattr(page, "evaluate")(
            """
() => {
  const normalize = value => (value || '').replace(/\\s+/g, ' ').trim();
  const collapseRepeatedText = value => {
    let text = normalize(value);
    while (text.length >= 8) {
      const half = Math.floor(text.length / 2);
      if (text.length % 2 === 0) {
        const first = normalize(text.slice(0, half));
        const second = normalize(text.slice(half));
        if (first && first === second) {
          text = first;
          continue;
        }
      }
      const parts = text.split(' ');
      if (parts.length >= 4 && parts.length % 2 === 0) {
        const firstHalf = parts.slice(0, parts.length / 2).join(' ');
        const secondHalf = parts.slice(parts.length / 2).join(' ');
        if (firstHalf && firstHalf === secondHalf) {
          text = firstHalf;
          continue;
        }
      }
      break;
    }
    return text;
  };
  const normalizeForMatch = value => collapseRepeatedText(value).normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').toLowerCase();
  const uiNoiseTexts = new Set(['x', '×', 'refresh', 'open main menu', 'close', 'chiudi', 'cookie policy', 'privacy policy']);
  const cookieTextMarkers = ['cookie', 'consenso', 'privacy policy', 'cookie policy', 'terze parti selezionate', 'finalita tecniche'];
  const baseNoiseSelector = [
    'nav',
    'footer',
    'aside',
    'dialog',
    '[role="dialog"]',
    '[aria-modal="true"]',
    '[id*="cookie" i]',
    '[class*="cookie" i]',
    '[id*="consent" i]',
    '[class*="consent" i]',
    '[id*="iubenda" i]',
    '[class*="iubenda" i]',
    '[id*="gdpr" i]',
    '[class*="gdpr" i]',
    '[id*="onetrust" i]',
    '[class*="onetrust" i]',
    '[id*="trustarc" i]',
    '[class*="trustarc" i]',
    '[id*="modal" i]',
    '[class*="modal" i]',
    '[id*="overlay" i]',
    '[class*="overlay" i]',
    '[id*="popup" i]',
    '[class*="popup" i]',
    '[id*="toast" i]',
    '[class*="toast" i]',
    '[id*="drawer" i]',
    '[class*="drawer" i]'
  ].join(',');
  const ctaNoiseSelector = [
    baseNoiseSelector,
    '[id*="menu" i]',
    '[class*="menu" i]',
    '[id*="navbar" i]',
    '[class*="navbar" i]',
    '[id*="sidenav" i]',
    '[class*="sidenav" i]'
  ].join(',');
  const selectors = {
    headings: 'h1,h2,h3,h4,h5,h6',
    ctas: 'a[href], button, [role="button"], input[type="button"], input[type="submit"]',
    copy: 'p,li,blockquote,figcaption',
  };

  const headlines = [];
  const ctas = [];
  const copyBlocks = [];
  const seenHeadlines = new Set();
  const seenCtas = new Set();
  const seenCopyBlocks = new Set();

  const pushHeadline = element => {
    if (headlines.length >= 20) return;
    if (element.closest(baseNoiseSelector)) return;
    const text = collapseRepeatedText(element.innerText || element.textContent || '');
    if (!text || seenHeadlines.has(text)) return;
    seenHeadlines.add(text);
    headlines.push({ tag: (element.tagName || 'h1').toLowerCase(), text });
  };

  const pushCta = element => {
    if (ctas.length >= 30) return;
    if (element.closest(ctaNoiseSelector)) return;
    const text = collapseRepeatedText(
      element.innerText ||
      element.textContent ||
      element.getAttribute?.('value') ||
      element.getAttribute?.('aria-label') ||
      ''
    );
    const loweredText = normalizeForMatch(text);
    if (!text || text.length < 2 || text.length > 120 || uiNoiseTexts.has(loweredText)) return;
    const tag = (element.tagName || 'button').toLowerCase();
    let href = null;
    if (tag === 'a') {
      const rawHref = element.getAttribute('href');
      if (rawHref) {
        try {
          href = new URL(rawHref, document.baseURI).href;
        } catch (_error) {
          href = rawHref;
        }
      }
    }
    const key = `${tag}|${text}|${href || ''}`;
    if (seenCtas.has(key)) return;
    seenCtas.add(key);
    ctas.push({ tag, text, url: href });
  };

  const pushCopyBlock = element => {
    if (copyBlocks.length >= 60) return;
    if (element.closest(baseNoiseSelector)) return;
    const text = collapseRepeatedText(element.innerText || element.textContent || '');
    const loweredText = normalizeForMatch(text);
    if (!text || text.length < 40 || text.length > 1200) return;
    if (cookieTextMarkers.some(marker => loweredText.includes(marker))) return;
    const tag = (element.tagName || 'p').toLowerCase();
    const key = `${tag}|${text}`;
    if (seenCopyBlocks.has(key)) return;
    seenCopyBlocks.add(key);
    copyBlocks.push({ tag, text });
  };

  const walk = root => {
    if (!root || !root.querySelectorAll) return;
    for (const element of root.querySelectorAll(selectors.headings)) pushHeadline(element);
    for (const element of root.querySelectorAll(selectors.ctas)) pushCta(element);
    for (const element of root.querySelectorAll(selectors.copy)) pushCopyBlock(element);
    for (const element of root.querySelectorAll('*')) {
      if (element.shadowRoot) walk(element.shadowRoot);
    }
  };

  walk(document);
  return { headlines, ctas, copyBlocks };
}
            """
        )
        if not isinstance(raw_sections, dict):
            return ([], [], [])

        headlines = [
            TextSnippet(tag=str(item.get("tag") or "h1"), text=str(item.get("text") or ""))
            for item in raw_sections.get("headlines", [])
            if isinstance(item, dict) and item.get("text")
        ]
        ctas = [
            CTARecord(
                text=str(item.get("text") or ""),
                url=str(item.get("url")) if item.get("url") else None,
                tag=str(item.get("tag") or "button"),
            )
            for item in raw_sections.get("ctas", [])
            if isinstance(item, dict) and item.get("text")
        ]
        copy_blocks = [
            TextSnippet(tag=str(item.get("tag") or "p"), text=str(item.get("text") or ""))
            for item in raw_sections.get("copyBlocks", [])
            if isinstance(item, dict) and item.get("text")
        ]
        return (headlines, ctas, copy_blocks)

    def _clean_extracted_text(self, value: str) -> str:
        text = self._normalize_text(value)
        while len(text) >= 8:
            half = len(text) // 2
            if len(text) % 2 == 0:
                first_half = self._normalize_text(text[:half])
                second_half = self._normalize_text(text[half:])
                if first_half and first_half == second_half:
                    text = first_half
                    continue
            parts = text.split()
            if len(parts) >= 4 and len(parts) % 2 == 0:
                first_half_words = " ".join(parts[: len(parts) // 2])
                second_half_words = " ".join(parts[len(parts) // 2 :])
                if first_half_words and first_half_words == second_half_words:
                    text = first_half_words
                    continue
            break
        return text

    def _normalized_match_text(self, value: str) -> str:
        return (
            self._clean_extracted_text(value)
            .lower()
            .translate(str.maketrans("àèéìíîòóù", "aeeiiioou"))
        )

    def _is_low_signal_heading(self, text: str) -> bool:
        return not text or len(text) > 200

    def _is_low_signal_cta_text(self, text: str) -> bool:
        if not text or len(text) < 2 or len(text) > 120:
            return True
        return self._normalized_match_text(text) in self.UI_NOISE_TEXTS

    def _is_low_signal_copy_text(self, text: str) -> bool:
        if len(text) < 40 or len(text) > 1200:
            return True
        lowered_text = self._normalized_match_text(text)
        return any(marker in lowered_text for marker in self.COOKIE_TEXT_MARKERS)

    def _is_noise_element(self, element: object, *, include_navigation: bool) -> bool:
        for ancestor in [element, *getattr(element, "parents", [])]:
            name = getattr(ancestor, "name", None)
            if name in {"nav", "footer", "aside", "dialog"}:
                return True
            if self._matches_noise_attributes(ancestor, include_navigation=include_navigation):
                return True
            role = str(getattr(ancestor, "get", lambda *_args, **_kwargs: None)("role") or "").lower()
            if role == "dialog":
                return True
            if str(getattr(ancestor, "get", lambda *_args, **_kwargs: None)("aria-modal") or "").lower() == "true":
                return True
        return False

    def _matches_noise_attributes(self, element: object, *, include_navigation: bool) -> bool:
        getter = getattr(element, "get", None)
        if getter is None:
            return False
        class_value = getter("class") or []
        if isinstance(class_value, str):
            class_tokens = [class_value]
        else:
            class_tokens = [str(token) for token in class_value]
        attribute_values = [
            str(getter("id") or ""),
            str(getter("aria-label") or ""),
            *class_tokens,
        ]
        lowered = " ".join(attribute_values).lower()
        if any(token in lowered for token in self.NOISE_ATTRIBUTE_TOKENS):
            return True
        if include_navigation and any(token in lowered for token in self.NAVIGATION_ATTRIBUTE_TOKENS):
            return True
        return False

    def _merge_assets(self, *groups: list[AssetRecord]) -> list[AssetRecord]:
        merged: list[AssetRecord] = []
        seen: set[str] = set()

        for group in groups:
            for asset in group:
                key = self._asset_merge_key(asset)
                if key in seen:
                    continue
                seen.add(key)
                merged.append(asset)

        reindexed: list[AssetRecord] = []
        for index, asset in enumerate(merged, start=1):
            reindexed.append(
                AssetRecord(
                    asset_id=f"asset-{index:03d}",
                    kind=asset.kind,
                    filename=asset.filename,
                    origin=asset.origin,
                    url=asset.url,
                    mime_type=asset.mime_type,
                    alt_text=asset.alt_text,
                    inline_content=asset.inline_content,
                    downloaded=asset.downloaded,
                    local_path=asset.local_path,
                    size_bytes=asset.size_bytes,
                    image_size=asset.image_size,
                )
            )
        return reindexed

    def _merge_text_snippets(self, *groups: list[TextSnippet]) -> list[TextSnippet]:
        merged: list[TextSnippet] = []
        seen: set[tuple[str, str]] = set()
        for group in groups:
            for item in group:
                key = (item.tag, item.text)
                if key in seen:
                    continue
                seen.add(key)
                merged.append(item)
        return merged

    def _merge_cta_records(self, *groups: list[CTARecord]) -> list[CTARecord]:
        merged: list[CTARecord] = []
        seen: set[tuple[str, str, str | None]] = set()
        for group in groups:
            for item in group:
                key = (item.tag, item.text, item.url)
                if key in seen:
                    continue
                seen.add(key)
                merged.append(item)
        return merged

    def _asset_merge_key(self, asset: AssetRecord) -> str:
        if asset.inline_content is not None:
            digest = hashlib.sha1(asset.inline_content.encode("utf-8")).hexdigest()
            return f"inline:{digest}"
        if asset.url:
            return f"url:{asset.url}"
        return f"fallback:{asset.kind}:{asset.filename}:{asset.origin}"

    def _infer_asset_kind(
        self,
        default_kind: str | None,
        origin: str,
        resolved_url: str,
        *,
        mime_type: str | None = None,
    ) -> str | None:
        path = urlparse(resolved_url).path.lower()
        origin_lower = origin.lower()
        mime_value = (mime_type or "").split(";")[0].strip().lower()
        prefers_audio = default_kind == "audio" or "audio" in origin_lower
        prefers_video = default_kind == "video" or "video" in origin_lower

        if self._is_ignored_asset_url(path):
            return None

        if mime_value.startswith("audio/"):
            return "audio"
        if mime_value.startswith("video/"):
            return "video"
        if mime_value in {
            "application/vnd.apple.mpegurl",
            "application/x-mpegurl",
            "application/dash+xml",
        }:
            if prefers_audio and not prefers_video:
                return "audio"
            return "video"

        if "icon" in origin or path.endswith((".ico", ".icns")):
            return "icon"
        if path.endswith(".svg"):
            if "/fonts/" in path or "/font/" in path:
                return None
            return "svg"
        if path.endswith((".mp3", ".m4a", ".aac", ".wav", ".flac", ".oga", ".opus")):
            return "audio"
        if path.endswith(".ogg"):
            if prefers_audio and not prefers_video:
                return "audio"
            return "video"
        if path.endswith((".mp4", ".mov", ".webm", ".m3u8", ".mpd")):
            if prefers_audio and not prefers_video:
                return "audio"
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
