#!/usr/bin/env python3
"""NewsFront -- the front page and the section pages behind it.

Read-only. Everything on screen was written by fetch.py; this app never calls
out to a feed, so the page still serves when a source is down or slow.

    ./venv/bin/python3 news.py     ->  http://127.0.0.1:5051

Routes are named after FILES -- "/", "/index.html", "/world.html" -- rather
than the usual "/section/world", because export.py writes exactly these paths
into docs/ for GitHub Pages. Serving them under the same names means one set of
plain relative links works both locally and on the static site, with nothing to
rewrite at export time.
"""
import datetime as dt
import re
from zoneinfo import ZoneInfo

from flask import Flask, abort, render_template

import sources as cfg
from store import connect

LOCAL = ZoneInfo("America/New_York")

app = Flask(__name__)


def slug(name):
    """Section name -> the filename its page is served and exported under."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


# A section renamed into a collision would silently make two sections share one
# page, so it is caught at import rather than discovered on the site.
SLUGS = {slug(s): s for s in cfg.SECTIONS}
assert len(SLUGS) == len(cfg.SECTIONS), "two sections share a slug"
assert "index" not in SLUGS, "a section named 'Index' would collide with the front page"


def to_local(iso):
    if not iso:
        return None
    stamp = dt.datetime.fromisoformat(iso)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=dt.timezone.utc)
    return stamp.astimezone(LOCAL)


def age_label(iso):
    stamp = to_local(iso)
    if not stamp:
        return ""
    hours = (dt.datetime.now(dt.timezone.utc) - stamp).total_seconds() / 3600
    if hours < 0:
        # Politico and others publish with a scheduled, future timestamp.
        return "just now"
    if hours < 1:
        return f"{int(hours * 60)}m ago"
    if hours < 48:
        return f"{int(hours)}h ago"
    return f"{int(hours // 24)}d ago"


# The two draws live in two columns -- is_current for the front page,
# section_slot for the section pages -- and every query below is parameterised
# by which one it is reading, so the two pages cannot drift apart in how they
# report themselves. The names are checked against a literal allowlist because
# they are interpolated into SQL.
SLOT_COLUMNS = ("is_current", "section_slot")


def stories(db, slot_col, section=None):
    """The drawn articles for one draw, in slot order."""
    assert slot_col in SLOT_COLUMNS
    where = f"a.{slot_col} > 0" + (" AND a.section = ?" if section else "")
    rows = db.execute(f"""
        SELECT a.title, a.url, a.published_at, a.section, a.is_current,
               a.{slot_col} AS slot, s.name AS source
          FROM articles a
          JOIN sources s ON s.id = a.source_id
         WHERE {where}
      ORDER BY a.{slot_col}
    """, (section,) if section else ()).fetchall()
    return [dict(title=r["title"], url=r["url"], source=r["source"],
                 section=r["section"], age=age_label(r["published_at"]),
                 on_front=r["is_current"] > 0) for r in rows]


def feed_stats(db, section, slot_col):
    """Per-source candidate counts for one section, so the draw is auditable."""
    assert slot_col in SLOT_COLUMNS
    rows = db.execute(f"""
        SELECT s.name, s.cap, s.last_error,
               COUNT(a.id) FILTER (
                   WHERE a.published_at >= datetime('now','-'||s.recency_hours||' hours')
               ) AS in_window,
               COUNT(a.id) FILTER (WHERE a.{slot_col} > 0) AS on_page
          FROM sources s
          LEFT JOIN articles a ON a.source_id = s.id
         WHERE s.section = ? AND s.active = 1
      GROUP BY s.id ORDER BY in_window DESC
    """, (section,)).fetchall()

    feeds = [dict(name=r["name"], cap=r["cap"], in_window=r["in_window"],
                  on_page=r["on_page"], error=r["last_error"],
                  # Only the front page HAS a per-source cap. A section page
                  # deals round-robin instead, so there is nothing to exceed.
                  over_cap=slot_col == "is_current" and r["on_page"] > r["cap"])
             for r in rows]
    pool = sum(f["in_window"] for f in feeds) or 1
    for f in feeds:
        f["share"] = round(f["in_window"] * 100 / pool, 1)
    return feeds


def edition_stamp(db):
    last = db.execute("SELECT MAX(fetched_at) AS t FROM articles").fetchone()["t"]
    drawn = to_local(last)
    return drawn.strftime("%-d %b %-I:%M %p") if drawn else None


def masthead(db, **extra):
    """The values every page's header and footer share."""
    return dict(
        drawn=edition_stamp(db),
        window=f"{min(s[4] for s in cfg.SOURCES)}-{max(s[4] for s in cfg.SOURCES)}",
        source_count=len(cfg.SOURCES),
        section_count=len(cfg.SECTIONS),
        phase=getattr(cfg, "PHASE", 0),
        **extra)


@app.route("/")
@app.route("/index.html")
def front():
    db = connect()
    drawn = stories(db, "is_current")

    sections = []
    for name in cfg.SECTIONS:
        picked = [a for a in drawn if a["section"] == name]
        feeds = feed_stats(db, name, "is_current")
        sections.append(dict(
            name=name, slug=slug(name), stories=picked,
            quota=cfg.QUOTAS.get(name, len(picked)),
            feeds=feeds, pool=sum(f["in_window"] for f in feeds),
            broken=[f["name"] for f in feeds if f["error"]],
            over_cap=[f["name"] for f in feeds if f["over_cap"]]))

    page = render_template("front.html", sections=sections, total=len(drawn),
                           quota_total=sum(cfg.QUOTAS.values()),
                           section_quota=cfg.SECTION_QUOTA,
                           **masthead(db))
    db.close()
    return page


@app.route("/<page>.html")
def section_page(page):
    name = SLUGS.get(page)
    if not name:
        abort(404)
    db = connect()
    picked = stories(db, "section_slot", name)
    feeds = feed_stats(db, name, "section_slot")

    page = render_template(
        "section.html", name=name,
        stories=picked, quota=cfg.SECTION_QUOTA,
        front_quota=cfg.QUOTAS.get(name, 0),
        feeds=feeds, pool=sum(f["in_window"] for f in feeds),
        broken=[f["name"] for f in feeds if f["error"]],
        others=[dict(name=s, slug=slug(s)) for s in cfg.SECTIONS if s != name],
        **masthead(db))
    db.close()
    return page


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5051, debug=True)
