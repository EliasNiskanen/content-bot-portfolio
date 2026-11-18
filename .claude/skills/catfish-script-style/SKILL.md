---
name: catfish-script-style
description: Voice, tone, structure and title rules for Catfish Gaming long videos (news and tips/game-guide formats). Use when writing or editing a script by hand (run --script), changing the script prompt in bot/scriptwriter.py or config channel.rules, choosing a topic, or reviewing a generated script.json.
---

# Catfish Gaming: long-video scripts

The prompt that enforces this lives in `bot/scriptwriter.py` and `config.yaml` (`channel.rules`, `script.*`).
Change rules there, not in one script, so every video stays the same.

## What the hit videos do (learned from GrandGecko, 3.4 M views on a 20 k channel)

1. **Relatable hook, then concrete surprises.** "If you've played GTA 5 for years, half of it is muscle memory.
   GTA 6 changes that: your car needs gas now, you can't flip it back over..." Two or three specific,
   surprising facts in the first 20 seconds, never vague hype.
2. **Promise + proof of work.** "I went through every trailer, interview and preview, and this is everything
   GTA 6 can do that GTA 5 never could."
2b. **Pay the title off in the first 60 seconds.** Lesson from our launch video (zqbN-fZBMsQ, 17.11.2026): the
   title promised "What You NEED to Do", the first practical chapter started at 3:26, and the average view ended at
   3:22. The hook now names the top 2-3 concrete actions right away, the first chapter starts before 1:30, and the
   practical chapters come first. Background, history and sales numbers go late, short, or get cut. The QA report
   notes (does not block) a first chapter after 1:30.
3. **Open loops.** Tease one payoff that only comes at the end ("there's one thing about the map most of you
   still believe, I'll get to it at the end") and one mid-video ("next is why your story might just stall").
4. **One topic per chapter, few chapters** (owner 18.11.2026: ten topics feel tiring): news 4-6, guide 5-7 tips,
   breakdown 5-8, launch-day 6-8 tips. Pick the strongest stories, skip the rest, and give each chapter 2-3
   minutes of depth. The chapter names also appear in the topic bar at the top of the video
   (`render.add_topic_bar`: current topic in brand blue, previous/next dimmed), so keep them short (max 5 words).
5. **GTA 5 vs GTA 6 comparisons** inside every chapter: "In GTA 5 you could... In GTA 6...". Comparison is the
   engine of these videos.
6. **Name the source every time**: "Rob Nelson told TGG", "according to IGN's preview", "Game Informer called
   it". Short verbatim quotes (quote cards).
7. **Myth-busting**: state the rumor, give the verdict ("Busted. That came from an anonymous 4chan post.").
8. **Practical advice** the viewer can use on day one ("check the gauge when you hear sirens", "hit an ATM after
   every job").
9. **Mid-video subscribe ask, around 40-50 %**, tied to the next tease: "This channel is brand new and most of you
   watching aren't subscribed. If you've learned something new so far, subscribing is free. Because next is..."
10. **Ending**: "Here's what I'm doing first: one, two, three", a hot take, one genuine question for the comments
    ("I'm reading all of them"), one short subscribe line, the release date.

## Voice

- A relaxed 30-year-old who has played games for twenty years. Plain conversational English, contractions,
  light self-deprecating humor, calm confidence. Like a friend explaining the news, not a hype streamer.
- Mild phrases are fine now and then: "honestly", "to be fair", "pretty wild", "here's the thing".
- Never: "no cap", "fr", "bro", "lowkey", "ngl", "cooked", "it's giving", "W"/"L", "y'all", "bussin",
  "let's get into it". Zero swearing (the filter in `bot/clean.py` is the last line, not the first).
- Written for the ear: short sentences, simple words, every point with a concrete example, number or detail.

## Formats (one topic per video, so we can post a lot)

- **News** (`format: long`): the day's GTA 6 news, sorted into confirmed / rumor / leak / opinion.
- **Tips / game guide** (`format: guide`): ONE subject per video, e.g. the map and regions, the police vs GTA 5,
  driving physics, weapons, animals, interactivity, NPC individuality, money, the story, what is missing at launch.
  Numbered chapters ("Tip 3: ..."), each with what it is, how it compares to GTA 5, and what to do about it.
- Length: at least 12 minutes of real content (~2 400 words). Never pad; go deeper instead (context,
  comparisons, practical advice, myth-busting).

## Facts

- Only what the sources say. Attribute every claim. Predictions and estimates are RUMOR cards, leaks LEAK,
  Rockstar/Take-Two statements CONFIRMED. Never invent dates, prices, numbers, quotes or leaks.
- An article with empty `text` is a headline only: do not build specifics on it.
- Skip shopping-deal articles as news; do not name private people when the story works without it.
- The trending videos are topic signals: never copy their title, structure or jokes verbatim.

## Titles (max 60 chars, must be true to the video)

Clickbait that is true: a curiosity gap or real stakes around the most surprising concrete fact ("GTA 6 Is
FINALLY Here: Do This Before You Play", "GTA 6: Don't Make This Day-One MISTAKE"), never a list of chapter topics,
and the video pays it off inside the first minute.
Start with "GTA 6". One or two CAPS words (NEED, NEVER, NEW, EVERYTHING). Proven shapes:
"GTA 6 Can Do Things GTA 5 NEVER Could", "GTA 6: 12 Things You NEED to Know Before You Play",
"GTA 6 - Everything You Need To Know About the Police", "GTA 6 - NEW X & Y Revealed". "?!" only for rumors.
The thumbnail text complements the title instead of repeating it (title "...Everything You Need To Know",
thumbnail "EVERYTHING").

## Description (template of the hit channels: GrandGecko, Arekkz, PlayStationGrenade)

1. Line 1 repeats the title (keyword line).
2. A keyword-rich 3-4 sentence summary: the release fact ("GTA 6 (GTA VI) comes out November 19, 2026 on PS5 and
   Xbox Series X|S."), the chapter topics as a comma list, which claims are CONFIRMED / REPORTED / BUSTED, and how
   it was checked ("Every claim is checked against Rockstar's trailers, interviews and hands-on previews" - only
   when true). Natural language, no keyword stuffing.
3. One genuine question for the comments.
4. The chapter timestamps (bot) with NO heading line ("0:00 Intro" first, like GrandGecko), then "If this helped, hit LIKE and SUBSCRIBE for more GTA 6 news and guides
   all the way to launch and after."
   Then the bot adds "Subscribe for more: https://www.youtube.com/@catfishgaminghd?sub_confirmation=1" (opens the
   subscribe dialog) and "Watch next:" with the channel's latest public long videos (`upload.watch_next_count`,
   default 3). Shorts get the full-video link and the subscribe link; every new public long video re-points ALL
   Shorts' "Full video" links to itself (`upload.relink_shorts`). Clickable end-screen elements can only be
   set in Studio (End screen -> Import from video copies the previous video's layout).
5. "Sources: Push Square, IGN, ..." on ONE line with publication names only (no long URLs, which YouTube cuts
   short; no creator videos or "trending thanks to" credits: owner 18.11.2026), music credit, AI-voice line,
   "Not affiliated with Rockstar Games or Take-Two Interactive."
   Timeline chapters only show when the channel has Advanced features in Studio (Settings -> Channel ->
   Feature eligibility); the description format alone is not enough.
6. Hashtags last: the 3 most specific first (they show above the title), then the configured list, max 30.

When the channel moves beyond GTA 6, change `channel.rules`, `trending.*`, `news.keywords` and
`upload.topic_hashtags`.
