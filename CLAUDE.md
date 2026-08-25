# NewsFront — Claude Code Project Guide

Read **README.md** first; it holds the operational detail and the measured
findings behind every rule below.

A personal, deliberately **anti-algorithmic** front page. Fixed slots per
section, filled by **random draw** from a per-source-capped pool. Frank is not a
programmer — prefer incremental edits, show changes in context, and explain
trade-offs in plain language.

## The design rule that outranks everything

**No ranking, no engagement data, no personalization.** Not by recency, not by
popularity, not by relevance. If a change would make one story more likely to
appear than another for any reason other than its source's cap and window, it is
wrong even if it would "improve" the page. "Shown" is tracked *only* for dedup.

Corollary: **show facts, never signals.** The audit panel explains how the draw
happened; it does not recommend anything.

## Layout

| file | role |
|---|---|
| `sources.py` | feed list, caps, windows, quotas, filters — **the only file to edit for editorial changes** |
| `fetch.py` | pulls feeds, prunes, filters, draws an edition. Runs standalone on a schedule |
| `news.py` | Flask app; **read-only, never touches the network** |
| `export.py` | renders the current edition to 10 self-contained files in `docs/` |
| `store.py` | DB connection + migrations, shared so fetch and news cannot drift |

Port **5051** (FinanceHub owns 5050). `news.db` is disposable *locally* — but
see the deployment rule below, where it is not.

## Rules learned the hard way — do not re-derive these

- **`news.db` is committed to git and must stay that way.** Every scheduled run
  gets a fresh runner, so the database is the only thing carrying dedup history
  between editions. Ignore it and the paper silently repeats itself.
- **Never apply the wire `1-3 word` junk filter globally.** It is scoped to the
  Google News sources on purpose. Measured against the other feeds it would kill
  real headlines — "Salmonella Is Everywhere", "Betye Saar obituary".
- **"Not modified" is not always a 304.** The Guardian answers a conditional GET
  with a **302 and an empty body**; feedparser explains it in `debug_message`.
  Checking only for 304 made Guardian Life look broken on every run.
- **Do not spoof a browser user agent.** Euractiv returns 403 to `Mozilla/...`
  and 200 to an honest `NewsFront/0.1`. Several publishers now work this way.
- **`articles.section` is denormalised.** Moving a source to another section must
  carry its existing articles across, or they strand under a dead section name.
  `seed_sources` does this; don't remove it.
- **Opinion is filtered on the URL path**, never on headline words — "opinion
  poll" is a news story. `/analysis/` is deliberately allowed: reported analysis
  is not an op-ed, and that is the distinction the spec draws.
- **Corriere della Sera's RSS is DEAD** — frozen at 13 May 2024, all sections,
  while still answering 200 with 69 items. Don't re-test it. Same class of trap
  as the Boston Globe feed below.
- **Compare a source's WINDOW to its actual pace.** ANSA English publishes in
  batches ~2 days behind the Italian service, so its 48h window sat on the newest
  item and the source contributed nothing at all until widened to 168h. The
  Florentine needs 720h for the same reason. A silent source is usually this,
  not a broken feed.
- **Verify a feed before relying on it.** From the original draft list: Boston
  Globe's only reachable feed last updated **May 2020**, National Geographic
  404s, Wired Ideas is abandoned, NYT Sports is empty, and WBUR's documented URL
  serves HTML (the real one is `wbur.org/feed`).
- **A sitemap can be a source.** AP publishes no RSS but its `robots.txt`
  advertises a Google-News sitemap carrying title, URL, date and **language**
  per article. `read_sitemap` consumes it as if it were a feed, selected by an
  optional 7th field on a source row (default `rss`). Prefer this to a Google
  News query wherever a publisher offers one: the URLs are real, so the
  URL-path filters work — **a Google News link is a redirect carrying no path
  at all**, which is why wire sources routed that way can never be scoped.
  Check `robots.txt` first and honour it: AP disallows `/api/v2/feed/`, so the
  sitemap is the sanctioned route, and no article text is ever read.
- **AP has no usable SECTIONS.** Its hubs are topic tags (`us-supreme-court`,
  `political-corruption`), hub pages are JS-rendered and yield no article links
  in raw HTML, and the sitemap carries no section field. Do not go looking
  again — this was tested on 2026-08-25.
