#!/usr/bin/env python3
"""NewsFront -- the front page.

Read-only. Everything on screen was written by fetch.py; this app never calls
out to a feed, so the page still serves when a source is down or slow.

    ./venv/bin/python3 news.py     ->  http://127.0.0.1:5051
"""
import datetime as dt
from zoneinfo import ZoneInfo

from flask import Flask, render_template

import sources as cfg
from store import connect

LOCAL = ZoneInfo("America/New_York")

app = Flask(__name__)


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


@app.route("/")
def front():
    db = connect()

    rows = db.execute("""
        SELECT a.title, a.url, a.published_at, a.section, a.is_current AS position,
               s.name AS source
          FROM articles a
          JOIN sources s ON s.id = a.source_id
         WHERE a.is_current > 0
      ORDER BY a.is_current
    """).fetchall()

    sections = []
    for name in cfg.SECTIONS:
        stories = [dict(title=r["title"], url=r["url"], source=r["source"],
                        age=age_label(r["published_at"]))
                   for r in rows if r["section"] == name]

        # Live candidate counts, so the balance is auditable on the page itself.
        caps = db.execute("""
            SELECT s.name, s.cap, s.last_error,
                   COUNT(a.id) FILTER (
                       WHERE a.published_at >= datetime('now','-'||s.recency_hours||' hours')
                   ) AS in_window,
                   COUNT(a.id) FILTER (WHERE a.is_current > 0) AS on_page
              FROM sources s
              LEFT JOIN articles a ON a.source_id = s.id
             WHERE s.section = ? AND s.active = 1
          GROUP BY s.id ORDER BY in_window DESC
        """, (name,)).fetchall()

        feeds = [dict(name=c["name"], cap=c["cap"], in_window=c["in_window"],
                      on_page=c["on_page"], error=c["last_error"],
                      over_cap=c["on_page"] > c["cap"]) for c in caps]
        pool = sum(f["in_window"] for f in feeds) or 1
        for f in feeds:
            f["share"] = round(f["in_window"] * 100 / pool, 1)

        sections.append(dict(name=name, stories=stories,
                             quota=cfg.QUOTAS.get(name, len(stories)),
                             feeds=feeds, pool=sum(f["in_window"] for f in feeds),
                             broken=[f["name"] for f in feeds if f["error"]],
                             over_cap=[f["name"] for f in feeds if f["over_cap"]]))

    last = db.execute("SELECT MAX(fetched_at) AS t FROM articles").fetchone()["t"]
    db.close()

    drawn = to_local(last)
    return render_template(
        "front.html",
        sections=sections,
        drawn=drawn.strftime("%-d %b %-I:%M %p") if drawn else None,
        total=len(rows),
        quota_total=sum(cfg.QUOTAS.values()),
        section_count=len(cfg.SECTIONS),
        window=f"{min(s[4] for s in cfg.SOURCES)}-{max(s[4] for s in cfg.SOURCES)}",
        source_count=len(cfg.SOURCES),
        phase=getattr(cfg, 'PHASE', 0),
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5051, debug=True)
