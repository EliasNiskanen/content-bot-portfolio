# 03 Automaatio

## Päivittäinen aikataulu (Suomen aikaa)

| Mitä | Ajo alkaa | Julkaisu (kun `upload.auto_publish: true`) |
|---|---|---|
| Uutisvideo | 04:30 | 08:00 (USA:ssa yöllä, näkyy aamulla valmiina) |
| Vinkkivideo | 06:30 | 10:00 |
| Uutisvideon Short | | 15:00 (USA:n aamu) |
| Vinkkivideon Short | | 19:00 (USA:n keskipäivä) |

Ajat ja formaatit: `schedule.jobs` ja `shorts.publish_times` tiedostossa `config.yaml`. Ajot tehdään peräkkäin,
eivät päällekkäin.

## Käynnistys Dockerilla

```bash
cd E:\gta6-bot
docker compose up -d --build
docker compose logs -f
docker compose exec gta6-bot python -m bot.main status
```

Kontti käynnistyy uudelleen itse (`restart: unless-stopped`) ja sillä on terveystarkistus (heartbeat).
Data on kansiossa `E:\gta6-bot\data` (ei Dockerin sisällä), joten konttia voi rakentaa uudelleen huoletta.

Windowsissa valvomatonta ajoa varten:

1. Docker Desktop → Settings → General → **Start Docker Desktop when you sign in**.
2. Kone ei saa nukkua klo 4–8: Virranhallinta → lepotila "Ei koskaan" tai herätysajastin (alla).
3. Windows-päivitysten automaattinen uudelleenkäynnistys pois yöajalta (Windows Update → Aktiivisuusaika).

## Julkaisu: ensin yksityisenä, sitten automaattisesti

- Nyt (18.11.2026 alkaen): `upload.privacy: public`, `upload.auto_publish: false`. Videot ja Shortsit menevät julkisiksi heti latauksen jälkeen, jos laatuportti hyväksyy ne. Jos YouTube näyttää videon silti yksityisenä, botti vaihtaa sen julkiseksi ja tarkistaa; jos se ei onnistu, tulee hälytys. Takaisin tarkistettavaksi: `upload.privacy: private`.
- Kun laatu on hyvä: vaihda `upload.auto_publish: true` ja käynnistä kontti uudelleen
  (`docker compose up -d`). Videot ladataan yksityisinä ajastetulla julkaisulla (publishAt), ja YouTube julkaisee
  ne itse aikataulun mukaan. Botti tarkistaa tunnin välein, että ajastetut videot todella julkaistiin.
- Huom: Google voi rajoittaa auditoimattoman API-projektin lataukset yksityisiksi. Botti varoittaa lokissa, jos
  näin käy; silloin julkaistaan Studiossa käsin tai haetaan YouTube API -auditointi.

## Varmistukset

Katso myös [07 Vianetsintä](07-vianetsinta.md) ja OHJE.md:n luotettavuusosio.

- Jokaisella vaiheella on aikaraja, ja koko ajo tapetaan ja yritetään uudelleen, jos se kestää liian kauan.
- Uusintayritys jatkaa siitä mihin jäätiin, eikä samaa videota ladata kahdesti.
- Laatuportti ennen latausta: pituus, ääni, mustat/jäätyneet kohdat, otsikko, kuvaus, thumbnailit, kiroilu.
- Ennakkotarkistus ennen ajoa: levytila, avaimet, YouTube-kirjautuminen, ffmpeg, verkko.
- Ilmoitukset Discordiin/Slackiin (`ALERT_WEBHOOK_URL`), jos jokin epäonnistuu, sekä päivän yhteenveto.

## Kertaluonteiset työt, ohitukset ja latausjono

- `schedule.once`: kertaluonteinen työ tiettynä päivänä (esim. `launchday` 2026-11-18 klo 15:00, julkaisupäivän
  vinkkivideo ensimmäisten pelaajien videoista, `privacy: public` vain tälle työlle). Sama lapsiprosessi, aikaraja,
  uusinta ja laatuportti kuin muillakin.
