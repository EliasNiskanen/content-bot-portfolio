# 07 Vianetsintä

Aloita aina: `run.bat status` (tai Dockerissa `docker compose exec gta6-bot python -m bot.main status`) ja
päivän loki `data\logs\bot-YYYY-MM-DD.log`.

| Oire | Syy | Korjaus |
|---|---|---|
| `No module named 'bot'` | komento ajettu väärästä kansiosta | käytä `run.bat` / `auth.bat` |
| `client_secret.json` puuttuu | tiedostolla kaksi päätettä | nimeä `client_secret.json.json` → `client_secret.json` |
| Kirjautuminen: *Pääsy estetty / access_denied* | tili ei ole testikäyttäjä | OAuth consent screen → Test users → lisää tili |
| `WinError 10048` portti 8765 | edellinen kirjautuminen jäi päälle | sulje terminaali tai Ctrl+C, aja uudelleen |
| Thumbnail 403 | kanavaa ei ole vahvistettu | youtube.com/verify |
| Kirjautuminen vanhenee viikossa | OAuth-sovellus testitilassa | vaihda tilaan In production, aja `auth.bat` |
| Lokissa `edge-tts: ei ääntä` | palvelu palauttaa välillä tyhjää | normaalia, botti yrittää uudelleen |
| Reddit 403 "Blocked" | Reddit estää tunnistamattomat botit | toimii ilman; korjaus = Redditin OAuth-sovellus |
| Laatuportti esti latauksen | video liian lyhyt, ääni hiljainen, mustia kohtia tms. | syy lokissa ja `status`-näkymässä; tiedostot jäävät `data\output` tarkistettavaksi |
| Ajo tapettiin aikarajaan | jokin vaihe jumittui | botti yrittää uudelleen; jos toistuu, katso loki |
| Video jäi yksityiseksi vaikka `auto_publish: true` | Google rajoittaa auditoimattoman API-projektin | julkaise Studiossa tai hae YouTube API -auditointi |
| `ANTHROPIC_API_KEY`-virhe tai 429 | saldo loppui tai raja täynnä | console.anthropic.com → lisää saldoa / nosta raja |
| Levy täynnä | vanhoja videoita kertynyt | `run.bat cleanup`, katso [06](06-tallennustila.md) |
| Docker-kontti ei käynnisty | Docker Desktop ei ole päällä | käynnistä Docker Desktop, `docker compose up -d` |

## Tunnetut sisältöhuomiot

- Rockstarin trailerissa on kohtaus, jossa näkyy someposti strippiklubista. Se voi päätyä taustaan: katso
  video ennen julkaisua ensimmäisinä päivinä.
- Artikkeli, jonka teksti ei latautunut (esim. GameSpot estää), on vain otsikkotasoinen lähde.
