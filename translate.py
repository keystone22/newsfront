#!/usr/bin/env python3
"""English glosses for the Italian headlines. DISPLAY ONLY.

The Italian sources exist for reading practice (see sources.LANG), so a
translation that replaced the headline would remove the reason they are here.
The Italian stays primary and the English is REVEALED by tapping "EN" -- you
try first, then check. Frank's choice, 2026-10-17.

Translated by MyMemory (api.mymemory.translated.net): keyless, no signup, and
documented at 5,000 characters a day anonymously. Only headlines ALREADY ON A
PAGE are sent -- about 20 a day, ~1,300 characters -- and each is translated
once and stored, so a headline is never sent twice. Nothing but the published
headline leaves this machine, and the gloss is labelled as machine translation
on the page so it is never mistaken for the paper's own wording.

Quality, measured 2026-10-17 on four live headlines: the sense was right every
time, with small slips -- a protest "corteo" came back as "parade", and a bill
before the Chamber lost the word "bill". Good enough to check yourself against,
not good enough to pass off as a translation.

No effect on the draw: this runs AFTER the edition is drawn and only ever adds
a column to rows already chosen.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

import sources as cfg

API = "https://api.mymemory.translated.net/get"
UA = "NewsFront/0.1 (personal news reader; single user)"
PAUSE = 1.0           # politeness between calls; ~20 calls a run at most
MAX_PER_RUN = 40      # a ceiling on the daily allowance, not a target
# MyMemory reports an exhausted quota INSIDE translatedText, in capitals,
# rather than as an HTTP error. Storing that would print it as a headline.
WARNINGS = ("MYMEMORY WARNING", "QUOTA", "INVALID", "PLEASE SELECT")


def translate(text):
    """One headline, Italian to English. Returns None if nothing usable came back."""
    url = f"{API}?{urllib.parse.urlencode({'q': text, 'langpair': 'it|en'})}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.load(resp)
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError):
        return None
    out = ((payload.get("responseData") or {}).get("translatedText") or "").strip()
    if not out or any(w in out.upper() for w in WARNINGS):
        return None
    # An unchanged string means MyMemory had nothing to offer; showing the
    # Italian twice is worse than showing it once.
    return None if out.casefold() == text.casefold() else out


def refresh(db):
    """Gloss the Italian headlines on the current pages. Returns a one-liner."""
    italian = tuple(cfg.LANG)
    if not italian:
        return "no Italian sources"
    marks = ",".join("?" * len(italian))
    rows = db.execute(f"""
        SELECT a.id, a.title FROM articles a JOIN sources s ON s.id = a.source_id
         WHERE s.name IN ({marks}) AND a.title_en IS NULL
           AND (a.is_current > 0 OR a.section_slot > 0)
      ORDER BY a.id
    """, italian).fetchall()
    if not rows:
        return "nothing new to translate"

    done = failed = 0
    for i, row in enumerate(rows[:MAX_PER_RUN]):
        if i:
            time.sleep(PAUSE)
        english = translate(row["title"])
        if english:
            db.execute("UPDATE articles SET title_en = ? WHERE id = ?", (english, row["id"]))
            done += 1
        else:
            failed += 1
            if failed >= 3 and not done:
                break          # the service is down or the allowance is spent
    db.commit()
    skipped = max(0, len(rows) - MAX_PER_RUN)
    return (f"{done} headline(s) glossed" + (f", {failed} unavailable" if failed else "")
            + (f", {skipped} left for the next run" if skipped else ""))
