"""The 70/30 trial: Top News drawn through an editorial gate. NOT LIVE.

Frank, 2026-09-13: the page lacks a strong editorial direction. The proposal on
trial is that about TRIAL_SHARE of Top News slots go to stories that at least
TRIAL_MIN_NEWSROOMS newsrooms are running at the same time, and the rest stay
the ordinary random draw.

How that squares with "no ranking": the newsroom count is used only as a GATE.
A story is in or it is out; among the stories that are in, the draw is random,
and a story on six newsrooms is no likelier to be picked than one on two. This
is the same move CLAUDE.md already makes when it prefers a newsroom's own front
page -- the editorial judgement happens before the pool, never inside the draw.

Nothing here WRITES. The page is rendered from what fetch.py stored; the live
draw, its dedup and shown_date are untouched. That also means the trial keeps
no memory between editions, so a big story can recur edition after edition --
a live version would apply the same DEDUP_DAYS as the front page.
"""
import datetime as dt
import random
import re

import sources as cfg

# A newsroom is the BRAND, not the feed: "NYT World" and "NYT" are one editor's
# judgement, so they are one vote. Matched as a name prefix.
BRANDS = ("Al Jazeera", "France 24", "Guardian", "Reuters", "Euronews",
          "NYT", "BBC", "NPR", "AP")

STOP = set("""the a an of to in on for and or is are was were be by with at from
as after over into its it his her their new says said say amid than more who what
how why not has have will first two one""".split())


def brand(source):
    return next((b for b in BRANDS if source.startswith(b)), source.split()[0])


def stems(title):
    """Content words cut to 5 letters, so "Swedes" meets "Sweden"."""
    return frozenset(w[:5] for w in re.findall(r"[a-z]{3,}", title.lower())
                     if w not in STOP)


def overlap(a, b):
    return len(a & b) / len(a | b) if a and b else 0.0


def group(rows):
    """Each headline gathers the unclaimed headlines from OTHER newsrooms that
    directly resemble it. Deliberately no chaining through intermediaries: a
    chained version was tried and merged unrelated stories into one."""
    groups, claimed = [], set()
    for i, r in enumerate(rows):
        if i in claimed:
            continue
        members = [i] + [j for j in range(i + 1, len(rows))
                         if j not in claimed and rows[j]["brand"] != r["brand"]
                         and overlap(r["stems"], rows[j]["stems"]) >= cfg.TRIAL_MATCH]
        claimed.update(members)
        groups.append([rows[k] for k in members])
    return groups


def _tag(rows):
    for r in rows:
        r["brand"], r["stems"] = brand(r["source"]), stems(r["title"])
    return rows


def run(db):
    """Draw a trial front page (3) and section page (10) for Top News."""
    votes = cfg.TRIAL_VOTE_SECTIONS
    rows = _tag([dict(r) for r in db.execute(f"""
        SELECT a.id, a.title, a.url, a.published_at, a.section, s.name AS source
          FROM articles a JOIN sources s ON s.id = a.source_id
         WHERE s.active = 1 AND a.section IN ({','.join('?' * len(votes))})
           AND a.published_at >= datetime('now', '-' || MIN(s.recency_hours, ?) || ' hours')
      ORDER BY a.id
    """, (*votes, cfg.TRIAL_VOTE_HOURS))])

    # A story qualifies on its newsroom count, but only Top News copy (or a
    # signal's) may be DRAWN for it -- a story only World feeds carry is World's.
    stories = []
    for members in group(rows):
        brands = sorted({m["brand"] for m in members})
        drawable = [m for m in members if m["section"] in ("Top News", cfg.SIGNALS)]
        if len(brands) >= cfg.TRIAL_MIN_NEWSROOMS and drawable:
            stories.append(dict(brands=brands, members=members, drawable=drawable,
                                drawn=False))

    # The ordinary pool, exactly as fetch.draw() sees it.
    suppress = (dt.datetime.now(dt.timezone.utc)
                - dt.timedelta(days=cfg.DEDUP_DAYS)).date().isoformat()
    pool = _tag([dict(r) for r in db.execute("""
        SELECT a.id, a.title, a.url, a.published_at, s.name AS source
          FROM articles a JOIN sources s ON s.id = a.source_id
         WHERE a.section = 'Top News' AND s.active = 1
           AND a.published_at >= datetime('now', '-' || s.recency_hours || ' hours')
           AND (a.shown_date IS NULL OR a.shown_date < ?)
    """, (suppress,))])

    random.shuffle(stories)
    random.shuffle(pool)
    picks = []

    def same_event(stem_sets):
        return any(overlap(a, b) >= cfg.TRIAL_SAME_EVENT
                   for p in picks for a in p["event"] for b in stem_sets)

    def fill(quota):
        want_gate = round(quota * cfg.TRIAL_SHARE)
        start = len(picks)
        for s in stories:
            if len(picks) >= quota or sum(p["kind"] == "gate" for p in picks) >= want_gate:
                break
            if s["drawn"] or same_event([m["stems"] for m in s["members"]]):
                continue
            used = {p["brand"] for p in picks}
            art = random.choice([m for m in s["drawable"] if m["brand"] not in used]
                                or s["drawable"])
            s["drawn"] = True
            picks.append(dict(art, kind="gate", brands=s["brands"],
                              event=[m["stems"] for m in s["members"]]))
        # The rest is the ordinary draw: one per newsroom first, then backfill.
        for honour_cap in (True, False):
            for a in pool:
                if len(picks) >= quota:
                    break
                taken = {p["id"] for p in picks}
                if a["id"] in taken or same_event([a["stems"]]):
                    continue
                if honour_cap and a["brand"] in {p["brand"] for p in picks}:
                    continue
                picks.append(dict(a, kind="random", brands=[], event=[a["stems"]]))
        new = picks[start:]
        random.shuffle(new)            # order on the page says nothing
        picks[start:] = new

    fill(cfg.QUOTAS["Top News"])
    front = list(picks)
    fill(cfg.SECTION_QUOTA)            # front picks stay pinned, as on the live page

    def count(cards, kind):
        return sum(c["kind"] == kind for c in cards)

    return dict(
        front=front, section=list(picks),
        front_gate=count(front, "gate"), section_gate=count(picks, "gate"),
        front_quota=cfg.QUOTAS["Top News"], section_quota=cfg.SECTION_QUOTA,
        share_pct=round(cfg.TRIAL_SHARE * 100), min_newsrooms=cfg.TRIAL_MIN_NEWSROOMS,
        vote_hours=cfg.TRIAL_VOTE_HOURS,
        headlines=len(rows), newsrooms=sorted({r["brand"] for r in rows}),
        qualifying=sorted(stories, key=lambda s: s["drawable"][0]["title"].lower()),
        pool=len(pool))
