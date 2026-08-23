"""The feed list. This is the one file you edit to add or retire a source.

Every entry is seeded into the `sources` table on each run of fetch.py, so
editing a value here and re-running the fetch is enough to change behaviour.
Filters are retroactive -- fetch.py drops stored articles that newly match.

Fields
------
name            display name, shown above the headline
section         which section the source feeds
endpoint        the feed URL
cap             the most slots this source may fill in its section on one draw.
                This is the anti-crowding rule: without it a 50-item wire feed
                takes every slot in the section it shares with a 10-item one.
recency_hours   how far back this source's articles stay eligible.
                Wire and daily news get 24-48h. Slower, evergreen publications
                get much more -- a four-day-old science piece or a month-old
                Eurozine essay is no worse than a fresh one, and a tight window
                would simply exclude them almost every day.
exclude_pattern optional regex matched (case-insensitively) against both the
                title and the URL. Anything matching never enters the pool.

Every feed below was fetched and verified on 2026-08-23. Verified-dead and
left out deliberately: Reuters and AP (no public RSS -- see WIRE_SERVICES),
Boston Globe (only reachable path last updated May 2020), National Geographic
(404), Wired Ideas (abandoned -- newest item June 19), NYT Sports (empty feed).
"""

# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

# Commerce. Wired's *main* feed measured 48% affiliate content on 2026-08-23 --
# coupon pages, product roundups and reviews -- so its section feeds are used
# instead. This is the safety net for those and for the other magazines.
COMMERCE = (
    r"(coupon|promo code|discount code|\d+% off|\$\d+ off"
    r"|\bbest\b.*\(20\d\d\)|^the \d+ best|\b\d+ best\b"
    r"|/gallery/|/review/|/coupons/)"
)

# Travel guides. Frank's call, 2026-08-23: the daily news bulletins stay, the
# "how to spend 48 hours in Sofia" pieces go. Applied to EVERY source, since a
# travel guide can arrive from any of them.
#
# Two signals, both deliberately narrow because the Italy section is the one
# most at risk of over-filtering:
#   * "/travel/" in the URL -- Euronews files travel in its own section, which
#     is a structural signal with no guesswork in it. Wanted in Rome puts
#     everything under /news/, so it gets nothing from this half.
#   * an explicit phrase list. Bare "guide" is deliberately NOT here: it
#     matches "A guide to the Italian health care system", which is exactly the
#     practical expat reporting that section is for.
# "tour" carries a cycling guard so Tour de France coverage stays.
TRAVEL = (
    r"(/travel/"
    r"|travel guide"
    r"|things to do"
    r"|where to stay"
    r"|itinerar"
    r"|best time to visit"
    r"|how to spend \d+ (hours|days)"
    r"|(?<!de )(?<!world )(?<!concert )\btours?\b(?! de france))"
)

# Opinion and editorial. A spec non-goal: "No opinion/editorial content (op-eds
# excluded by request)". Every general news feed mixes them in -- NYT's home
# feed, Guardian's section feeds and Le Monde all did, 12 rows out of 929.
# Matched on the URL PATH SEGMENT, which is unambiguous, rather than on words
# in the headline, where "opinion poll" is a news story.
#
# "/analysis/" is deliberately NOT here. Reported analysis is not an op-ed, and
# the distinction is the one the spec actually draws.
OPINION = r"(/opinion/|/commentisfree/|/editorial|/columnists/|/opinions/)"

# Applied to every source, on top of that source's own exclude_pattern.
GLOBAL_EXCLUDE = f"(?:{TRAVEL})|(?:{OPINION})"

# --- wire services, via Google News -------------------------------------
# AP and Reuters killed their public RSS years ago (see WIRE_SERVICES_DEAD).
# Google News republishes both as a free, keyless feed, filtered by source.
# Two things to know about it:
#   * it appends " - AP News" / " - Reuters" to every title; fetch.py strips it
#   * links go to news.google.com and redirect to the publisher in the browser,
#     so a click passes through Google. That is the price of not needing an
#     API key; newsdata.io is the paid-signup alternative if it ever matters.
# "allinurl:" is NOT supported here -- it returns zero items -- so the topic
# split is a plain keyword and is therefore approximate.
GN = "https://news.google.com/rss/search?hl=en-US&gl=US&ceid=US:en&q="

