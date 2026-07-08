#!/usr/bin/env python3
"""
app.py - Flask + APScheduler Event Collector with Eventbrite/Meetup venue lookup and optional OpenCage geocoding.

Requirements:
    pip install flask apscheduler requests beautifulsoup4 python-dateutil

Environment (optional tokens):
    SERPAPI_KEY       - SerpApi API key for searching
    EVENTBRITE_TOKEN  - Eventbrite personal OAuth token (to fetch venue details)
    MEETUP_TOKEN      - Meetup API token (to fetch venue details)
    OPENCAGE_KEY      - OpenCage geocoder key (to normalize addresses)

Run:
    python app.py
"""

import os
import re
import json
import html
import hashlib
import logging
import sqlite3
import urllib.parse
from datetime import datetime, timezone
from typing import Tuple, Optional
from urllib.parse import urlparse

from flask import Flask, jsonify, render_template, send_from_directory, request
from apscheduler.schedulers.background import BackgroundScheduler
import requests
from bs4 import BeautifulSoup

# ----- CONFIG -----
SERPAPI_KEY = "a391a3ec65a772e25e8483b0be03ec7f0ba80b15d78e3ff9d68099ddd1bee45b"
EVENTBRITE_TOKEN = "V7XZ3QTC4MKV3GSOUH"  # optional to fetch Eventbrite venue
MEETUP_TOKEN = os.getenv("MEETUP_TOKEN", "")          # optional to fetch Meetup venue
OPENCAGE_KEY = os.getenv("OPENCAGE_KEY", "")          # optional to normalize address

CITY = "Pune,Mumbai,Bengaluru"
DATA_DIR = "data"
HTML_DIR = os.path.join(DATA_DIR, "html")
DB_PATH = os.path.join(DATA_DIR, "events.db")
USER_AGENT = "Mozilla/5.0 (compatible; PuneEventBot/1.0; +https://example.com/bot)"
SEARCH_KEYWORDS = '(hackathon OR meetup OR "conference" OR "workshop" OR "tech talk" OR "webinar" OR "seminar")'
SEARCH_INTERVAL_MINUTES = 360  # default: every 6 hours
SERPAPI_URL = "https://serpapi.com/search.json"
NUM_RESULTS = 20

# City list used to detect area from address/title/snippet.
CITY_LIST = [
    "Pune", "Mumbai", "Bengaluru", "Bangalore", "Delhi", "New Delhi",
    "Chennai", "Hyderabad", "Kolkata", "Ahmedabad", "Noida", "Gurgaon", "Gurugram"
]

