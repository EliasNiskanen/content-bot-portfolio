# Catfish Gaming -videobotti

Tekee kanavalle **Catfish Gaming** noin 12 minuutin peliuutisvideoita ja vinkki-/opasvideoita (nyt GTA 6),
kolme thumbnail-vaihtoehtoa ja Shortsin per video, ja lataa ne YouTubeen **yksityisinä** (tai ajastettuina,
`upload.auto_publish: true`). Dockerissa kaksi ajoa päivässä: uutisvideo 4.30 ja opasvideo 6.30.

Koko käyttöohje: **[OHJE.md](../OHJE.md)**.

```
trending.py     YouTube Data API: kuumimmat GTA 6 -videot (katselut/tunti × sitoutuminen × outlier-kerroin)
research.py     trenditutkimus: YouTube-outlierit + Reddit-pöhinä (aihesignaalit), opasvideon aihehaut
news.py         RSS (Google News, GameSpot, Eurogamer, PC Gamer, IGN, VGC, Insider Gaming, GamesRadar, Push Square,
                Pure Xbox) + artikkelien koko teksti, opasvideolle aihesuodatus
scriptwriter.py Claude: ~2 400 sanan JSON-käsikirjoitus hittirakenteella (uutis- tai opasvideo), kortit, otsikko,
                kuvaus, tagit, thumbnailit, Shortsien omat HPC-minikäsikirjoitukset
clean.py        kirosanasuodatin (prompti + suodatin + uusintayritys)
tts.py          edge-tts (ilmainen) / ElevenLabs / Piper, sanatason ajoitukset tekstitykseen
cards.py        uutis- ja CONFIRMED / RUMOR / LEAK / MY TAKE -kortit
render.py       ffmpeg: trailerit + viralliset still-kuvat taustaksi, kortit, tekstitys, äänen miksaus
music.py        taustamatto (saumaton silmukka, LUFS-normalisointi), intron ja loppuruudun musiikki
brand.py        intro, loppuruutu, brändin värit ja tekstitystyyli
thumbnail.py    virallinen GTA 6 -kuva + maskotin ele + otsikko, 3 varianttia + vertailukuva
poses.py        maskotin eleet (etsivä, yllättynyt, mietteliäs, hype, vihainen, nauraa, osoittaa)
shorts.py       pystyvideot omista Hook-Progression-Climax-käsikirjoituksista, "full video" -loppukortti
state.py        käytetyt trendivideot, ajot ja opasvideoiden aiheet (SQLite)
uploader.py     YouTube-lataus (private tai ajastettu publishAt), AI-merkintä, thumbnail, tilan takaisinluku
scheduler.py    päivittäiset ajot (schedule.jobs) peräkkäin, uusinta, julkaisuajat, schedule --dry-run
housekeeping.py siivous: välitiedostot, vanhat työkansiot/outputit/lokit (cleanup)
logsetup.py     päivälokit data/logs/bot-YYYY-MM-DD.log + heartbeat (Docker healthcheck)
```

## Pika-aloitus (Windows)

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
winget install Gyan.FFmpeg
copy .env.example .env            # täytä YOUTUBE_API_KEY ja ANTHROPIC_API_KEY
auth.bat                          # YouTube-kirjautuminen kerran
run.bat run --mock                # testi ilman API-kutsuja
run.bat run --mock --format guide # opasvideon testi
run.bat research                  # mitä trenditutkimus kerää
run.bat run --no-upload           # oikea video, ei latausta
run.bat run                       # oikea video + lataus yksityisenä
```

Automaattinen ajo (Docker Desktop / Raspberry Pi): `docker compose up -d --build`, lokit `docker compose logs -f`,
aikataulu `docker compose run --rm gta6-bot schedule --dry-run`. Windows-asetukset (käynnistys kirjautuessa, ei
lepotilaa) ja julkaisun vaihto: [OHJE.md](../OHJE.md) kohta 3.

## Mitä repoon ei kuulu

`.env`, `data/secrets/` (OAuth-tunnukset), `data/` (videot, välimuistit, malli ja Rockstarin kuvat) ja `.venv/`
on rajattu pois `.gitignore`-tiedostolla. Rockstarin viralliset kuvat ladataan uudelleen ohjeen mukaan.

Fontit: Anton ja Bangers (SIL Open Font License, lisenssit `assets/fonts/`).
