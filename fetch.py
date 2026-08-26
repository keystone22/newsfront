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

then, for each SECTION PAGE, a second draw over the same pool: pin that
section's front-page picks, then deal the remaining slots one per source per
round until the quota is full. See draw_sections() for why it differs.

No ranking. No recency ordering. No click, view or engagement data is read or
written anywhere in this file.
"""
import re, random, sys, time
import datetime as dt
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import urllib.request
import urllib.error
import xml.etree.ElementTree as ET

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


_EN_RE      = re.compile(getattr(cfg, "EN_WORDS", r"(?!)"), re.I)
_FOREIGN_RE = re.compile(getattr(cfg, "FOREIGN_WORDS", r"(?!)"), re.I)
_FCHAR_RE   = re.compile(getattr(cfg, "FOREIGN_CHARS", r"(?!)"), re.I)
_LONGWORD_RE = re.compile(r"[^\W\d_]{13,}", re.UNICODE)


def looks_foreign(title):
    """Heuristic 'this headline is not in English', for multilingual sources.

    Only consulted for sources named in cfg.ENGLISH_ONLY -- never globally, and
    never for the Italian feeds, which are Italian deliberately.
    """
    words = [w for w in re.findall(r"[^\W\d_]+", title, re.UNICODE) if len(w) > 1]
    if len(words) < 3 or _EN_RE.search(title):
        return False
    return bool(_FCHAR_RE.search(title) or _FOREIGN_RE.search(title)
                or _LONGWORD_RE.search(title))


def clean_title(raw, from_google_news=False):
    """Feeds put HTML in titles. Strip tags; feedparser already un-escapes."""
    t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", raw or "")).strip()
    # Reuters prefixes every Breakingviews headline with the desk name, which
    # the source label beside the headline already says.
    t = re.sub(r"^breakingviews\s*[-\u2013\u2014]\s*", "", t, flags=re.I)
    if from_google_news:
        # No fallback to the original: a title that is ENTIRELY a publisher
        # suffix (" - AP News") is an empty-titled hub page, and returning ""
        # makes the caller skip it, which is what should happen.
        t = _GN_SUFFIX_RE.sub("", t).strip()
    return t


class _SitemapFeed:
    """A Google-News-sitemap parsed into something shaped like a feedparser result.

    Only enough of the interface for pull() to consume it unchanged: .entries,
    .bozo and .get(). Entries are plain dicts, which is fine -- pull() reads
    them with .get() exactly as it reads feedparser's.
    """

    def __init__(self, entries, status, etag=None):
        self.entries = entries
        self.bozo = 0
        self._d = {"status": status, "etag": etag, "modified": None}

    def get(self, k, default=None):
        return self._d.get(k, default)


_SM_NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9",
          "n": "http://www.google.com/schemas/sitemap-news/0.9"}


def read_sitemap(src):
    """Read a Google-News sitemap as if it were a feed.

    AP publishes NO usable RSS -- apnews.com/index.rss answers 401 and every
    other path 404s -- but its robots.txt advertises this sitemap explicitly and
    disallows nothing that touches it. It carries title, URL and publication
    date per article, which is exactly what a feed gives us; no article text is
    read, so this stays inside "link out only".

    Three things it does better than the Google News route it replaces:
    real apnews.com URLs, so the URL-PATH filters work (a Google News link is a
    redirect carrying no path at all); a <news:language> tag per article, which
    cleanly separates AP's Spanish service (151 of 468 articles) from the
    English one; and about three times the volume.
    """
    headers = {"User-Agent": UA}
    if src["etag"]:
        headers["If-None-Match"] = src["etag"]
    req = urllib.request.Request(src["endpoint"], headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            body, status, etag = resp.read(), resp.status, resp.headers.get("ETag")
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return None, "not modified"
        raise RuntimeError(f"HTTP {e.code}")

    root = ET.fromstring(body)
    entries = []
    for url in root.findall("s:url", _SM_NS):
        news = url.find("n:news", _SM_NS)
        if news is None:
            continue
        lang = news.findtext("n:publication/n:language", "", _SM_NS)
        # AP runs an English and a Spanish wire through one sitemap.
        if lang and not lang.startswith("en"):
            continue
        title = (news.findtext("n:title", "", _SM_NS) or "").strip()
        link = (url.findtext("s:loc", "", _SM_NS) or "").strip()
        pub = news.findtext("n:publication_date", "", _SM_NS)
        if not (title and link and pub):
            continue
        try:
            stamp = dt.datetime.fromisoformat(pub)
        except ValueError:
            continue
        entries.append({"title": title, "link": link,
                        "published_parsed": stamp.utctimetuple()})
    if not entries:
        raise RuntimeError("sitemap parsed but held no usable entries")
    return _SitemapFeed(entries, status, etag), None


def read_feed(src):
    """Fetch one feed, sending conditional-GET headers.

    Returns (feed, note). `feed` is None when the server said 304 Not Modified
    -- nothing changed since our last pull, which is a success, not a failure.
    """
    if src["type"] == "sitemap":
        _pace(src["endpoint"])
        return read_sitemap(src)

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
    for row in cfg.SOURCES:
        # The 7th element is the reader kind and defaults to 'rss', so every
        # existing six-element row keeps working untouched.
        name, section, endpoint, cap, recency, excl = row[:6]
        kind = row[6] if len(row) > 6 else "rss"
        db.execute("""
            INSERT INTO sources (name, type, endpoint, section, cap, recency_hours,
                                 exclude_pattern, active)
                 VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(name) DO UPDATE SET
                 type=excluded.type,
                 endpoint=excluded.endpoint, section=excluded.section,
                 cap=excluded.cap, recency_hours=excluded.recency_hours,
                 exclude_pattern=excluded.exclude_pattern, active=1
        """, (name, kind, endpoint, section, cap, recency, excl))
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

    # The language heuristic is not a regex, so it needs its own retroactive
    # sweep or rows imported before it existed would sit in the pool forever.
    for src in db.execute("SELECT id, name FROM sources"):
        if src["name"] not in getattr(cfg, "ENGLISH_ONLY", ()):
            continue
        doomed = [r["id"] for r in db.execute(
            "SELECT id, title FROM articles WHERE source_id=?", (src["id"],))
            if looks_foreign(r["title"])]
        for chunk in (doomed[i:i + 400] for i in range(0, len(doomed), 400)):
            db.execute(f"DELETE FROM articles WHERE id IN "
                       f"({','.join('?' * len(chunk))})", chunk)
            removed += len(chunk)

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
                if src["name"] in getattr(cfg, "ENGLISH_ONLY", ()) and looks_foreign(title):
                    stats["excluded"] += 1
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


def draw_sections(db):
    """Choose each SECTION page's slots. Returns a per-section audit.

    Deliberately different from draw() in three ways, each for a reason:

      * The cap becomes a ROUND-ROBIN. "Max 1 per source" cannot fill ten slots
        from four sources. Dealing one card per source per round is that same
        anti-crowding rule generalised -- the front page is simply a single
        round of it -- and it degrades gracefully, because a source that runs
        out just stops being dealt to while the others carry on.

      * The front page's picks are PINNED here. A reader who taps "World" after
        reading a World headline expects to find it on the page; one that
        silently drops the story they just read reads as a bug rather than as
        randomness. They keep their front-page order and lead the section.

      * Nothing is marked SHOWN. Dedup exists so the front page does not repeat
        itself, and the front page is scarce enough for that to matter. A
        section page is the overflow view -- the place an article goes when it
        did NOT win a front-page slot -- so suppressing it here because it
        appeared here yesterday would thin the page for no gain. Italy would
        take the worst of it: 46 candidates against 10 slots x 4 draws a day.

    No ranking here either. The only ordering is the pinned front-page picks,
    which were themselves drawn at random, and the page says so.
    """
    quota = cfg.SECTION_QUOTA
    db.execute("UPDATE articles SET section_slot = 0")
    audit = []

    for section in cfg.SECTIONS:
        rows = db.execute("""
            SELECT a.id, s.name AS source, a.is_current
              FROM articles a
              JOIN sources s ON s.id = a.source_id
             WHERE a.section = ?
               AND s.active = 1
               AND a.published_at >= datetime('now', '-' || s.recency_hours || ' hours')
          ORDER BY a.is_current
        """, (section,)).fetchall()
        source_of = {r["id"]: r["source"] for r in rows}

        # The front page's picks, keeping the order they hold there. Sliced to
        # the quota so a SECTION_QUOTA set below a front-page quota would give a
        # short page rather than an over-full one.
        taken = [r["id"] for r in rows if r["is_current"] > 0][:quota]
        pinned = len(taken)

        # Everything else, grouped by source, shuffled inside each group.
        queues = {}
        for r in rows:
            if r["is_current"] == 0:
                queues.setdefault(r["source"], []).append(r["id"])
        for q in queues.values():
            random.shuffle(q)

        # Deal one per source per round. The ORDER of the sources is reshuffled
        # every round: leaving it fixed would hand whichever source sorted first
        # the earliest slot in every round, which is a ranking by source.
        rounds = 0
        while len(taken) < quota and queues:
            rounds += 1
            for name in random.sample(list(queues), len(queues)):
                if len(taken) >= quota:
                    break
                taken.append(queues[name].pop())
                if not queues[name]:
                    del queues[name]

        for pos, aid in enumerate(taken, 1):
            db.execute("UPDATE articles SET section_slot=? WHERE id=?", (pos, aid))

        per_src = {}
        for aid in taken:
            per_src[source_of[aid]] = per_src.get(source_of[aid], 0) + 1

        audit.append(dict(section=section, pool=len(rows), quota=quota,
                          filled=len(taken), pinned=pinned, rounds=rounds,
                          by_source=per_src))
    db.commit()
    return audit


def headroom(db):
    """How many days of drawable articles each section is holding.

    Science starved on 2026-08-25 with no warning: its feeds made ~6.5
    articles/day while the front page ate 8, so the pool drained over two days
    and the draw came up short. Nothing reported the trend, only the failure.
    This does -- a section is flagged while there is still time to add a feed.

    Drawable means in its source's window AND not suppressed by dedup, i.e.
    exactly what draw() may choose from.
    """
    suppress = (now_utc() - dt.timedelta(days=cfg.DEDUP_DAYS)).date().isoformat()
    out = []
    for section, quota in cfg.QUOTAS.items():
        n = db.execute("""
            SELECT COUNT(*) FROM articles a JOIN sources s ON s.id = a.source_id
             WHERE a.section = ? AND s.active = 1
               AND a.published_at >= datetime('now','-'||s.recency_hours||' hours')
               AND (a.shown_date IS NULL OR a.shown_date < ?)
        """, (section, suppress)).fetchone()[0]
        per_day = quota * cfg.DRAWS_PER_DAY
        out.append(dict(section=section, drawable=n, per_day=per_day,
                        days=n / per_day if per_day else 0.0))
    return sorted(out, key=lambda r: r["days"])


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
            gap = a["quota"] - a["filled"]
            print(f"     !! {a['section']} short by {gap}")
            # GitHub Actions turns this into a warning annotation on the run, so
            # a thin section is visible in the Actions UI WITHOUT failing the
            # job. Failing it aborted export and commit, which meant one short
            # section stopped the whole paper from updating -- 16 headlines lost
            # to save 1. Reported, not fatal.
            print(f"::warning title=Section short::{a['section']} filled "
                  f"{a['filled']}/{a['quota']} -- its feeds are not keeping up "
                  f"with {a['quota']} picks x 4 draws a day")
            short += 1

    print("\ndraw sections")
    for a in draw_sections(db):
        caps = " ".join(f"{k}={v}" for k, v in sorted(a["by_source"].items()))
        print(f"     {a['section']:15s} {a['pool']:3d} candidates -> "
              f"{a['filled']}/{a['quota']}  ({a['pinned']} pinned, "
              f"{a['rounds']} rounds)   {caps}")
        # A short section PAGE is a supply fact, not a failure: it means the
        # pool genuinely holds fewer than SECTION_QUOTA. Only a short FRONT page
        # fails the run, because that is the page the schedule exists to fill.
        if a["filled"] < a["quota"]:
            print(f"     -- {a['section']} page holds {a['filled']}, pool is that thin")

    print("\nheadroom")
    for h in headroom(db):
        flag = "  !!" if h["days"] < cfg.HEADROOM_WARN_DAYS else "    "
        print(f"   {flag} {h['section']:15s} {h['drawable']:4d} drawable / "
              f"{h['per_day']:2d} per day = {h['days']:4.1f} days")
        if h["days"] < cfg.HEADROOM_WARN_DAYS:
            print(f"::warning title=Section running dry::{h['section']} has "
                  f"{h['drawable']} drawable article(s), {h['days']:.1f} days at "
                  f"{h['per_day']}/day. Add a feed or lower its quota.")

    db.close()
    # Always zero. A thin section is a CONTENT problem and is surfaced three
    # ways -- the warning annotation above, the count on the page, and the audit
    # panel. It is not a pipeline failure, and treating it as one is what made a
    # single short section blank the whole edition on 2026-08-25.
    return 0


if __name__ == "__main__":
    sys.exit(main())