os.makedirs(HTML_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("event_collector")

# ----- DB helpers & migration -----
def init_db(path=DB_PATH):
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()
    cur.execute("""
      CREATE TABLE IF NOT EXISTS events (
        id TEXT PRIMARY KEY,
        url TEXT UNIQUE,
        domain TEXT,
        title TEXT,
        description TEXT,
        start_time TEXT,
        fetched_at TEXT,
        serp_source TEXT,
        html_path TEXT,
        notes TEXT
      )
    """)
    cur.execute("""
      CREATE TABLE IF NOT EXISTS folders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL
      )
    """)
    cur.execute("""
      CREATE TABLE IF NOT EXISTS event_folders (
        event_id TEXT,
        folder_id INTEGER,
        PRIMARY KEY(event_id, folder_id),
        FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE CASCADE,
        FOREIGN KEY(folder_id) REFERENCES folders(id) ON DELETE CASCADE
      )
    """)
    conn.commit()
    # Ensure new columns exist
    ensure_event_columns(conn, ["location_name", "address", "area", "normalized_address", "lat", "lon"])
    return conn

def ensure_event_columns(conn, columns):
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(events)")
    existing = {row[1] for row in cur.fetchall()}
    for col in columns:
        if col not in existing:
            logger.info("Adding missing column to events table: %s", col)
            # lat/lon numeric columns are TEXT for simplicity
            cur.execute(f"ALTER TABLE events ADD COLUMN {col} TEXT")
    conn.commit()

conn = init_db()

def make_id(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]

def exists_url(conn, url: str) -> bool:
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM events WHERE url = ? LIMIT 1", (url,))
    return cur.fetchone() is not None

def insert_event(ev: dict):
    cur = conn.cursor()
    cur.execute("""
      INSERT OR IGNORE INTO events
      (id, url, domain, title, description, start_time, fetched_at, serp_source, html_path, notes,
       location_name, address, area, normalized_address, lat, lon)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        ev['id'],
        ev['url'],
        ev.get('domain'),
        ev.get('title'),
        ev.get('description'),
        ev.get('start_time'),
        ev.get('fetched_at'),
        ev.get('serp_source'),
        ev.get('html_path'),
        ev.get('notes'),
        ev.get('location_name'),
        ev.get('address'),
        ev.get('area'),
        ev.get('normalized_address'),
        str(ev.get('lat') or ""),
        str(ev.get('lon') or ""),
    ))
    conn.commit()

# ----- HTTP session & helpers -----
session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT})

def safe_json_load(text: str):
    try:
        return json.loads(text)
    except Exception:
        # try to salvage first JSON object
        matches = re.findall(r'(\{.*\})', text, flags=re.S)
        for m in matches:
            try:
                return json.loads(m)
            except Exception:
                continue
    return None

def pick_city_from_text(text: str) -> Optional[str]:
    if not text:
        return None
    for city in CITY_LIST:
        if city.lower() in text.lower():
            return city
    return None

def extract_title_from_html(html_text: str) -> Optional[str]:
    try:
        soup = BeautifulSoup(html_text, "html.parser")
        og = soup.find("meta", property="og:title")
        if og and og.get("content"):
            return og["content"].strip()
        tw = soup.find("meta", property="twitter:title")
        if tw and tw.get("content"):
            return tw["content"].strip()
        t = soup.find("title")
        if t:
            return t.get_text(strip=True)
        h1 = soup.find("h1")
        if h1:
            return h1.get_text(" ", strip=True)
    except Exception:
        pass
    return None

def save_html(url: str, html_text: str) -> str:
    uid = make_id(url)
    filename = uid + ".html"
    path = os.path.join(HTML_DIR, filename)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        f.write(html_text)
    os.replace(path + ".tmp", path)
    return path

# ----- improved address extraction (as before) -----
def extract_event_location(html_text: str, url: str, snippet: str = None) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    location_name = None
    address = None
    area = None
    try:
        soup = BeautifulSoup(html_text, "html.parser")
        # JSON-LD
        scripts = soup.find_all("script", type="application/ld+json")
        for s in scripts:
            txt = s.string
            if not txt:
                continue
            data = safe_json_load(txt)
            if not data:
                continue
            items = data if isinstance(data, list) else [data]
            expanded = []
            for it in items:
                if isinstance(it, dict) and "@graph" in it:
                    g = it.get("@graph") or []
                    if isinstance(g, list):
                        expanded.extend(g)
                expanded.append(it)
            for it in expanded:
                if not isinstance(it, dict):
                    continue
                typ = it.get("@type") or it.get("type")
                if isinstance(typ, list):
                    is_event = "Event" in typ or any("Event" in t for t in typ if isinstance(t, str))
                else:
                    is_event = isinstance(typ, str) and "Event" in typ
                if not is_event:
                    continue
                loc = it.get("location") or it.get("place")
                if not loc:
                    continue
                if isinstance(loc, dict):
                    if loc.get("name"):
                        location_name = location_name or loc.get("name")
                    addr = loc.get("address") or loc.get("addressLocality") or loc.get("addressRegion")
                    if isinstance(addr, dict):
                        parts = []
                        for k in ("streetAddress", "addressLocality", "addressRegion", "postalCode", "addressCountry"):
                            v = addr.get(k)
                            if v:
                                parts.append(str(v).strip())
                        if parts:
                            address = ", ".join(parts)
                    elif isinstance(addr, str):
                        address = address or addr.strip()
                    if not address:
                        parts = []
                        for k in ("streetAddress", "addressLocality", "addressRegion", "postalCode", "addressCountry"):
                            v = loc.get(k)
                            if v:
                                parts.append(str(v).strip())
                        if parts:
                            address = ", ".join(parts)
                elif isinstance(loc, str):
                    if not location_name:
                        location_name = loc.strip()
                    if "," in loc and not address:
                        address = loc.strip()
                if not area:
                    area = pick_city_from_text(" ".join(filter(None, [location_name, address, snippet or ""])))
                if location_name or address:
                    return (location_name, address, area)
        # microdata / address tag / hints
        addr_nodes = soup.select('[itemprop*=address], address')
        for node in addr_nodes:
            text = node.get_text(" ", strip=True)
            if text:
                text = html.unescape(text)
                if len(text) > (len(address or "")):
                    address = text
                parent = node.parent
                if parent:
                    name_node = parent.find(attrs={"itemprop": "name"})
                    if name_node and not location_name:
                        location_name = name_node.get_text(" ", strip=True)
                if not area:
                    area = pick_city_from_text(text)
        if address:
            return (location_name, address, area)
        addr_tag = soup.find("address")
        if addr_tag:
            text = addr_tag.get_text(" ", strip=True)
            if text:
                address = text
                if not area:
                    area = pick_city_from_text(text)
                return (location_name, address, area)
        hint_keywords = ["address", "venue", "location", "place", "map", "directions", "where", "addr"]
        candidates = []
        for kw in hint_keywords:
            nodes = soup.find_all(lambda tag: (
                (tag.has_attr("id") and kw in tag.get("id").lower()) or
                (tag.has_attr("class") and any(kw in c.lower() for c in tag.get("class")))
            ))
            for n in nodes:
                text = n.get_text(" ", strip=True)
                if text:
                    text = html.unescape(text)
                    if len(text) > 10:
                        candidates.append(text)
        if candidates:
            candidates = sorted(set(candidates), key=lambda s: -len(s))
            address = candidates[0]
            if not area:
                area = pick_city_from_text(address)
            if not location_name:
                first_line = address.split("\n")[0].split(",")[0].strip()
                if first_line and len(first_line) < 120:
                    location_name = first_line
            return (location_name, address, area)
        text = soup.get_text("\n", strip=True)
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        paragraphs = []
        i = 0
        while i < len(lines):
            para = lines[i]
            j = i + 1
            while j < len(lines) and j < i + 4:
                para = para + ", " + lines[j]
                j += 1
            paragraphs.append(para)
            i += 1
        pin_re = re.compile(r'\b[1-9][0-9]{5}\b')
        street_words = ["road","rd","street","st","lane","ln","market","building","bldg","floor","fl","venue","hall","center","centre","complex","plot","sector","block"]
        best = None
        for p in paragraphs:
            if pin_re.search(p) or any(sw in p.lower() for sw in street_words):
                score = (5 if pin_re.search(p) else 0) + p.count(",")
                if not best or score > best[0]:
                    best = (score, p)
        if best:
            address = best[1].strip()
            if not area:
                area = pick_city_from_text(address)
            if not location_name:
                first = address.split(",")[0].strip()
                if len(first) < 120:
                    location_name = first
            return (location_name, address, area)
        if snippet and not address:
            if "," in snippet or pin_re.search(snippet):
                address = snippet
                area = area or pick_city_from_text(snippet)
                return (location_name, address, area)
        return (location_name, address, area)
    except Exception as ex:
        logger.debug("extract_event_location error: %s", ex)
        return (location_name, address, area)

# ----- platform-specific venue lookups -----
def fetch_eventbrite_venue(event_url: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (venue_name, formatted_address) or (None, None)."""
    if not EVENTBRITE_TOKEN:
        return None, None
    # extract event id: patterns like ...-123456789012 or /e/123456789012
    m = re.search(r'-([0-9]{8,})($|[/?])', event_url) or re.search(r'/e/([0-9]{8,})($|[/?])', event_url)
    if not m:
        return None, None
    event_id = m.group(1)
    api_url = f"https://www.eventbriteapi.com/v3/events/{event_id}/?expand=venue"
    headers = {"Authorization": f"Bearer {EVENTBRITE_TOKEN}"}
    try:
        resp = requests.get(api_url, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        venue = data.get("venue") or {}
        venue_name = venue.get("name")
        addr = venue.get("address") or {}
        parts = []
        for k in ("address_1", "address_2", "city", "region", "postal_code", "country"):
            v = addr.get(k)
            if v:
                parts.append(str(v).strip())
        formatted = ", ".join(parts) if parts else None
        return venue_name, formatted
    except Exception as e:
        logger.debug("Eventbrite API error for %s: %s", event_url, e)
        return None, None

def fetch_meetup_venue(event_url: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (venue_name, formatted_address) or (None, None). Best-effort; requires MEETUP_TOKEN."""
    if not MEETUP_TOKEN:
        return None, None
    # meetup event URL typically: https://www.meetup.com/{group}/events/{event_id}/
    m = re.search(r'/events/([0-9]{6,})', event_url)
    if not m:
        return None, None
    event_id = m.group(1)
    api_url = f"https://api.meetup.com/2/events/{event_id}?fields=venue"
    # Note: Meetup API v2 is legacy; tokens/URLs vary. This is best-effort.
    headers = {"Authorization": f"Bearer {MEETUP_TOKEN}"}
    try:
        resp = requests.get(api_url, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        venue = data.get("venue") or {}
        venue_name = venue.get("name")
        addr_parts = []
        for k in ("address_1", "address_2", "city", "state", "zip", "country"):
            v = venue.get(k)
            if v:
                addr_parts.append(str(v).strip())
        formatted = ", ".join(addr_parts) if addr_parts else None
        return venue_name, formatted
    except Exception as e:
        logger.debug("Meetup API error for %s: %s", event_url, e)
        return None, None

# ----- optional OpenCage geocoding to normalize addresses -----
def geocode_with_opencage(address: str) -> Tuple[Optional[str], Optional[float], Optional[float]]:
    """
    Use OpenCage to normalize an address.
    Returns: (formatted_address, lat, lng) or (None, None, None)
    """
    if not OPENCAGE_KEY or not address:
        return None, None, None
    try:
        params = {"q": address, "key": OPENCAGE_KEY, "no_annotations": 1, "limit": 1}
        r = requests.get("https://api.opencagedata.com/geocode/v1/json", params=params, timeout=8)
        r.raise_for_status()
        js = r.json()
        results = js.get("results") or []
        if not results:
            return None, None, None
        top = results[0]
        formatted = top.get("formatted")
        geometry = top.get("geometry") or {}
        lat = geometry.get("lat")
        lng = geometry.get("lng")
        return formatted, lat, lng
    except Exception as e:
        logger.debug("OpenCage geocode error for %s: %s", address, e)
        return None, None, None

# ----- classification (same as before) -----
FOLDER_MAP = {
    "hackathon": "Hackathons",
    "coding challenge": "Hackathons",
    "webinar": "Webinars",
    "online seminar": "Webinars",
    "conference": "Conferences",
    "summit": "Conferences",
    "workshop": "Workshops",
    "training": "Workshops",
    "meetup": "Meetups",
    "community": "Meetups",
    "concert": "Music",
    "music": "Music",
    "dance": "Dance",
    "festival": "Music",
}

def classify(text: str):
    text = (text or "").lower()
    matched = set()
    for kw, folder in FOLDER_MAP.items():
        if kw in text:
            matched.add(folder)
    return list(matched) if matched else ["Other"]

# ----- SerpApi search & collect (if using SerpApi) -----
def serpapi_search(query: str, num: int = 10):
    if not SERPAPI_KEY:
        logger.warning("SERPAPI_KEY not set. Skipping SerpApi query.")
        return {}
    params = {"engine": "google", "q": query, "num": num, "api_key": SERPAPI_KEY}
    r = session.get(SERPAPI_URL, params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def collect_links_from_serp(serp_json) -> list:
    if not serp_json:
        return []
    links = []
    for bucket in ("organic_results", "top_stories", "news_results", "inline_results", "related_searches"):
        for it in (serp_json.get(bucket) or []):
            link = it.get("link") or it.get("url") or it.get("source")
            snippet = it.get("snippet") or it.get("description") or it.get("title")
            if link:
                links.append((link, snippet, bucket))
    seen = set()
    out = []
    for link, snippet, src in links:
        if link in seen:
            continue
        seen.add(link)
        out.append({"link": link, "snippet": snippet, "src": src})
    return out

# ----- collector job (core) -----
def collector_job():
    try:
        q = f"events in {CITY} {SEARCH_KEYWORDS}"
        logger.info("Running SerpApi search: %s", q)
        serp = serpapi_search(q, num=NUM_RESULTS) if SERPAPI_KEY else {}
        links = collect_links_from_serp(serp) if serp else []
        for item in links:
            url = item["link"]
            if not url.lower().startswith("http"):
                continue
            if exists_url(conn, url):
                logger.debug("Already exists: %s", url)
                continue
            if url.lower().endswith((".pdf", ".jpg", ".jpeg", ".png", ".gif")):
                continue
            try:
                r = session.get(url, timeout=15)
                r.raise_for_status()
            except Exception as e:
                logger.warning("Failed to fetch %s: %s", url, e)
                continue
            html_text = r.text
            title = extract_title_from_html(html_text) or item.get("snippet") or ""
            uid = make_id(url)
            html_path = save_html(url, html_text)

            # 1) in-page extraction
            location_name, address, area = extract_event_location(html_text, url, snippet=item.get("snippet"))

            # 2) platform-specific lookups if address missing or looks 'Virtual'
            domain = urlparse(url).netloc.lower()
            if (not address or ("virtual" in (address or "").lower() and "virtual" not in (location_name or "").lower())):
                # Eventbrite
                if "eventbrite.com" in domain and EVENTBRITE_TOKEN:
                    vb_name, vb_address = fetch_eventbrite_venue(url)
                    if vb_address:
                        location_name = location_name or vb_name
                        address = vb_address
                        area = area or pick_city_from_text(vb_address)
                # Meetup
                if (not address) and "meetup.com" in domain and MEETUP_TOKEN:
                    mu_name, mu_address = fetch_meetup_venue(url)
                    if mu_address:
                        location_name = location_name or mu_name
                        address = mu_address
                        area = area or pick_city_from_text(mu_address)

            # 3) optionally normalize via OpenCage
            normalized_address = None
            lat = None
            lon = None
            if address and OPENCAGE_KEY:
                try:
                    formatted, latv, lonv = geocode_with_opencage(address)
                    if formatted:
                        normalized_address = formatted
                        lat = latv
                        lon = lonv
                        # if area empty, try to pick city from normalized
                        area = area or pick_city_from_text(formatted)
                except Exception as e:
                    logger.debug("OpenCage error: %s", e)

            # fallback area detection
            area = area or pick_city_from_text(" ".join(filter(None, [title, item.get("snippet") or "", address or ""])))

            ev = {
                "id": uid,
                "url": url,
                "domain": domain,
                "title": title,
                "description": item.get("snippet"),
                "start_time": None,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "serp_source": item.get("src"),
                "html_path": html_path,
                "notes": None,
                "location_name": location_name,
                "address": address,
                "area": area,
                "normalized_address": normalized_address,
                "lat": lat,
                "lon": lon
            }
            insert_event(ev)
            # associate folders
            folders = classify(" ".join(filter(None, [title, ev['description']])))
            cur = conn.cursor()
            for fname in folders:
                cur.execute("INSERT OR IGNORE INTO folders(name) VALUES (?)", (fname,))
                conn.commit()
                cur.execute("SELECT id FROM folders WHERE name = ?", (fname,))
                fid_row = cur.fetchone()
                if fid_row:
                    fid = fid_row[0]
                    cur.execute("INSERT OR IGNORE INTO event_folders(event_id, folder_id) VALUES (?, ?)", (uid, fid))
                    conn.commit()
            logger.info("Saved event %s (%s) area=%s address=%s", uid, title, area, address)
    except Exception as e:
        logger.exception("Collector job error: %s", e)

# ----- Flask app & routes (area filtering supported) -----
app = Flask(__name__)
app.config['SEARCH_INTERVAL_MINUTES'] = SEARCH_INTERVAL_MINUTES
app.config['DB_PATH'] = DB_PATH

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/folders_page")
def list_folders_page():
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM folders ORDER BY name")
    rows = cur.fetchall()
    folders = [ {"id": r[0], "name": r[1]} for r in rows ]
    return render_template("folders.html", folders=folders)

@app.route("/folders/<int:fid>")
def folder_events_page(fid):
    area = request.args.get("area", None)
    cur = conn.cursor()
    cur.execute("SELECT name FROM folders WHERE id = ?", (fid,))
    row = cur.fetchone()
    if not row:
        return "Folder not found", 404
    folder_name = row[0]
    if area:
        cur.execute("""
          SELECT e.id, e.title, e.url, e.description, e.fetched_at, e.html_path, e.location_name, e.address, e.area, e.normalized_address, e.lat, e.lon
          FROM events e
          JOIN event_folders ef ON e.id = ef.event_id
          WHERE ef.folder_id = ? AND (e.area = ? OR e.area LIKE ?)
          ORDER BY e.fetched_at DESC
        """, (fid, area, f"%{area}%"))
    else:
        cur.execute("""
          SELECT e.id, e.title, e.url, e.description, e.fetched_at, e.html_path, e.location_name, e.address, e.area, e.normalized_address, e.lat, e.lon
          FROM events e
          JOIN event_folders ef ON e.id = ef.event_id
          WHERE ef.folder_id = ?
          ORDER BY e.fetched_at DESC
        """, (fid,))
    events = []
    for r in cur.fetchall():
        events.append({
            "id": r[0],
            "title": r[1],
            "url": r[2],
            "description": r[3],
            "fetched_at": r[4],
            "html_path": r[5],
            "location_name": r[6],
            "address": r[7],
            "area": r[8],
            "normalized_address": r[9],
            "lat": r[10],
            "lon": r[11],
            "html_file": os.path.basename(r[5]) if r[5] else None
        })
    cur.execute("SELECT DISTINCT area FROM events WHERE area IS NOT NULL ORDER BY area")
    areas = [row[0] for row in cur.fetchall() if row[0]]
    return render_template("events.html", events=events, folder_name=folder_name, areas=areas, selected_area=area)

@app.route("/event/<eid>")
def event_page(eid):
    cur = conn.cursor()
    cur.execute("SELECT id, title, url, description, fetched_at, html_path, location_name, address, area, normalized_address, lat, lon FROM events WHERE id = ?", (eid,))
    r = cur.fetchone()
    if not r:
        return "Event not found", 404
    event = {
        "id": r[0],
        "title": r[1],
        "url": r[2],
        "description": r[3],
        "fetched_at": r[4],
        "html_path": r[5],
        "location_name": r[6],
        "address": r[7],
        "area": r[8],
        "normalized_address": r[9],
        "lat": r[10],
        "lon": r[11],
        "html_file": os.path.basename(r[5]) if r[5] else None
    }
    return render_template("event.html", event=event)

@app.route("/html/<path:filename>")
def serve_html_page(filename):
    return send_from_directory(HTML_DIR, filename)

# JSON endpoints
@app.route("/api/areas")
def api_areas():
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT area FROM events WHERE area IS NOT NULL ORDER BY area")
    rows = cur.fetchall()
    return jsonify([r[0] for r in rows if r[0]])

@app.route("/api/folders")
def api_list_folders():
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM folders ORDER BY name")
    rows = cur.fetchall()
    return jsonify([{"id": r[0], "name": r[1]} for r in rows])

@app.route("/api/folders/<int:fid>/events")
def api_folder_events(fid):
    area = request.args.get("area", None)
    cur = conn.cursor()
    if area:
        cur.execute("""
          SELECT e.id, e.title, e.url, e.description, e.fetched_at, e.html_path, e.location_name, e.address, e.area, e.normalized_address, e.lat, e.lon
          FROM events e
          JOIN event_folders ef ON e.id = ef.event_id
          WHERE ef.folder_id = ? AND (e.area = ? OR e.area LIKE ?)
          ORDER BY e.fetched_at DESC
        """, (fid, area, f"%{area}%"))
    else:
        cur.execute("""
          SELECT e.id, e.title, e.url, e.description, e.fetched_at, e.html_path, e.location_name, e.address, e.area, e.normalized_address, e.lat, e.lon
          FROM events e
          JOIN event_folders ef ON e.id = ef.event_id
          WHERE ef.folder_id = ?
          ORDER BY e.fetched_at DESC
        """, (fid,))
    events = []
    for r in cur.fetchall():
        events.append({
            "id": r[0], "title": r[1], "url": r[2], "description": r[3], "fetched_at": r[4],
            "html_path": r[5], "location_name": r[6], "address": r[7], "area": r[8],
            "normalized_address": r[9], "lat": r[10], "lon": r[11]
        })
    return jsonify(events)

# ----- scheduler start -----
def start_scheduler():
    scheduler = BackgroundScheduler()
    scheduler.add_job(collector_job, "interval", minutes=SEARCH_INTERVAL_MINUTES, next_run_time=datetime.now(timezone.utc))
    scheduler.start()
    logger.info("Scheduler started: interval=%d minutes", SEARCH_INTERVAL_MINUTES)
    return scheduler

if __name__ == "__main__":
    try:
        logger.info("Running initial collection job (startup)...")
        collector_job()
    except Exception as e:
        logger.exception("Initial collector job failed: %s", e)
    scheduler = start_scheduler()
    try:
        app.run(host="0.0.0.0", port=5000, debug=False)
    finally:
        scheduler.shutdown()
