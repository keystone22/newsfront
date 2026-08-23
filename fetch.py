#!/usr/bin/env python3
"""Pull the feeds and draw a new front page.

Runs standalone, not inside Flask -- scheduled tasks (PythonAnywhere now, cron
on the Mac mini later) run as their own process. The web app never fetches; it
only reads what this wrote, so the site still serves if a feed is down.

    ./venv/bin/python3 fetch.py

The selection algorithm, in order:
  1. pull every active feed into a candidate pool
  2. keep only items inside that source's recency window
  3. drop anything shown in the last DEDUP_DAYS days (by normalised URL)
  4. cap each source's contribution to its section
  5. random sample within the capped pool to fill the section quota
  6. backfill from the same pool if the caps could not fill the quota

No ranking. No recency ordering. No click, view or engagement data is read or
written anywhere in this file.
"""
import re, random, sys, time
import datetime as dt
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import feedparser
import sources as cfg
from store import connect

# An honest user agent, deliberately not a spoofed browser string. Euractiv
# answers 403 to "Mozilla/..." and 200 to this one -- several publishers now
# reject fake browser UAs while allowing declared bots.
UA = "NewsFront/0.1 (personal news reader; single user)"
RETRIES = 3          # transient 5xx / timeouts
BACKOFF = 4          # seconds, doubled each retry

# Minimum gap between two requests to the SAME host. The feed list holds seven
# Guardian sections, five Google News queries and five NYT feeds, and firing
# them back to back is what made Guardian Life return an empty 302 and Euractiv
# answer 403 -- both served fine seconds later on their own. Polite pacing fixes
# at source what a retry can only paper over. Different hosts are not delayed
# against each other, so the whole pull still finishes well inside a minute.
HOST_DELAY = 2.0
_last_host_hit = {}


def _pace(url):
    """Sleep just long enough that this host is not hit too fast."""
    host = urlsplit(url).netloc.lower()
    wait = HOST_DELAY - (time.monotonic() - _last_host_hit.get(host, 0.0))
    if wait > 0:
        time.sleep(wait)
    _last_host_hit[host] = time.monotonic()

# Tracking parameters that vary between pulls of the same article. Left in the
# url_key they would defeat dedup entirely -- Politico appends utm_source to
# every link it publishes.
JUNK_PARAMS = re.compile(r"^(utm_|fbclid|gclid|mc_|ref|ref_src|cmpid|smid|partner)", re.I)


def now_utc():
    return dt.datetime.now(dt.timezone.utc)


def url_key(url):
    """Normalise a link so the same article is recognised across pulls."""
    s = urlsplit(url.strip())
    q = [(k, v) for k, v in parse_qsl(s.query, keep_blank_values=True)
         if not JUNK_PARAMS.match(k)]
    path = s.path.rstrip("/") or "/"
    return urlunsplit((s.scheme.lower() or "https", s.netloc.lower(), path,
                       urlencode(sorted(q)), ""))


# Google News appends " - <publisher>" to every headline it syndicates. On a
# page that already prints the source beside the headline that is pure
# duplication, so it comes off. Anchored to the end and requiring the dash to be
# space-separated, so a genuine hyphenated title keeps its own punctuation.
_GN_SUFFIX_RE = re.compile(r"\s+[-\u2013\u2014]\s+[^-\u2013\u2014]{2,40}$")


def clean_title(raw, from_google_news=False):
    """Feeds put HTML in titles. Strip tags; feedparser already un-escapes."""
    t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", raw or "")).strip()
    if from_google_news:
        # No fallback to the original: a title that is ENTIRELY a publisher
        # suffix (" - AP News") is an empty-titled hub page, and returning ""
        # makes the caller skip it, which is what should happen.
        t = _GN_SUFFIX_RE.sub("", t).strip()
    return t


def read_feed(src):
    """Fetch one feed, sending conditional-GET headers.

    Returns (feed, note). `feed` is None when the server said 304 Not Modified
    -- nothing changed since our last pull, which is a success, not a failure.
    """
    delay = BACKOFF
    for attempt in range(1, RETRIES + 1):
        _pace(src["endpoint"])
        feed = feedparser.parse(src["endpoint"], agent=UA,
                                etag=src["etag"], modified=src["last_modified"])
        status = feed.get("status")

        # "Not modified" is not always a 304. The Guardian answers a conditional
        # GET with a 302 and an empty body, which feedparser recognises and
        # explains in debug_message -- "The feed has not changed since you last
        # checked, so the server sent no data." Treating that as an error made
        # Guardian Life report as broken on every run after the first.
        unchanged = status == 304 or (
            not feed.entries
            and (src["etag"] or src["last_modified"])
            and "has not changed" in str(feed.get("debug_message", ""))
        )
        if unchanged:
            return None, "not modified"
        if feed.entries:
            return feed, None

        # 403/429 are the bot-protection answers; retrying immediately makes it
        # worse, so only retry what is plausibly transient. A success status
        # carrying zero entries counts: Guardian Life answered 302-with-nothing
        # once and served 54 items seconds later.
        transient = status is None or status >= 500 or status < 400
        if attempt < RETRIES and transient:
            time.sleep(delay)
            delay *= 2
            continue

        if status and status >= 400:
            raise RuntimeError(f"HTTP {status}")
        raise RuntimeError(type(feed.get("bozo_exception")).__name__
                           if feed.bozo else "no entries")
    raise RuntimeError("unreachable")


