# NewsFront — Phase 2

A finite front page, plus a full page behind each section. Fixed slots, filled
by random draw from whatever the feeds are carrying. No ranking, no engagement
data, no personalization of any kind.

## Where it runs

The scheduled draw runs on **GitHub Actions**, not on any machine of Frank's:
`.github/workflows/edition.yml` pulls the feeds 4x/day, exports the page and
commits it. GitHub Pages serves `docs/`. Cost is zero — Actions
minutes are unlimited on a public repo, and this app uses no API keys at all.

`workflow_dispatch` is enabled, so the "Run workflow" button (including in the
GitHub phone app) forces an off-schedule draw.

Two things about that setup that are easy to get wrong:

* **The workflow stages `docs` with `-A`, not `docs/index.html`.** The export
  writes ten pages — the front page and one per section — and a section retired
  from `sources.py` has its stale page deleted, which only `-A` stages.
* **`news.db` is committed, and must stay that way.** Every run gets a fresh
  runner, so the database is the only thing carrying dedup history between
  editions. Ignore it and the paper repeats itself. `prune()` keeps it bounded
  at twice the longest recency window.
* **GitHub cron is UTC and has no DST handling**, so the four draw times drift
  by an hour in winter. They are also set to `:17` rather than the hour —
  GitHub delays scheduled jobs under load, and the top of the hour is worst.

Rejected: **PythonAnywhere's free tier cannot run this.** Its outbound access is
allowlisted — 38 of the 43 feed hosts are blocked, only the Google News ones get
through — and the free tier has **no scheduled tasks at all**, so nothing would
ever refresh. The cheapest plan that fixes both is Developer at $10/month.

## Running it locally

Pull the feeds and draw a new edition — the front page and every section page:

    ./venv/bin/python3 fetch.py

Serve them:

    ./venv/bin/python3 news.py     →  http://127.0.0.1:5051

The two are deliberately separate. `news.py` never touches the network — it only
reads what `fetch.py` wrote — so the pages still serve when a feed is slow or
down, and the scheduled pull runs as its own process (GitHub Actions now, cron
on the Mac mini later).

Write the static copy the way GitHub Pages serves it:

    ./venv/bin/python3 export.py   →  docs/index.html + one page per section

## Changing what appears

Everything editorial lives in `sources.py`:

* **`SOURCES`** — the feed list. `cap` is the most slots one source may fill in
  its section; `recency_hours` is how far back its articles stay eligible.
* **`QUOTAS`** — front-page slots per section.
* **`SECTION_QUOTA`** — slots on a section page. 10.
* **`DEDUP_DAYS`** — how long a shown article is suppressed. Front page only.

Edit, re-run `fetch.py`, reload. Nothing else needs touching.

## Files

| file | what it does |
|---|---|
| `sources.py` | the feed list and quotas — the file you edit |
| `fetch.py` | pulls feeds, picks the edition; run on a schedule |
| `news.py` | the Flask app; read-only |
| `store.py` | database connection, shared so the two can't drift |
| `schema.sql` | table definitions |
| `templates/front.html`, `templates/section.html`, `static/news.css` | the pages |
| `news.db` | SQLite; disposable — delete it and re-run `fetch.py` |

## Section pages: the same pool, dealt a different way

Every heading on the front page links to that section's own page — ten stories
instead of one to three, drawn from the same candidate pool. Locally they are
`http://127.0.0.1:5051/world.html`; on the site they are files in `docs/`.

**The front page's per-source cap cannot be reused here, and the fix is to
recognise what that cap actually is.** "Max 1 per source" is a single round of
dealing. A section page keeps dealing: one card per source per round, until ten
slots are full. Same anti-crowding property, and it degrades gracefully — a
source that runs out of candidates simply stops being dealt to.

The effect is the point. In Top News, Reuters and AP supply 152 of the 183
candidates and would take all ten slots if volume decided it; round-robin gives
NYT and Guardian US two or three each. The panel at the foot of every section
page draws both bars — share of the pool, and share of the slots — so the gap
between them is visible rather than asserted.

Three decisions worth keeping:

* **The order of the SOURCES is reshuffled every round**, not just the articles
  inside each source. Fixed, whichever source sorted first would take the
  earliest slot in every round — a ranking by source, wearing a shuffle.
* **Front-page picks are pinned to the top of their section page**, tagged "on
  the front page". Otherwise you tap a section after reading its headline and
  the headline is not there, which reads as a bug and not as randomness.
* **Nothing on a section page is marked as shown.** The dedup window exists so
  the *front* page does not repeat itself, and the front page is scarce enough
  for that to matter. A section page is where an article goes when it did *not*
  win a front-page slot, so suppressing it there costs supply and buys nothing;
  Italy would take the worst of it, at 46 candidates against 10 slots four times
  a day. This is why `fetch.py` prints a smaller candidate count for the front
  draw than the section draw on the same section — that difference *is* the
  dedup window.

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

Sport reaches the news sections through these feeds, and fixing it taught the
most useful thing about this route: **a trailing keyword does not filter, but a
path inside `site:` does.** `site:apnews.com+sports` happily returns a robotics
story about a "100m sprint and high jump"; `site:reuters.com/world` returns 64
items of real world news and no sport at all. So both Reuters feeds are now
path-scoped, and Top News went from three sports stories in ten slots to one.

**AP is the exception and cannot be fixed this way** — every AP path form returns
zero items, because its URLs are `apnews.com/article/<slug>` with no section in
them. AP therefore still leaks roughly one sports story onto Top News, which is
a deliberate trade (Frank's call, 2026-08-23): keeping AP's wire coverage is
worth the occasional stray game report. `WIRE_SPORT` catches about a third of it.

That filter is also a cautionary tale about **measuring recall, not just
precision**. It was swept for false positives first — 22 caught, every one
genuine sport — and reported as a fix. It was not: measured properly, by
fetching `site:reuters.com/sports` as **ground truth** and intersecting against
our pool, 38% of Reuters' Top News contribution was sport, and the keyword list
caught a third of it. Sports headlines are mostly team and athlete proper nouns,
which no word list covers. When a source publishes its own classification, use
that as the yardstick instead of a list you wrote.

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
