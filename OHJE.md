# Catfish Gaming -videobotti: käyttöohje

Botti tekee pitkän peliuutisvideon (nyt GTA 6) tai vinkki-/opasvideon yhdestä aiheesta, kolme
thumbnail-vaihtoehtoa ja Shortsin (`shorts.count: 1`). Dockerissa se ajaa joka päivä kaksi videota: uutisvideo
klo 4.30 ja opasvideo klo 6.30 (kohta 3, *Automaattinen ajo*). Oletuksena kaikki ladataan YouTubeen **yksityisinä**:
sinä katsot, valitset ja julkaiset. Kun laatu on todettu hyväksi, yksi asetus (`upload.auto_publish: true`) vaihtaa
ajastettuun julkaisuun.

## 1. Mitä botti tekee

| Vaihe | Moduuli | Mitä tapahtuu |
|---|---|---|
| 1. Trendit | `trending.py` | Hakee GTA 6 -videot viim. 48 h. Pisteet = katselut/tunti × sitoutuminen × **outlier-kerroin** (video sai X kertaa kanavansa normaalit katselut). Shortsit, alle 3 min videot ja muunkieliset pois. Jo käytettyjä ei oteta uudelleen. |
| 2. Trenditutkimus | `research.py` | Mistä puhutaan: YouTuben outlier-videot (8 parasta) ja Redditin r/GTA6, r/GTA, r/rockstar (hot + päivän top, 15 parasta otsikkoa pisteineen). Vain aihesignaaleja, ei faktalähteitä. Jos lähde ei vastaa, botti jatkaa ilman sitä. |
| 3. Uutiset | `news.py` | 10 tuoretta artikkelia RSS-syötteistä (Google News, GameSpot, Eurogamer, PC Gamer, IGN, VGC, Insider Gaming, GamesRadar, Push Square, Pure Xbox), julkaisijoiden omat sivut ensin (koko teksti), max 2 per lähde. |
| 4. Käsikirjoitus | `scriptwriter.py` | Claude kirjoittaa ~2 400 sanaa (≥ 2 200, muuten pyydetään uudelleen) hittivideoiden rakenteella (ks. kohta 5): faktat vain artikkeleista, lähde joka kerta, GTA 5 -vertailu joka luvussa. Sävy: rento 30-vuotias pelaaja, ei teinislangia, ei kiroilua. Lisäksi Shortsien (`shorts.count`) omat minikäsikirjoitukset. |
| 5. Ääni | `tts.py` | edge-tts (ilmainen), ääni `en-US-AndrewMultilingualNeural`. |
| 6. Video | `render.py`, `cards.py`, `brand.py`, `music.py` | 3 s intro omalla musiikilla, trailerit + Rockstarin viralliset still-kuvat hitaalla zoomilla, uutis- ja fakta-kortit, tekstitys, taustamusiikki, loppuruutu loppumusiikilla. Trailereiden mustat palkit rajataan pois. |
| 7. Thumbnailit | `thumbnail.py`, `poses.py` | Virallinen GTA 6 -kuva taustalle, maskotin ele sisällön mukaan, otsikko ja leima. |
| 8. Shortsit | `shorts.py` | Omat Hook-Progression-Climax-minikäsikirjoitukset pystymuotoon, koukku ylhäällä, taustamusiikki, loppukortti pitkään videoon. |
| 9. Lataus | `uploader.py` | Private-tila (tai ajastettu julkaisu, `upload.auto_publish`), AI-merkintä, thumbnail 1, kuvaus isojen GTA-kanavien pohjalla (kohta 5). Lopuksi videon tila luetaan takaisin YouTubesta ja kirjataan lokiin. |
| 10. Siivous | `housekeeping.py` | Ajon isot välitiedostot pois, vain 10 uusinta työkansiota ja 14 päivän outputit jäävät (`cleanup.*`). |

Yksi ajo kestää noin 30–45 minuuttia (ääni on hitain vaihe).

## 2. Asennus (Windows)

1. Python 3.12 ja virtuaaliympäristö kansioon `.venv`:
   ```bash
   python -m venv .venv
   .venv\Scripts\python.exe -m pip install -r requirements.txt
   ```
2. ffmpeg: `winget install Gyan.FFmpeg` (botti löytää sen itse, vaikka PATH ei olisi päivittynyt).
3. Avaimet: kopioi `.env.example` nimelle `.env` ja täytä
   - `YOUTUBE_API_KEY` – Google Cloud Console → YouTube Data API v3 → Credentials → API key
   - `ANTHROPIC_API_KEY` – console.anthropic.com
