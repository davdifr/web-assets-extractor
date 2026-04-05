from __future__ import annotations

import unittest

from bs4 import BeautifulSoup

from web_assets_extractor.services.analyzer import WebAnalyzer
from web_assets_extractor.services.exporter import ReportExporter


class FakePage:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def evaluate(self, _script: str) -> object:
        return self._payload


class WebAnalyzerTests(unittest.TestCase):
    def test_clean_extracted_text_collapses_repeated_labels(self) -> None:
        analyzer = WebAnalyzer(ReportExporter())

        self.assertEqual(analyzer._clean_extracted_text("Chi siamoChi siamo"), "Chi siamo")
        self.assertEqual(
            analyzer._clean_extracted_text("Diventa Health Coach Diventa Health Coach"),
            "Diventa Health Coach",
        )

    def test_extract_rendered_copy_sections_maps_browser_payload(self) -> None:
        analyzer = WebAnalyzer(ReportExporter())
        sections = analyzer._extract_rendered_copy_sections(
            FakePage(
                {
                    "headlines": [{"tag": "h1", "text": "Hero headline"}],
                    "ctas": [{"tag": "a", "text": "Start now", "url": "https://example.com/start"}],
                    "copyBlocks": [{"tag": "p", "text": "This is a rendered paragraph block."}],
                }
            ),
            "https://example.com",
        )

        self.assertEqual([(item.tag, item.text) for item in sections[0]], [("h1", "Hero headline")])
        self.assertEqual(
            [(item.tag, item.text, item.url) for item in sections[1]],
            [("a", "Start now", "https://example.com/start")],
        )
        self.assertEqual(
            [(item.tag, item.text) for item in sections[2]],
            [("p", "This is a rendered paragraph block.")],
        )

    def test_build_copy_sections_filters_cookie_and_navigation_noise(self) -> None:
        analyzer = WebAnalyzer(ReportExporter())
        soup = BeautifulSoup(
            """
            <html>
              <body>
                <nav class="main-menu">
                  <a href="/about">Chi siamoChi siamo</a>
                </nav>
                <div class="cookie-banner">
                  <button>Accetta</button>
                  <p>Noi e terze parti selezionate utilizziamo cookie o tecnologie simili per finalità tecniche.</p>
                </div>
                <main>
                  <h1>Be Our Best</h1>
                  <a href="/start">INIZIA IL TUO PERCORSO</a>
                  <p>Digital health e remote monitoring per il benessere mentale e fisico, personalizzato per te.</p>
                </main>
              </body>
            </html>
            """,
            "html.parser",
        )

        headlines, ctas, copy_blocks = analyzer._build_copy_sections(soup, "https://example.com")

        self.assertEqual([(item.tag, item.text) for item in headlines], [("h1", "Be Our Best")])
        self.assertEqual(
            [(item.tag, item.text, item.url) for item in ctas],
            [("a", "INIZIA IL TUO PERCORSO", "https://example.com/start")],
        )
        self.assertEqual(
            [(item.tag, item.text) for item in copy_blocks],
            [
                (
                    "p",
                    "Digital health e remote monitoring per il benessere mentale e fisico, personalizzato per te.",
                )
            ],
        )


if __name__ == "__main__":
    unittest.main()
