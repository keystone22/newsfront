# NewsFront — Phase 1

A finite front page. Fixed slots per section, filled by random draw from
whatever the feeds are carrying. No ranking, no engagement data, no
personalization of any kind.

## Running it

Pull the feeds and draw a new front page:

    ./venv/bin/python3 fetch.py

Serve the page:

    ./venv/bin/python3 news.py     →  http://127.0.0.1:5051

The two are deliberately separate. `news.py` never touches the network — it only
reads what `fetch.py` wrote — so the page still serves when a feed is slow or
down, and the scheduled pull can run as its own process (PythonAnywhere's
scheduled tasks now, cron on the Mac mini later).

## Changing what appears

Everything editorial lives in `sources.py`:

* **`SOURCES`** — the feed list. `cap` is the most slots one source may fill in
  its section; `recency_hours` is how far back its articles stay eligible.
* **`QUOTAS`** — front-page slots per section.
* **`DEDUP_DAYS`** — how long a shown article is suppressed.

Edit, re-run `fetch.py`, reload. Nothing else needs touching.

## Files

| file | what it does |
|---|---|
| `sources.py` | the feed list and quotas — the file you edit |
| `fetch.py` | pulls feeds, picks the edition; run on a schedule |
| `news.py` | the Flask app; read-only |
| `store.py` | database connection, shared so the two can't drift |
| `schema.sql` | table definitions |
| `templates/front.html`, `static/news.css` | the page |
| `news.db` | SQLite; disposable — delete it and re-run `fetch.py` |

## Wire services: AP and Reuters, via Google News, no API key

Their own RSS is gone — verified 2026-08-23, six URLs, all dead (recorded in
`WIRE_SERVICES_DEAD`). AP's answers **401 Unauthorized**: an authenticated,
paid feed.

They arrive instead through **Google News RSS filtered by source**, which is
free, needs no key, and carries no delay. Five sources: AP and Reuters in Top
News, AP World and Reuters World in World, AP Sports in Sports.

This beats both free API tiers, which was the surprise:

| route | cost | signup | delay | note |
|---|---|---|---|---|
| **Google News RSS** | free | none | **none** | links route via Google |
| NewsAPI.org free | free | key | **24 hours** | forbids non-development use |
| newsdata.io free | free | key | **12 hours** | 200 credits/day |
| NewsAPI.org Business | $449/mo | key | none | — |

A 12–24 hour delay is fatal for a page whose lead section is the day's news, so
the keyless route is also the fresher one. Three things to know about it:

* Google appends `" - AP News"` / `" - Reuters"` to every title; `clean_title`
  strips it when the source is Google News.
* Links point at `news.google.com` and redirect to the publisher **in the
  browser** (verified — a Reuters link resolved to the full article). There is
  no server-side redirect, so a click passes through Google. That is the price
  of not needing a key.
* `allinurl:` is **not supported** by Google News RSS — it returns zero items —
  so the per-topic split is a plain keyword and is therefore approximate.

