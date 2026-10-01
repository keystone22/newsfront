#!/usr/bin/env python3
"""The NYT print edition, read through the Article Search API. PROBE STAGE.

NYT's Today's Paper page answers 403 to an honest user agent (2026-10-01), and
this project never poses as a browser. The Article Search API is NYT's own
sanctioned route: each article carries print_section and print_page, so page 1
of section A is the printed front page, and page 1 of every other section is
that desk's own front. Free key, 5 calls a minute, 500 a day. The key lives in
the GitHub secret NYT_API_KEY and nowhere else -- not in the repo, not locally.

    python nyt_print.py      # with NYT_API_KEY set: prints a summary, writes nothing

Run by .github/workflows/nyt-probe.yml, by hand, until it is known what each
print section holds and which NewsFront section it should feed.
"""
import collections
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.nytimes.com/svc/search/v2/articlesearch.json"
UA = "NewsFront/0.1"
PAUSE = 13            # seconds between calls; the limit is 5 a minute


def search(key, **params):
    """One Article Search call. Errors carry the status and NYT's message,
    never the request URL, which holds the key."""
    params["api-key"] = key
    req = urllib.request.Request(f"{API}?{urllib.parse.urlencode(params)}",
                                 headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read()[:300].decode('utf-8', 'replace')}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"network: {e.reason}") from None


def unpack(payload):
    """(docs, hits) -- tolerant of both the older 'meta' and newer 'metadata'."""
    r = payload.get("response") or {}
    meta = r.get("meta") or r.get("metadata") or {}
    return r.get("docs") or [], meta.get("hits")


def headline(doc):
    h = doc.get("headline") or {}
    return (h.get("print_headline") or h.get("main") or "").strip()


def collect(key, pages, **params):
    """Up to `pages` pages of 10 docs. Returns (docs, hits, error)."""
    docs, hits = [], None
    for page in range(pages):
        if page:
            time.sleep(PAUSE)
        try:
            got, hits = unpack(search(key, page=page, **params))
        except RuntimeError as e:
            return docs, hits, str(e)
        docs += got
        if len(got) < 10:
            break
    return docs, hits, None


def summarise(label, docs):
    """Print-field facts about a set of articles, read client-side.

    The first probe's fq filters (_exists_:print_section, print_page:1) matched
    NOTHING with no error, while the same day held 169 articles -- so the print
    fields are read from each article here instead of trusting a filter."""
    have = [d for d in docs if d.get("print_section") or d.get("print_page")]
    print(f"\n{label}: {len(docs)} articles read, {len(have)} carry print fields")
    by = collections.defaultdict(collections.Counter)
    for doc in have:
        by[str(doc.get("print_section") or "?")][doc.get("section_name") or "?"] += 1
    for sec in sorted(by):
        print(f"  {sec:>4}  {sum(by[sec].values()):3}  web desks: {dict(by[sec].most_common(4))}")
    fronts = [d for d in have if str(d.get("print_page")) == "1"]
    print(f"  section fronts (print_page 1): {len(fronts)}")
    for doc in sorted(fronts, key=lambda x: str(x.get("print_section"))):
        print(f"    {str(doc.get('print_section')):>4} {(doc.get('section_name') or '?')[:12]:12} "
              f"{headline(doc)[:80]}")


def main():
    key = os.environ.get("NYT_API_KEY")
    if not key:
        print("NYT_API_KEY is not set -- nothing to do")
        return 1

    # Articles published online YESTERDAY are the bulk of THIS MORNING's paper.
    # A day four back is sampled too, in case print placement is filled in late.
    now = dt.datetime.now(dt.timezone.utc).date()
    day1, day4 = now - dt.timedelta(days=1), now - dt.timedelta(days=4)

    d = day1.strftime("%Y%m%d")
    docs1, hits1, err = collect(key, 20, begin_date=d, end_date=d)
    print(f"NYT articles published {day1} (UTC): hits={hits1}, read {len(docs1)}  {err or ''}")
    if docs1:
        print("  fields on an article:", sorted(docs1[0].keys()))
        print("  print fields, first 8:", [(x.get("print_section"), x.get("print_page")) for x in docs1[:8]])
    summarise(f"Published {day1}", docs1)

    time.sleep(PAUSE)
    d = day4.strftime("%Y%m%d")
    docs4, hits4, err = collect(key, 3, begin_date=d, end_date=d)
    print(f"\nNYT articles published {day4} (UTC): hits={hits4}, read {len(docs4)}  {err or ''}")
    summarise(f"Published {day4} (first {len(docs4)})", docs4)
    return 0


if __name__ == "__main__":
    sys.exit(main())