# Google News mixes NON-ARTICLES in with the news: AP topic hub pages ("Ohio",
# "Formula One", "Joe Biden") and Reuters stock-quote pages ("(QQQP.O) | Stock
# Price & Latest News"). Measured 2026-08-23: 45 of 336 wire rows, 13%.
#
# The 1-3 word test is what catches the hub pages, and it is applied ONLY to
# these wire sources -- never globally. Measured against the other 639 rows it
# would have killed ten real headlines: "Salmonella Is Everywhere",
# "The Barbie Backlash", "Betye Saar obituary", "Feminist cities". A genuine
# wire headline is essentially never three words, so the trade is safe here and
# nowhere else.
WIRE_JUNK = (
    r"(^(?:\W*\w+){1,3}\W*$"                       # 1-3 words: a topic hub page
    r"|\|\s*stock price"                            # Reuters quote page
    r"|^\([A-Z0-9.^]+\)\s*\|"                       # "(TICKER) | ..."
    r"|\|\s*(latest|scores)|scores, news"           # "MLB | Latest News, Stats..."
    r"|^about .+\([A-Z0-9.]{2,10}\)\s*$)"            # Reuters fund/company profile page
)

# Sport arriving in a NEWS section. Frank's report, Aug 23 2026: "I see sports
# in top news. Not expected." He was right, and three of the ten Top News slots
# were football, baseball and golf that day.
#
# The cause is that the wire queries have NO WORKING TOPIC FILTER. Measured the
# same day: "site:apnews.com+sports" returns 100 items including a robotics
# story about a "100m sprint and high jump", because the trailing keyword is a
# soft relevance hint to Google, not a filter. The Top News queries carry no
# keyword at all, so they return everything the wire published -- and 11-12% of
# that is sport (AP 11 of 88, Reuters 10 of 89). NYT and Guardian US measured
# ZERO, because those feeds are edited front pages rather than firehoses.
#
# The structural fix the rest of this file prefers -- filter on the URL path,
# as OPINION does -- is IMPOSSIBLE here: a Google News link is a
# news.google.com redirect, and the entry carries only the bare domain
# ("https://www.reuters.com") with no path. Verified against the live feed.
# So this is a title test, and like WIRE_JUNK it is scoped to the wire sources
# ONLY. Applied globally it would gut the Sports section: swept across every
# stored article it matched 28 rows outside the wires, and all but one were
# ESPN and Guardian Football pieces sitting correctly in Sports.
#
# High-precision terms only. Deliberately EXCLUDED as ordinary English that
# happens to be sporting: "open", "champions", "coach", "athletic", "season",
# "win", "match", "final" on its own -- each of which appears in real news.
# Swept 2026-08-23 across all stored articles: 22 caught in the wire news
# sections, every one genuine sport, no false positives.
WIRE_SPORT = (
    r"\b(nfl|nba|mlb|nhl|ncaa|fifa|uefa|pga|atp|wta"
    r"|premier league|la ?liga|serie a|bundesliga|champions league|world cup"
    r"|formula one|f1 (?:race|team|season)|grand prix"
    r"|touchdown|quarterback|home run|innings|midfielder|striker"
    r"|preseason|playoffs?|semi-?final|quarter-?final"
    r"|mixed doubles|u\.s\. open|wimbledon|the masters"
    r"|scoreless|shutout|batting|pitcher|linebacker)\b"
)

# What a wire feeding a NEWS section gets: hub-page junk plus sport.
# AP Sports deliberately gets WIRE_JUNK alone -- sport is the point there.
WIRE_NEWS = f"(?:{WIRE_JUNK})|(?:{WIRE_SPORT})"

# Sport arriving in a news section from a NON-wire source, caught the way
# OPINION is -- on the URL path segment, which is unambiguous and needs no
# keyword guessing. This is the reliable half of the sport problem: the
# Guardian's us-news feed carries its tennis and football coverage under
# /sport/, measured at 4 of 18 rows on 2026-08-23, and that is a fact about
# the URL rather than a guess about the headline.
#
# Scoped to the general news feeds, NOT global. Measured across every stored
# Sports article it currently matches nothing -- ESPN files under /nfl/ and
# /mlb/, the Guardian's football feed under /football/, and the team blogs are
# their own domains -- but that is luck, not structure, and a Guardian Sport
# feed added later would be killed by a global rule.
NEWS_SPORT = r"(/sport/|/sports/)"

# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

# Front-page order, like a print paper. Quotas total 15, matching the spec's
# "~13-15 headlines/day, sized like a print front page".
SECTIONS = ["Top News", "World", "Europe", "Italy", "Science",
            "Tech & Hobbies", "Arts & Culture", "Sports", "Human Interest"]

