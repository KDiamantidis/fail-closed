"""Export the slide deck to PDF (16:9, 1920x1080 per page), plus a PNG per slide to check it.

    backend/.venv/Scripts/python docs/slides/export.py      # Windows
    backend/.venv/bin/python docs/slides/export.py          # Linux / macOS

Writes docs/slides/fail-closed.pdf and docs/slides/check/slide-NN.png (not committed).
"""

from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent


def main() -> None:
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1920, "height": 1080})
        page.goto((HERE / "slides.html").as_uri())
        page.evaluate("document.fonts.ready")
        page.wait_for_function("[...document.images].every(i => i.complete && i.naturalWidth > 0)")
        page.pdf(path=str(HERE / "fail-closed.pdf"), width="1920px", height="1080px", print_background=True,
                 margin={"top": "0", "right": "0", "bottom": "0", "left": "0"})
        check = HERE / "check"
        check.mkdir(exist_ok=True)
        slides = page.locator("section.slide")
        for i in range(slides.count()):
            slides.nth(i).screenshot(path=str(check / f"slide-{i + 1:02d}.png"))
        print(f"wrote docs/slides/fail-closed.pdf ({slides.count()} slides)")
        b.close()


if __name__ == "__main__":
    main()
