"""Export the slide deck to PDF (16:9, 1920x1080 per page), plus a PNG per slide.

    backend/.venv/Scripts/python docs/slides/export.py      # Windows
    backend/.venv/bin/python docs/slides/export.py          # Linux / macOS

Writes docs/slides/fail-closed-vm.pdf (the demo on the Vultr VM) and
docs/slides/check/<target>-NN.png (not committed).
"""

from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent


def main() -> None:
    check = HERE / "check"
    check.mkdir(exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch()
        for target in ("vm",):
            page = b.new_page(viewport={"width": 1920, "height": 1080})
            page.goto((HERE / "slides.html").as_uri() + f"?target={target}")
            page.evaluate("document.fonts.ready")
            page.wait_for_function("[...document.images].every(i => i.complete)")
            page.wait_for_timeout(300)
            pdf = page.pdf(width="1920px", height="1080px", print_background=True,
                           margin={"top": "0", "right": "0", "bottom": "0", "left": "0"})
            (HERE / f"fail-closed-{target}.pdf").write_bytes(pdf)  # written by us: robust on Windows
            slides = page.locator("section.slide")
            for i in range(slides.count()):
                (check / f"{target}-{i + 1:02d}.png").write_bytes(slides.nth(i).screenshot())
            print(f"wrote docs/slides/fail-closed-{target}.pdf ({slides.count()} slides)")
            page.close()
        b.close()


if __name__ == "__main__":
    main()
