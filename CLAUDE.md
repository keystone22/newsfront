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
| `export.py` | renders the current edition to one ~21 KB self-contained file |
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
- **Verify a feed before relying on it.** From the original draft list: Boston
  Globe's only reachable feed last updated **May 2020**, National Geographic
  404s, Wired Ideas is abandoned, NYT Sports is empty, and WBUR's documented URL
  serves HTML (the real one is `wbur.org/feed`).
- **AP and Reuters have no public RSS.** Six URLs verified dead; AP's answers
  401. They come via **Google News RSS filtered by source** — free, no API key,
  no delay. Both free aggregator tiers are worse (NewsAPI 24h delay and forbids
  non-development use; newsdata.io 12h). AP reads free; **Reuters is metered**.
- **Be polite to hosts.** `HOST_DELAY` keeps 2s between hits on the same host —
  the list has seven Guardian feeds, five Google News queries, five NYT.

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

## Phase status

Phases 0 and 1 shipped Aug 23, 2026. **Not built, deliberately:** Comics (needs
an image-card layout), Wildcard (Phase 3), and **section pages showing ~10
articles — Frank asked for these as a later phase.** Note that section pages
cannot reuse the front page's cap rule: "max 1 per source" cannot fill 10 slots
from 3 sources, so that needs its own rule.

Spec: `~/Documents/Claude Projects/news-aggregator-spec.md`.

## Note on memory

Claude Code's memory is scoped per working directory. Notes written while
working out of `/Users/scibilia/FinanceHub` do **not** load in a session opened
here. That is why this file exists — durable project knowledge belongs in the
repo, not in memory.
