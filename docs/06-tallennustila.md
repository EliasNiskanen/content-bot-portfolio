# 06 Tallennustila

## Missä tiedostot ovat

Docker-kontti käyttää kansiota `E:\gta6-bot\data` suoraan, joten videot ja kuvat ovat E-levyllä, eivät Dockerin
sisällä. Dockerin oma levykuva (C:) sisältää vain ohjelman imagen (~1,9 Gt) ja rakennusvälimuistin.

## Paljonko tilaa menee (mitattu)

| Kohde | Koko | Kasvaako |
|---|---|---|
| Yksi 15 min video + Short + thumbnailit (`data\output`) | ~250 Mt | kyllä, siivotaan |
| Yhden ajon välitiedostot (`data\work`) ennen siivousta | ~1,1 Gt | poistetaan heti onnistuneen ajon jälkeen (~80 Mt jää) |
| Kuvat, trailerit, mallit, musiikki, välimuistit | ~1,6 Gt | ei |
| Docker-image (C:) | ~1,9 Gt | ei |

Arvio vakiotilasta kahdella videolla päivässä: noin 6–8 Gt E-levyllä.

## Siivous

Automaattisesti jokaisen onnistuneen ajon jälkeen (`cleanup.*` asetuksissa):

- välitiedostot (videopalat, tausta) pois heti
- 10 uusinta työkansiota säilytetään (`cleanup.keep_work`)
- valmiit videot säilytetään 14 päivää (`cleanup.keep_output_days`); YouTube on arkisto
- lokit 30 päivää

Käsin: `run.bat cleanup --dry-run` näyttää mitä poistettaisiin, `run.bat cleanup` poistaa.
Siivous ei koskaan koske kansioihin `secrets`, `promo`, `official_images`, `music`, `brand_music`, `brand_assets`,
`gta6_footage`, `models`, `brand_cache` eikä tiedostoon `state.db`.

## C-levyn säästäminen

- Docker Desktop → Settings → Resources → Advanced → **Disk image location** → esim. `E:\DockerData`.
- Rakennusvälimuistin tyhjennys: `docker builder prune -f`.

## Lisää tilaa

Vaihda `docker-compose.yml`:n rivi `./data:/data` esimerkiksi muotoon `F:\catfish-data:/data` (ulkoinen levy tai
NAS) ja siirrä `data`-kansion sisältö sinne.
