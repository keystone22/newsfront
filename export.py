#!/usr/bin/env python3
"""Render the current edition to a single self-contained HTML file.

The page changes only when fetch.py draws a new edition -- four times a day --
and weighs about 21 KB with the stylesheet inlined. It therefore does not need
a Python server in front of it to be READ. Exporting lets the paper live on any
always-on static host for free, so the iPhone can open it whether or not this
Mac happens to be awake.

    ./venv/bin/python3 export.py [outfile]

Default output is ./docs/index.html, which GitHub Pages can serve
directly from the main branch with no deploy step.
"""
import re
import sys
from pathlib import Path

from news import app

HERE = Path(__file__).parent
CSS = HERE / "static" / "news.css"


def render():
    """Ask the Flask app for the page, then inline its stylesheet."""
    with app.test_client() as client:
        resp = client.get("/")
        if resp.status_code != 200:
            raise SystemExit(f"app returned HTTP {resp.status_code}")
        html = resp.get_data(as_text=True)

    css = CSS.read_text()
    # Replace the <link> to the stylesheet with the stylesheet itself. Anchored
    # on the news.css href so the Google Fonts link is left alone -- that one
    # must stay a link, and it is the only external request the page makes.
    html, n = re.subn(
        r'<link rel="stylesheet" href="[^"]*news\.css[^"]*">',
        f"<style>\n{css}\n</style>",
        html,
    )
    if n != 1:
        raise SystemExit(f"expected 1 stylesheet link to inline, found {n}")
    return html


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "docs" / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    html = render()
    out.write_text(html)
    print(f"wrote {out}  ({len(html.encode()) / 1024:.1f} KB)")
    if "news.css" in html:
        print("!! stylesheet link still present -- page would be unstyled off-site")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
