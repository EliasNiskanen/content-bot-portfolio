# 01 Asennus

## Tarvittavat ohjelmat (Windows)

| Ohjelma | Asennus | Mihin |
|---|---|---|
| Python 3.12 | python.org | botti ilman Dockeria |
| ffmpeg | `winget install Gyan.FFmpeg` | video ja ääni (botti löytää sen itse) |
| Docker Desktop | `winget install Docker.DockerDesktop` | automaattinen päivittäinen ajo |
| Git | git-scm.com | versionhallinta, GitHub |

```bash
cd E:\gta6-bot
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Avaimet (`.env`)

Kopioi `.env.example` nimelle `.env` ja täytä:

| Avain | Mistä | Pakollinen |
|---|---|---|
| `YOUTUBE_API_KEY` | Google Cloud Console → APIs & Services → YouTube Data API v3 → Credentials → API key | kyllä |
| `ANTHROPIC_API_KEY` | console.anthropic.com → API keys (lataa saldoa, aseta kulutusraja) | kyllä |
| `ELEVENLABS_API_KEY` | vain jos `tts.engine: elevenlabs` | ei |
| `ALERT_WEBHOOK_URL` | Discord-kanava → Integraatiot → Webhookit → Uusi | ei (suositeltu) |

Älä koskaan liitä avaimia keskusteluihin tai committeihin.

## YouTube-kirjautuminen (kerran)

1. Google Cloud Console → Credentials → **Create credentials → OAuth client ID → Desktop app** → lataa JSON.
2. Tallenna nimellä `data\secrets\client_secret.json`. Windows lisää helposti toisen päätteen
   (`client_secret.json.json`): tarkista.
3. **OAuth consent screen → Test users** → lisää kanavan Google-tili.
4. Aja `auth.bat`, avaa tulostettu linkki, valitse Catfish Gaming -kanava ja hyväksy.
   Syntyy `data\secrets\token.json`.
5. Vahvista kanava puhelinnumerolla osoitteessa youtube.com/verify (muuten thumbnailin asetus ei onnistu).
6. Testitilassa kirjautuminen vanhenee 7 päivässä: vaihda OAuth-sovellus tilaan **In production**.

## Kuvat, trailerit ja musiikki (`data\`, ei GitHubissa)

| Kansio | Sisältö | Mistä |
|---|---|---|
| `data\official_images\screenshots`, `...\artwork` | Rockstarin kuvakaappaukset ja taidekuvat | rockstargames.com/VI/downloads: Screenshots- ja Artwork & Wallpapers -zipit |
| `data\promo\` | Kaikki GTA VI -sivuston kuvat (Only in Leonida, media-sivu, Ultimate/Vintage-paketit) | rockstargames.com/VI |
| `data\promo\cutouts` | 13 läpinäkyvää hahmokuvaa thumbnaileihin | Rockstarin omat Hero_FG-kuvat + Jason & Lucia irrotettu julisteista |
| `data\promo\backgrounds` | Puhtaat taustat ilman hahmoja | Rockstarin Hero_BG-kuvat |
| `data\gta6_footage` | Rockstarin viralliset trailerit | Rockstarin YouTube-kanava |
| `data\clips` | Omat pelitallenteet | oma tallennus |
| `data\music` | Taustamusiikki: Pulsar (The Grey Room / Density & Time) | YouTube Studio → Audiokirjasto |
| `data\brand_music` | Intro/outro: Neon nights (Patrick Patrikios) | YouTube Studio → Audiokirjasto |
| `data\brand_assets\gta6_logo.png` | Virallinen GTA VI -logo | rockstargames.com/VI |
| `data\models` | Hahmon irrotusmalli (~180 Mt) | latautuu itse ensimmäisellä ajolla |

Musiikki vain YouTube Audio Librarystä: Pixabayn "Content ID Registered" -kappaleet aiheuttavat vaatimuksia.
Kuvissa noudatetaan perheystävällisyyttä: poissuljetut kuvat ovat listassa `paths.exclude_images`.

## Testaa asennus

```bash
run.bat run --mock
run.bat run --mock --format guide
run.bat research
```