4. YouTube-kirjautuminen kerran:
   - Google Cloud Console → Credentials → **OAuth client ID (Desktop app)** → lataa JSON ja tallenna nimellä
     `data\secrets\client_secret.json` (Windows saattaa lisätä toisen `.json`-päätteen – tarkista).
   - OAuth consent screen → **Test users** → lisää kanavan Google-tili.
   - Aja `auth.bat`, avaa linkki, valitse kanava, hyväksy. Syntyy `data\secrets\token.json`.
   - Kanava pitää vahvistaa puhelinnumerolla (youtube.com/verify), jotta botti voi asettaa thumbnailin.
5. Rockstarin viralliset kuvat (taustat ja thumbnailit), noin 360 Mt:
   - rockstargames.com/VI/downloads → *Screenshots* ja *Artwork & Wallpapers* -zipit
   - pura kansioihin `data\official_images\screenshots` ja `data\official_images\artwork`
   - kansion nimi on kategoria (esim. `Vice City`, `Lucia Caminos`), josta Claude valitsee thumbnailin taustan
6. Trailerit: `data\gta6_footage` (Rockstarin viralliset trailerit). Omat tallenteet: `data\clips`.
   Taustamusiikki (tekijänoikeusvapaa) kansioon `data\music`.

## 3. Käyttö

```bash
run.bat run --mock          # testaa koko putken ilman API-kutsuja
run.bat run --no-upload     # oikea video, jää kansioon data\output
run.bat run                 # oikea video + lataus yksityisenä
run.bat run --script oma.json   # valmis käsikirjoitus (+ "articles"), ei API-kutsuja eikä latausta
run.bat run --format guide                  # opasvideo: seuraava käyttämätön aihe listalta guide.topics
run.bat run --format guide --topic "GTA 6 police vs GTA 5"   # opasvideo omasta aiheesta
run.bat run --mock --format guide           # opasvideon putkitesti ilman API-kutsuja
run.bat research                            # näyttää mitä trenditutkimus kerää (YouTube, Reddit, RSS)
run.bat research --topic "GTA 6 animals"    # sama opasvideon aiheelle
run.bat schedule            # pyörii ja ajaa joka päivä schedule.jobs-ajot (uutiset 4.30, opas 6.30)
run.bat schedule --dry-run  # näyttää seuraavat ajot ja julkaisuajat, ei aja mitään
run.bat cleanup --dry-run   # näyttää mitä siivous poistaisi; ilman --dry-run poistaa
```

Jokainen ajo kirjoittaa lokin myös tiedostoon `data\logs\bot-YYYY-MM-DD.log` (30 päivää säilyy).

**Opasvideo (`--format guide`)**: yksi aihe per video, numeroidut luvut ("TIP 1: ..."), joissa jokaisessa mitä se
on, miten se eroaa GTA 5:stä ja käytännön neuvo. Aiheet ovat `guide.topics`-listalla järjestyksessä; käytetyt
tallentuvat `state.db`:hen (taulu `guide_topics`) ja lista alkaa alusta, kun kaikki on käytetty. Tutkimus: YouTube-haku
aiheella (30 pv), r/GTA6-haku, Google News -haku aiheella ja tavalliset syötteet aiheen avainsanoilla
(`guide.topics[].keywords`). Muuten sama putki (kortit, paneelit, musiikki, thumbnailit, Shortsit, lataus upload.privacy-asetuksella).
Opasvideon kuvaukseen ei tule "Topic trending thanks to" -riviä.

`run.bat` ja `auth.bat` toimivat mistä tahansa kansiosta.

### Automaattinen ajo (Docker Desktop, Windows)

Botti pyörii omassa kontissaan (`docker-compose.yml`, palvelu `gta6-bot`) ja ajaa `schedule.jobs`-listan joka
päivä (Helsingin aikaa):

| Ajo | Alkaa | Muoto | Julkaisu (vain `auto_publish: true`) | Short |
|---|---|---|---|---|
| `news` | 04:30 | uutisvideo | 08:00 | 15:00 |
| `guide` | 06:30 | opasvideo (seuraava `guide.topics`-aihe) | 10:00 | 19:00 |

