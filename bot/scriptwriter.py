"""Kirjoittaa videon käsikirjoituksen Claude API:lla.

Malli saa trendivideon METATIEDOT (otsikko, kuvaus, tagit, luvut) - ei transkriptiota -
ja tuoreet uutisartikkelit. Tavoite: sama aihe, mutta oma, tarkistettu ja lisäarvoa tuova video.
"""
import json
from pathlib import Path
import logging
import re

from . import clean as profanity

log = logging.getLogger(__name__)

SLANG = {
    "adult": ("Natural, relaxed voice of a 30-year-old who has played games for twenty years and follows the "
              "industry closely. Plain conversational English with contractions. Mild casual phrases are fine now "
              "and then ('honestly', 'to be fair', 'pretty wild', 'here's the thing', 'I'll be honest'), but NO teen "
              "or internet slang: never say 'no cap', 'fr', 'bro', 'lowkey', 'ngl', 'cooked', 'it's giving', "
              "'W', 'L', 'y'all', 'bussin', 'let's get into it'. Calm confidence and light humor, like a friend "
              "explaining the news over coffee, not a hype streamer."),
    "low": "Light casual tone. Occasional 'honestly', 'lowkey', 'kinda'. Max one slang word per paragraph.",
    "medium": ("Casual gamer-friend tone like a popular GTA YouTuber. Sprinkle natural slang such as "
               "'lowkey', 'no cap', 'ngl', 'bro', 'wild', 'cooked', 'W'/'L', 'it's giving', 'fr', "
               "'let's get into it', 'y'all'. Roughly 1-2 slang touches per paragraph - never forced, "
               "never in every sentence. Use contractions, rhetorical questions, short punchy lines."),
    "high": ("Very energetic streamer voice, lots of slang and hype reactions, but every sentence must "
             "still be easy to understand for a non-native English speaker."),
}

SYSTEM = """You write voiceover scripts for a gaming news & analysis YouTube channel. The current focus is GTA 6.
Rules you never break:
- ZERO profanity or swear words, not even mild ones (no damn, hell, crap, ass, wtf, sh*t etc.). Family-friendly.
- Never copy another creator's script, jokes, structure or title. The trending video only tells you WHAT topic is hot.
- TREND FIRST: the channel grows only on what people search and click right now. Build the video around the
  hottest trending topic in the input (the trending video and WHAT PEOPLE ARE TALKING ABOUT), lead with it,
  and put the same search words in the title and the first sentence (the words, never the other title).
- Clearly separate CONFIRMED facts (from Rockstar/Take-Two or reliable reporting) from RUMORS, LEAKS and YOUR OPINION.
- Never invent facts, dates, quotes or numbers. If unsure, say it's unconfirmed.
- Attribute news to its outlet by name ("according to IGN...").
- Add real value beyond the trending video: context, timeline, fact-check of the claims in its title, what it means for players, a clear take.
- Written for the ear: people listen while driving or cooking and cannot scroll back. Short sentences, simple words,
  natural rhythm, and every point backed by a concrete example, number or detail. No emojis, no hashtags, no stage
  directions inside the spoken text.
- The first 30 seconds decide whether people stay: open with the single most interesting claim and say what the
  viewer will get from the video. No greeting, no channel intro.
- A long video must be long because it has real content: never pad, repeat or stretch. If today's news is thin, go
  deeper instead: context, timeline, comparisons with earlier launches, what it means for players, what to watch next.
Output ONLY valid JSON, no markdown fences."""

CARD_SCHEMA = """null | {"type": "news", "article": <index into ARTICLES>, "highlight": "max 12 words of the key point, in your own words", "image": "<one BACKGROUND category>"}
                   | {"type": "quote", "article": <index into ARTICLES>, "text": "12-45 words copied WORD FOR WORD from ARTICLES[index]", "highlight": "exact sub-phrase of text, max 10 words"}
                   | {"type": "fact", "status": "confirmed|rumor|leak|opinion", "text": "max 12 words"}"""

SCHEMA = """{
  "video_title": "max 60 chars, follows the TITLE rules below, NOT a copy of the trending title",
  "thumbnail_text": "1-4 word punchy text for the thumbnail that complements (does not repeat) the title",
  "thumbnail_variants": [
    {"text": "2-4 WORDS, wrap the ONE power word in *asterisks*", "style": "cutout|zoom|stamp", "badge": "confirmed|rumor|leak|opinion|null", "mascot": "<one MASCOT pose>", "background": "<one BACKGROUND category>"__CHARACTER__}
  ],
  "shorts": [
    {"hook": "max 7 words on-screen hook", "title": "max 70 chars, starts with GTA 6",
     "segments": [{"text": "spoken text, 1-2 sentences", "card": <same card shapes as below>}]}
  ],
  "description": "3-4 sentence keyword-rich summary paragraph, see DESCRIPTION below",
  "comment_question": "one genuine question to the viewers, ending with 'Tell us in the comments!'",
  "tags": ["10-15 tags"],
  "value_add": "one sentence: what this video adds that the trending video doesn't",
  "segments": [
    {
      "section": "max 5 words (OPTIONAL: only on the first segment of a new section, never on segment 0)",
      "text": "spoken text for this segment, 1-4 sentences",
      "visual": ["1-2 VISUAL tags: what the background footage should show while this is said"],
      "card": __CARD__
    }
  ]
}"""

_QUOTES = {"‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'", "`": "'",
           "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"',
           "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-",
           " ": " "}
_QUOTE_TABLE = str.maketrans(_QUOTES)


