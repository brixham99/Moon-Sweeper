"""High scores: a shared dreamlo Top 10 with country flags, plus a local
cache so the game works offline. Mirrors the leaderboards in Daleks in
Hedges and Dinosaur vs Bees (its own board, its own save folder). Network
calls run on daemon threads with short timeouts, so the game never waits
and never crashes offline. Dreamlo itself is plain HTTP; the HTTPS lookups
(country, flags) go through open_url(), which copes with a Python that has
no CA certificates (as on AVON-3, see Daleks in Hedges 2ebaaa9)."""
import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import pygame

NAME_MAX = 8                 # leaderboard names: letters, digits and spaces
TOP_N = 10
# Hosted leaderboard (dreamlo.com, free tier). Free dreamlo boards are
# client-side by design: the private code that adds scores ships in the game.
DREAMLO_PUBLIC = "6ae327308f40bb201431dd4d"
DREAMLO_PRIVATE = "GkZ0bjKs2023SOArMvyuzgJ8iCMv9mY0-7cCHZKBRIvQ"
DREAMLO_URL = "http://dreamlo.com/lb/"
DREAMLO_TIMEOUT = 4.0
SAVE_DIR = os.path.join(os.path.expanduser("~"), ".moon_sweeper")
FLAG_DIR = os.path.join(SAVE_DIR, "flags")   # flag images, downloaded once each
COUNTRY_URLS = ("https://ipinfo.io/country", "https://ipapi.co/country/")  # no key; first that answers
COUNTRY_TIMEOUT = 2.0
FLAG_URL = "https://flagcdn.com/w40/{code}.png"  # 40x20 PNG
FLAG_TIMEOUT = 3.0
FLAG_HEIGHT = 16
SAVE_FILE = os.path.join(SAVE_DIR, "scores.json")  # last name, local best, cached Top 10, unsent scores


def clean_country(text) -> str:
    """A 2-letter ISO country code ("GB"), or "" for anything else."""
    text = str(text or "").strip().upper()
    return text if len(text) == 2 and text.isascii() and text.isalpha() else ""


def open_url(req, timeout):
    """urlopen that copes with Macs whose Python has no CA certificates
    (python.org installs before "Install Certificates" is run): retry with
    certifi if present, else without verification. Only used for public,
    read-only lookups (country code, flag PNGs that are checked on arrival)."""
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.URLError as e:
        if not isinstance(getattr(e, "reason", None), ssl.SSLError):
            raise
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl._create_unverified_context()
    return urllib.request.urlopen(req, timeout=timeout, context=ctx)


def lookup_country() -> str:
    """This player's country from a free HTTPS lookup (blocking: call it on a
    background thread). "" if every service fails."""
    for url in COUNTRY_URLS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "MoonSweeper/1.0"})
            with open_url(req, COUNTRY_TIMEOUT) as resp:
                code = clean_country(resp.read(64).decode("ascii", "replace"))
            if code:
                return code
        except Exception:
            continue
    return ""


class Flags:
    """Small flag images for the Top 10, fetched from flagcdn.com on a
    background thread and kept in FLAG_DIR, so each is downloaded once."""

    def __init__(self, folder: str = FLAG_DIR, download: bool = True):
        self.folder = folder
        self.download = download
        self.images = {}       # code -> Surface (or None: not available)
        self.fetching = set()  # codes being downloaded or already tried
        self.lock = threading.Lock()

    def _path(self, code: str) -> str:
        return os.path.join(self.folder, code.lower() + ".png")

    def _fetch(self, code: str):
        try:
            req = urllib.request.Request(FLAG_URL.format(code=code.lower()),
                                         headers={"User-Agent": "MoonSweeper/1.0"})
            with open_url(req, FLAG_TIMEOUT) as resp:
                data = resp.read(65536)
            if data[:8] != b"\x89PNG\r\n\x1a\n":
                return
            os.makedirs(self.folder, exist_ok=True)
            tmp = self._path(code) + ".tmp"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, self._path(code))
        except Exception:
            pass
        finally:
            with self.lock:
                self.images.pop(code, None)  # look on disk again next frame

    def get(self, code: str):
        """The flag for code (FLAG_HEIGHT px tall), or None for now."""
        code = clean_country(code)
        if not code:
            return None
        with self.lock:
            if code in self.images:
                return self.images[code]
        img = None
        path = self._path(code)
        if os.path.exists(path):
            try:
                raw = pygame.image.load(path)
                h = FLAG_HEIGHT
                w = max(1, int(round(raw.get_width() * h / raw.get_height())))
                img = pygame.transform.smoothscale(raw.convert_alpha(), (w, h))
            except Exception:
                img = None
        with self.lock:
            self.images[code] = img
            start = img is None and self.download and code not in self.fetching
            if start:
                self.fetching.add(code)
        if start:
            threading.Thread(target=self._fetch, args=(code,), daemon=True).start()
        return img


def clean_name(text: str) -> str:
    """Letters, digits and single spaces only, at most NAME_MAX characters."""
    out = "".join(ch for ch in str(text) if ch.isascii() and (ch.isalnum() or ch == " "))
    return " ".join(out.split())[:NAME_MAX]


def submit_name(text: str) -> str:
    """The name as it goes on the board: clean_name, with a lowercase first
    letter capitalised ("archie" -> "Archie", "mcDuff" -> "McDuff"); the
    rest stays as typed."""
    name = clean_name(text)
    return name[:1].upper() + name[1:] if name[:1].islower() else name