# Top News and World run 3 rather than 2 because each holds FOUR sources, two
# of which are wires carrying ~100 candidates against NYT's 15. At a quota of 2
# the two wire slots filled almost every draw and the papers were squeezed out
# entirely -- the per-source cap limits any ONE source, but cannot spread three
# ways across two firehoses. Three slots gives the papers a real chance, and
# matches the spec's own draft (Top 3, World 2-3).
QUOTAS = {
    "Top News":        3,
    "World":           3,
    "Europe":          2,
    "Italy":           1,
    "Science":         2,
    "Tech & Hobbies":  1,
    "Arts & Culture":  2,
    "Sports":          2,
    "Human Interest":  1,
}

SOURCES = [
    # name,                section,           endpoint,                                                          cap, recency, exclude

    # --- Top News: no AP/Reuters wire until Phase 2, so these stand in. Kept
    #     to a 24h window because a stale lead story is worse than a thin one.
    ("NYT",                "Top News",        "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",        1,   24,  NEWS_SPORT),
    ("Guardian US",        "Top News",        "https://www.theguardian.com/us-news/rss",                          1,   24,  NEWS_SPORT),
    ("AP",                 "Top News",        GN + "when:1d+site:apnews.com",                                     1,   24,  WIRE_NEWS),
    ("Reuters",            "Top News",        GN + "when:1d+site:reuters.com",                                    1,   24,  WIRE_NEWS),

    # --- World
    ("NYT World",          "World",           "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",           1,   48,  NEWS_SPORT),
    ("Guardian World",     "World",           "https://www.theguardian.com/world/rss",                            1,   48,  NEWS_SPORT),
    ("AP World",           "World",           GN + "when:2d+site:apnews.com+world",                               1,   48,  WIRE_NEWS),
    ("Reuters World",      "World",           GN + "when:2d+site:reuters.com+world",                              1,   48,  WIRE_NEWS),

    # --- Europe
    ("Euronews",           "Europe",          "https://www.euronews.com/rss",                                     1,   48,  None),
    ("Euractiv",           "Europe",          "https://www.euractiv.com/feed/",                                   1,   48,  None),
    ("Politico Europe",    "Europe",          "https://www.politico.eu/feed/",                                    1,   48,  None),
    ("Le Monde",           "Europe",          "https://www.lemonde.fr/en/rss/une.xml",                            1,   48,  NEWS_SPORT),
    ("Guardian Europe",    "Europe",          "https://www.theguardian.com/world/europe-news/rss",                1,   48,  NEWS_SPORT),
    ("NYT Europe",         "Europe",          "https://rss.nytimes.com/services/xml/rss/nyt/Europe.xml",          1,   48,  NEWS_SPORT),

    # --- Italy: the thinnest section by a wide margin, so three of the four
    #     sources run long windows to keep a real pool behind a quota of 1.
    ("ANSA English",       "Italy",           "https://www.ansa.it/english/english_rss.xml",                      1,   48,  None),
    ("The Local Italy",    "Italy",           "https://feeds.thelocal.com/rss/it",                                1,  168,  None),
    ("Wanted in Rome",     "Italy",           "https://www.wantedinrome.com/news?format=rss",                     1,   96,  None),
    ("Guardian Italy",     "Italy",           "https://www.theguardian.com/world/italy/rss",                      1,  168,  None),

    # --- Science: low daily volume everywhere, and science ages well, so the
    #     windows are wide on purpose.
    ("NYT Science",        "Science",         "https://rss.nytimes.com/services/xml/rss/nyt/Science.xml",         1,   96,  None),
    ("Guardian Science",   "Science",         "https://www.theguardian.com/science/rss",                          1,   96,  None),
    ("Ars Technica Science","Science",        "https://feeds.arstechnica.com/arstechnica/science",                1,  168,  COMMERCE),
    ("Wired Science",      "Science",         "https://www.wired.com/feed/category/science/latest/rss",           1,  168,  COMMERCE),

    # --- Tech & Hobbies
    ("Ars Technica",       "Tech & Hobbies",  "https://feeds.arstechnica.com/arstechnica/index",                  1,   48,  COMMERCE),
    ("Popular Mechanics",  "Tech & Hobbies",  "https://www.popularmechanics.com/rss/all.xml/",                    1,   96,  COMMERCE),
    ("NYT Technology",     "Tech & Hobbies",  "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml",      1,   48,  None),
    ("Wired Business",     "Tech & Hobbies",  "https://www.wired.com/feed/category/business/latest/rss",          1,  168,  COMMERCE),
    ("Wired Security",     "Tech & Hobbies",  "https://www.wired.com/feed/category/security/latest/rss",          1,  168,  COMMERCE),

    # --- Arts & Culture: upscale, not pop culture. NYT Books rather than NYT
    #     Arts, per the spec. Eurozine publishes ~2 essays a WEEK, so it needs a
    #     30-day window to appear at all -- at 48h it contributed nothing.
    ("NYT Books",          "Arts & Culture",  "https://rss.nytimes.com/services/xml/rss/nyt/Books.xml",           1,   96,  None),
    ("Guardian Books",     "Arts & Culture",  "https://www.theguardian.com/books/rss",                            1,   96,  None),
    ("Guardian Art",       "Arts & Culture",  "https://www.theguardian.com/artanddesign/rss",                     1,   96,  None),
    ("Arts Fuse",          "Arts & Culture",  "https://artsfuse.org/feed/",                                       1,  336,  None),
    ("Eurozine",           "Arts & Culture",  "https://www.eurozine.com/feed/",                                   1,  720,  None),

    # --- Sports: general first, then Frank's three teams. Team feeds get long
    #     windows because a single-team blog goes quiet between games.
    ("ESPN",               "Sports",          "https://www.espn.com/espn/rss/news",                               1,   24,  None),
    ("ESPN NFL",           "Sports",          "https://www.espn.com/espn/rss/nfl/news",                           1,   48,  None),
    ("ESPN MLB",           "Sports",          "https://www.espn.com/espn/rss/mlb/news",                           1,   48,  None),
    ("ESPN College FB",    "Sports",          "https://www.espn.com/espn/rss/ncf/news",                           1,   48,  None),
    ("AP Sports",          "Sports",          GN + "when:2d+site:apnews.com+sports",                              1,   48,  WIRE_JUNK),
    ("Eagles (BGN)",       "Sports",          "https://www.bleedinggreennation.com/rss/index.xml",                1,   96,  None),
    ("Phillies (Good Phight)","Sports",       "https://www.thegoodphight.com/rss/index.xml",                      1,   96,  None),
    ("Penn State (BSD)",   "Sports",          "https://www.blackshoediaries.com/rss/index.xml",                   1,  168,  None),

    # --- Human Interest: WBUR stands in for the Boston Globe, whose only
    #     reachable feed stopped updating in May 2020.
    ("Guardian Life",      "Human Interest",  "https://www.theguardian.com/lifeandstyle/rss",                     1,   48,  COMMERCE),
    ("NYT Style",          "Human Interest",  "https://rss.nytimes.com/services/xml/rss/nyt/FashionandStyle.xml", 1,   96,  None),
    ("WBUR",               "Human Interest",  "https://www.wbur.org/feed",                                        1,   48,  None),
]