def norm_quote(s: str) -> str:
    """Lainauksen vertailumuoto: pienet kirjaimet, yksi välilyönti, suorat lainausmerkit ja tavuviivat."""
    return re.sub(r"\s+", " ", (s or "").translate(_QUOTE_TABLE).lower()).strip()


def _articles_block(articles: list[dict]) -> str:
    parts = []
    for i, a in enumerate(articles):
        if a.get("kind") == "transcript":  # julkaisupäivä: pelaajan videon tekstitys (max ~2 500 sanaa)
            body = (a.get("text") or "")[:18000]
            parts.append(f"[{i}] TRANSCRIPT | creator: {a['source']} | video: {a['title']}\nURL: {a['url']}\n{body}")
            continue
        body = (a.get("text") or a.get("summary") or "")[:3500]
        parts.append(f"[{i}] {a['source']} | {a['date']} | {a['title']}\nURL: {a['url']}\n{body}")
    return "\n\n".join(parts) or "(no articles found today)"


def _parse_json(raw: str) -> dict:
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
    start, end = raw.find("{"), raw.rfind("}")
    return json.loads(raw[start:end + 1])


def _check_quote(card: dict, articles: list[dict] | None) -> dict:
    """Lainaus kelpaa vain sanasta sanaan artikkelin tekstistä; muuten siitä tulee saman artikkelin uutiskortti."""
    idx = card.get("article")
    excerpt = re.sub(r"\s+", " ", str(card.get("text") or "")).strip()
    body = norm_quote((articles[idx].get("text") or "")) if articles and 0 <= idx < len(articles) else ""
    if not excerpt or not body or norm_quote(excerpt) not in body or profanity.find_profanity(excerpt):
        log.info("Lainaus ei löydy artikkelista %s sellaisenaan -> uutiskortti", idx)
        return {"type": "news", "article": idx, "highlight": None}
    hl = card.get("highlight")
    if not isinstance(hl, str) or not hl.strip() or norm_quote(hl) not in norm_quote(excerpt):
        hl = None
    return {"type": "quote", "article": idx, "text": excerpt, "highlight": hl.strip() if hl else None}


def _card(card, n_articles: int, articles: list[dict] | None) -> dict | None:
    """Kortin tarkistus (pitkä video ja Shortsien omat käsikirjoitukset): väärä indeksi -> None, lainaus sanasta
    sanaan tai uutiskortti, tuntematon tyyppi -> None."""
    if isinstance(card, dict):
        if card.get("type") in ("news", "quote"):
            idx = card.get("article")
            if not isinstance(idx, int) or not (0 <= idx < n_articles):
                card = None
            elif card["type"] == "quote":
                card = _check_quote(card, articles)
            if card and card["type"] == "news":
                img = card.get("image")
                card = {"type": "news", "article": idx, "highlight": card.get("highlight"),
                        **({"image": img.strip()[:60]} if isinstance(img, str) and img.strip() else {})}
        elif card.get("type") == "fact":
            card["text"] = profanity.clean(card.get("text", ""))
            if card.get("status") not in ("confirmed", "rumor", "leak", "opinion"):
                card["status"] = "opinion"
        else:
            card = None
    else:
        card = None
    if card and card.get("highlight") and card["type"] != "quote":
        card["highlight"] = profanity.clean(card["highlight"])
    return card


def _short_title(t: str) -> str:
    t = re.sub(r"\s+", " ", profanity.clean(t or "")).strip()
    if t and not re.match(r"gta\s*(6|vi)(?![a-z0-9])", t, re.I):
        t = "GTA 6: " + t
    return t[:70].rstrip()


def _validate_shorts(script: dict, n_seg: int, n_articles: int, articles: list[dict] | None) -> list[dict]:
    """Uusi muoto: {"hook", "title", "segments": [{"text", "card"}]} = oma HPC-minikäsikirjoitus.
    Vanha muoto (yhä tuettu): "segments" = pitkän videon segmenttien indeksit."""
    out = []
    for v in script.get("shorts") or []:
        if isinstance(v, dict) and not isinstance(v.get("segments"), list) and isinstance(v.get("script"), list):
            v = {**v, "segments": v["script"]}  # jo tarkistettu muoto (tallennettu script.json) tai mallin avain "script"
        if not isinstance(v, dict) or not isinstance(v.get("segments"), list) or not v["segments"]:
            log.warning("Short hylätty (ei segmenttejä): %s", str(v)[:120])
            continue
        hook = profanity.clean(v.get("hook", ""))
        if all(isinstance(i, int) for i in v["segments"]):
            out.append({"segments": v["segments"], "hook": hook, "title": profanity.clean(v.get("title", ""))[:90]})
            continue
        segs = []
        for sg in v["segments"]:
            if isinstance(sg, str):
                sg = {"text": sg}
            if not isinstance(sg, dict):
                continue
            text = profanity.clean(sg.get("text", ""))
            if text:
                segs.append({"text": text, "card": _card(sg.get("card"), n_articles, articles)})
        if not segs:
            continue
        n = sum(len(x["text"].split()) for x in segs)
        if not 50 <= n <= 130:
            log.warning("Shortsin käsikirjoitus %d sanaa (tavoite 60-120): %s", n, hook)
        out.append({"hook": hook, "title": _short_title(v.get("title", "")), "script": segs})
    if not out:
        fb = _fallback_short(script)
        if fb:
            log.warning("Käsikirjoituksessa ei kelvollista Shortsia -> varashortsi osiosta: %s", fb["hook"])
            out.append(fb)
    return out