Ajot tehdään peräkkäin (yksi renderöinti kerrallaan, 30–45 min); jos uutisajo on yhä kesken klo 6.30, opasajo
alkaa heti sen perään. Epäonnistunut ajo kirjataan lokiin virheineen ja yritetään kerran uudelleen 20 min päästä
(`schedule.retry_minutes`); scheduler itse ei kaadu. Jos kone nukkui ajoaikana, myöhästynyt ajo tehdään kerran heti
herättyä.

Kerran tehtävät asetukset:

1. **Docker Desktop** → Settings → General → **Start Docker Desktop when you sign in to your computer** päälle.
   Kontti käynnistyy itse (`restart: unless-stopped`), kun Docker Desktop käynnistyy.
2. **Windows ei saa nukkua klo 4.30–8.00**: Asetukset → Järjestelmä → Virta ja akku (Virta ja lepotila) →
   *Kun tietokone on kytketty verkkovirtaan, siirrä laite lepotilaan* = **Ei koskaan**. Näytön saa sammua. Kone
   pitää olla päällä ja käyttäjä kirjautuneena (Docker Desktop käynnistyy vasta kirjautumisen jälkeen).
   Windows Update: Asetukset → Windows Update → Lisäasetukset → *Aktiiviset tunnit* esim. 3–23, ettei kone
   käynnisty uudelleen kesken ajon.