- **AP and Reuters have no public RSS.** Six URLs verified dead; AP's answers
  401. They come via **Google News RSS filtered by source** — free, no API key,
  no delay. Both free aggregator tiers are worse (NewsAPI 24h delay and forbids
  non-development use; newsdata.io 12h). AP reads free; **Reuters is metered**.
- **A trailing keyword in a Google News query does NOT filter.** `site:apnews.com
  +sports` returns a robotics story about a "100m sprint and high jump" — the
  keyword is a soft relevance hint, not a filter. **A path inside `site:` DOES**
  (`site:reuters.com/world` → 64 real world items, `/sports` → 100). Negation
  (`-sports`) returns junk rows; `OR` is ignored. **AP cannot be path-scoped at
  all** — every form returns zero, because AP URLs are `apnews.com/article/<slug>`
  with no section in them. Reuters can, and is.
- **You cannot filter a wire on the URL, because there is no URL.** A Google News
  entry carries a `news.google.com` redirect and the bare domain only — no path.
  This is why the OPINION-style path test works for the Guardian and NYT but not
  for AP/Reuters, and why sport in Top News needed the query fixed instead.
- **Measure a filter's RECALL, not just its precision.** A sport keyword list was
  swept for false positives (22 caught, all genuine) and shipped — and missed
  two thirds of the sport, because headlines are mostly team and athlete proper
  nouns. The honest measurement was to fetch `site:reuters.com/sports` as
  **ground truth** and intersect: 38% of Reuters' Top News was sport, not the
  11% the keyword net reported. Use the source's own classification when one
  exists rather than a list you wrote.
- **Be polite to hosts.** `HOST_DELAY` keeps 2s between hits on the same host —
  the list has seven Guardian feeds, five Google News queries, five NYT.

## A thin section must never fail the run

`fetch.py` **always exits 0**. It used to exit 1 when a front-page section came
up short, on the reasoning that a short page was the user-visible failure worth
alarming on. That was wrong, and it cost a whole edition on 2026-08-25: a failed
step aborts the job, so export and commit never ran and **the entire page went
stale to save one missing headline**. A shortfall is a CONTENT problem — report
it (`::warning::` annotation, the count on the page, the audit panel), never
abort on it.

## Sections starve slowly; watch headroom, not failures

Each section's front page eats `quota x DRAWS_PER_DAY` articles a day. If its
feeds make less than that, the drawable pool drains at the difference and the
section fails **days later**, with no signal in between — which is exactly how
Science died: ~6.5 articles/day of supply against 8/day of consumption, fine for
two days, then zero.

`headroom()` runs every draw and prints days-of-supply per section, thinnest
first, warning under `HEADROOM_WARN_DAYS`. **When it flags a section, add a feed
or lower that section's quota — do not wait for it to come up short.** The
sections at risk are always the low-volume ones (Science, Arts & Culture), never
the wire-fed ones.

## Sourcing is editorial; the draw is not

"No ranking" governs how a story is picked **within** the pool. It says nothing
about what goes **into** it, and conflating the two produced a Top News section
holding "How the No. 2 pencil became a uniquely American school supply". A print
front page is not a random draw over all wire copy — an editor chose the
candidates first. **A newsroom's own front page is that same judgement**, made
before it reaches us, and using one is not a violation.

Prefer human-edited front pages (BBC, NPR, France 24) to algorithmic
aggregation. Google News's top-stories feed was tested and is good, but it is an
opaque ranking; a named newsroom's front page is the same call with someone
accountable for it. That is the whole thesis of this project.

Watch for feeds that are **wider than their name**: `euronews.com/rss` is the
whole site (13 of 50 rows actually European — New Zealand reached the Europe
section), the Guardian's World feed carries `/uk-news/`, and the BBC mixes video
packages into news feeds. All three were caught by looking at URL paths, which
is the first thing to check when a section reads wrong.

## Method: measure a filter before you ship it

Every filter here was checked against the whole article pool for false positives
first, and several were changed as a result — bare `guide` would have killed "A
guide to the Italian health care system"; a global short-title rule would have
killed real headlines. **Query the DB and count what a pattern would drop,
including on the sources you did not intend it for, before adding it.**

## UI

**Light mode only — Frank dislikes dark mode.** No `prefers-color-scheme`
block, no `[data-theme]` stamps; `color-scheme: light` is set so the browser
does not restyle controls. Verify by rendering with the browser forced to dark.

Fonts: Newsreader (headlines) / IBM Plex Sans (labels) / IBM Plex Mono (data).

## Deployment

