# 02 Käyttö

`run.bat` ja `auth.bat` toimivat mistä tahansa kansiosta. Dockerissa samat komennot ajetaan muodossa
`docker compose run --rm gta6-bot <komento>` (kansiossa `E:\gta6-bot`).

## Komennot

| Komento | Mitä tekee | Maksaa |
|---|---|---|
| `run.bat run --mock` | Koko putki ilman API-kutsuja, lyhyt testivideo | ei |
| `run.bat run --mock --format guide` | Sama vinkkivideolle | ei |
| `run.bat research` | Näyttää, mitä trenditutkimus löytää (YouTube, uutiset, Reddit) | ei |
| `run.bat run --no-upload` | Oikea uutisvideo, jää kansioon `data\output` | Claude ~0,2 $ |
| `run.bat run` | Oikea uutisvideo + lataus | Claude ~0,2 $ |
| `run.bat run --format guide` | Vinkkivideo jonon seuraavasta aiheesta | Claude ~0,2 $ |
| `run.bat run --format guide --topic "GTA 6 police vs GTA 5"` | Vinkkivideo annetusta aiheesta | Claude ~0,2 $ |
| `run.bat run --script data\work\<ajo>\script.json` | Sama käsikirjoitus uudelleen renderöitynä (ei Claudea, ei latausta) | ei |
| `run.bat schedule` | Päivittäinen aikataulu ilman Dockeria | |
| `run.bat schedule --dry-run` | Näyttää seuraavat ajo- ja julkaisuajat | ei |
| `run.bat status` | Päivän ajot, linkit, virheet, levytila | ei |
| `run.bat cleanup` / `cleanup --dry-run` | Vanhojen välitiedostojen siivous | ei |
| `auth.bat` | YouTube-kirjautuminen | ei |

## Videoformaatit

| Formaatti | Sisältö | Pituus |
|---|---|---|
| `long` (uutiset) | Päivän GTA 6 -uutiset: vahvistetut, huhut, vuodot, oma näkemys | vähintään 12 min |
| `guide` (vinkit/peli) | Yksi aihe kerrallaan: kartta, poliisit vs GTA 5, ajofysiikka, aseet, eläimet, NPC:t... | vähintään 12 min |
| Shorts | Oma lyhyt käsikirjoitus (koukku → eteneminen → ratkaisu lopussa) | 20–45 s |

Vinkkiaiheiden jono on asetuksessa `guide.topics`; botti muistaa käytetyt ja aloittaa alusta, kun kaikki on käyty.

## Mitä yksi ajo tuottaa (`data\output`)

- `<aika>_<otsikko>.mp4`: pitkä video
- `..._short1.mp4`: Shorts
- `..._thumb1_bigword.jpg`, `..._thumb2_banner.jpg`, `..._thumb3_stack.jpg` ja `..._thumbs_compare.jpg`
- `<aika>_<otsikko>.json`: otsikko, kuvaus, tagit, Shortsien tiedot

Työkansio `data\work\<aika>_<otsikko>\` sisältää `script.json`-käsikirjoituksen (artikkeleineen), äänen ja
tekstityksen. Isot välitiedostot poistetaan onnistuneen ajon jälkeen.

## Kulut

| Palvelu | Hinta |
|---|---|
| Claude (claude-sonnet-5-5) | noin 0,15–0,30 $ per video, kaksi videota päivässä noin 10–18 $/kk |
| YouTube Data API | ilmainen; haut ja lataukset omissa 100 kutsun päiväkiintiöissään |
| edge-tts, YouTube Audio Library | ilmaisia |