def _fallback_short(script: dict) -> dict | None:
    """Varashortsi pitkän videon osiosta (peräkkäiset segmentit, 50-130 sanaa), jotta Short ei koskaan puutu."""
    segs = script.get("segments") or []
    starts = [i for i, s in enumerate(segs) if i and s.get("section")] or list(range(1, len(segs)))
    for i in starts:
        idx, n = [], 0
        for j in range(i, len(segs)):
            w = len(segs[j].get("text", "").split())
            if n + w > 130:
                break
            idx.append(j)
            n += w
        if n >= 50:
            sec = (segs[i].get("section") or "").strip()
            return {"segments": idx, "hook": (sec or script.get("thumbnail_text") or "GTA 6")[:40],
                    "title": _short_title(sec or script.get("video_title", ""))}
    return None


def _validate(script: dict, n_articles: int, articles: list[dict] | None = None) -> dict:
    """articles: tarvitaan lainausten tarkistukseen (ilman niitä lainaukset muuttuvat uutiskorteiksi)."""
    segs = []
    for k, s in enumerate(script.get("segments", [])):
        text = profanity.clean(s.get("text", ""))
        if not text:
            continue
        card = _card(s.get("card"), n_articles, articles)
        seg = {"text": text, "card": card}
        from .footage import clean_tags
        vis = clean_tags(s.get("visual"))[:3]
        if vis:  # mitä taustalla näytetään (footage.TAGS)
            seg["visual"] = vis
        if re.fullmatch(r"\s*\d+:\d{2}\s*", str(s.get("moment") or "")):  # breakdown: lähdevideon hetki
            seg["moment"] = s["moment"].strip()
        section = s.get("section")
        if segs and isinstance(section, str):  # ei koskaan segmentille 0 (koukku)
            section = re.sub(r"\s+", " ", profanity.clean(section)).strip()[:40].strip()
            if section:
                seg["section"] = section
        segs.append(seg)
    script["segments"] = segs
    for k in ("video_title", "thumbnail_text", "description", "comment_question"):
        script[k] = profanity.clean(script.get(k, ""))
    script["video_title"] = script["video_title"][:100]
    tv = []
    for v in script.get("thumbnail_variants") or []:
        if isinstance(v, dict) and v.get("text"):
            v["text"] = profanity.clean(v["text"])
            tv.append(v)
    script["thumbnail_variants"] = tv[:3]
    script["shorts"] = _validate_shorts(script, len(segs), n_articles, articles)
    return script


def _spoken_words(script: dict) -> int:
    return sum(len(s.get("text", "").split()) for s in script.get("segments", []))


DEEP_RULE = 'FEWER, DEEPER TOPICS (owner 18.11.2026: ten topics feel tiring): pick only the strongest stories and skip the rest instead of listing them; each chapter gets 2-3 minutes with context, the GTA 5 comparison, what it means for players and a clear take'

HIT_STRUCTURE = """STRUCTURE (this is what the biggest GTA videos do; follow it in your own words):
1. HOOK, first 20 seconds: open with something every GTA 5 player relates to, then two or three CONCRETE, surprising
   facts from ARTICLES (a specific mechanic, number or quote), never vague hype. No greeting, no channel intro.
2. PROMISE: say what the viewer will get. Only if it is true for this script (you really draw on several
   trailers, interviews, previews or reports in ARTICLES), add a proof-of-work line in your own words, e.g.
   "I went through every trailer, interview and preview, so you don't have to". Never claim research you did not do.
   PAY THE TITLE OFF EARLY (viewers leave when the promise is late; our launch video lost most viewers at 3:22,
   right before its first practical chapter): inside the first 60 seconds give a quick version of what the title
   promises, e.g. the 2-3 most important concrete actions ("Before anything else: check you have 210 GB free,
   install the PS5 update, and turn on auto-download"), then say each one gets its own chapter with the details.
3. OPEN LOOPS: in the first minute tease ONE payoff that only comes at the very end ("there's one thing about X most
   people still get wrong, and I'll get to it at the end") and really pay it off in the last chapter. Around the
   middle tease the next chapter once ("next is why your story might just stall").
4. CHAPTERS: {chapters}, one topic each. ORDER BY VALUE TO THE VIEWER: the chapters that deliver the title's
   promise (the practical, "do this" or "this changes how you play" ones) come FIRST, and the very first chapter
   must start before 1:30. Background, history, sales numbers, marketing and industry talk go to the second half,
   short, or are cut. Never open with a recap of old news. In EVERY chapter compare with GTA 5 ("In GTA 5 you could... In GTA 6...").
   Well-known facts about GTA 5 (out since 2013) may come from your own knowledge; everything about GTA 6 comes
   ONLY from ARTICLES.
5. NAME THE SOURCE every time ("according to IGN's preview", "as Rockstar told Game Informer"); use quote cards.
6. MYTH-BUSTING: when a rumor or community claim is going around (see WHAT PEOPLE ARE TALKING ABOUT), state it, then
   give a clear verdict with the reason and the source: "Busted.", "Confirmed." or "Still unconfirmed."
7. PRACTICAL ADVICE the viewer can use on day one, in every chapter where it fits ("check the gauge when you hear
   sirens", "hit an ATM after every job"), but only advice the sources support.
8. MID-VIDEO SUBSCRIBE, exactly once, at 40-50 % of the script, tied to the next tease, e.g. "This channel is brand
   new and most of you watching aren't subscribed. If you've learned something so far, subscribing is free. Because
   next is ..."
9. ENDING: "Here's what I'm doing first: one, ... two, ... three, ...", then a hot take (clearly your opinion), then
   ONE genuine question for the comments ("I'm reading all of them"), then one short subscribe line (no begging).
   Mention the release date only if ARTICLES give it.
"""


