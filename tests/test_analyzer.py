from __future__ import annotations

import unittest

from bs4 import BeautifulSoup

from web_assets_extractor.services.analyzer import RenderedLinkCandidate, RenderedPageSnapshot, WebAnalyzer
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

    def test_build_copy_sections_assigns_page_url(self) -> None:
        analyzer = WebAnalyzer(ReportExporter())
        soup = BeautifulSoup(
            """
            <html>
              <body>
                <main>
                  <h1>Brand headline</h1>
                  <a href="/start">Start now</a>
                  <p>This is a paragraph long enough to be considered meaningful product copy for the page.</p>
                </main>
              </body>
            </html>
            """,
            "html.parser",
        )

        headlines, ctas, copy_blocks = analyzer._build_copy_sections(soup, "https://example.com/brand")

        self.assertTrue(all(item.page_url == "https://example.com/brand" for item in headlines))
        self.assertTrue(all(item.page_url == "https://example.com/brand" for item in ctas))
        self.assertTrue(all(item.page_url == "https://example.com/brand" for item in copy_blocks))

    def test_discover_site_routes_prioritizes_main_internal_pages(self) -> None:
        analyzer = WebAnalyzer(ReportExporter())
        html = """
        <html>
          <body>
            <header>
              <nav>
                <a href="/s/about">About us</a>
                <a href="/s/services">Services</a>
                <a href="/s/contact">Contact</a>
                <a href="/s/login">Login</a>
              </nav>
            </header>
            <main>
              <a href="/s/blog/2026/launch-story">Launch story</a>
              <a href="https://external.example.com/partner">External</a>
            </main>
            <footer>
              <a href="/privacy-policy">Privacy policy</a>
            </footer>
          </body>
        </html>
        """
        rendered_snapshot = RenderedPageSnapshot(
            html="",
            final_url="https://brand.example.com/s/",
            media_responses=[],
            internal_links=[
                RenderedLinkCandidate(
                    url="https://brand.example.com/s/come-funziona",
                    text="Come funziona",
                    context="nav",
                ),
                RenderedLinkCandidate(
                    url="https://brand.example.com/s/partner",
                    text="Partner",
                    context="footer",
                ),
            ],
        )

        routes = analyzer._discover_site_routes(
            "https://brand.example.com/s/",
            html,
            rendered_snapshot,
            max_routes=4,
        )

        self.assertEqual(
            set(routes),
            {
                "https://brand.example.com/s/come-funziona",
                "https://brand.example.com/s/about",
                "https://brand.example.com/s/services",
                "https://brand.example.com/s/contact",
            },
        )

    def test_extract_assets_assigns_page_url(self) -> None:
        analyzer = WebAnalyzer(ReportExporter())
        soup = BeautifulSoup(
            """
            <html>
              <body>
                <img src="/images/logo.png" alt="Logo" />
              </body>
            </html>
            """,
            "html.parser",
        )

        assets = analyzer._extract_assets(
            soup,
            str(soup),
            "https://example.com/brand",
            [],
            page_url="https://example.com/brand",
        )

        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0].page_url, "https://example.com/brand")


if __name__ == "__main__":
    unittest.main()