- `schedule.skip`: säännöllinen työ ohitetaan annettuna päivänä (2026-11-18 opasvideo, jotta kanavan latausrajaan
  jää tilaa).
- `research: launch_streams`: YouTube-haku NZ-julkaisun jälkeen, englanninkieliset tekstitykset yt-dlp:llä. Jos alle
  3 tekijää, uusi haku 15:30 ja 16:00, sitten uutiset + saatavilla olevat tekstitykset. Jos YouTube vastaa
  tekstityshakuihin HTTP 429, haku tehdään selaimena (yt-dlp `--impersonate chrome`, curl_cffi); jos raja silti jatkuu, video tehdään uutisista. Testi:
  `python -m bot.main research --mode launch_streams --after 2026-11-17T00:00:00Z`.
- Latausraja (uploadLimitExceeded): video jää jonoon `data/pending_uploads.json` ja ajastin yrittää tunnin välein
  (`upload-pending` lapsiprosessina). Sama video ei nouse kahdesti (state.db).

## Shortsien linkit ja jumiin jäänyt käsittely

- Kun uusi pitkä video on julkinen, botti päivittää KAIKKIEN kanavan Shortsien "Full video" -linkin siihen
  (`upload.relink_shorts`, enintään `relink_shorts_max` uusinta latausta). Ajastetuilla videoilla tämä tehdään, kun
  tarkistus toteaa videon julkaistuksi.
- Jos pitkä video on yhä YouTuben käsittelyssä (kesto 0) `upload.stuck_minutes` (75) min latauksen jälkeen, tunnin
  välein ajettava tarkistus lataa saman tiedoston uudelleen samalla näkyvyydellä, vaihtaa Shortsien linkit uuteen
  ja poistaa jumiin jääneen vasta kun uusi on ylhäällä. Kerran per työ, ja omistajalle lähtee hälytys.

## Breakdown-vahti (Rockstarin uusi video)

`breakdown.enabled: true`: ajastin lukee 20 min välein Rockstarin uploads-soittolistan (ei search.list). Uusi GTA VI
-video (julkaistu `watch_from` jälkeen, >= 30 s) -> ladataan `data/gta6_footage/`, avainkuvat käsikirjoittajalle,
breakdown-video (vähintään 8 min) tehdään heti edellisen työn jälkeen, enintään `max_per_day` päivässä, julkisena.
Testi: `python -m bot.main breakdown --dry-run --watch-from 2026-08-01` ja `run --mock --format breakdown`.

## Jos kone ei ole päällä

Jos kone on sammuksissa, ajoja ei tehdä. Myöhästynyt ajo tehdään kerran, kun kone herää.

| Vaihtoehto | Hinta | Huomio |
|---|---|---|
| Kone lepotilaan + herätys | ilmainen | Tehtävien ajoitus → tehtävä klo 4:20 → "Herätä tietokone tämän tehtävän suorittamiseksi". Toimii lepotilasta, ei sammutetusta |
| Minitietokone (esim. Intel N100) aina päällä | arviolta 150–250 € + sähkö | Suositus pidemmälle. Kopioi `data` ja `.env`, aja `docker compose up -d` |
| Pilvipalvelin (4–8 ydintä) | arviolta 10–25 €/kk | Tunnukset ja Rockstarin aineisto palvelimelle, palvelin suojattava |

Hinnat ovat suuntaa antavia. Raspberry Pi on nykyisille videoille luultavasti liian hidas.

## Siirto toiselle koneelle

1. Asenna Docker ja Git, kloonaa repo: `git clone https://github.com/EliasNiskanen/content-bot-portfolio.git`.
2. Kopioi vanhalta koneelta `.env` ja koko `data`-kansio (sisältää `secrets`, kuvat, musiikit, `state.db`).
3. `docker compose up -d --build` ja tarkista `status`.
4. Pysäytä vanha kone (`docker compose down`), ettei kaksi konetta lataa samoja videoita.