def _thumb_extra(cfg) -> tuple[str, str]:
    """(kehotteen MASCOT/BACKGROUND/CHARACTER-ohje, skeeman character-kenttä). Thumbnail-lisät ovat valinnaisia."""
    extra, char_field = "", ""
    try:
        from . import thumbnail
        poses, cats = thumbnail.available_poses(cfg), sorted(thumbnail.background_index(cfg))
        if poses and cats:
            extra = (
                "MASCOT: each thumbnail shows the channel mascot (a catfish). Pick the pose that matches that "
                "thumbnail's emotion, and use different poses across the 3 variants when it fits: "
                + "; ".join(f"{k} = {thumbnail.POSES[k]}" for k in poses) + ".\n"
                "BACKGROUND: pick the official artwork category that best matches the story (a character only "
                "if the story is about them, otherwise a place): " + ", ".join(cats) + ".\n")
        chars_fn = getattr(thumbnail, "available_characters", None)
        chars = list(chars_fn(cfg) or []) if callable(chars_fn) else []
        if chars:
            extra += ("CHARACTER: optionally put one official GTA 6 character on a thumbnail when the story is about "
                      "them, otherwise null: " + ", ".join(map(str, chars)) + ".\n")
            char_field = ', "character": "<one CHARACTER or null>"'
    except Exception:  # noqa: BLE001
        pass
    return extra, char_field


LAUNCH_BLOCK = """LAUNCH-DAY VIDEO: "GTA 6 First Things To Do". GTA 6 unlocked in New Zealand a few hours ago and the first
players (streamers and creators, many playing on New Zealand time) have shared what they found. The ARTICLES marked
TRANSCRIPT are auto-captions of the most-watched of those videos ({n_creators} creators: {creators}); the others are
news articles.
- Build the video as NUMBERED TIPS: each tip is its own chapter whose first segment has "section": "TIP N: ..."
  (e.g. "TIP 4: Hit The ATM Early"). 6-8 tips, the most useful ones only, each explained properly. Each tip gives CONCRETE steps ("open the phone, go to ..., then ..."),
  what the first players actually ran into (mistakes, surprises, things that confused them), and a GTA 5 comparison.
- ORDER the tips by how much they matter in the first hour: TIP 1 is what every player should do first, and it
  starts before 1:30. The hook already names the top 2-3 actions in one sentence each.
- Attribute every launch-day claim to the creator: "according to <creator>, who played it at launch". Only use what
  the TRANSCRIPTS or ARTICLES really say; captions are automatic and may mishear words, so skip anything unclear and
  never invent numbers, prices, mission names or locations.
- QUOTE CARDS from a TRANSCRIPT copy its text WORD FOR WORD exactly as written there (the captions have no
  punctuation; keep it that way), "article" = that transcript's index.
- Never describe their gameplay footage or say "as you can see": our video shows only Rockstar's official material.
- At least {minutes} minutes. TITLE per the title rules, matching the real number of tips, e.g.
  "GTA 6: 7 Things You NEED to Do First".
- The "description" paragraph says the tips come from the first players' launch streams and videos; the bot lists
  those videos as sources automatically.
{early}
"""
EARLY_NOTE = ("- FEW TRANSCRIPTS were available yet: say clearly in the promise, and once more near the end, that "
              "early impressions are still coming in and this covers the first hours only; lean on the ARTICLES "
              "for anything the players have not covered yet.")


def launch_block(cfg, launch: dict | None) -> str:
    """Julkaisupäivän "GTA 6 First Things To Do" -ohje kehotteeseen ('' jos ei julkaisupäivän tutkimusta)."""
    if not launch:
        return ""
    cr = launch.get("creators") or []
    names = ", ".join(c["creator"] for c in cr) or "none yet"
    return LAUNCH_BLOCK.format(n_creators=len(cr), creators=names,
                               minutes=cfg.script.get("target_minutes_long", 12),
                               early=EARLY_NOTE if launch.get("early") else "") + "\n"


