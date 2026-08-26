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

# Video packages. The BBC mixes them into its news feeds and separates them
# only by path -- "What do locals think of IndyCar in Washington DC?" reached
# the World page as a /news/videos/ item. A video is not a headline to read, and
# this page links out to text. Small but structural: 5 rows across the BBC feeds
# when measured 2026-08-25.
# Video AND audio packages. This page links out to TEXT, so neither is a
# headline to read. The BBC mixes video into its news feeds and Politico
# publishes a lot of podcast episodes through its article feed -- measured
# 2026-08-25, /podcast caught 13 rows with no false positives, among them
# "Sam and Anne's guide to the term ahead" and "What Ukraine needs to survive
# another winter", both of which read as articles in a headline list and are
# not. It is also where Politico's German-language output arrives
# ("Der Herbst des Friedrich Merz").
NOT_TEXT = (
    r"(/news/videos/|/video/|/av/|/podcast|/audio/"
    # The Guardian marks an audio piece with a trailing "- podcast" and Reuters
    # with a leading "PODCAST:". Both are needed as well as the paths: the
    # Creatine episode arrived under /science/audio/, which the path catches,
    # but a bare \bpodcast\b test would ALSO have dropped "How Two British
    # Historians Made a Smash Hit Podcast", which is a news story ABOUT one.
    r"|[-\u2013\u2014]\s*podcast\s*$|^\s*podcast\s*:)"
)

# Applied to every source, on top of that source's own exclude_pattern.
# Letters to the editor are opinion, but publishers file them under the section
# they concern rather than under /opinion/, so the path test cannot see them.
# The trailing "| Letter" / "| Letters" is a house convention and precise:
# 2 rows across 2,636, both genuine letters.
LETTERS = r"\|\s*letters?\s*$"

GLOBAL_EXCLUDE = f"(?:{TRAVEL})|(?:{OPINION})|(?:{NOT_TEXT})|(?:{LETTERS})"

# --- wire services, via Google News -------------------------------------
# AP and Reuters killed their public RSS years ago (see WIRE_SERVICES_DEAD).
# Google News republishes both as a free, keyless feed, filtered by source.
# Two things to know about it:
#   * it appends " - AP News" / " - Reuters" to every title; fetch.py strips it
#   * links go to news.google.com and redirect to the publisher in the browser,
#     so a click passes through Google. That is the price of not needing an
#     API key; newsdata.io is the paid-signup alternative if it ever matters.
# "allinurl:" is NOT supported here -- it returns zero items.
#
# A TRAILING KEYWORD DOES NOT FILTER. Measured 2026-08-23: "site:apnews.com
# +sports" returns 100 items including a robotics story about a "100m sprint
# and high jump", because the keyword is a soft relevance hint to Google, not a
# filter. Treat any "+topic" below as decoration, not a guarantee.
#
# A PATH INSIDE site: DOES filter, and is the only reliable topic control here.
# Verified: site:reuters.com/world returns 64 items of real world news,
# /sports 100, /business 37, /technology 2. This is why both Reuters feeds are
# path-scoped and AP's are not -- every AP path form returns ZERO items,
# because AP URLs are apnews.com/article/<slug> with no section in them.
# Negation ("-sports") is worse than useless: it returns junk rows like
# "- rmb.reuters.com". OR is ignored.
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

# AP's own sitemap gives real apnews.com URLs, so sport can finally be caught
# the way this file prefers -- on the URL, not the headline. The SLUG is a
# better surface than a title: lowercase, hyphenated, and it carries team and
# league names directly. Paired with AP_NOT_NEWS below it took AP's 317 English
# articles down to 236 real news stories on 2026-08-25.
AP_SPORT = (
    r"/article/[a-z0-9\-]*\b(nfl|nba|mlb|nhl|ncaa|fifa|uefa|pga|atp|wta"
    r"|soccer|football|basketball|baseball|hockey|tennis|golf|boxing|olympic"
    r"|cricket|vuelta|marathon|heisman|touchdown|quarterback|playoffs?"
    r"|avalanche|yankees|dodgers|astros|mets|rockies|mariners|phillies|brewers"
    r"|chargers|rays|nationals|celtics|lakers|scheffler"
    r"|longhorns|razorbacks|buckeyes|crimson-tide|seminoles|nittany|rutgers"
    r"|transfer-portal|signing-day|starting-qb)\b"
)
# ~3% of AP's English output still reads as sport after this, almost all NFL and
# college football. That residual is ACCEPTED on purpose. Pushing further means
# team nicknames that are ordinary English -- Bills, Browns, Giants, Saints,
# Chiefs, Texans -- and a slug test for "bills" would match a story about tax
# bills. A title-keyword sweep is worse still: tested 2026-08-25, "recruit"
# caught "Colombia's armed groups recruit children and train them in drone
# warfare". Losing that to catch a football story is the wrong trade, and the
# per-source cap of 1 means at most one AP item reaches the front page anyway.