Runs on **GitHub Actions**, not on Frank's machines:
`github.com/keystone22/newsfront` (public — this app uses **no API keys at
all**, so there is nothing to leak). `.github/workflows/edition.yml` draws
4x/day and commits; Pages serves `docs/` at
**https://keystone22.github.io/newsfront/**. `workflow_dispatch` gives a "Run
workflow" button in the GitHub phone app.

- GitHub cron is **UTC with no DST handling**, so draw times drift an hour in
  winter. Set at `:17` deliberately — GitHub delays scheduled jobs at the hour.
- **PythonAnywhere's free tier cannot run this**: outbound access is allowlisted
  (38 of 43 feed hosts blocked) and it has **zero** scheduled tasks. The spec's
  "4 scheduled tasks/day on free tier" is wrong. Developer at $10/mo is the
  cheapest that works — not currently needed.

## Section pages (Phase 2, Aug 23 2026)

Every front-page heading links to `/<slug>.html` — the full section, `SECTION_QUOTA`
(10) stories drawn from the same pool. Built by `draw_sections()` in `fetch.py`,
stored in `articles.section_slot`, rendered by `templates/section.html`.

**The cap rule is a ROUND-ROBIN, and the front page is one round of it.** "Max 1
per source" cannot fill ten slots from four sources, so a section page deals one
card per source per round until the quota is full. Measured on the live pools:
every section fills 10/10, the thinnest being Italy at 46 candidates across 4
sources. **What it buys is visible in Top News** — Reuters and AP carry 152 of
its 183 candidates and would take all ten slots on volume, where round-robin
gives NYT and Guardian US two or three apiece.

- **Reshuffle the SOURCE ORDER every round** (`random.sample`), not just the
  articles within a source. Leaving it fixed hands whichever source sorted first
  the earliest slot in every round, which is a ranking by source.
- **Front-page picks are PINNED onto their section page** and lead it. Without
  this, tapping "World" after reading a World headline can fail to show it,
  which reads as a bug rather than as randomness. They carry an "on the front
  page" tag — the ordering is a stated fact, not a hidden signal.
- **The section draw writes NO `shown_date`.** Dedup is a *front-page* scarcity
  rule; a section page is the overflow view, the place an article goes when it
  did **not** win a front-page slot. Suppressing it there would thin the page for
  no gain — Italy would take the worst of it at 46 candidates against 10 slots ×
  4 draws a day. Verified by fingerprint: a section draw leaves `shown_date` and
  `is_current` byte-identical and touches only `section_slot`. This is why the
  front draw reports a *smaller* candidate pool than the section draw for the
  same section (Italy 29 vs 46) — that gap is the dedup window, and it is
  correct.
- A short section page is a **supply fact, not a failure**: `fetch.py` still
  exits nonzero only when a FRONT page section comes up short.

**Routes are named after FILES** — `/`, `/index.html`, `/world.html` — because
`export.py` writes exactly those paths into `docs/`. One set of plain relative
links then works both locally and on Pages with nothing to rewrite at export
time. `news.py` asserts at import that no two sections share a slug.

Two traps this shipped with:
- **`git add docs/index.html` in the workflow would silently never commit the
  nine new pages.** It is `git add -A docs` — `-A` because a section retired
  from `sources.py` has its stale page *deleted* by `export.py`, and only `-A`
  stages a deletion.
- **CSS added for the section panel must be scoped to `.split`.** An unscoped
  `.mech .bar{flex-direction:column}` also turns the FRONT page's horizontal
  stacked bar on its side. Caught in review; verify a `.mech` change by reading
  `getComputedStyle` on the front page's bar, which must stay `row`.

## Phase status

Phases 0, 1 and 2 shipped Aug 23, 2026. **Not built, deliberately:** Comics
(needs an image-card layout) and Wildcard (Phase 3).

Spec: `news-aggregator-spec.md`, sitting in the working directory but
**deliberately gitignored** — this repo is public and the spec names personal
subscriptions and interests (Frank's call, Aug 23 2026). It moved here from
`~/Documents/Claude Projects/` the same day; that old path is now empty, so this
is the only copy. It is therefore NOT backed up by the repo — worth knowing if
the folder is ever rebuilt from a clone.

## Note on memory

Claude Code's memory is scoped per working directory. Notes written while
working out of `/Users/scibilia/FinanceHub` do **not** load in a session opened
here. That is why this file exists — durable project knowledge belongs in the
repo, not in memory.
