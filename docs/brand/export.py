"""Export the submission cover and the architecture diagram to PNG (versioned, same look as the UI).

    backend/.venv/Scripts/python docs/brand/export.py      # Windows
    backend/.venv/bin/python docs/brand/export.py          # Linux / macOS

Writes docs/brand/cover-1920x1080.png, cover-1280x720.png, architecture.png and
architecture-laptop.png (3200x1800, 2x; the laptop variant for Plan B),
and preview-*-400.png (how they look at 400 px wide, e.g. a gallery thumbnail; not committed).
Needs: pip install playwright && python -m playwright install chromium.
"""

from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent


def shoot(browser, src: Path, out: Path, width: int, height: int, scale: float) -> None:
    page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=scale)
    page.goto(src.as_uri())
    page.evaluate("document.fonts.ready")
    page.wait_for_timeout(300)
    out.write_bytes(page.screenshot(full_page=False))  # written by us: robust on Windows
    page.close()
    print("wrote", out.relative_to(HERE.parents[1]))


def preview(browser, png: Path, out: Path, width: int = 400) -> None:
    """How it looks at `width` px (a gallery thumbnail). A file page next to the PNG, so it can load it."""
    tmp = HERE / "_preview.html"
    tmp.write_text(f'<body style="margin:0"><img src="{png.name}" style="width:{width}px;display:block"></body>',
                   encoding="utf-8")
    try:
        page = browser.new_page(viewport={"width": width, "height": 400})
        page.goto(tmp.as_uri())
        page.wait_for_function("document.querySelector('img').complete && document.querySelector('img').naturalWidth > 0")
        out.write_bytes(page.locator("img").screenshot())
        page.close()
    finally:
        tmp.unlink(missing_ok=True)


def main() -> None:
    import sys

    only_diagrams = "--diagrams" in sys.argv  # leave the (approved) cover as it is
    with sync_playwright() as p:
        b = p.chromium.launch()
        if not only_diagrams:
            shoot(b, HERE / "cover.html", HERE / "cover-1920x1080.png", 1920, 1080, 1)
            shoot(b, HERE / "cover.html", HERE / "cover-1280x720.png", 1920, 1080, 2 / 3)
        shoot(b, HERE / "architecture.svg", HERE / "architecture.png", 1600, 900, 2)
        shoot(b, HERE / "architecture-laptop.svg", HERE / "architecture-laptop.png", 1600, 900, 2)
        for name in (("architecture", "architecture-laptop") if only_diagrams
                     else ("cover-1280x720", "architecture", "architecture-laptop")):
            preview(b, HERE / f"{name}.png", HERE / f"preview-{name}-400.png")
        b.close()


if __name__ == "__main__":
    main()
