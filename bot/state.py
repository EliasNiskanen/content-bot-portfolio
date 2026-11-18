import json
import sqlite3
import time

# Ajastetun työn vaiheet (jobs.stage) järjestyksessä. Uusinta jatkaa siitä mitä puuttuu (main.run_job).
STAGES = ("started", "rendered", "qa_failed", "qa_passed", "uploading_long", "long_uploaded", "upload_queued", "done")
TERMINAL = ("done",)


class State:
    def __init__(self, path: str):
        # timeout: ajastin ja ajo (eri prosessit) voivat kirjoittaa yhtä aikaa -> odotetaan lukkoa
        self.db = sqlite3.connect(path, timeout=30)
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS used_sources (video_id TEXT PRIMARY KEY, used_at REAL);
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, started REAL, title TEXT,
                source_video TEXT, output TEXT, youtube_id TEXT, status TEXT);
            CREATE TABLE IF NOT EXISTS guide_topics (topic TEXT PRIMARY KEY, used_at REAL);
            CREATE TABLE IF NOT EXISTS scheduled (
                video_id TEXT PRIMARY KEY, kind TEXT, publish_at TEXT, created REAL, checked INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS jobs (
                name TEXT, day TEXT, stage TEXT, attempts INTEGER DEFAULT 0, work_dir TEXT, output TEXT,
                meta TEXT, long_id TEXT, shorts TEXT, qa TEXT, error TEXT, started REAL, updated REAL,
                PRIMARY KEY (name, day));
            CREATE TABLE IF NOT EXISTS summaries (day TEXT PRIMARY KEY, sent REAL);
            CREATE TABLE IF NOT EXISTS breakdowns (
                video_id TEXT PRIMARY KEY, day TEXT, job TEXT, status TEXT, data TEXT, seen REAL);
            """
        )

    def is_used(self, video_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM used_sources WHERE video_id=?", (video_id,)).fetchone() is not None

    def mark_used(self, video_id: str):
        self.db.execute("INSERT OR REPLACE INTO used_sources VALUES (?,?)", (video_id, time.time()))
        self.db.commit()

    def recent_titles(self, n: int = 15) -> list[str]:
        rows = self.db.execute(
            "SELECT title FROM runs WHERE title IS NOT NULL ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall()
        return [r[0] for r in rows]

    def log_run(self, **kw) -> int:
        cur = self.db.execute(
            "INSERT INTO runs (started,title,source_video,output,youtube_id,status) VALUES (?,?,?,?,?,?)",
            (time.time(), kw.get("title"), kw.get("source_video"), kw.get("output"),
             kw.get("youtube_id"), kw.get("status")),
        )
        self.db.commit()
        return cur.lastrowid

    # ---- opasvideoiden aiheet (config guide.topics järjestyksessä, alusta kun kaikki käytetty)
    def next_guide_topic(self, topics: list[str]) -> str | None:
        if not topics:
            return None
        used = {r[0] for r in self.db.execute("SELECT topic FROM guide_topics").fetchall()}
        for t in topics:
            if t not in used:
                return t
        self.db.execute("DELETE FROM guide_topics")  # kaikki käytetty -> alusta
        self.db.commit()
        return topics[0]

    def mark_guide_topic(self, topic: str):
        self.db.execute("INSERT OR REPLACE INTO guide_topics VALUES (?,?)", (topic, time.time()))
        self.db.commit()

    # ---- ajastetut julkaisut (upload.auto_publish): Shortsien vapaat paikat + myöhempi lukitustarkistus
    def add_scheduled(self, video_id: str, kind: str, publish_at: str):
        """publish_at = ISO 8601 aikavyöhykkeen kanssa."""
        self.db.execute("INSERT OR REPLACE INTO scheduled (video_id,kind,publish_at,created,checked) VALUES (?,?,?,?,0)",
                        (video_id, kind, publish_at, time.time()))
        self.db.commit()

    def scheduled_times(self, kind: str) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT publish_at FROM scheduled WHERE kind=?", (kind,)).fetchall()]

    def unchecked_scheduled(self) -> list[dict]:
        rows = self.db.execute("SELECT video_id, kind, publish_at FROM scheduled WHERE checked=0").fetchall()
        return [{"video_id": r[0], "kind": r[1], "publish_at": r[2]} for r in rows]

    def mark_checked(self, ids: list[str]):
        self.db.executemany("UPDATE scheduled SET checked=1 WHERE video_id=?", [(i,) for i in ids])
        self.db.commit()

    # ---- ajastetut työt: yksi rivi per (työn nimi, päivä) -> uusinta ei koskaan lataa samaa videota kahdesti
    _JOB_COLS = ("name", "day", "stage", "attempts", "work_dir", "output", "meta", "long_id", "shorts", "qa",
                 "error", "started", "updated")

    def _job_row(self, r) -> dict:
        d = dict(zip(self._JOB_COLS, r))
        for k in ("meta", "qa"):
            d[k] = json.loads(d[k]) if d[k] else None
        d["shorts"] = json.loads(d["shorts"]) if d["shorts"] else {}
        return d

    def job(self, name: str, day: str) -> dict | None:
        r = self.db.execute(f"SELECT {','.join(self._JOB_COLS)} FROM jobs WHERE name=? AND day=?",
                            (name, day)).fetchone()
        return self._job_row(r) if r else None

    def jobs_on(self, day: str) -> list[dict]:
        rows = self.db.execute(f"SELECT {','.join(self._JOB_COLS)} FROM jobs WHERE day=? ORDER BY started",
                               (day,)).fetchall()
        return [self._job_row(r) for r in rows]

    def recent_job_errors(self, n: int = 5) -> list[dict]:
        rows = self.db.execute(f"SELECT {','.join(self._JOB_COLS)} FROM jobs WHERE error IS NOT NULL AND error != '' "
                               "ORDER BY updated DESC LIMIT ?", (n,)).fetchall()
        return [self._job_row(r) for r in rows]

    def job_begin(self, name: str, day: str) -> dict:
        """Uusi yritys: rivi luodaan tarvittaessa, attempts + 1, virhe tyhjäksi. Palauttaa rivin."""
        now = time.time()
        self.db.execute("INSERT OR IGNORE INTO jobs (name, day, stage, attempts, started, updated) "
                        "VALUES (?,?,?,?,?,?)", (name, day, "started", 0, now, now))
        self.db.execute("UPDATE jobs SET attempts=attempts+1, error=NULL, updated=? WHERE name=? AND day=?",
                        (now, name, day))
        self.db.commit()
        return self.job(name, day)

    def job_update(self, name: str, day: str, **kw):
        """Päivittää annetut sarakkeet (meta/qa = dict, shorts = {tiedosto: youtube_id})."""
        if not kw:
            return
        vals = []
        for k, v in kw.items():
            if k not in self._JOB_COLS or k in ("name", "day"):
                raise KeyError(k)
            if k in ("meta", "qa", "shorts") and v is not None:
                v = json.dumps(v, ensure_ascii=False)
            vals.append(v)
        sets = ",".join(f"{k}=?" for k in kw) + ",updated=?"
        self.db.execute(f"UPDATE jobs SET {sets} WHERE name=? AND day=?", (*vals, time.time(), name, day))
        self.db.commit()

    def job_short_uploaded(self, name: str, day: str, file: str, video_id: str):
        """Yksi Short ladattu: kirjataan heti (ennen mitään muuta), ettei uusinta lataa sitä uudelleen."""
        j = self.job(name, day) or {}
        shorts = dict(j.get("shorts") or {})
        shorts[file] = video_id
        self.job_update(name, day, shorts=shorts)

    def summary_sent(self, day: str) -> bool:
        return self.db.execute("SELECT 1 FROM summaries WHERE day=?", (day,)).fetchone() is not None

    def mark_summary(self, day: str):
        self.db.execute("INSERT OR REPLACE INTO summaries VALUES (?,?)", (day, time.time()))
        self.db.commit()

    # ---- breakdown-vahti: nähdyt Rockstarin videot (yksi rivi per video), työ = jobs-taulun nimi sille päivälle
    def breakdown_seen(self, video_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM breakdowns WHERE video_id=?", (video_id,)).fetchone() is not None

    def add_breakdown(self, video: dict, day: str, status: str, job: str | None = None):
        self.db.execute("INSERT OR REPLACE INTO breakdowns VALUES (?,?,?,?,?,?)",
                        (video["id"], day, job, status, json.dumps(video, ensure_ascii=False), time.time()))
        self.db.commit()

    def breakdown_jobs(self, day: str) -> list[dict]:
        rows = self.db.execute("SELECT job, data FROM breakdowns WHERE day=? AND job IS NOT NULL ORDER BY seen",
                               (day,)).fetchall()
        return [{"job": r[0], "video": json.loads(r[1])} for r in rows]