def seed_sources(db):
    """Upsert sources.py into the table. Idempotent, safe to run every pull."""
    for name, section, endpoint, cap, recency, excl in cfg.SOURCES:
        db.execute("""
            INSERT INTO sources (name, type, endpoint, section, cap, recency_hours,
                                 exclude_pattern, active)
                 VALUES (?, 'rss', ?, ?, ?, ?, ?, 1)
            ON CONFLICT(name) DO UPDATE SET
                 endpoint=excluded.endpoint, section=excluded.section,
                 cap=excluded.cap, recency_hours=excluded.recency_hours,
                 exclude_pattern=excluded.exclude_pattern, active=1
        """, (name, endpoint, section, cap, recency, excl))
    # Anything dropped from sources.py stops contributing but keeps its history.
    names = [s[0] for s in cfg.SOURCES]
    db.execute(f"UPDATE sources SET active=0 WHERE name NOT IN "
               f"({','.join('?' * len(names))})", names)

    # articles.section is denormalised off the source, so moving a source to a
    # different section has to carry its existing articles across -- otherwise
    # they are stranded under a section name that no longer exists and quietly
    # stop being drawable.
    moved = db.execute("""
        UPDATE articles SET section = (SELECT section FROM sources
                                        WHERE sources.id = articles.source_id)
         WHERE section IS NOT (SELECT section FROM sources
                                WHERE sources.id = articles.source_id)
    """).rowcount
    db.commit()
    return moved


def purge_excluded(db):
    """Drop stored articles that the current filters would now reject.

    Filters are applied when an article arrives, so without this an article
    imported before you added a pattern would stay eligible forever. Running it
    on every pull is what makes editing sources.py take effect immediately,
    which is what "edit, re-run fetch.py, reload" in the README promises.
    """
    removed = 0
    for src in db.execute("SELECT id, exclude_pattern FROM sources"):
        pats = [p for p in (getattr(cfg, "GLOBAL_EXCLUDE", None),
                            src["exclude_pattern"]) if p]
        if not pats:
            continue
        rx = re.compile("|".join(f"(?:{p})" for p in pats), re.I)
        doomed = [r["id"] for r in db.execute(
            "SELECT id, title, url FROM articles WHERE source_id=?", (src["id"],))
            if rx.search(r["title"]) or rx.search(r["url"])]
        for chunk in (doomed[i:i + 400] for i in range(0, len(doomed), 400)):
            db.execute(f"DELETE FROM articles WHERE id IN "
                       f"({','.join('?' * len(chunk))})", chunk)
            removed += len(chunk)
    db.commit()
    return removed


def prune(db):
    """Drop articles older than anything could still draw.

    The database is committed alongside the code so dedup history survives
    between scheduled runs, which means it has to stay bounded. Keeping twice
    the longest recency window leaves every drawable article plus a wide margin
    for the dedup lookback.
    """
    keep_h = max(s[4] for s in cfg.SOURCES) * 2
    n = db.execute("""
        DELETE FROM articles
         WHERE published_at < datetime('now', ?)
           AND (shown_date IS NULL OR shown_date < date('now', ?))
    """, (f"-{keep_h} hours", f"-{cfg.DEDUP_DAYS} days")).rowcount
    db.commit()
    return n


