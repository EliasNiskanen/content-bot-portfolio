---
name: catfish-video
description: Make, check, schedule and upload Catfish Gaming YouTube videos with the bot in this repo (news + tips/guide long videos, Shorts, thumbnails, Docker scheduler). Use whenever the user asks to make, render, re-render, fix, check, upload, publish, schedule, replace or delete a channel video, change the daily automation, or asks to see a finished video.
---

# Catfish Gaming: video end to end

The bot in this repo makes everything. Your job is to run it, check the result like a picky editor, fix what
is wrong in the code (never by hand-editing one output), and report. Every video must look like the last one.
Sibling skills: `catfish-script-style` (words, structure, titles, descriptions), `catfish-shorts` (Shorts),
`catfish-thumbnails` (thumbnails, images, cutouts).

## Pipeline (one run)

research (YouTube outliers on 7 queries, Reddit if reachable, 10+ RSS feeds with full article text) ->
Claude script (hit structure, ~2 400 words, sections, news/fact/quote cards, title, description, 3 thumbnail
variants, 1-2 dedicated HPC Shorts) -> edge-tts voice -> background (trailers without letterbox + official
stills with Ken Burns) -> intro (logo + Neon nights) -> news panels, quote cards, section titles, subtitles ->
end screen (Neon nights ending) -> background music bed (Pulsar loop, ~9 LU under the voice) -> thumbnails ->
Shorts -> upload (`upload.privacy: public` since 18.11.2026, after the QA gate) -> all channel Shorts relinked
to the new long video.

## Run

```
E:\gta6-bot\run.bat run --mock                 # pipeline test, no API calls, ~1 min
E:\gta6-bot\run.bat run --mock --format guide  # guide format test
E:\gta6-bot\run.bat research                   # what the trend research collects (no Claude)
E:\gta6-bot\run.bat run --no-upload            # real news video, stays in data\output
E:\gta6-bot\run.bat run                        # real news video + upload
E:\gta6-bot\run.bat run --format guide [--topic "GTA 6 police vs GTA 5"]   # one-topic tips video
E:\gta6-bot\run.bat run --script data\work\<run>\script.json   # re-render a saved script (no Claude, no upload)
docker compose up -d                           # unattended daily schedule (news 04:30, guide 06:30 Helsinki)
```

- A real run takes 30-45 min. Run it in the background, log to a file, wait for the notification; do not poll.
- Code changes do not reach a run that is already going. Stop and restart if the fix matters for that video.
- After any code change run `--mock` (both formats). A hanging final render (0-byte mp4, ffmpeg growing in
  memory) means a background piece has another pixel format/colour range: everything must be yuv420p, tv, bt709.
- Claude costs about 0.15-0.30 $ per real run; test with `--mock`/`--script` instead of extra real runs.

## Check every video before calling it done

