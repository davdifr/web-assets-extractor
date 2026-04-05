from __future__ import annotations

import json
from pathlib import Path

from web_assets_extractor.models import AnalysisResult


class ReportExporter:
    def write_session_reports(self, result: AnalysisResult) -> None:
        self.export_json(result, result.paths.report_json)
        self.export_markdown(result, result.paths.report_markdown)

    def export_json(self, result: AnalysisResult, destination: Path) -> None:
        destination.write_text(
            json.dumps(result.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def export_markdown(self, result: AnalysisResult, destination: Path) -> None:
        destination.write_text(self.build_markdown(result), encoding="utf-8")

    def build_markdown(self, result: AnalysisResult) -> str:
        scan_mode = (
            f"Brand Scan (up to {result.options.max_route_pages} extra routes)"
            if result.options.explore_site_routes
            else "Single page"
        )
        lines: list[str] = [
            "# Web Assets Extractor Report",
            "",
            "## Source",
            f"- Requested URL: {result.source_url}",
            f"- Final URL: {result.final_url}",
            f"- Page Title: {result.page_title or 'N/A'}",
            f"- Page Description: {result.page_description or 'N/A'}",
            f"- HTTP Status: {result.status_code if result.status_code is not None else 'N/A'}",
            f"- Analysed At: {result.analysed_at}",
            f"- Output Directory: {result.paths.root_dir}",
            "",
            "## Overview",
            f"- Scan Mode: {scan_mode}",
            f"- Analysis Duration: {result.duration_ms} ms",
            f"- Pages Scanned: {max(1, len(result.scanned_pages))}",
            f"- Word Count: {result.word_count}",
            f"- Fonts Detected: {result.fonts_count}",
            f"- Colors Detected: {result.colors_count}",
            f"- Main Headlines: {result.headlines_count}",
            f"- CTA Candidates: {result.ctas_count}",
            f"- Copy Blocks: {result.copy_blocks_count}",
            f"- Assets Detected: {result.assets_count}",
            f"- Downloaded Assets: {result.downloaded_assets_count}",
            "",
            "## Scanned Pages",
        ]

        lines.extend(self._scanned_pages_section(result))
        lines.extend([
            "",
            "## Fonts",
        ])

        lines.extend(self._fonts_section(result))
        lines.extend(["", "## Color Palette"])
        lines.extend(self._colors_section(result))
        lines.extend(["", "## Main Headlines"])
        lines.extend(self._headlines_section(result))
        lines.extend(["", "## CTA"])
        lines.extend(self._ctas_section(result))
        lines.extend(["", "## Copy Blocks"])
        lines.extend(self._copy_blocks_section(result))
        lines.extend(["", "## Assets"])
        lines.extend(self._assets_section(result))
        lines.extend(["", "## Downloaded Assets"])
        lines.extend(self._downloaded_assets_section(result))
        lines.extend(["", "## Notes"])
        lines.extend(self._notes_section(result))
        return "\n".join(lines).rstrip() + "\n"

    def _fonts_section(self, result: AnalysisResult) -> list[str]:
        if not result.options.analyze_fonts:
            return ["Analysis skipped."]
        if not result.fonts:
            return ["No fonts detected."]
        lines = ["| Family | Occurrences |", "| --- | ---: |"]
        for font in result.fonts:
            lines.append(f"| {self._escape_table(font.family)} | {font.occurrences} |")
        return lines

    def _colors_section(self, result: AnalysisResult) -> list[str]:
        if not result.options.analyze_colors:
            return ["Analysis skipped."]
        if not result.colors:
            return ["No colors detected."]
        lines = ["| Color | Source | Occurrences |", "| --- | --- | ---: |"]
        for color in result.colors:
            lines.append(
                f"| `{color.value}` | {self._escape_table(color.source)} | {color.occurrences} |"
            )
        return lines

    def _scanned_pages_section(self, result: AnalysisResult) -> list[str]:
        pages = result.scanned_pages or [result.final_url]
        return [f"- {page_url}" for page_url in pages]

    def _headlines_section(self, result: AnalysisResult) -> list[str]:
        if not result.options.analyze_copy:
            return ["Analysis skipped."]
        if not result.headlines:
            return ["No headline candidates detected."]
        return [
            f"{index}. `{headline.tag}` {headline.text}"
            + (
                f" _(Page: {headline.page_url})_"
                if headline.page_url
                else ""
            )
            for index, headline in enumerate(result.headlines, start=1)
        ]

    def _ctas_section(self, result: AnalysisResult) -> list[str]:
        if not result.options.analyze_copy:
            return ["Analysis skipped."]
        if not result.ctas:
            return ["No CTA candidates detected."]
        lines = ["| Text | URL | Tag | Page |", "| --- | --- | --- | --- |"]
        for cta in result.ctas:
            lines.append(
                "| "
                + " | ".join(
                    [
                        self._escape_table(cta.text),
                        self._escape_table(cta.url or "N/A"),
                        cta.tag,
                        self._escape_table(cta.page_url or "N/A"),
                    ]
                )
                + " |"
            )
        return lines

    def _copy_blocks_section(self, result: AnalysisResult) -> list[str]:
        if not result.options.analyze_copy:
            return ["Analysis skipped."]
        if not result.copy_blocks:
            return ["No copy blocks detected."]
        return [
            f"{index}. `{block.tag}` {block.text}"
            + (
                f" _(Page: {block.page_url})_"
                if block.page_url
                else ""
            )
            for index, block in enumerate(result.copy_blocks, start=1)
        ]

    def _assets_section(self, result: AnalysisResult) -> list[str]:
        if not result.options.analyze_assets:
            return ["Analysis skipped."]
        if not result.assets:
            return ["No assets detected."]
        lines = [
            "| ID | Type | Filename | Source | Origin | Page | Downloaded |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for asset in result.assets:
            source = asset.url or "inline content"
            lines.append(
                "| "
                + " | ".join(
                    [
                        asset.asset_id,
                        self._escape_table(asset.kind),
                        self._escape_table(asset.filename),
                        self._escape_table(source),
                        self._escape_table(asset.origin),
                        self._escape_table(asset.page_url or "N/A"),
                        "Yes" if asset.downloaded else "No",
                    ]
                )
                + " |"
            )
        return lines

    def _downloaded_assets_section(self, result: AnalysisResult) -> list[str]:
        if not result.downloaded_assets:
            return ["No assets downloaded yet."]
        lines = [
            "| Filename | Type | Local Path | Size (bytes) | Image Size |",
            "| --- | --- | --- | ---: | --- |",
        ]
        for asset in result.downloaded_assets:
            lines.append(
                "| "
                + " | ".join(
                    [
                        self._escape_table(asset.filename),
                        self._escape_table(asset.kind),
                        self._escape_table(asset.local_path),
                        str(asset.size_bytes or 0),
                        self._escape_table(asset.image_size or "N/A"),
                    ]
                )
                + " |"
            )
        return lines

    def _notes_section(self, result: AnalysisResult) -> list[str]:
        if not result.notes:
            return ["- No additional notes."]
        return [f"- {note}" for note in result.notes]

    @staticmethod
    def _escape_table(value: str) -> str:
        return value.replace("|", "\\|")