def build_prompt(cfg, video: dict | None, articles: list[dict], recent_titles: list[str],
                 related: list[dict] | None = None, research: dict | None = None, topic: str | None = None,
                 launch: dict | None = None, breakdown: dict | None = None) -> str:
    """Käyttäjäkehote. video = trendivideo (uutisvideo), topic = opasvideon aihe (format guide),
    launch = julkaisupäivän tutkimus (research.launch_research) -> "First Things To Do" -vinkkivideo,
    breakdown = Rockstarin uusi video (bot/breakdown.prepare) -> format breakdown, kuvat erikseen (user_content)."""
    from . import research as researchmod

    short = cfg.format == "short"
    bd = cfg.format == "breakdown" or bool(breakdown)
    guide = not bd and (cfg.format == "guide" or bool(topic))
    words = cfg.script.target_words_short if short else cfg.script.target_words_long
    min_words = 0 if short else int(cfg.script.get("min_words_long", 0) or 0)
    if cfg.format == "breakdown":  # vähintään 8 min (qa.min_minutes_breakdown)
        words = int(cfg.script.get("target_words_breakdown", 1800) or 1800)
        min_words = int(cfg.script.get("min_words_breakdown", 1500) or 1500)
    minutes = cfg.script.get("target_minutes_long", 12)
    n_shorts = int(cfg.shorts.get("count", 2) or 2)
    if short:
        fmt = ("YouTube Short (vertical, under 60 seconds): open with a 1-sentence hook in the first 2 seconds, "
               "one main point, end with a quick question to the viewer.")
    elif guide:
        fmt = (f"TIPS / GAME-GUIDE video about ONE topic only: \"{topic}\". At least {minutes} minutes of real content "
               "(8+ minutes allows mid-roll ads). Stay on this one topic the whole video; other GTA 6 news only when "
               "it directly matters for this topic. Numbered chapters: each chapter explains what it is, how it "
               "compares to GTA 5, and practical advice on what to do about it. This is not a news video: do not "
               "frame it around today's headlines.")
    elif bd:
        bmin = (cfg.get("qa") or {}).get("min_minutes_breakdown", 8)
        info = (breakdown or {}).get("info") or {}
        fmt = (f"BREAKDOWN video of at least {bmin} minutes about Rockstar's NEW official video \"{info.get('title', '')}\" "
               f"({info.get('url', '')}). The frames of that video are attached as images, each labeled with its "
               "timestamp. Walk through it in order and explain what is shown (\"at 0:42 we see ...\"): what is new, "
               "what it confirms, GTA 5 comparisons, details most people miss, and what it means for players. Facts "
               "only from what is visible in the frames, said in the video's subtitles/description (article 0) or in "
               "the other ARTICLES; anything you infer from a frame is labeled as our read (\"our read\", \"it looks "
               "like\"). When a segment talks about a specific moment, add \"moment\": \"M:SS\" (that frame's "
               "timestamp) to the segment so the video shows that moment behind the narration.")
    else:
        fmt = (f"NEWS video of at least {minutes} minutes (8+ minutes allows mid-roll ads; watch time is what gets the "
               "channel monetized): the day's GTA 6 news, sorted into confirmed / rumor / leak / opinion, with the "
               "angle that has the most heat right now.")
    rules = cfg.channel.get("rules") or []
    rules_block = ("CHANNEL RULES:\n" + "\n".join(f"- {r}" for r in rules) + "\n\n") if rules else ""
    signals_block = researchmod.prompt_block(research)
    if not signals_block and related:
        signals_block = ("OTHER HOT VIDEOS RIGHT NOW (look for the pattern: if several are about the same thing, "
                         "that is what people want; do not copy them):\n"
                         + "\n".join(f"- {r['title']} ({r['channel']}, {r['views']:,} views, "
                                     f"{r.get('outlier', 0):.1f}x the channel's normal views)" for r in related)
                         + "\n\n")
    if video:
        head = f"""TRENDING VIDEO (topic signal only, do not copy):
Title: {video['title']}
Channel: {video['channel']}
Views: {video['views']:,} in {video['hours_old']} h | likes {video['likes']:,} | comments {video['comments']:,}
Tags: {', '.join(video.get('tags', []))}
Description excerpt: {video['description'][:800]}

"""
    elif guide:
        head = f"GUIDE TOPIC: {topic}\n\n"
    elif bd:
        head = "NEW OFFICIAL ROCKSTAR VIDEO: " + json.dumps((breakdown or {}).get("info") or {}) + "\n\n"
    else:
        head = ""
    thumb_extra, char_field = _thumb_extra(cfg)
    if short:
        sections_rule, structure = "", ""
    elif guide:
        sections_rule = ('SECTIONS: mark the first segment of each numbered chapter with "section": "TIP 1: ..." style '
                         '(e.g. "TIP 3: Watch The Gas Gauge") or a short chapter title, max 6 words in total. Never on '
                         'segment 0 (the hook); every other segment has no "section".\n')
        structure = HIT_STRUCTURE.format(chapters=("6-8" if launch else "5-7") + " numbered chapters (TIP 1, TIP 2, ...). " + DEEP_RULE)
    elif bd:
        sections_rule = ('SECTIONS: mark the first segment of each of the 5-8 sections with "section": a short '
                         'on-screen title (max 5 words, e.g. "The Port Gellhorn Shot"). Never on segment 0.\n')
        structure = HIT_STRUCTURE.format(chapters="5-8 chapters following the video in order, each a section. " + DEEP_RULE)
    else:
        sections_rule = ('SECTIONS: mark the first segment of each of the 4-6 sections with "section": a short '
                         'on-screen title\n(max 5 words, e.g. "The Hacker Verdict"). Never on segment 0 (the hook); '
                         'every other segment has no "section".\n')
        structure = HIT_STRUCTURE.format(chapters="4-6 chapters, each a section. " + DEEP_RULE)
    title_shapes = ('"GTA 6 NEW Trailer Breakdown: Everything You Missed", "GTA 6 NEW Trailer: 25 Details You MISSED"'
                    if bd else
                    '"GTA 6: 7 Things You NEED to Know Before You Play", "GTA 6 - Everything You Need To Know About '
                    'the Police", "GTA 6 Can Do Things GTA 5 NEVER Could"' if guide else
                    '"GTA 6: 5 Things You NEED to Know Before Launch", "GTA 6 Can Do Things GTA 5 NEVER Could", '
                    '"GTA 6 - Everything We Know About X", "GTA 6 - NEW X & Y Revealed", "GTA 6 Just Confirmed X"')
    schema = SCHEMA.replace("__CARD__", CARD_SCHEMA).replace("__CHARACTER__", char_field)
    if guide:
        schema = schema.replace("NOT a copy of the trending title", "about the GUIDE TOPIC").replace(
            "what this video adds that the trending video doesn't", "what the viewer can do after watching")
    from . import footage
    visual_tags = ", ".join(k for k in footage.TAGS if k not in footage.AVOID)
    return f"""{rules_block}{head}{launch_block(cfg, launch)}{signals_block}ARTICLES (the ONLY source of facts; cite by index in cards):
{_articles_block(articles)}

OUR RECENT TITLES (avoid repeating these angles or title patterns):
{chr(10).join('- ' + t for t in recent_titles) or '- none yet'}

FORMAT: {fmt}
{structure}LENGTH: about {words} spoken words in total{f' (never fewer than {min_words})' if min_words else ''}. Keep each
segment to 1-4 sentences, so a long script simply has more segments.
VOICE: {SLANG.get(cfg.script.slang_level, SLANG['adult'])}
TITLE: written to get clicks from GTA fans, in the style of the biggest GTA channels, but true to this video.
- Start with "GTA 6" (that is what people search). Max 60 characters so it is not cut off on phones.
- Put ONE or TWO key words in CAPS for emphasis (e.g. NEED, NEVER, NEW, EVERYTHING, BIGGEST), never the whole title.
- Use proven shapes, adapted to the real content: {title_shapes}. Use a number only if the video really covers that
  many items.
- CLICKBAIT THAT IS TRUE: build a curiosity gap or real stakes around the single most surprising concrete fact
  in this video: what changed, what the viewer could get wrong, or what nobody expected (shapes like "GTA 6 Is
  FINALLY Here: Do This Before You Play", "GTA 6: Don't Make This Day-One MISTAKE", "GTA 6: Rockstar Confirmed
  What Nobody Expected"). Strong verbs and stakes beat a list of topics; never just list the chapter topics.
  Write 5 candidate titles in your head and output the one a GTA fan would click first that the video fully
  delivers, inside the first minute (see PAY THE TITLE OFF EARLY).
- "?!" or "?" only when the headline claim is a rumor, leak or prediction.
- Never promise something the video does not deliver; no fake leaks, fake dates or fake review scores.
VISUAL: every long-video segment gets "visual": 1-2 tags from this list, chosen for what the viewer should SEE
while it is said (the bot then cuts official footage that matches, e.g. talking about the map -> ["map", "aerial"],
the police -> ["police", "chase"], Lucia -> ["characters"]): {visual_tags}.
THUMBNAILS: give exactly 3 thumbnail_variants with clearly DIFFERENT angles (e.g. curiosity / shock / bold claim),
one per style: "cutout" (one or two HUGE words across the top, e.g. "EVERYTHING"), "zoom" (short line in a black
banner, e.g. "YOU'LL NEVER GET BORED!"), "stamp" (2-3 line text stack with a status sticker, e.g. "EARLY INSIDER
98 SCORE?!"). Max 4 words each, readable on a phone, emotional, never misleading - the video must deliver on the
promise. The thumbnail text complements the title instead of repeating it.
{thumb_extra}SHORTS: write exactly {n_shorts} Shorts, each its OWN mini-script of 60-120 spoken words (not a copy of
long-video segments; they may reuse its facts and sources), each on ONE topic GTA fans already want (a surprising
mechanic, a GTA 5 vs GTA 6 difference, a busted myth, a day-one tip, a confirmed number). Hook-Progression-Climax:
- HOOK (first sentence, under 5 seconds): introduce the topic AND open a question. No greeting; the first word is
  never "so", "and" or "also". The on-screen "hook" (max 7 words) says the same thing.
- PROGRESSION: deliver exactly what the hook promised, step by step, with concrete details and the source.
- PAYOFF at the END: the answer, the verdict or the number comes last, never in the first 5 seconds.
- The LAST segment is ONLY one short line pointing to the full video (e.g. "The full breakdown is on the channel.").
- Understandable with zero context. Cards follow the same rules as below (quotes word for word).
Use a news card whenever you reference an article, and fact cards for key claims (mark rumors/leaks honestly).
{sections_rule}NEWS CARD IMAGE: on news cards, "image" is the BACKGROUND category that best matches that story (list above).
DESCRIPTION: the bot builds the YouTube description: line 1 = the title, then your "description" paragraph, then your
"comment_question", then chapters, a like/subscribe line, sources, credits and hashtags (all added automatically).
- "description": 3-4 natural sentences, keyword-rich but no keyword stuffing, no hashtags, no links. Start with the
  release fact when ARTICLES confirm it (e.g. "GTA 6 (GTA VI) comes out November 19, 2026 on PS5 and Xbox Series X|S."),
  then list the chapters' topics as a comma list, say which claims are CONFIRMED / REPORTED / BUSTED when relevant,
  and end with how it was checked, only what is true for these ARTICLES (e.g. "Every claim is checked against
  Rockstar's trailers, interviews and hands-on previews.").
- "comment_question": one genuine question that invites an opinion, e.g. "Is no GTA Online at launch a good thing or a
  mistake? Tell us in the comments!"
QUOTES: use 2-5 quote cards per long video where the narration refers to what an outlet actually wrote. "text" is
12-45 words copied WORD FOR WORD from that article's text above (no paraphrasing, no fixing, no joining separate
sentences with "..."); "highlight" is an exact sub-phrase of "text", max 10 words. Quotes that do not match the
article exactly are thrown away, so copy carefully.

Return JSON exactly in this shape:
{schema}"""


