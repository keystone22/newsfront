"""The 70/30 trial: news sections drawn through an editorial gate. NOT LIVE.

Frank, 2026-09-13: the page lacks a strong editorial direction. The proposal on
trial is that about TRIAL_SHARE of each TRIAL_SECTIONS section's slots go to
stories that at least TRIAL_MIN_NEWSROOMS newsrooms are running at the same
time, and the rest stay the ordinary random draw.

How that squares with "no ranking": the newsroom count is used only as a GATE.
A story is in or it is out; among the stories that are in, the draw is random,
and a story on six newsrooms is no likelier to be picked than one on two. This
is the same move CLAUDE.md already makes when it prefers a newsroom's own front
page -- the editorial judgement happens before the pool, never inside the draw.

A story qualifies for a section only if that section's OWN feeds carry it; the
other newsrooms' copies count as votes, not as candidates. AP (a SIGNALS source)
votes everywhere and may be the version drawn, but never makes a story qualify.

Nothing here WRITES. The pages are rendered from what fetch.py stored; the live
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
BRANDS = ("Al Jazeera", "France 24", "Guardian", "Reuters", "Euronews", "Le Monde",
          "Politico", "DW", "NYT", "BBC", "NPR", "AP")

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
    """Draw a trial front page and section page for every TRIAL_SECTIONS section."""
    votes = cfg.TRIAL_VOTE_SECTIONS
    rows = _tag([dict(r) for r in db.execute(f"""
        SELECT a.id, a.title, a.url, a.published_at, a.section, s.name AS source
          FROM articles a JOIN sources s ON s.id = a.source_id
         WHERE s.active = 1 AND a.section IN ({','.join('?' * len(votes))})
           AND a.published_at >= datetime('now', '-' || MIN(s.recency_hours, ?) || ' hours')
      ORDER BY a.id
    """, (*votes, cfg.TRIAL_VOTE_HOURS))])

    stories = []
    for members in group(rows):
        brands = sorted({m["brand"] for m in members})
        if len(brands) >= cfg.TRIAL_MIN_NEWSROOMS:
            stories.append(dict(brands=brands, members=members, drawn_for=None,
                                event=[m["stems"] for m in members]))
    qualifying = {s: [g for g in stories if any(m["section"] == s for m in g["members"])]
                  for s in cfg.TRIAL_SECTIONS}

    # Narrowest first, so the small sections can fill. Ties broken at random.
    order = sorted(cfg.TRIAL_SECTIONS, key=lambda s: (len(qualifying[s]), random.random()))

    suppress = (dt.datetime.now(dt.timezone.utc)
                - dt.timedelta(days=cfg.DEDUP_DAYS)).date().isoformat()
    everywhere = []                    # every pick on every trial page

    def same_event(stem_sets):
        return any(overlap(a, b) >= cfg.TRIAL_SAME_EVENT
                   for p in everywhere for a in p["event"] for b in stem_sets)

    out = {}
    for section in order:
        # The ordinary pool, exactly as fetch.draw() sees it.
        pool = _tag([dict(r) for r in db.execute("""
            SELECT a.id, a.title, a.url, a.published_at, s.name AS source
              FROM articles a JOIN sources s ON s.id = a.source_id
             WHERE a.section = ? AND s.active = 1
               AND a.published_at >= datetime('now', '-' || s.recency_hours || ' hours')
               AND (a.shown_date IS NULL OR a.shown_date < ?)
        """, (section, suppress))])
        candidates = list(qualifying[section])
        random.shuffle(candidates)
        random.shuffle(pool)
        picks = []

        def add(card):
            picks.append(card)
            everywhere.append(card)

        def fill(quota):
            want_gate = round(quota * cfg.TRIAL_SHARE)
            start = len(picks)
            for g in candidates:
                if len(picks) >= quota or sum(p["kind"] == "gate" for p in picks) >= want_gate:
                    break
                if g["drawn_for"] or same_event(g["event"]):
                    continue
                drawable = [m for m in g["members"] if m["section"] in (section, cfg.SIGNALS)]
                used = {p["brand"] for p in picks}
                art = random.choice([m for m in drawable if m["brand"] not in used] or drawable)
                g["drawn_for"] = section
                add(dict(art, kind="gate", brands=g["brands"], event=g["event"]))
            # The rest is the ordinary draw: one per newsroom first, then backfill.
            for honour_cap in (True, False):
                for a in pool:
                    if len(picks) >= quota:
                        break
                    if a["id"] in {p["id"] for p in everywhere} or same_event([a["stems"]]):
                        continue
                    if honour_cap and a["brand"] in {p["brand"] for p in picks}:
                        continue
                    add(dict(a, kind="random", brands=[], event=[a["stems"]]))
            new = picks[start:]
            random.shuffle(new)        # order on the page says nothing
            picks[start:] = new

        fill(cfg.QUOTAS[section])
        front = list(picks)
        fill(cfg.SECTION_QUOTA)        # front picks stay pinned, as on the live page
        out[section] = dict(
            front=front, section=list(picks),
            front_gate=sum(p["kind"] == "gate" for p in front),
            section_gate=sum(p["kind"] == "gate" for p in picks),
            front_quota=cfg.QUOTAS[section], section_quota=cfg.SECTION_QUOTA,
            pool=len(pool), dealt=order.index(section) + 1)

    # Listed with the section's OWN copy of each story, now that every draw is done.
    for section in cfg.TRIAL_SECTIONS:
        listed = []
        for g in qualifying[section]:
            own = next(m for m in g["members"] if m["section"] == section)
            listed.append(dict(brands=g["brands"], title=own["title"], url=own["url"],
                               drawn_for=g["drawn_for"]))
        out[section]["qualifying"] = sorted(listed, key=lambda s: s["title"].lower())

    return dict(
        sections=out, order=order,
        share_pct=round(cfg.TRIAL_SHARE * 100), min_newsrooms=cfg.TRIAL_MIN_NEWSROOMS,
        vote_hours=cfg.TRIAL_VOTE_HOURS,
        headlines=len(rows), newsrooms=sorted({r["brand"] for r in rows}))
