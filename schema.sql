-- NewsFront schema. Applied idempotently on every run of fetch.py and news.py.

CREATE TABLE IF NOT EXISTS sources (
    id              INTEGER PRIMARY KEY,
    name            TEXT    NOT NULL UNIQUE,
    type            TEXT    NOT NULL DEFAULT 'rss',   -- 'rss' | 'api'  (api arrives in Phase 2)
    endpoint        TEXT    NOT NULL,
    section         TEXT    NOT NULL,
    cap             INTEGER NOT NULL DEFAULT 1,       -- max slots this source may fill in its section
    recency_hours   INTEGER NOT NULL DEFAULT 48,      -- per-source freshness window
    exclude_pattern TEXT,                             -- optional regex; matching titles/urls are dropped
    active          INTEGER NOT NULL DEFAULT 1,
    -- Conditional-GET bookkeeping. Sending these back lets a server answer
    -- "304 Not Modified" instead of re-sending the feed, which is both faster
    -- and what keeps repeat pulls from tripping a site's bot protection.
    etag            TEXT,
    last_modified   TEXT
);

CREATE TABLE IF NOT EXISTS articles (
    id           INTEGER PRIMARY KEY,
    source_id    INTEGER NOT NULL REFERENCES sources(id),
    section      TEXT    NOT NULL,
    title        TEXT    NOT NULL,
    url          TEXT    NOT NULL,          -- raw link, used for the outbound click
    url_key      TEXT    NOT NULL UNIQUE,   -- normalised link; THE dedup key
    published_at TEXT,                      -- ISO-8601 UTC
    fetched_at   TEXT    NOT NULL,          -- ISO-8601 UTC
    shown_date   TEXT,                      -- YYYY-MM-DD this article was last drawn onto a front page
    is_current   INTEGER NOT NULL DEFAULT 0, -- 0 = not on the front page; otherwise its slot there
    -- Same idea for the section page, drawn separately and with its own rule.
    -- Two columns rather than one because an article can hold a slot on both:
    -- the front-page picks are pinned onto their section page deliberately.
    section_slot INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_articles_current ON articles(is_current);
CREATE INDEX IF NOT EXISTS idx_articles_shown   ON articles(shown_date);
CREATE INDEX IF NOT EXISTS idx_articles_section ON articles(section, published_at);