3. Käynnistä (kansiossa `E:\gta6-bot`):
   ```bash
   docker compose up -d --build
   ```
   `--build` tarvitaan aina, kun koodi (`bot\`, `assets\`) on muuttunut. `config.yaml` luetaan uudelleen ennen
   jokaista ajoa, mutta varmin tapa ottaa muutos käyttöön on `docker compose restart`.
4. Tarkista: `docker compose run --rm gta6-bot schedule --dry-run` (seuraavat ajo- ja julkaisuajat) ja
   `docker compose ps` (tila `healthy`: scheduler koskettaa `data\logs\heartbeat` minuutin välein).

Lokit ja ylläpito:

```bash
docker compose logs -f            # elävä loki (Ctrl+C lopettaa katselun, botti jatkaa)
docker compose logs --since 24h   # viimeinen vuorokausi
docker compose ps                 # healthy / unhealthy
docker compose restart            # config.yaml-muutosten jälkeen
docker compose down               # pysäytä botti
docker compose run --rm gta6-bot run --mock    # putkitesti kontissa (ei API-kutsuja)
docker compose run --rm gta6-bot cleanup --dry-run
```

Samat lokit ovat tiedostoina `data\logs\bot-YYYY-MM-DD.log`. Varoitukset (`WARNING`/`ERROR`) kannattaa
katsoa joka aamu, esim. `findstr "WARNING ERROR" data\logs\bot-2026-11-18.log`.

### Julkaisu: ensin yksityisenä, sitten ajastettuna

- **Alkuun (`upload.auto_publish: false`, oletus)**: kaikki ladataan yksityisinä kuten ennenkin. Katso videot
  Studiossa ja julkaise itse (kohta 4).
- **Kun laatu on hyvä**: vaihda `config.yaml`:ssa
  ```yaml
  upload:
    auto_publish: true
  ```
  ja aja `docker compose restart`. Nyt pitkä video ladataan yksityisenä + `publishAt` = ajon `publish_at`
  (uutiset 08:00, opas 10:00; jos aika on jo mennyt, seuraavana päivänä), ja Shortsit ajastetaan
  `shorts.publish_times`-paikoille (15:00 ja 19:00 = Yhdysvaltojen itärannikon aamu ja keskipäivä) järjestyksessä,
  aina aikaisintaan pitkän videon jälkeen. Jos Shortseja on enemmän kuin paikkoja, loput menevät seuraavalle päivälle.
  Varatut paikat ovat `state.db`:n taulussa `scheduled`.
- **Google API -auditointi**: Google voi lukita varmentamattoman API-projektin (luotu 28.7.2020 jälkeen) kaikki
  lataukset yksityisiksi, eikä ajastus silloin julkaise niitä. Botti lukee jokaisen latauksen tilan takaisin ja
  tarkistaa julkaisuajan jälkeen, julkaistiinko video; jos ei, lokiin tulee varoitus *"YouTube lukitsi videon ...
  yksityiseksi ..."* ja video pitää julkaista itse Studiossa. Pysyvä korjaus: Google Cloud Console →
  YouTube Data API v3 → hae **YouTube API Services Audit and Quota Extension** -lomakkeella
  (support.google.com/youtube/contact/yt_api_form) projektin auditointia. Vaikka auditointi on kunnossa,
  Studion käsityöt (kohta 4: Test & compare, loppuruutu, mid-roll, Related video) jäävät sinulle.

## 4. Päivittäinen rutiini YouTube Studiossa (5–10 min)

- Katso uusi yksityinen video läpi ja tarkista faktat.
- Avaa `data\output\*_thumbs_compare.jpg`: lukeeko teksti pienessäkin koossa?
- **Thumbnail → Test & compare**: lisää kaikki 3 thumbnailia.
- **Loppuruutu**: tilausnappi logon päälle ja kaksi videota kehyksiin (`assets\brand\endscreen_template.png`).
- **Mainokset**: 8+ min video sallii mid-roll-mainokset – lisää ne Studiossa (ei onnistu API:n kautta).
- Shortsit: julkaise porrastetusti (1–2/päivä; `auto_publish` ajastaa ne itse) ja lisää jokaiseen
  **Related video** -linkki pitkään videoon.

## 5. Videon raamit

**Pitkä video (16:9, vähintään 12 min, ~2 400 sanaa)**

| Kohta | Sisältö |
|---|---|
| 0–3 s | Logo-intro |
| 3–23 s | Koukku: jotain mihin jokainen GTA 5 -pelaaja samaistuu + 2–3 konkreettista yllättävää faktaa. Lupaus ("kävin läpi jokaisen trailerin, haastattelun ja ennakon", vain jos totta). Yksi avoin silmukka, joka maksetaan lopussa. |
| 30 s – 11 min | 8–14 lukua (osiota), yksi aihe kussakin: GTA 5 vs GTA 6 -vertailu, lähde nimeltä, myyttien murtaminen selvällä tuomiolla ("Busted."), käytännön neuvo |
| 40–50 % | Yksi tilauspyyntö, sidottu seuraavaan teaseriin |
| loppu | "Here's what I'm doing first: one, two, three", hot take, yksi kysymys kommentteihin ("I'm reading all of them"), lyhyt tilausrivi |
| viim. 12 s | Loppuruutu + loppumusiikki |

Pituus tulee oikeasta sisällöstä: jos uutisia on vähän, Claude menee syvemmälle (tausta, aikajana, vertailu
aiempiin julkaisuihin), ei toista samaa. Perustelu (ohjevideo *Claude Code Monetized My Faceless Channel*):
8+ min sallii mid-roll-mainokset, ja 12 min video kerryttää monetisointiin tarvittavaa katseluaikaa yli
kaksinkertaisesti 5 min videoon verrattuna.

**Short (9:16, 20–50 s, `shorts.count: 1` = 2 Shortsia/päivä)**: oma 60–120 sanan minikäsikirjoitus Hook-Progression-Climax-rakenteella
(skill `catfish-shorts`): koukku (aihe + avoin kysymys, alle 5 s) → eteneminen → vastaus vasta LOPUSSA → yksi rivi,
joka ohjaa koko videoon (sen päällä "FULL VIDEO ON THE CHANNEL" + @catfishgaminghd). Koukku-yläpalkki koko ajan,
taustamusiikki satunnaisesta kohdasta. Vanhat käsikirjoitukset (Shorts = pitkän videon segmenttien indeksit)
toimivat yhä `--script`-ajossa.

**Kuvaus (pitkä video ja opas)**: 1) otsikko, 2) avainsanarikas tiivistelmä (julkaisufakta jos totta, lukujen aiheet,
CONFIRMED / REPORTED / BUSTED, miten tarkistettu), 3) kysymys katsojille, 4) Chapters, 5) LIKE + SUBSCRIBE -rivi
(`upload.cta_line`), 6) lähteet, musiikkikrediitti (`upload.music_credit`), AI-ääni-rivi, "Not affiliated..."
(`upload.disclaimer`), 7) hashtagit: 3 tarkinta ensin (`upload.lead_hashtags`, opasaiheen oma `hashtag`, esim.
#GTA6vsGTA5), sitten muut, max 30. Yli 4 900 merkkiä → lähteitä karsitaan lopusta.

**Musiikki** (`music.py`, YouTube Audio Library):

| Kohta | Kappale | Taso |
|---|---|---|
| Intro 0–3 s | *Neon nights* (Patrick Patrikios) kohdasta 12,6 s → biitti pudotus ~0,4 s introon, häivytys 0,8 s ensimmäisten sanojen alle | ~ -16 LUFS |
| Puhe | *Pulsar* (The Grey Room / Density & Time), tasainen osa 20–224 s saumattomana silmukkana (3 s ristihäivytys), välimuisti `data\brand_cache\bed_*.flac` | -30 LUFS (puhe ~ -21) |
| Loppuruutu | *Neon nights* viimeiset 12 s niin, että kappaleen oma loppu (159,5 s) osuu videon loppuun; 2 s ristihäivytys taustamatosta | -26 LUFS puheen alla, -16 LUFS hiljaisessa hännässä |
| Shortsit | *Pulsar* satunnaisesta kohdasta silmukkaa | -30 LUFS |

Jos `brand.intro.music` on tyhjä tai tiedosto puuttuu, intro käyttää syntetisoitua whoosh/tömähdys-ääntä.
Vaihda kappaleet: `video.music_file` + `video.music_loop`, `brand.intro.music` + `music_start`,
`brand.outro.music` + `music_end`, ja päivitä `upload.music_credit`.

**Thumbnail**: virallinen GTA 6 -kuva, teksti vasemmalla (max 4 sanaa, yksi korostettu sana sinisellä),
maskotti oikealla alhaalla, leima vasemmalla ylhäällä. Variantit eri kulmilla ja eri taustoilla.

## 6. Maskotin eleet

Claude valitsee jokaiselle thumbnailille eleen sisällön mukaan:

| Ele | Milloin |
|---|---|
| `default` | yleinen uutinen |
| `detective` (hattu + suurennuslasi) | tutkii, faktantarkistus, vuoto tai huhu |
| `shocked` (isot silmät, huutomerkit) | iso yllätys |
| `hype` (säteet, kimallukset) | hyvä uutinen, vahvistus |
| `thinking` (ajatuskupla, kysymysmerkit) | avoin kysymys, spekulaatio |
| `angry` (punainen hehku, höyry) | huono uutinen, viivästys, kohu |
| `laughing` (naurun kyyneleet) | hauska tai absurdi juttu |
| `pointing` (nuoli otsikkoon) | yksi tietty yksityiskohta tai luku |

Eleet on piirretty logon maskotin päälle tarvikkeina (`python -m bot.poses` luo ne uudelleen). Jos haluat
oikeita piirroksia (esim. evät poskilla ja suu auki), tallenna läpinäkyvä PNG nimellä
`assets\brand\poses\<ele>.png` – botti käyttää sitä automaattisesti eikä generaattori ylikirjoita sitä.

## 7. Tärkeimmät asetukset (`config.yaml`)

| Asetus | Arvo | Selitys |
|---|---|---|
| `script.target_minutes_long` | 12 | videon tavoitepituus |
| `script.target_words_long` / `min_words_long` | 2400 / 2200 | sanamäärä; alle minimin pyydetään uudelleen |
| `script.slang_level` | adult | adult / low / medium / high, kiroilu aina estetty |
| `channel.rules` | lista | kanavan säännöt, annetaan Claudelle joka kerta |
| `news.max_articles` / `news.feeds` | 10 / lista | lähdeartikkelien määrä ja RSS-syötteet |
| `trending.queries` | 7 hakua | YouTube-trendihaut (jokainen 100 kiintiöyksikköä) |
| `research.*` | | `reddit_subs`, `reddit_on_topic_subs` (koko subi aiheesta), `youtube_top` 8, `reddit_top` 15, `topic_lookback_days` 30 |
| `guide.topics` | 12 aihetta | opasvideoiden aiheet järjestyksessä, `keywords` uutissuodatukseen, valinnainen `hashtag` |
| `shorts.count` | 1 | Shortsit per video (2 ajoa/päivä → 2 Shortsia/päivä) |
| `shorts.publish_times` | ["15:00", "19:00"] | Shortsien julkaisupaikat (`auto_publish`) |
| `video.music_file` / `music_loop` / `music_lufs` | Pulsar / [20, 224] / -30 | taustamatto |
| `brand.intro.music*` / `brand.outro.music*` | Neon nights | intron ja loppuruudun musiikki, tasot LUFS |
| `upload.lead_hashtags` / `cta_line` / `disclaimer` / `music_credit` | | kuvauksen pohja |
| `video.stills_ratio` | 0.35 | osuus taustasta virallisia still-kuvia |
| `upload.topic_hashtags` / `hashtags` | listat | aihe- ja yleiset pelihashtagit kuvaukseen (max 30) |
| `upload.privacy` | public | private / unlisted / public / none (public: laatuportin jälkeen suoraan julkiseksi) |
| `upload.auto_publish` | false | true = ajastettu julkaisu (private + publishAt), vaatii API-auditoinnin |
| `tts.edge_voice` | en-US-AndrewMultilingualNeural | ääninäytteet: `data\voice_samples` |
| `brand.intro` / `brand.outro` | 3 s / 12 s | intro ja loppuruutu |
| `schedule.jobs` | news 04:30 → 08:00, guide 06:30 → 10:00 | päivittäiset ajot: `name`, `time`, `format` (long / guide), `publish_at`, valinnainen `topic`. Ilman listaa `schedule.time` = yksi uutisajo |
| `schedule.retry_minutes` | 20 | epäonnistuneen ajon uusinta (kerran) |
| `cleanup.keep_work` / `keep_output_days` / `keep_logs_days` | 10 / 14 / 30 | siivous onnistuneen ajon jälkeen ja `cleanup`-komennolla; `data\secrets`, `promo`, `official_images`, `music`, `brand_*`, `gta6_footage`, `models` ja `state.db` eivät koskaan lähde |
| `paths.logs_dir` | data\logs | päivälokit + heartbeat |

Kun kanava laajenee muihin peleihin: vaihda `trending.queries`, `trending.title_must_contain_any`,
`news.keywords`, `upload.topic_hashtags` ja `channel.rules`.

## 8. Kustannukset

| Palvelu | Hinta |
|---|---|
| Claude (claude-sonnet-5-5) käsikirjoitus | noin 0,15–0,30 $ / video (12 min video, mahdollinen uusintapyyntö) |
| YouTube Data API | ilmainen, 10 000 yksikköä/päivä. Lataus ≈ 1 600 / video, haku 100, tilan luku 1. Uutisajo (7 hakua + video + 1 Short) ≈ 4 000, opasajo (1 haku + video + 1 Short) ≈ 3 400 → päivän kaksi ajoa ≈ 7 400. Käsin tehty lisäajo samana päivänä ylittää kiintiön (`shorts.count: 2` samoin). |
| Reddit | julkinen JSON, ei avainta; voi palauttaa 403 (estetty), jolloin botti jatkaa ilman Reddit-signaaleja |
| edge-tts | ilmainen |

## 9. Ongelmia

| Oire | Syy ja korjaus |
|---|---|
| `No module named 'bot'` | aja `run.bat` / `auth.bat`, tai ensin `cd E:\gta6-bot` |
| `client_secret.json` puuttuu | tiedostolla on kaksi päätettä (`.json.json`) – nimeä uudelleen |
| Kirjautuminen: *Pääsy estetty / access_denied* | lisää tili OAuth consent screenin **Test users** -listaan |
| `WinError 10048` portti 8765 | edellinen kirjautuminen jäi päälle – sulje terminaali tai Ctrl+C |
| Thumbnail 403 | kanavaa ei ole vahvistettu puhelinnumerolla |
| `edge-tts: ei ääntä` lokissa | palvelu palauttaa välillä tyhjää, botti yrittää uudelleen itse |
| Testitilassa kirjautuminen vanhenee 7 päivässä | vaihda OAuth-sovellus tilaan *In production* (muuten automaattiajot katkeavat viikon päästä) |
| Lokissa *"YouTube lukitsi videon ... yksityiseksi"* | API-projekti ei ole auditoitu: julkaise Studiossa, hae auditointia (kohta 3, *Julkaisu*) |
| `docker compose ps` näyttää *unhealthy* | scheduler ei ole koskenut heartbeatia 5 minuuttiin tai ajo on jumissa yli 4 h: `docker compose logs --tail 200`, sitten `docker compose restart` |
| Aamun ajo jäi tekemättä | kone nukkui tai Docker Desktop ei ollut käynnissä (kirjautuminen puuttui): katso virta-asetukset, `docker compose ps` |
| Levy täyttyy | `run.bat cleanup` (tai `docker compose run --rm gta6-bot cleanup`); säädä `cleanup.*` |

## 10. Pidä kanava monetisoitavana

YouTube ei rankaise tekoälystä vaan massatuotetusta, toistuvasta sisällöstä (*inauthentic content*). Siksi:
oma käsikirjoitus ja näkökulma lähteineen, ei kopiointia, AI-ääni merkitään, ja jokainen video katsotaan
ennen julkaisua. Pelikuvana vain Rockstarin virallinen materiaali ja omat tallenteet, lyhyinä paloina
selostuksen alla.