def user_content(text: str, frames: list[dict] | None = None):
    """Kehote + avainkuvat (Messages API image-lohkot, JPEG <= 1280 px), jokainen kuva aikaleimalla."""
    if not frames:
        return text
    import base64
    blocks = [{"type": "text", "text": text}]
    for f in frames:
        blocks.append({"type": "text", "text": f"Frame at {f['stamp']}:"})
        blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                   "data": base64.b64encode(Path(f["path"]).read_bytes()).decode()}})
    return blocks


def write_script(cfg, video: dict | None, articles: list[dict], recent_titles: list[str], api_key: str,
                 related: list[dict] | None = None, research: dict | None = None, topic: str | None = None,
                 launch: dict | None = None, breakdown: dict | None = None) -> dict:
    import anthropic

    short = cfg.format == "short"
    words = cfg.script.target_words_short if short else cfg.script.target_words_long
    min_words = 0 if short else int(cfg.script.get("min_words_long", 0) or 0)
    if cfg.format == "breakdown":  # vähintään 8 min (qa.min_minutes_breakdown)
        words = int(cfg.script.get("target_words_breakdown", 1800) or 1800)
        min_words = int(cfg.script.get("min_words_breakdown", 1500) or 1500)
    user = build_prompt(cfg, video, articles, recent_titles, related=related, research=research, topic=topic,
                        launch=launch, breakdown=breakdown)
    frames = (breakdown or {}).get("frames") or []

    # eksplisiittinen aikaraja (striimissä: enintään näin kauan ilman uutta dataa) + SDK:n omat uusinnat
    # (yhteysvirheet, 408/409/429/5xx); alla oleva 3 yrityksen silmukka on näiden lisäksi
    sc = cfg.script
    client = anthropic.Anthropic(api_key=api_key, timeout=float(sc.get("api_timeout_s", 900) or 900),
                                 max_retries=int(sc.get("api_max_retries", 3) or 3))
    best = None
    attempts = 3
    for attempt in range(attempts):
        # striimaus: pitkä käsikirjoitus + ajattelu voi viedä paljon tokeneita.
        # fallbacks="default": jos malli kieltäytyy, API ajaa saman pyynnön varamallilla samassa kutsussa.
        with client.beta.messages.stream(model=cfg.script.model, max_tokens=32000, system=SYSTEM,
                                         messages=[{"role": "user", "content": user_content(user, frames)}],
                                         betas=["server-side-fallback-2026-07-01"], fallbacks="default") as stream:
            msg = stream.get_final_message()
        if msg.stop_reason == "refusal":
            log.warning("Malli kieltäytyi (%s), yritys %d", getattr(msg.stop_details, "category", None), attempt + 1)
            continue
        if msg.stop_reason == "max_tokens":
            log.warning("Vastaus katkesi max_tokens-rajaan (yritys %d)", attempt + 1)
            continue
        raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        try:
            script = _parse_json(raw)
        except (ValueError, json.JSONDecodeError):
            log.warning("JSON-parsinta epäonnistui (yritys %d)", attempt + 1)
            continue
        spoken = " ".join(s.get("text", "") for s in script.get("segments", []))
        hits = profanity.find_profanity(spoken)
        n = _spoken_words(script)
        log.info("Käsikirjoitus: %d sanaa (tavoite %d, minimi %d), yritys %d", n, words, min_words, attempt + 1)
        if not hits and (n >= min_words or best is None or n > _spoken_words(best)):
            best = script
        last = attempt == attempts - 1
        if hits and not last:
            log.warning("Kiroilua löytyi %s -> pyydetään uusi versio", hits)
            user += f"\n\nYour previous draft contained banned words: {hits}. Rewrite fully clean."
            continue
        if min_words and n < min_words and not last:
            log.warning("Liian lyhyt (%d < %d sanaa) -> pyydetään pidempi versio", n, min_words)
            user += (f"\n\nYour previous draft had only {n} spoken words, but the video needs at least {min_words} "
                     f"(target {words}). Write the full script again, longer, with more real depth: more context, "
                     "examples, comparisons and what it means for players. No padding or repetition.")
            continue
        break
    if best is None:
        raise RuntimeError("Käsikirjoituksen generointi epäonnistui")
    if min_words and _spoken_words(best) < min_words:
        log.warning("Käsikirjoitus jäi lyhyeksi: %d sanaa (minimi %d)", _spoken_words(best), min_words)
    return _validate(best, len(articles), articles)