class Leaderboard:
    """dreamlo Top 10 plus a local cache. All network calls run on daemon
    threads with a short timeout, so the game never waits and never crashes
    offline. Scores that could not be sent are kept and retried later."""

    def __init__(self, online: bool = True, path: str = SAVE_FILE):
        self.online = online
        self.path = path
        self.lock = threading.Lock()
        self.save_lock = threading.Lock()
        self.entries = []          # [(name, score, country)], best first
        self.country = None        # this player's country code ("" unknown), looked up once a session
        self.saved_country = ""
        self.pending = []          # [(name, score)] still to send
        self.local_best = 0
        self.last_name = ""
        self.status = "cached"     # "cached" | "loading" | "online" | "offline"
        self.busy = False
        self._load()
        if not self.entries and online:
            self.status = "loading"

    # ----- local file -------------------------------------------------
    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.entries = [(clean_name(e[0]), int(e[1]), clean_country(e[2] if len(e) > 2 else ""))
                            for e in data.get("top", [])][:TOP_N]
            self.saved_country = clean_country(data.get("country", ""))
            self.pending = [(clean_name(n), int(v)) for n, v in data.get("pending", [])]
            self.local_best = int(data.get("local_best", 0))
            self.last_name = clean_name(data.get("last_name", ""))
        except Exception:
            pass

    def _save(self):
        if not self.online:
            return  # headless test runs leave the player's file alone
        with self.lock:
            data = {"top": self.entries, "pending": self.pending,
                    "local_best": self.local_best, "last_name": self.last_name,
                    "country": self.country or self.saved_country}
        with self.save_lock:
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                tmp = self.path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f)
                os.replace(tmp, self.path)
            except Exception:
                pass

    # ----- what the game shows ----------------------------------------
    def top(self, n: int = TOP_N):
        """Cached dreamlo Top 10 merged with unsent scores (best per name):
        [(name, score, country)]."""
        with self.lock:
            mine = self.country or self.saved_country
            best = {}
            rows = [(e[0], e[1], e[2] if len(e) > 2 else "") for e in self.entries]
            rows += [(name, score, mine) for name, score in self.pending]
            for name, score, country in rows:
                if name and score > best.get(name, (-1, ""))[0]:
                    best[name] = (score, country)
        return sorted(((k, v[0], v[1]) for k, v in best.items()), key=lambda e: (-e[1], e[0]))[:n]

    def high_score(self) -> int:
        top = self.top(1)
        return max(self.local_best, top[0][1] if top else 0)

    def qualifies(self, score: int) -> bool:
        """True if score would get onto the Top 10 shown right now."""
        if score <= 0:
            return False
        top = self.top(TOP_N)
        return len(top) < TOP_N or score > top[-1][1]

    def record_local(self, score: int):
        with self.lock:
            self.local_best = max(self.local_best, int(score))
        self._save()

    # ----- network ----------------------------------------------------
    @staticmethod
    def _get(url: str) -> str:
        req = urllib.request.Request(url, headers={"User-Agent": "MoonSweeper/1.0"})
        with urllib.request.urlopen(req, timeout=DREAMLO_TIMEOUT) as resp:
            return resp.read().decode("utf-8", "replace")

    @staticmethod
    def _parse(text: str):
        board = (json.loads(text).get("dreamlo") or {}).get("leaderboard") or {}
        entry = board.get("entry") or []
        if isinstance(entry, dict):  # dreamlo sends a lone entry as an object
            entry = [entry]
        out = []
        for e in entry:
            try:
                out.append((clean_name(e.get("name", "")), int(float(e.get("score", 0))),
                            clean_country(e.get("text", ""))))  # older entries have no code
            except (TypeError, ValueError):
                continue
        out = [e for e in out if e[0]]
        out.sort(key=lambda e: (-e[1], e[0]))
        return out[:TOP_N]

    def _send(self, name: str, score: int) -> bool:
        # /add/name/score/seconds/text: the country code rides in the text field.
        url = (DREAMLO_URL + DREAMLO_PRIVATE + "/add/"
               + urllib.parse.quote(name, safe="") + "/" + str(int(score)))
        country = self.country or ""
        if country:
            url += "/0/" + country
        return self._get(url).strip().startswith("OK")

    def _work(self):
        try:
            with self.lock:
                todo = list(self.pending)
                need_country = bool(todo) and self.country is None
            if need_country:
                code = lookup_country()  # once a session, on this background thread
                with self.lock:
                    self.country = code
            sent = []
            for item in todo:
                try:
                    if self._send(*item):
                        sent.append(item)
                except Exception:
                    break  # offline: keep the rest for next time
                time.sleep(1.1)  # dreamlo refuses repeat requests within a second
            with self.lock:
                self.pending = [p for p in self.pending if p not in sent]
            try:
                entries = self._parse(self._get(DREAMLO_URL + DREAMLO_PUBLIC + "/json"))
                with self.lock:
                    self.entries = entries
                    self.status = "online"
            except Exception:
                with self.lock:
                    self.status = "offline"
            self._save()
        finally:
            with self.lock:
                self.busy = False

    def _spawn(self, force: bool = False):
        if not self.online:
            return
        with self.lock:
            if self.busy and not force:
                return  # a refresh is already on its way
            self.busy = True
        threading.Thread(target=self._work, daemon=True).start()

    def refresh(self):
        self._spawn()

    def submit(self, name: str, score: int):
        name = submit_name(name)
        with self.lock:
            self.last_name = name or self.last_name
            self.local_best = max(self.local_best, int(score))
        if name and score > 0:
            with self.lock:
                self.pending.append((name, int(score)))
        self._save()
        if name and score > 0:
            self._spawn(force=True)
