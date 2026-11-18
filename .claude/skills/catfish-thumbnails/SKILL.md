---
name: catfish-thumbnails
description: Thumbnail, image and character-cutout rules for Catfish Gaming (YouTube-hit style, GTA VI logo, optional mascot avatar). Use when making, fixing, reviewing or swapping thumbnails, adding background images or character cutouts, or changing bot/thumbnail.py or bot/poses.py.
---

# Catfish Gaming: thumbnails

Code: `bot/thumbnail.py`, `bot/poses.py`; config `thumbnail.*`, `paths.exclude_images`.
Reference level: GrandGecko "EVERYTHING" (3.4 M views), "GTA 6 Can Do Things GTA 5 NEVER Could", TmarTn2
"EVERYTHING", Arekkz "EARLY INSIDER 98 REVIEW SCORE?! & NEWS". Match that polish; the channel's own style is the
Vice City palette and our wording.

## The look

- 1280x720, bright and saturated, depth in layers: background -> optional giant faint VI logo -> headline ->
  hero character in FRONT of the lower part of the headline -> small GTA VI logo in a corner.
- Background: an official place image or a clean plate (`data/promo/backgrounds`), slightly blurred so the
  hero pops. No navy darkening, except a gradient behind text where it must be read.
- Hero: a character cutout from `data/promo/cutouts` (Rockstar's own cutouts + Jason & Lucia cut from official
  key art), big: face large, bottom-anchored, with a subtle white rim and pink/cyan glow. Faces never covered.
- Headline: max 4 words, heavy condensed caps (Anton). Big-word layout uses a Vice City gradient fill (pink ->
  orange -> yellow) with a dark outline and glow; banner/stack layouts use white with ONE yellow power word.
- Three variants for Test & compare: A "bigword" (one or two huge words across the top, e.g. EVERYTHING), B
  "banner" (black rounded bar on top, e.g. YOU'LL NEVER GET BORED!), C "stack" (2-3 lines on the left + tilted
  status sticker CONFIRMED / RUMOR / LEAKED / MY TAKE). Different hero and background per variant.
- GTA VI logo (`data/brand_assets/gta6_logo.png`, Rockstar trademark, not in git) in a free corner.
- Nothing important bottom-right: YouTube puts the video length there.
- The mascot avatar is optional (`thumbnail.avatar`, default off since the hit thumbnails do not use one); when
  on, a 190 px round badge with the pose (detective, shocked, thinking, hype, angry, laughing, pointing) whose
  props may break out of the circle.
- Must read at phone size: check the small row of `*_thumbs_compare.jpg` every time. Never misleading.

## Images

- `data/official_images` (Rockstar downloads page zips), `data/promo` (all images from the GTA VI site:
  Only in Leonida, media page, Ultimate/Vintage packs), `data/promo/cutouts` (13 transparent characters),
  `data/promo/backgrounds` (clean plates). All Rockstar material: never commit it.
- Family friendly only: no strip clubs, pole dancing, bikinis/lingerie as the focus, sexual poses. Excluded file
  stems live in `paths.exclude_images` (applies to thumbnails and video stills). Review new images on numbered
  contact sheets and extend the list. Guns and action are fine; gore is not.
- Never use other creators' thumbnails or frames.