MOCK_SHORTS = [
    {"hook": "Is GTA 6 still on track?", "title": "GTA 6: Is the release still on track? Take-Two answered",
     "segments": [
         {"text": "Is GTA 6 still coming out on time, or is another delay hiding in the fine print?", "card": None},
         {"text": "Take-Two was asked about it straight up in its latest earnings call, and the report spells "
                  "out exactly what the company said.",
          "card": {"type": "quote", "article": 0,
                   "text": "Take-Two reiterated the release window during its earnings call, and the company said "
                           "that development is \"on track\" for the planned launch date.",
                   "highlight": "development is \"on track\""}},
         {"text": "No new trailer, no pre-order date, nothing extra. But the one word everybody was waiting for "
                  "was there. On track. The window holds.",
          "card": {"type": "fact", "status": "confirmed", "text": "Release window reaffirmed"}},
         {"text": "The full breakdown is on the channel.", "card": None},
     ]},
    {"hook": "This GTA 6 leak fooled everyone?", "title": "GTA 6 leak: is this new Vice City landmark real?",
     "segments": [
         {"text": "A clip of a brand new Vice City landmark is all over your feed. Is it real?", "card": None},
         {"text": "Here is what we actually know. The clip is unverified, and Rockstar has not said a word about "
                  "it.", "card": {"type": "news", "article": 1, "highlight": "Unverified clip, no comment"}},
         {"text": "So the verdict for now: still unconfirmed. Treat it as a rumor until Rockstar shows it.",
          "card": {"type": "fact", "status": "rumor", "text": "Still unconfirmed"}},
         {"text": "The full breakdown is on the channel.", "card": None},
     ]},
]


