"""Kertaluontoinen YouTube-kirjautuminen.

1. Google Cloud Console -> ota käyttöön "YouTube Data API v3"
2. OAuth client ID, tyyppi "Desktop app" -> lataa JSON nimellä data/secrets/client_secret.json
3. Aja koneella jossa on selain:  python -m bot.auth_youtube
   (tai Pillä: ssh -L 8765:localhost:8765 pi@raspberrypi ja avaa tulostettu linkki omalla koneella)
4. Syntyy data/secrets/token.json -> botti käyttää sitä jatkossa itse.
"""
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

from .config import load
from .uploader import SCOPES

if __name__ == "__main__":
    cfg = load()
    sec = Path(cfg.paths.secrets_dir)
    flow = InstalledAppFlow.from_client_secrets_file(str(sec / "client_secret.json"), SCOPES)
    creds = flow.run_local_server(host="localhost", port=8765, open_browser=False,
                                  authorization_prompt_message="Avaa selaimessa:\n{url}\n")
    (sec / "token.json").write_text(creds.to_json())
    print("OK ->", sec / "token.json")