1. Length >= 12:00 (`ffprobe`); log line `Käsikirjoitus: N sanaa` >= 2200.
2. Frames (intro 1.5 s, a news panel, a quote card, a section title, three spread points, the end screen, one per
   Short): no black bars, panels/cards readable, subtitles in the bottom strip, nothing suggestive on screen
   (Rockstar's trailers contain a "strip club" social-media post at one point; report it if it shows up).
3. Thumbnails (`*_thumbs_compare.jpg`): see `catfish-thumbnails`.
4. Script (`script.json`): `catfish-script-style` rules, sources, no invented numbers, no deal ads.
5. Description (`<name>.json`): template from `catfish-script-style`, chapters present, <= 30 hashtags.
6. Audio: music audible but under the voice (intro ~-16 LUFS, narration ~-21 LUFS, bed alone ~-30 LUFS).

Show the user the mp4 with `SendUserFile` plus the thumbnail compare sheet; quote title, length, thumbnails.

## Upload, publish, replace, delete

- Since 18.11.2026 the owner wants everything public right away: `upload.privacy: public`,
  `upload.auto_publish: false`. The QA gate is the only filter, so never weaken it to get a video out. If YouTube
  still shows a video private, `uploader.ensure_public` switches it and checks; failure sends an alert. Back to
  owner review: `upload.privacy: private`. (This project's API uploads have gone public fine; scheduled
  `auto_publish` would still need the YouTube API audit.)
- `upload.contains_synthetic_media: false` (owner 18.11.2026): no "Altered or synthetic content" label; only the
  narrator voice is synthetic and the description says "Voiceover is AI-generated". The API never returns this
  field, so it cannot be read back; check in Studio if asked.
- Game title ("Grand Theft Auto VI" under Category: Gaming) has no API field: Studio -> video -> Show more ->
  Category Gaming -> Game title. Same for end-screen elements (see below).
- Every new public long video re-points the "Full video" link of ALL channel Shorts to itself
  (`uploader.relink_shorts`, `upload.relink_shorts`). Scheduled long videos do it when the hourly check sees them
  public.
- Replacing a video: upload the new one first, verify it (`videos().list`), then delete the old one. Delete
  only what the user asked for; mention orphaned Shorts instead of deleting them unasked.
- Quota: search.list and videos.insert have their own 100-calls/day buckets; other calls use the 10 000-unit
  pool (thumbnails.set 50). Two full runs a day fit.

## Editing style (Learn By Leo, "How to edit SO good your viewers get addicted", sLgHqZSe2o0)

Four pillars: visual variety, immersion (movement + guided attention), visual continuity, immersive audio. What the
bot does with them, so every video keeps the same feel:

- Variety: background cuts every 4-8 s (`clip_min_s`/`clip_max_s`), news panels, quote cards, section titles. Do not
  shorten shots just to cut more; a clip may stay if it is interesting.
- Movement: every background video piece slowly zooms in or out by 6 % (`render._motion`, `video.motion`,
  `motion_zoom`; `scale ... eval=frame`, factor clamped to [1, 1+z] or ffmpeg crashes), stills get Ken Burns.
  No wiggle/shake keyframes.
- Look: background dim, saturation 1.08, light vignette (`video.vignette`, 0.55 rad) on background only; panels,
  cards and the topic bar stay crisp.
- Captions: at most 3 words at a time (`video.caption_words`), current word in brand blue.
- Guided attention: quote cards highlight the key phrase; the topic bar shows where we are in the video.
- Continuity: panels and quotes slide/fade in with a whoosh, section titles animate in. Nothing pops in silently.
- Audio (all self-synthesized in `panels.py`, no licences): whoosh on panels/titles (-24 dBFS), a low hit on every
  section title (`_hit`, -16 dBFS), a riser only before the LAST section, the promised payoff (`_riser`, -22 dBFS;
  overuse kills the effect). The music bed dips to -9 dB for ~1.2 s before each new topic and pauses completely
  for the payoff riser (`music.dip_expr`, `plan["music_dips"]`). Toggles: `brand.hits`, `risers`, `music_dips`.
  Music stays YouTube Audio Library only.

## Background footage

- Only official Rockstar material (`data\gta6_footage`, from Rockstar's YouTube uploads) and the owner's own
  recordings (`data\clips`, always used; the owner is trying to get real gameplay). List Rockstar's uploads with
  the uploads playlist (`playlistItems`, 1 unit/page), not search.list (keep that quota for the jobs), download
  with `yt_dlp --impersonate chrome`, video only, <= 1080p.
- Only landscape 16:9 files belong in the pool. Rockstar's vertical/square cuts (480x854 Shorts versions, the
  1080x1080 trailer) come out blurry when cropped to 16:9: they live in `data\footage_vertical` (not a clip dir).
- "Grand Theft Auto VI: An Extended Look" (27 min gameplay) is age-restricted on YouTube: yt-dlp needs a
  signed-in account, and age-restricted footage could get our own videos age-restricted. Not used; ask the owner.
- PlayStation and Xbox channels re-post Rockstar's GTA VI trailers. Only new cuts are worth taking: the PS5
  "Features Trailer" (8L0CzL2V72c) is real gameplay with HUD, but about half is PS5 ad cards (pastel pink
  background, controller/headset images). Keep gameplay scenes only (scene cuts with `scdet`, drop bright pastel
  frames): `official_8L0CzL2V72c_GTA_VI_PS5_Features_gameplay_only.mp4`. Originals and duplicate cuts with console
  branding (Xbox Pre-Load) live in `data\footage_unused` (not a clip dir). Never put console ads in the pool.
- Background pieces are picked weighted by sqrt(duration), and every piece is checked with blackdetect (same
  meter as QA: pix_th 0.10 after the background dim); black pieces are re-cut at another spot (`BLACK_TRIES`).

## Runbook: what went wrong before and how the bot handles it now

| Symptom | Cause | Fix in the bot |
|---|---|---|
| QA rejects "mustaa < 3 s" twice, job ends for the day | trailer fades to black, darkened by `background_dim` | piece-level blackdetect + re-cut; a picture-only QA failure reuses the same script.json (no new Claude call) |
| Upload fails "RedirectMissingLocation" 10 times | own `httplib2.Http` (for timeouts) treated 308 Resume Incomplete as a redirect | `uploader._service` removes 308 from `redirect_codes` like `googleapiclient.http.build_http`; a test guards it |
| Video on YouTube but stuck "processing", duration P0D | broken upload session | hourly `recover_stuck` (after `upload.stuck_minutes`, 75): re-upload same file and privacy, relink Shorts, delete the stuck one only after the new one is up, alert; once per job |
| Interrupted upload left a video with the right title | crash after insert | `publish()` adopts it (`find_recent_upload`), then sets the thumbnail and checks public |
| C: drive full, tests fail en masse | tests left `bottest_*` temp dirs (622 dirs, 15 GB) | `tests/helpers.py` writes to `E:\gta6-bot\.tmp_tests` and deletes at exit. Ask the owner before deleting anything else to free space |
| Subtitle downloads HTTP 429 | YouTube rate limit on yt-dlp | `--impersonate chrome` + `--sleep-subtitles 3` (owner approved); still falls back to news |
| Docker exec of a helper script: "No module named bot" / bad path | Git Bash path conversion; script outside /app | `MSYS_NO_PATHCONV=1` and `-e PYTHONPATH=/app` |

Viewer feedback rules (17.11 analytics: 44 % CTR but 3:22 average view on 16:40): pay the title off in the first
minute, first chapter before 1:30, practical chapters first (see `catfish-script-style`). The QA report notes a
late first chapter.

## Things the API cannot do (tell the user)

Test & compare thumbnails, end-screen elements (`assets\brand\endscreen_template.png`), mid-roll ad breaks,
Shorts "Related video" link. Checklist: `OHJE.md` section 4.
- End screen: the graphic is rendered into the last ~10 s, but the clickable subscribe/video elements exist only
  in Studio. Every API upload needs: Studio -> video -> End screen -> Import from video (previous video). If the
  owner is signed in to Studio in the built-in browser, offer to do it; never type their password.
- Timeline chapters: the description format is right (0:00 first, >= 3, >= 10 s, no heading line), but they only
  show when the channel has Advanced features (Studio -> Settings -> Channel -> Feature eligibility). Tell the
  owner; it is their account step.

## Never

- Commit or print `.env`, `data\secrets\*`, tokens, or Rockstar/music assets (`data\` is git-ignored on purpose).
- Use other creators' footage or thumbnails. Only Rockstar's official material and the owner's recordings.
- Add paid AI tools (image generators, ElevenLabs) without the owner asking. Music only from YouTube Audio
  Library (Pixabay tracks with "Content ID Registered" cause claims).
- Work around blocks (Reddit 403, bot checks); report them and propose the official route.