Google News also mixes in non-articles: AP topic hub pages ("Ohio", "Formula
One", "Joe Biden") and Reuters stock-quote pages. `WIRE_JUNK` removes them,
measured at 45 of 336 wire rows. Its 1–3 word test applies **only** to the wire
sources — run globally it would have killed ten real headlines, among them
"Salmonella Is Everywhere" and "Betye Saar obituary".

## Notes from building it

* **Travel guides are filtered everywhere; daily news bulletins are not.**
  Frank's call, 2026-08-23. `TRAVEL` in `sources.py` applies to every source.
  It leans on two narrow signals -- a `/travel/` URL section, and an explicit
  phrase list -- because the Italy section is easy to over-filter. Bare "guide"
  is deliberately excluded from the list: it matches "A guide to the Italian
  health care system", which is the practical expat reporting that section is
  for. Verified against every article in hand: 3 dropped, all genuine travel
  guides, and "Tour de France" / "tourism sector" / the Euronews daily bulletin
  all survive.
* **Filter edits are retroactive.** `fetch.py` purges stored articles that the
  current patterns would now reject, so editing `sources.py` and re-running is
  enough -- no need to delete `news.db`.
* **Feeds die quietly, so verify before relying.** Of the spec's draft source
  list: the Boston Globe's only reachable feed last updated **May 2020**,
  National Geographic 404s, Wired's Ideas section stopped in June, and NYT
  Sports returns an empty feed. WBUR's spec URL serves an HTML page, not a feed
  — the real one is `wbur.org/feed`. All are left out or replaced; WBUR stands
  in for the Globe in Human Interest. The FT was dropped separately: its feeds
  work, but the content is paywalled and there is no subscription.
* **"Not modified" is not always a 304.** The Guardian answers a conditional
  GET with a **302 and an empty body**; feedparser recognises it and says so in
  `debug_message` ("The feed has not changed since you last checked"). Checking
  only for 304 made Guardian Life report as broken on every run after the
  first. If a feed reports empty but works when fetched by hand, check
  `debug_message` before assuming a network problem — two plausible-sounding
  theories (rate limiting, then retry logic) were both wrong here.
* **Be polite to hosts.** The list holds seven Guardian sections, five Google
  News queries and five NYT feeds. `HOST_DELAY` keeps 2 seconds between hits on
  the same host; different hosts are not delayed against each other, so a full
  pull still finishes in well under a minute. Euractiv answered 403 for ~40
  minutes after being fetched ~18 times in quick succession during testing,
  then recovered untouched — a feed failing once usually means nothing, and
  `read_feed` deliberately does NOT retry a 403/429, where retrying is what
  makes it worse.
* **Opinion is filtered globally.** A spec non-goal, and every general feed
  mixes it in — 12 rows of 929 from NYT, Guardian and Le Monde. Matched on the
  URL path segment (`/opinion/`, `/commentisfree/`), never on headline words,
  where "opinion poll" is a news story. `/analysis/` is deliberately allowed:
  reported analysis is not an op-ed.
* **`articles.section` is denormalised**, so moving a source between sections
  has to carry its existing articles with it. `seed_sources` does this on every
  run; without it those articles are stranded under a section name that no
  longer exists and silently stop being drawable.
* **Wired's main feed is 48% commerce** — coupon pages, product roundups and
  reviews, measured 2026-08-23. Phase 0 uses its Science section feed (5%)
  instead. `COMMERCE` in `sources.py` is the regex safety net for the rest.
* **Don't spoof a browser user agent.** Euractiv answers `403` to
  `Mozilla/...` and `200` to an honest `NewsFront/0.1`. Several publishers now
  reject fake browser strings while allowing declared bots.
* **Conditional GET matters.** `fetch.py` stores each feed's `etag` /
  `last_modified` and sends them back, so an unchanged feed answers `304` and
  costs nothing. This is also what keeps repeat pulls from tripping bot
  protection.
* **Politico publishes future-dated articles** — timestamps up to ~an hour
  ahead of the pull. Displayed as "just now" rather than a negative age.
* **A failed feed makes the page exceed a cap.** When a source doesn't answer,
  the remaining ones backfill past their cap so the page stays full; the panel
  at the foot of the page says so, in red, rather than hiding it.

## Section supply — resolved in Phase 1

Phase 0 ran 7 feeds across 3 sections and two of the three could not sustain
their quota at 4 pulls/day. Phase 1's 41 feeds fixed it: every section now
carries a candidate pool many times its daily consumption, the smallest being
Top News at 46 against 8/day. No section has come up short since.

Two levers did the work, and both are per-source settings in `sources.py`:
more sources per section, and **much wider recency windows on slow
publications**. Eurozine publishes about two essays a week — at the 48h window
it contributed literally nothing, and at 720h it carries ~19 candidates.

`fetch.py` exits nonzero **only** when a section could not be filled. A feed
erroring is routine -- publishers rate-limit, time out and move URLs -- and the
page already reports it in red, so it does not fail the scheduled run.