# Not articles at all, and AP's robots.txt disallows /gallery/ anyway.
AP_NOT_NEWS = r"apnews\.com/(photo-gallery|newsletter|live|video|press-release)/"

AP_NEWS = f"(?:{AP_SPORT})|(?:{AP_NOT_NEWS})"

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
# "/football/" and "/calcio/" are here for the Italy section, where soccer is the
# bulk of the leakage -- ANSA English measured 21 of 75 rows (28%) under
# /sports/, and the Guardian files its Serie A coverage under /football/. This is
# EXACTLY why the pattern must stay per-source: theguardian.com/football/ is also
# where Guardian Football lives, which is a SPORTS source and must keep every row.
#
# Scoped to the general news feeds, NOT global. Measured across every stored
# Sports article it currently matches nothing -- ESPN files under /nfl/ and
# /mlb/, the Guardian's football feed under /football/, and the team blogs are
# their own domains -- but that is luck, not structure, and a Guardian Sport
# feed added later would be killed by a global rule.
NEWS_SPORT = r"(/sport/|/sports/|/football/|/calcio/)"

# Entertainment and soft features arriving in a NEWS section, caught on the URL
# path like NEWS_SPORT. Euronews files celebrity and viral-trend pieces under
# /culture/ -- Dolly Parton's health, whether Meghan Markle was dropped from a
# TV show, "What is an 'Aura Battle'? Young Mexicans turn confidence into a
# TikTok trend" -- and all of it was reaching the Europe page. Le Monde's
# /summer-reads/ is its seasonal feature slot.
#
# SCOPED to news sections, never global: measured 2026-08-25 it caught 19 rows
# in the news sections and would also have taken 2 legitimate Guardian Art
# pieces out of Arts & Culture, where culture is the entire point.
#
# "/lifestyle/" and "/style/" were TESTED AND REJECTED: 16 hits in the news
# sections, but they were ANSA filing the Messina museum art theft and the
# Palio di Siena -- real Italian news -- plus 23 rows in Arts and Human
# Interest. Too blunt.
NEWS_CULTURE = r"(/culture/|/summer-reads/)"

# Food and service-travel pieces reaching a NEWS section. Frank's brief,
# 2026-08-25, for Italy: "Not travel and food." The same rule improves the other
# news sections -- "Where to Eat in New York City Right Now" was sitting in Top
# News, and a "best places for peace and quiet in France" guide in Europe.
#
# Scoped to news sections, NOT global: Human Interest legitimately carries the
# Guardian's recipe columns, and Frank has never objected to those.
#
# "vineyard" and "winery" were TESTED AND REJECTED -- "vineyard" matched
# "As alpha-gal spikes on Martha's Vineyard", a medical story. "pizza" and
# "pasta" are left out too: commerce roundups like "The Best Outdoor Pizza
# Ovens" are already COMMERCE's job, and the bare words appear in real news.
# Stock touting, which is not news. A finance page is worth having only if it
# reports; a column of "3 stocks to buy before September" reads as advice, and
# this project shows facts and never signals. Yahoo Finance and Investing.com
# were TESTED AND REJECTED as sources for exactly this -- their feeds are mostly
# analyst-rating churn and pundit calls. This catches the same shape when it
# leaks from a reporting source.
FINANCE_TOUT = (
    r"\b(stocks? to (buy|watch|own|sell)|best stocks?|top \d+ stocks?"
    r"|price target|upgrades?|downgrades?|reiterates?|reaffirms?"
    r"|buy rating|sell rating|outperform|underperform|overweight"
    r"|should you (buy|sell|invest)|is .{2,24} a (buy|sell)"
    r"|motley fool|zacks|jim cramer"
    r"|investing club|\bour \w+ stocks?\b|\bwe('re| are) (buying|selling|trimming)\b)\b"
)

