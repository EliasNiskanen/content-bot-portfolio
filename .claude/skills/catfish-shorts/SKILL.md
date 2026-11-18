---
name: catfish-shorts
description: How Catfish Gaming Shorts are written, edited and posted so they can go viral (Hook-Progression-Climax). Use when writing, generating, reviewing or scheduling Shorts, or changing bot/shorts.py or the Shorts part of the script prompt.
---

# Catfish Gaming: Shorts

Learned from "How to make shorts that go viral every time" (Logan Smith, 3 M views) and applied to a GTA
channel. The algorithm pushes what keeps people watching: retention decides everything.

## Topic first

- One topic the GTA audience already wants: a surprising mechanic, a GTA 5 vs GTA 6 difference, a busted myth,
  a day-one tip, a confirmed number. If it would not make a GTA fan stop scrolling, it is not a Short.
- Look at what already works in the niche (the trending research the bot does) and stay on the core audience.

## HPC structure (20-45 s)

1. **Hook (0-3 s, max 5 s)**: introduce the topic AND open a question. Fast, no greeting, on-screen hook text.
   "GTA 6 cops remember what you were wearing." / "This GTA 5 habit will get you killed in GTA 6."
2. **Progression**: deliver exactly what the hook promised, step by step, with concrete details and the source.
   Never switch topic, never pad.
3. **Climax at the END**: the payoff (the answer, the verdict, the number) comes last. Never pay off in the first
   5 seconds, or people swipe away. Then one short line pointing to the full video.

## Editing

- Big captions (already in the bot), quick visual changes every 2-4 s, music under the voice, no dead air.
- The hook text bar stays on screen; the end card points to the full video and the channel.

## Writing rules for the bot

- Shorts are written as their own mini-scripts (HPC), not just cut out of the long video, so the payoff can be
  saved for the end. They may reuse facts and sources from the long video.
- 60-120 spoken words, understandable with zero context, first words must not be "so", "and", "also".
- Title max 70 chars, starts with "GTA 6", ends with "#Shorts" (added by the bot).

## Posting

- Consistency beats volume: about one to two Shorts per day per channel; four per day was too many in the
  source's tests (half never got picked up).
- Post when the audience is active: the audience is mostly US, so the bot schedules for the US morning/afternoon
  (Finnish afternoon/evening). Check Studio > Analytics > Audience > "When your viewers are on YouTube" after a few
  weeks and adjust `schedule` in config.
- In Studio add the "Related video" link to the full video on every Short (the API cannot do it).
- Shorts go up public with the long video (`upload.privacy: public`, after the QA gate). Their description has
  "Full video: https://youtu.be/<id>"; every new public long video re-points this link on ALL channel Shorts to
  itself (`uploader.relink_shorts`), so old Shorts keep sending viewers to the newest video. Captions stay at
  3 words or fewer.