# Direct AP/Reuters RSS is gone. Verified 2026-08-23, six URLs, all dead: every
# Reuters path returns 301/404, and apnews.com/index.rss answers 401
# Unauthorized -- an authenticated, paid feed. Recorded so nobody re-tests them.
WIRE_SERVICES_DEAD = {
    "Reuters": ["https://www.reutersagency.com/feed/",
                "https://feeds.reuters.com/reuters/topNews",
                "https://www.reuters.com/arc/outboundfeeds/rss/?outputType=xml"],
    "AP":      ["https://apnews.com/index.rss",
                "https://apnews.com/hub/ap-top-news.rss",
                "https://feeds.apnews.com/rss/apf-topnews"],
}

# Slots on a SECTION page -- the fuller view behind each front-page heading.
#
# The front page's "max 1 per source" cannot fill ten slots from four sources,
# so a section page deals differently: ONE card per source per round, source
# order reshuffled each round, until the quota is full. That is the same
# anti-crowding rule generalised -- the front page is simply a single round --
# and it degrades gracefully, because a source that runs out of candidates just
# stops being dealt to while the others carry on.
#
# 10 is safe in every section as measured on 2026-08-23. The thinnest candidate
# pool is Italy at 46 across 4 sources (16/13/9/8), which fills ten slots inside
# three rounds; the widest source list is Sports at 8, so even there every
# source is dealt to in round one. What the rule buys is visible in Top News:
# Reuters and AP carry 152 of its 183 candidates and would take all ten slots on
# volume alone, where round-robin gives NYT and Guardian US two apiece.
SECTION_QUOTA = 10

# Shown in the page footer, so it can't go stale in the template.
PHASE = 2

# How many days an article stays suppressed after being shown, so a fresh draw
# doesn't repeat yesterday's front page. Phase 3 tunes this.
DEDUP_DAYS = 2