NEWS_FOOD = (
    r"\b(recipes?|pastry|pastries|gelato|trattoria|osteria|tasting menu|foodie"
    r"|where to (watch|eat|stay|go)|best places?|day trip|weekend in)\b"
)

# What a NON-wire source feeding a news section gets.
NEWS_NOISE = f"(?:{NEWS_SPORT})|(?:{NEWS_CULTURE})|(?:{NEWS_FOOD})"

# Italian-language sources need two exclusions the shared patterns cannot reach.
# ANSA files travel under "/canale_viaggi/", not "/travel/", so GLOBAL_EXCLUDE's
# TRAVEL half misses it entirely. Rai publishes video and text through one feed
# and separates them only by path -- 17 of 40 items on 2026-08-23 were video.
RAI_ANSA_IT = r"(/canale_viaggi/|/sport)"
RAI_VIDEO   = r"(/video/)"

# Sources published in ITALIAN. Tagged on the page so a headline is recognisable
# as reading practice before it is clicked. Display only -- language has no
# effect on the draw, which is why this is a lookup here rather than a column on
# the sources table.
# Sources that publish the SAME piece in several languages. Eurozine is a
# network of European cultural journals: it runs an essay in the original and in
# translation, so "Der geopolitische Kommunikationsmodus" and "The geopolitical
# mode of communication" are one article, and the English one is already in the
# feed. Dropping the translations loses nothing and stops one essay taking two
# slots. Frank reported the foreign titles on 2026-08-26.
#
# This CANNOT be a regex in exclude_pattern, so fetch.py applies it by name.
# It is emphatically NOT global: ANSA and Rai are Italian ON PURPOSE and are
# tagged IT for display, and a naive sweep flags all of them.
ENGLISH_ONLY = {"Eurozine"}

# The test is "does this look like English", not "does it look foreign" --
# tried the other way round first and it both missed German compounds with no
# diacritics ("Straflosigkeit produziert Straflosigkeit") and fired on English
# titles carrying a loan word ("El Nino", "Hurtgen Forest"): 326 false
# positives. A title is treated as foreign only when it has three or more real
# words, NO English function word, AND a positive foreign signal.
#
# The long-word clause is what catches German compounds. "without" and friends
# are in the English list because leaving them out dropped the genuinely
# English "Social experiments without experimentalism".
# Measured 2026-08-26: 19 of Eurozine's 100 dropped, no English among them.
EN_WORDS = (
    r"\b(the|a|an|of|in|on|at|to|for|and|or|but|is|are|was|were|be|by|with|from|as"
    r"|that|this|these|those|how|why|what|when|where|who|its|it|you|your|we|our|their"
    r"|his|her|not|no|can|will|would|should|more|most|after|before|between|against"
    r"|about|into|over|under|out|up|down|new|first|last|still|does|do|did|has|have|had|s"
    r"|without|within|through|across|among|beyond|during|despite|toward|towards|upon"
    r"|since|other|another|such|only|even|also|than|then|too|very|just|all|any|each|both)\b"
)
FOREIGN_WORDS = (
    r"\b(der|die|das|und|ist|nicht|f\u00fcr|zum|zur|eine|einer|des"
    r"|le|les|une|dans|pour|avec|sur|est|sont|qui|par|contre|ou"
    r"|el|los|las|para|con|por|que|del|una|il|lo|gli|della|che|sono|per"
    r"|je|su|koje|koji|kako|koliko|nije|za|cat|este|sunt|pentru|care|de"
    r"|om|livets|och|att|na|ceste|utan)\b"
)
FOREIGN_CHARS = r"[\u010d\u0107\u017e\u0161\u0111\u0103\u00e2\u00ee\u0219\u021b\u00df\u00e0\u00e4\u00e9\u00e8\u00ea\u00eb\u00ec\u00ef\u00f2\u00f4\u00f6\u00f9\u00fb\u00fc\u00f1\u00f5\u00e3\u00e7\u0430-\u044f\u0451]"

