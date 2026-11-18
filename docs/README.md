# Catfish Gaming -videobotti: käsikirja

Botti tekee kanavalle **Catfish Gaming** (@catfishgaminghd) joka päivä kaksi pitkää videota (uutiset ja yksi
pelin aihe kerrallaan), Shortsit ja thumbnailit, ja lataa ne YouTubeen. Tämä kansio kertoo kaiken, mitä botin
käyttämiseen, ylläpitoon ja jatkokehitykseen tarvitaan.

| Ohje | Mitä siinä on |
|---|---|
| [01 Asennus](01-asennus.md) | Ohjelmat, avaimet, YouTube-kirjautuminen, kuvat, trailerit ja musiikki |
| [02 Käyttö](02-kaytto.md) | Komennot, videoformaatit, testiajot, tila ja siivous |
| [03 Automaatio](03-automaatio.md) | Docker, päivittäinen aikataulu, ajastettu julkaisu, kone pois päältä, siirto palvelimelle |
| [04 Videoiden sisältö](04-sisalto.md) | Trenditutkimus, käsikirjoitus, kortit, musiikki, thumbnailit, Shortsit, kuvaukset |
| [05 YouTube Studio](05-youtube-studio.md) | Päivittäinen tarkistus, mitä API ei osaa, Content ID, kanavan asetukset |
| [06 Tallennustila](06-tallennustila.md) | Paljonko tilaa menee, mihin, ja miten se pysyy kurissa |
| [07 Vianetsintä](07-vianetsinta.md) | Tunnetut ongelmat ja ratkaisut |
| [08 Kehitys](08-kehitys.md) | Rakenne, skillit, git, kanavan laajentaminen muihin peleihin |

Pikaohje ja asetusten selitykset: [../OHJE.md](../OHJE.md).

## Periaatteet

- **Laatu ennen määrää.** Jokainen video tarkistetaan automaattisesti (pituus, ääni, kuva, otsikko, kuvaus)
  ennen latausta. Ensimmäiset päivät lataukset menevät yksityisinä ja omistaja katsoo ne.
- **Rehellisyys.** Vain lähteiden faktat, lähteet nimetään, huhut merkitään huhuiksi. Ei keksittyjä vuotoja,
  päivämääriä tai arvosanoja.
- **Mainosystävällisyys.** Ei kiroilua, ei seksuaalista kuvamateriaalia, vain YouTube Audio Libraryn musiikkia,
  vain Rockstarin virallista materiaalia ja omia tallenteita.
- **Salaisuudet pysyvät koneella.** `.env`, `data/secrets`, Rockstarin kuvat ja musiikit eivät mene GitHubiin.