def pull(db):
    """Fetch every active source into `articles`. Returns a per-source report."""
    report, fetched = [], now_utc()
    for src in db.execute("SELECT * FROM sources WHERE active=1 ORDER BY id"):
        # A source's own filter plus the global one (travel guides), so a
        # source needs no per-source rule to get the shared exclusions.
        pats = [p for p in (getattr(cfg, "GLOBAL_EXCLUDE", None), src["exclude_pattern"]) if p]
        excl = re.compile("|".join(f"(?:{p})" for p in pats), re.I) if pats else None
        cutoff = fetched - dt.timedelta(hours=src["recency_hours"])
        stats = dict(name=src["name"], seen=0, fresh=0, excluded=0,
                     undated=0, new=0, error=None, note=None)
        try:
            feed, note = read_feed(src)
            if feed is None:                   # 304: nothing new since last pull
                stats["note"] = note
                db.execute("UPDATE sources SET last_success=?, last_error=NULL WHERE id=?",
                           (fetched.isoformat(), src["id"]))
                report.append(stats)
                continue

            db.execute("UPDATE sources SET etag=?, last_modified=? WHERE id=?",
                       (feed.get("etag"), feed.get("modified"), src["id"]))

            is_gn = "news.google.com" in src["endpoint"]
            for e in feed.entries:
                stats["seen"] += 1
                title = clean_title(e.get("title"), from_google_news=is_gn)
                link = (e.get("link") or "").strip()
                if not title or not link:
                    continue

                stamp = e.get("published_parsed") or e.get("updated_parsed")
                if not stamp:
                    stats["undated"] += 1          # cannot be windowed; skip
                    continue
                pub = dt.datetime.fromtimestamp(time.mktime(stamp), dt.timezone.utc)
                if pub < cutoff:
                    continue
                stats["fresh"] += 1

                if excl and (excl.search(title) or excl.search(link)):
                    stats["excluded"] += 1
                    continue

                cur = db.execute("""
                    INSERT OR IGNORE INTO articles
                        (source_id, section, title, url, url_key, published_at, fetched_at)
                    VALUES (?,?,?,?,?,?,?)
                """, (src["id"], src["section"], title, link, url_key(link),
                      pub.isoformat(), fetched.isoformat()))
                stats["new"] += cur.rowcount
            db.execute("UPDATE sources SET last_success=?, last_error=NULL WHERE id=?",
                       (fetched.isoformat(), src["id"]))
        except Exception as ex:
            stats["error"] = f"{ex}"[:120]
            db.execute("UPDATE sources SET last_error=? WHERE id=?",
                       (stats["error"], src["id"]))
        report.append(stats)
    db.commit()
    return report


def draw(db):
    """Choose this edition's front page. Returns a per-section audit."""
    drawn = now_utc()
    today = drawn.date().isoformat()
    suppress = (drawn - dt.timedelta(days=cfg.DEDUP_DAYS)).date().isoformat()

    db.execute("UPDATE articles SET is_current = 0")
    audit = []

    for section, quota in cfg.QUOTAS.items():
        # Candidates: inside their own source's window, and not recently shown.
        rows = db.execute("""
            SELECT a.id, a.title, s.name AS source, s.cap, a.published_at
              FROM articles a
              JOIN sources s ON s.id = a.source_id
             WHERE a.section = ?
               AND s.active = 1
               AND a.published_at >= datetime('now', '-' || s.recency_hours || ' hours')
               AND (a.shown_date IS NULL OR a.shown_date < ?)
        """, (section, suppress)).fetchall()

        pool = [dict(r) for r in rows]
        random.shuffle(pool)                       # step 5: random, not ranked

        taken, per_src = [], {}
        for a in pool:                             # step 4: honour the cap
            if len(taken) >= quota:
                break
            if per_src.get(a["source"], 0) >= a["cap"]:
                continue
            per_src[a["source"]] = per_src.get(a["source"], 0) + 1
            taken.append(a)

        capped_fill = len(taken)
        chosen = {a["id"] for a in taken}
        for a in pool:                             # step 6: backfill past the cap
            if len(taken) >= quota:
                break
            if a["id"] not in chosen:
                taken.append(a)
                chosen.add(a["id"])

        for pos, a in enumerate(taken, 1):
            db.execute("UPDATE articles SET is_current=?, shown_date=? WHERE id=?",
                       (pos, today, a["id"]))

        audit.append(dict(section=section, pool=len(pool), quota=quota,
                          filled=len(taken), backfilled=len(taken) - capped_fill,
                          by_source=per_src))
    db.commit()
    return audit


def main():
    db = connect()
    moved = seed_sources(db)
    if moved:
        print(f"moved {moved} article(s) to a source's new section")

    pruned = prune(db)
    if pruned:
        print(f"prune {pruned} article(s) past the retention window")

    purged = purge_excluded(db)
    if purged:
        print(f"purge {purged} stored article(s) now match the filters")

    print(f"pull  {now_utc().isoformat(timespec='seconds')}")
    problems = 0
    for s in pull(db):
        if s["error"]:
            print(f"  !! {s['name']:16s} {s['error']}")
            problems += 1
        elif s["note"]:
            print(f"     {s['name']:16s} {s['note']}")
        else:
            extra = []
            if s["excluded"]:
                extra.append(f"{s['excluded']} filtered")
            if s["undated"]:
                extra.append(f"{s['undated']} undated")
            print(f"     {s['name']:16s} {s['seen']:3d} seen  {s['fresh']:3d} in window"
                  f"  {s['new']:3d} new" + ("   " + ", ".join(extra) if extra else ""))

    print("\ndraw")
    short = 0
    for a in draw(db):
        note = f"  ({a['backfilled']} past cap)" if a["backfilled"] else ""
        caps = " ".join(f"{k}={v}" for k, v in a["by_source"].items())
        print(f"     {a['section']:15s} {a['pool']:3d} candidates -> "
              f"{a['filled']}/{a['quota']}{note}   {caps}")
        if a["filled"] < a["quota"]:
            print(f"     !! {a['section']} short by {a['quota'] - a['filled']}")
            short += 1

    db.close()
    return 1 if short else 0


if __name__ == "__main__":
    sys.exit(main())