LANG = {
    "ANSA Cronaca":  "IT",
    "ANSA Economia": "IT",
    "ANSA Politica": "IT",
    "Rai Cronaca":   "IT",
    "Rai Politica":  "IT",
}

# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

# Front-page order, like a print paper. Quotas total 15, matching the spec's
# "~13-15 headlines/day, sized like a print front page".
SECTIONS = ["Top News", "World", "Europe", "Italy", "Finance", "Science",
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
    "Finance":         2,
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
    ("NYT",                "Top News",        "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",        1,   24,  NEWS_NOISE),
    ("Guardian US",        "Top News",        "https://www.theguardian.com/us-news/rss",                          1,   24,  NEWS_NOISE),
    # AP reads from its OWN sitemap rather than Google News. Frank's call,
    # 2026-08-25: "AP has the best US content", so it is worth sourcing
    # properly. apnews.com publishes no RSS (index.rss answers 401, every other
    # path 404s) but robots.txt advertises this sitemap and disallows nothing
    # that touches it. Three wins over the Google News route: real apnews.com
    # URLs so the path filters work, a per-article language tag that drops AP's
    # Spanish wire cleanly, and ~3x the volume. The 7th field selects the
    # reader; every other source stays on the default 'rss'.
    ("AP",                 "Top News",        "https://apnews.com/news-sitemap-content.xml",                      1,   24,  AP_NEWS, "sitemap"),
    ("Reuters",            "Top News",        GN + "when:1d+site:reuters.com/world",                                    1,   24,  WIRE_NEWS),
    # Added 2026-08-25. Top News read as a random slice of everything the wires
    # published -- "How the No. 2 pencil became a uniquely American school
    # supply" took a slot. The cause is the SOURCES, not the draw: a firehose
    # search returns features and filler alongside news, and NYT's HomePage feed
    # is whatever the paper is pushing (all four lead items were one NFL/CTE
    # package when checked). These three are FRONT PAGES a newsroom chose.
    #
    # Human-edited on purpose. Google News's own top-stories feed was tested and
    # is good, but it is an opaque ranking; a named newsroom's front page is the
    # same editorial judgement with someone's name on it, which is the whole
    # point of this project. Deliberately NOT using it.
    ("BBC",                "Top News",        "https://feeds.bbci.co.uk/news/rss.xml",                            1,   24,  NEWS_NOISE),
    ("NPR",                "Top News",        "https://feeds.npr.org/1001/rss.xml",                               1,   24,  NEWS_NOISE),
    # France24 is the closest thing to AFP that exists publicly: a French public
    # broadcaster running AFP wire copy. AFP itself has NO usable public feed --
    # afp.com/en/news/rss.xml 404s, afp.com/rss.xml is a corporate feed with
    # nothing in 48h, and site:afp.com via Google News returns press releases.
    ("France 24",          "Top News",        "https://www.france24.com/en/rss",                                  1,   24,  NEWS_NOISE),

    # --- World
    ("NYT World",          "World",           "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",           1,   48,  NEWS_NOISE),
    # The Guardian files UK domestic stories in its World feed -- 10 of 111 rows
    # on 2026-08-25, which is how "Prince Harry quits board of wildlife charity"
    # (/uk-news/) reached the World page. Britain is not the world.
    ("Guardian World",     "World",           "https://www.theguardian.com/world/rss",                            1,   48,  f"(?:{NEWS_NOISE})|(?:/uk-news/)"),
    # AP World REMOVED 2026-08-25. AP's paths return zero items through Google
    # News, so it could only be scoped by the keyword "world" -- which is far
    # too weak: it filed "The last few witnesses in the Lindsay Clancy murder
    # trial", a MASSACHUSETTS case, under World. BBC World and Al Jazeera below
    # cover the same ground with a real section behind them.
    ("BBC World",          "World",           "https://feeds.bbci.co.uk/news/world/rss.xml",                      1,   48,  NEWS_NOISE),
    ("Al Jazeera",         "World",           "https://www.aljazeera.com/xml/rss/all.xml",                        1,   48,  NEWS_NOISE),
    # Euronews' general vertical, kept because Frank likes their daily "Latest
    # news bulletin" (his call, 2026-08-23) and it no longer arrives via Europe
    # now that that source points at the my-europe vertical.
    ("Euronews Global",    "World",           "https://www.euronews.com/rss?level=vertical&name=news",            1,   48,  NEWS_NOISE),
    ("Reuters World",      "World",           GN + "when:2d+site:reuters.com/world",                              1,   48,  WIRE_NEWS),

    # --- Europe
    # These three ran with NO filter at all until 2026-08-25 -- not even the
    # sport one every other news source had -- which is a large part of why
    # Europe read worst of all the sections.
    # euronews.com/rss is the whole SITE: only 13 of 50 rows were European, which
    # is how "New Zealand's government introduces legislation..." reached the
    # Europe section. The my-europe vertical is 50/50 European.
    ("Euronews",           "Europe",          "https://www.euronews.com/rss?level=vertical&name=my-europe",       1,   48,  NEWS_NOISE),
    ("Euractiv",           "Europe",          "https://www.euractiv.com/feed/",                                   1,   48,  NEWS_NOISE),
    ("Politico Europe",    "Europe",          "https://www.politico.eu/feed/",                                    1,   48,  NEWS_NOISE),
    ("Le Monde",           "Europe",          "https://www.lemonde.fr/en/rss/une.xml",                            1,   48,  NEWS_NOISE),
    ("Guardian Europe",    "Europe",          "https://www.theguardian.com/world/europe-news/rss",                1,   48,  NEWS_NOISE),
    # NYT files US stories in its Europe feed -- "French Tourist Dies in Death
    # Valley" arrived under nytimes.com/.../us/. Scoped to THIS source: the same
    # /us/ path is correct in Top News, where 40 rows of real US news use it.
    ("NYT Europe",         "Europe",          "https://rss.nytimes.com/services/xml/rss/nyt/Europe.xml",          1,   48,  f"(?:{NEWS_NOISE})|(?:nytimes\\.com/\\d{{4}}/\\d{{2}}/\\d{{2}}/us/)"),
    # Added 2026-08-25: two more European newsrooms with real Europe desks.
    ("BBC Europe",         "Europe",          "https://feeds.bbci.co.uk/news/world/europe/rss.xml",               1,   48,  NEWS_NOISE),
    ("France 24 Europe",   "Europe",          "https://www.france24.com/en/europe/rss",                           1,   48,  NEWS_NOISE),
    # Frank, 2026-08-25: "I would like some more economic and business stories
    # in EU, not US or global politics." These four are European business desks
    # rather than general papers' international pages, so they report the EU as
    # the EU -- packaging rules, Dutch gas reserves, French industry -- instead
    # of syndicating the same global story the Top News and World pages carry.
    ("Euractiv Economy",   "Europe",          "https://www.euractiv.com/sections/economy-jobs/feed/",             1,  168,  NEWS_NOISE),
    ("Euronews Business",  "Europe",          "https://www.euronews.com/rss?level=vertical&name=business",        1,   96,  NEWS_NOISE),
    ("Le Monde Economy",   "Europe",          "https://www.lemonde.fr/en/economy/rss_full.xml",                   1,   96,  NEWS_NOISE),
    ("DW Business",        "Europe",          "https://rss.dw.com/rdf/rss-en-bus",                                1,  168,  NEWS_NOISE),

    # --- Italy: the thinnest section by a wide margin, so three of the four
    #     sources run long windows to keep a real pool behind a quota of 1.
    #     168h, not 48h: ANSA's ENGLISH service publishes in batches and runs
    #     about two days behind. Measured 2026-08-23 its 75 items spanned
    #     48-149h old, so a 48h window sat exactly on the newest item and the
    #     source contributed ~nothing. The Italian feed is live to the minute.
    ("ANSA English",       "Italy",           "https://www.ansa.it/english/english_rss.xml",                      1,  168,  NEWS_NOISE),
    ("The Local Italy",    "Italy",           "https://feeds.thelocal.com/rss/it",                                1,  168,  NEWS_NOISE),
    ("Wanted in Rome",     "Italy",           "https://www.wantedinrome.com/news?format=rss",                     1,   96,  NEWS_NOISE),
    ("Guardian Italy",     "Italy",           "https://www.theguardian.com/world/italy/rss",                      1,  168,  NEWS_NOISE),
    #     The Florentine publishes about twice a week: measured 2026-08-23 its
    #     10 items spanned 268-434h old, so at 168h it contributes NOTHING and
    #     even 336h reaches only 5. 720h, same reasoning as Eurozine.
    ("The Florentine",     "Italy",           "https://www.theflorentine.net/feed/",                              1,  720,  NEWS_NOISE),
    #     Italian-language, for reading practice. ANSA is a wire, so its copy is
    #     the plainest Italian available here; it is also free, where Repubblica
    #     truncated to 262-603 words. Its travel channel is excluded by hand --
    #     the global TRAVEL filter looks for "/travel/" and ANSA files under
    #     "/canale_viaggi/", so the shared pattern does not reach it.
    # ANSA and Rai both publish domestic SECTION feeds, and the catch-all ones
    # these replace were the main reason Italy read like a world paper: their
    # URLs carry no section at all (all 112 Rai rows were unscopeable), so the
    # US-Iran economic war arrived twice in one edition, in Italian. Frank's
    # brief, 2026-08-25: Italy should be "stories about Italy and Italian life
    # ... mainly social, economic, human interest, lifestyle", with a little
    # politics. cronaca is precisely that register -- Lombardy's first assisted
    # death through the health service, a social taxi for elderly patients in
    # Foggia, a drowned lifeguard and flags at half-mast in Versilia.
    ("ANSA Cronaca",       "Italy",           "https://www.ansa.it/sito/notizie/cronaca/cronaca_rss.xml",         1,   48,  RAI_ANSA_IT),
    ("ANSA Economia",      "Italy",           "https://www.ansa.it/sito/notizie/economia/economia_rss.xml",       1,   48,  RAI_ANSA_IT),
    ("ANSA Politica",      "Italy",           "https://www.ansa.it/sito/notizie/politica/politica_rss.xml",       1,   96,  RAI_ANSA_IT),
    #     17 of Rai's 40 items are VIDEO, which this is not a place for.
    ("Rai Cronaca",        "Italy",           "https://www.rainews.it/rss/cronaca",                               1,   48,  RAI_VIDEO),
    ("Rai Politica",       "Italy",           "https://www.rainews.it/rss/politica",                              1,   96,  RAI_VIDEO),

    # --- Finance: global markets, investing and the money story, as distinct
    #     from the EU business desks that sit in Europe. Frank asked for this
    #     section on 2026-08-26: "Global finance, investing, markets."
    #
    #     Reporting-led sources only. Yahoo Finance (39 items/day) and
    #     Investing.com were tested and left out: their feeds are analyst-rating
    #     churn and pundit stock calls, which would turn the page into a
    #     recommendation list. The Economist's finance desk runs about one piece
    #     a day, so it needs a long window to appear at all -- the Eurozine rule.
    # site:reuters.com/markets was TESTED AND REJECTED: 94 of its 100 items are
    # ticker QUOTE pages ("DIVD.OQ - | Stock Price & Latest News", ".DJUSCX"),
    # leaving about six real articles. /business is the reporting path.
    ("Reuters Business",   "Finance",         GN + "when:2d+site:reuters.com/business",                           1,   48,  f"(?:{WIRE_NEWS})|(?:{FINANCE_TOUT})"),
    ("CNBC",               "Finance",         "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114", 1, 24, FINANCE_TOUT),
    ("MarketWatch",        "Finance",         "https://feeds.content.dowjones.io/public/rss/mw_topstories",       1,   48,  FINANCE_TOUT),
    ("NYT Business",       "Finance",         "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",        1,   48,  f"(?:{NEWS_NOISE})|(?:{FINANCE_TOUT})"),
    ("BBC Business",       "Finance",         "https://feeds.bbci.co.uk/news/business/rss.xml",                   1,   48,  f"(?:{NEWS_NOISE})|(?:{FINANCE_TOUT})"),
    ("Guardian Business",  "Finance",         "https://www.theguardian.com/uk/business/rss",                      1,   48,  f"(?:{NEWS_NOISE})|(?:{FINANCE_TOUT})"),
    ("Economist Finance",  "Finance",         "https://www.economist.com/finance-and-economics/rss.xml",          1,  336,  FINANCE_TOUT),

    # --- Science: low daily volume everywhere, and science ages well, so the
    #     windows are wide on purpose.
    ("NYT Science",        "Science",         "https://rss.nytimes.com/services/xml/rss/nyt/Science.xml",         1,   96,  None),
    ("Guardian Science",   "Science",         "https://www.theguardian.com/science/rss",                          1,   96,  None),
    ("Ars Technica Science","Science",        "https://feeds.arstechnica.com/arstechnica/science",                1,  168,  COMMERCE),
    ("Wired Science",      "Science",         "https://www.wired.com/feed/category/science/latest/rss",           1,  168,  COMMERCE),
    # Added 2026-08-25 after Science STARVED: its four feeds made ~6.5
    # articles/day against a front page eating 8 (quota 2 x 4 draws), so the
    # drawable pool drained to zero over two days and the draw came up short.
    # These three carry the volume; Quanta is the Eurozine of science -- superb
    # and slow -- so it gets a long window or it would never appear.
    ("Nature",             "Science",         "http://feeds.nature.com/nature/rss/current",                       1,   96,  None),
    ("Scientific American","Science",         "http://rss.sciam.com/ScientificAmerican-Global",                   1,   96,  None),
    ("BBC Science",        "Science",         "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",    1,   96,  None),
    ("Quanta",             "Science",         "https://api.quantamagazine.org/feed/",                             1,  336,  None),

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
    # Added 2026-08-25 for the same reason as the Science block, before it bit:
    # Arts had 24 drawable against 8/day, about three days from starving.
    ("Hyperallergic",      "Arts & Culture",  "https://hyperallergic.com/feed/",                                  1,  168,  None),
    ("3 Quarks Daily",     "Arts & Culture",  "https://3quarksdaily.com/feed",                                    1,  168,  None),
    ("Literary Hub",       "Arts & Culture",  "https://lithub.com/feed/",                                         1,  168,  None),
    ("ARTnews",            "Arts & Culture",  "https://www.artnews.com/feed/",                                    1,  168,  COMMERCE),
    ("Aeon",               "Arts & Culture",  "https://aeon.co/feed.rss",                                         1,  336,  None),

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
    # Frank, 2026-08-26: Human Interest should carry some good news, and "Not
    # recipes." The Guardian files its recipe columns under /food/ -- 10 of its
    # rows -- and its product-review vertical under /thefilter, which is how
    # "best dog beds" was arriving. /food/ is scoped to THIS source on purpose:
    # the only other /food/ row in the database is Guardian Science's "Cookies
    # made from plastic may be on the menu", which is real science reporting.
    ("Guardian Life",      "Human Interest",  "https://www.theguardian.com/lifeandstyle/rss",                     1,   48,  f"(?:{COMMERCE})|(?:/food/|/thefilter)"),

    # Good news, deliberately sourced rather than hoped for. Good News Network
    # carries the volume; Positive News and Reasons to be Cheerful are
    # solutions journalism at a few pieces a week, so they take long windows --
    # the same rule Eurozine and Quanta get. Guardian's "The Upside" and Yes!
    # Magazine were TESTED AND REJECTED: nothing in either for over a week.
    # "Good News in History, August 25" is a daily almanac post, not a story.
    ("Good News Network",  "Human Interest",  "https://www.goodnewsnetwork.org/feed/",                            1,  168,  f"(?:{COMMERCE})|(?:^good news in history)"),
    ("Positive News",      "Human Interest",  "https://www.positive.news/feed/",                                  1,  336,  COMMERCE),
    ("Reasons to be Cheerful","Human Interest","https://reasonstobecheerful.world/feed/",                          1,  336,  COMMERCE),
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

# How many times a day the schedule draws (matches the cron in
# .github/workflows/edition.yml). Used only to convert the drawable pool into
# "days of headroom" for the starvation warning -- change both together.
DRAWS_PER_DAY = 4

# Warn when a section holds fewer than this many days of drawable articles.
# Two days is enough notice to add a feed before the section actually comes up
# short, which is how Science failed on 2026-08-25 with no prior signal.
HEADROOM_WARN_DAYS = 2.0

# Shown in the page footer, so it can't go stale in the template.
PHASE = 2

# How many days an article stays suppressed after being shown, so a fresh draw
# doesn't repeat yesterday's front page. Phase 3 tunes this.
DEDUP_DAYS = 2