def mock_guide_script(articles: list[dict], topic: str) -> dict:
    """run --mock --format guide: lyhyt opasvideon käsikirjoitus numeroiduilla luvuilla."""
    return _validate({
        "video_title": "GTA 6 Police - Everything You NEED To Know",
        "thumbnail_text": "WANTED",
        "thumbnail_variants": [
            {"text": "NEW *COPS*", "style": "cutout", "badge": None},
            {"text": "THEY *REMEMBER* YOU", "style": "zoom", "badge": "confirmed"},
            {"text": "GTA 5 HABITS *FAIL*", "style": "stamp", "badge": "opinion"},
        ],
        "shorts": MOCK_SHORTS,
        "description": "GTA 6 (GTA VI) guide: " + topic + ". Covers how the police react, how it compares to "
                       "GTA 5 and what to do on day one. Every claim is checked against the reports linked below.",
        "comment_question": "Will you play it safe or go loud on day one? Tell us in the comments!",
        "tags": ["gta 6", "gta 6 guide", "gta 6 tips", "gta 6 police", "gta 6 vs gta 5"],
        "value_add": "Practical day-one advice on one topic, compared with GTA 5.",
        "segments": [
            {"text": "If you played GTA 5 for years, losing the cops is muscle memory. In GTA 6 that habit could "
                     "cost you.", "card": None},
            {"section": "TIP 1: The Release Window",
             "text": "First, the timing. Take-Two says the game is on track, so you will be testing this soon.",
             "card": {"type": "news", "article": 0, "highlight": "Development on track", "image": "vice city"}},
            {"section": "TIP 2: Rumors To Ignore",
             "text": "In GTA 5 every leak turned out to be fake. This new clip is unverified too, so treat it as a "
                     "rumor.", "card": {"type": "fact", "status": "rumor", "text": "Unverified clip"}},
            {"text": "Here's what I'm doing first: one, play it slow, two, learn the map, three, keep cash on you. "
                     "What's your plan? I'm reading all of them.",
             "card": {"type": "fact", "status": "opinion", "text": "Play it slow on day one"}},
        ],
    }, len(articles), articles)


def mock_script(articles: list[dict]) -> dict:
    return _validate({
        "video_title": "GTA 6 Just Got Real: What's Confirmed vs What's Cap",
        "thumbnail_text": "CONFIRMED?!",
        "thumbnail_variants": [
            {"text": "GTA 6 *LEAKED?*", "style": "cutout", "badge": "leak"},
            {"text": "LOOK *CLOSER*", "style": "zoom", "badge": None},
            {"text": "ROCKSTAR *CONFIRMS* IT", "style": "stamp", "badge": "confirmed"},
        ],
        "shorts": MOCK_SHORTS,
        "description": "GTA 6 (GTA VI) news, sorted: the release window, the Take-Two earnings call, the leaked Vice "
                       "City landmark and what it all means for players. The release window is CONFIRMED, the "
                       "landmark leak is still a RUMOR. Every claim is checked against the reports linked below.",
        "comment_question": "Do you trust the new Vice City leak, or is it fake? Tell us in the comments!",
        "tags": ["gta 6", "gta vi", "rockstar", "vice city", "gta 6 news"],
        "value_add": "Fact-checks the viral leak claim against official statements.",
        "segments": [
            {"text": "Yo, what's good y'all. GTA 6 is all over your feed again, and ngl, this one's lowkey wild.",
             "card": None},
            {"section": "The Facts",
             "text": "First, the facts. Take-Two just doubled down on the release window in their latest earnings call.",
             "card": {"type": "news", "article": 0, "highlight": "Release window reaffirmed by Take-Two",
                      "image": "vice city"}},
            {"text": "Here is how the report puts it, word for word, so you can judge it yourself.",
             "card": {"type": "quote", "article": 0,
                      "text": "Take-Two reiterated the release window during its earnings call, and the company "
                              "said that development is “on track” for the planned launch date.",
                      "highlight": "development is \"on track\""}},
            {"section": "The Leak",
             "text": "Now, the spicy part. A clip going around shows a brand new Vice City landmark. No cap, it looks legit, but Rockstar hasn't said a word.",
             "card": {"type": "fact", "status": "rumor", "text": "New Vice City landmark spotted"}},
            {"text": "My take? Hype is valid, but don't treat every leak like gospel. Drop your thoughts below, and I'll catch you in the next one.",
             "card": {"type": "fact", "status": "opinion", "text": "Stay hyped, stay skeptical"}},
        ],
    }, len(articles), articles)


def mock_breakdown_script(articles: list[dict]) -> dict:
    """run --mock --format breakdown: uutis-mockin pohjalta, otsikko ja hetkiviittaukset breakdown-muodossa."""
    s = mock_script(articles)
    s["video_title"] = "GTA 6 NEW Trailer Breakdown: Everything You Missed"
    s["tags"] = ["gta 6", "gta 6 trailer", "gta 6 trailer breakdown", "gta vi", "rockstar"]
    for k, seg in enumerate(s["segments"][1:4], 1):
        seg["moment"] = f"0:{10 * k:02d}"
    return s
