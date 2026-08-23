#!/usr/bin/env python3
"""Render the current edition to self-contained HTML files.

The pages change only when fetch.py draws a new edition -- four times a day --
and each weighs about 10-25 KB with the stylesheet inlined. They therefore do
not need a Python server in front of them to be READ. Exporting lets the paper
live on any always-on static host for free, so the iPhone can open it whether
or not this Mac happens to be awake.

    ./venv/bin/python3 export.py [outdir]

Default output is ./docs/, which GitHub Pages can serve directly from the main
branch with no deploy step. One file per page: index.html plus one per section,
named exactly as news.py serves them, so the plain relative links between them
work unchanged on the static site.
"""
import re
import sys
from pathlib import Path

import sources as cfg
from news import app, slug

HERE = Path(__file__).parent
CSS = HERE / "static" / "news.css"


def render(path):
    """Ask the Flask app for one page, then inline its stylesheet."""
    with app.test_client() as client:
        resp = client.get(path)
        if resp.status_code != 200:
            raise SystemExit(f"{path}: app returned HTTP {resp.status_code}")
        html = resp.get_data(as_text=True)

    # Replace the <link> to the stylesheet with the stylesheet itself. Anchored
    # on the news.css href so the Google Fonts link is left alone -- that one
    # must stay a link, and it is the only external request the page makes.
    html, n = re.subn(
        r'<link rel="stylesheet" href="[^"]*news\.css[^"]*">',
        f"<style>\n{CSS.read_text()}\n</style>",
        html,
    )
    if n != 1:
        raise SystemExit(f"{path}: expected 1 stylesheet link to inline, found {n}")
    return html


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "docs"
    out.mkdir(parents=True, exist_ok=True)

    pages = ["index.html"] + [f"{slug(s)}.html" for s in cfg.SECTIONS]
    total = 0
    for page in pages:
        html = render("/" + page)
        if "news.css" in html:
            print(f"!! {page}: stylesheet link still present -- would be unstyled off-site")
            return 1
        (out / page).write_text(html)
        total += len(html.encode())

    # A section renamed or retired in sources.py leaves its old page behind,
    # still served by Pages and still committed, with nothing linking to it.
    # Only files this script writes are candidates, so nothing else in docs/
    # (a CNAME, .nojekyll) is at risk.
    for stale in sorted(set(p.name for p in out.glob("*.html")) - set(pages)):
        (out / stale).unlink()
        print(f"removed {stale}  (no longer a section)")

    print(f"wrote {len(pages)} pages to {out}  ({total / 1024:.1f} KB total)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
