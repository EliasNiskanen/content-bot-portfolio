"""Laatuportti (hyvä vs. rikottu tuotos), esitarkistus (levy, avaimet) ja hälytysviestit (Discord/Slack paikallista
valepalvelinta vasten)."""
import http.server
import json
import threading
from pathlib import Path

from helpers import env, fake_output, make_cfg, make_thumbs, make_video, script_doc, tmpdir

from bot import alerts, preflight, qa


def _gate(cfg, fo, mock=True, **kw):
    sd = script_doc()
    arts = sd.pop("articles")
    args = dict(out=fo["out"], meta=fo["meta"], thumbs=fo["thumbs"],
                shorts=[{"path": p, "title": s["title"]} for p, s in zip(fo["shorts"], fo["meta"]["shorts"])],
                script=sd, articles=arts, mock=mock)
    args.update(kw)
    return qa.gate(cfg, **args)


def test_qa_passes_good_output():
    cfg = make_cfg()
    fo = fake_output(cfg)
    rep = _gate(cfg, fo)
    print(rep.text())
    assert rep.ok, rep.failures()
    assert any("mock" in n for n in rep.notes)


def test_qa_fails_broken_outputs():
    cfg = make_cfg()
    good = fake_output(cfg)
    d = Path(cfg.paths.output_dir)
    # 1) hiljainen ääni
    silent = make_video(d / "silent.mp4", 30, audio="silent")
    rep = _gate(cfg, good, out=silent)
    assert not rep.ok and any("keskitaso" in f for f in rep.failures()), rep.failures()
    # 2) liian lyhyt oikeassa (ei mock) ajossa
    rep = _gate(cfg, good, mock=False)
    assert any("kesto 11.5-40 min" in f for f in rep.failures()), rep.failures()
    # 3) musta kuva
    black = make_video(d / "black.mp4", 40, video="black")
    rep = _gate(cfg, good, out=black)
    assert any("mustaa" in f for f in rep.failures()), rep.failures()
    # 4) metatiedot ja thumbnailit
    bad_meta = dict(good["meta"], title="Short", description="x " + "#tag " * 70)
    small = make_thumbs(tmpdir(), "small", n=2, size=(640, 360))
    rep = _gate(cfg, good, meta=bad_meta, thumbs=small)
    fails = " | ".join(rep.failures())
    for want in ("otsikko 10-100", "otsikko alkaa 'GTA 6'", "hashtageja <= 60", "3 tiedostoa", "1280x720"):
        assert want in fails, (want, fails)
    # 5) kirosana ja väärä lainaus käsikirjoituksessa
    sd = script_doc()
    arts = sd.pop("articles")
    sd["segments"][0]["text"] = "this is damn bad"
    sd["segments"][1]["card"]["text"] = "words that are not in the article"
    rep = _gate(cfg, good, script=sd, articles=arts)
    fails = " | ".join(rep.failures())
    assert "ei kirosanoja" in fails and "lainauskortit" in fails, fails
    # 6) Short väärän kokoinen
    wrong = make_video(d / "wrong_short.mp4", 15, size=(1920, 1080))
    rep = _gate(cfg, good, shorts=[{"path": wrong, "title": "GTA 6: wrong size short"}])
    assert any("1080x1920" in f for f in rep.failures()), rep.failures()


def test_preflight_disk_low_runs_cleanup_then_fails():
    cfg = make_cfg()
    cleaned = []
    err = preflight.check_disk(cfg, cleanup=lambda c: cleaned.append(1), measure=lambda p: 1.5)
    assert cleaned == [1] and err and "1.5 Gt" in err, err
    vals = iter([2.0, 50.0])  # siivous vapauttaa tilaa -> ok
    assert preflight.check_disk(cfg, cleanup=lambda c: None, measure=lambda p: next(vals)) is None


def test_preflight_missing_keys_and_full_run():
    cfg = make_cfg()
    with env(YOUTUBE_API_KEY="", ANTHROPIC_API_KEY=None):
        err = preflight.check_keys(cfg)
        assert "YOUTUBE_API_KEY" in err and "ANTHROPIC_API_KEY" in err, err

        class Resp:
            status_code = 200

        class FakeYT:
            def channels(self):
                return self

            def list(self, **kw):
                assert kw.get("mine") is True
                return self

            def execute(self):
                return {"items": [{"id": "UC123"}]}

        problems = preflight.run(cfg, service=lambda c: FakeYT(), measure=lambda p: 100.0,
                                 get=lambda *a, **k: Resp())
    assert len(problems) == 1 and problems[0].startswith(".env"), problems


def test_preflight_youtube_failure():
    cfg = make_cfg()

    def broken(c):
        raise RuntimeError("token revoked")

    err = preflight.check_youtube(cfg, service=broken)
    assert "token revoked" in err


class _Hook(http.server.BaseHTTPRequestHandler):
    bodies: list = []

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        _Hook.bodies.append(json.loads(self.rfile.read(n)))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *a):
        pass


def test_alert_payloads_discord_and_slack():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Hook)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/hook"
    _Hook.bodies.clear()
    try:
        secret = "AIzaSyTESTKEYabcdefghijklmnopqrstu"
        with env(ALERT_WEBHOOK_URL=url, ALERT_WEBHOOK_KIND=None, YOUTUBE_API_KEY=secret):
            assert alerts.send(f"Työ news epäonnistui: 403 https://x/api?part=id&key={secret}")
        with env(ALERT_WEBHOOK_URL=url, ALERT_WEBHOOK_KIND="slack"):
            assert alerts.send("Laatuportti hylkäsi videon")
        with env(ALERT_WEBHOOK_URL=None):
            assert alerts.send("vain lokiin") is False
    finally:
        srv.shutdown()
    assert len(_Hook.bodies) == 2, _Hook.bodies
    d, s = _Hook.bodies
    assert set(d) == {"content"} and "Työ news epäonnistui" in d["content"], d
    assert secret not in d["content"] and "key=***" in d["content"], d
    assert s == {"text": "Laatuportti hylkäsi videon"}, s
    long = alerts.payload("x" * 5000, "discord")["content"]
    assert len(long) <= 1800
