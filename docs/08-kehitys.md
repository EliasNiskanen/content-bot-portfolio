# 08 Kehitys

## Rakenne

```
bot/
  main.py         komentorivi (run, schedule, research, status, cleanup), yhden ajon putki
  scheduler.py    päivittäiset ajot peräkkäin, uusintayritys, julkaisuajat
  research.py     trenditutkimus (YouTube-outlierit, Reddit, aihehaut)
  trending.py     YouTube Data API -haku ja pisteytys
  news.py         RSS ja artikkelien tekstit
  scriptwriter.py Claude-käsikirjoitus (prompti, JSON-skeema, tarkistus, mock-käsikirjoitukset)
  clean.py        kirosanasuodatin
  tts.py          puhe (edge-tts / ElevenLabs / Piper), sanatason ajoitukset
  cards.py        uutis-, fakta- ja lainauskortit, Shortsin yläpalkki ja loppukortti
  panels.py       uutispaneeli, lainauskortti-klipit, osio-otsikot, siirtymä-äänet
  render.py       tausta (trailerit + still-kuvat), tekstitys, lopullinen ffmpeg-renderöinti
  music.py        taustamusiikin luuppi, intro/outro-musiikki, miksaus ja äänitasot
  brand.py        intro, loppuruutu, brändin värit
  thumbnail.py    thumbnailit (youtube-tyyli + classic), hahmokirjasto
  poses.py        maskotin eleet
  shorts.py       Shortsien renderöinti
  uploader.py     YouTube-lataus, ajastus, tarkistus
  state.py        SQLite: käytetyt aiheet, ajot, vinkkiaiheet, ajastukset
  housekeeping.py siivous
  logsetup.py     lokit ja heartbeat
.claude/skills/   Claude Coden skillit: tyyli- ja laatusäännöt
docs/             tämä käsikirja
config.yaml       kaikki asetukset kommentteineen
```

## Skillit

Skillit ovat Claude Coden ohjeita, jotka latautuvat, kun Claude Code avataan tässä kansiossa (kopio myös
`~/.claude/skills`). Ne ovat videoiden tyylin "lähdekoodi": kun tyyliä muutetaan, muutetaan skill ja
vastaava koodi samassa commitissa.

| Skill | Sisältö |
|---|---|
| `catfish-video` | Putki, ajaminen, tarkistuslista, julkaisu, automaatio, kiellot |
| `catfish-script-style` | Hittirakenne, sävy, formaatit, faktat, otsikot, kuvaukset |
| `catfish-shorts` | Koukku → eteneminen → ratkaisu, julkaisutahti |
| `catfish-thumbnails` | Thumbnail-tyyli, kuvat, hahmokirjasto, perheystävällisyys |

## Kehitystapa

1. Muutos koodiin ja tarvittaessa skilliin.
2. `run.bat run --mock` ja `run.bat run --mock --format guide`; testit `tests/`-kansiossa.
3. Isommissa muutoksissa `--script`-uudelleenrenderöinti oikealla käsikirjoituksella ja ruutujen tarkistus.
4. Commit ja push: `git add -A`, `git commit`, `git push`. `.gitignore` pitää salaisuudet ja aineiston poissa.
5. Docker: `docker compose up -d --build` ottaa uuden koodin käyttöön.

## Kanavan laajentaminen muihin peleihin

Asetukset: `trending.queries`, `trending.title_must_contain_any`, `news.keywords`, `news.feeds`,
`research.reddit_subs`, `guide.topics`, `upload.topic_hashtags`, `channel.rules`. Kuvat ja trailerit omiin
kansioihinsa, hahmokuvat `data\promo\cutouts`. Otsikkosääntö "alkaa GTA 6" on promptissa ja laatuportissa:
yleistä se pelin nimeksi, kun toinen peli otetaan mukaan.
