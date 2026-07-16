# EventCart

EventCart is a Django-based platform that discovers, organizes, and recommends technology events (hackathons, meetups, conferences, webinars, and workshops) in Indian cities like Pune, Mumbai, and Bengaluru.

**Live Demo:** [https://your-live-link-here.com]([https://your-live-link-here.com)
](https://eventcart.onrender.com)
---

## Features

- 🔍 **Event Discovery** — Automatically scrapes tech events from the web using SerpAPI
- 📍 **Location Enrichment** — Detects and geocodes venue/city information
- 🗂️ **Smart Categorization** — Organizes events into folders (Hackathons, Meetups, Conferences, etc.)
- 👤 **User Accounts** — Register, log in, and save event preferences
- 🔔 **Notifications** — Email alerts when new events match your interests
- 🛠️ **Admin Dashboard** — Manage and trigger the event collector

---

## Tech Stack

- **Backend:** Python, Django 5.1
- **Database:** SQLite
- **Scraping:** BeautifulSoup, Requests, SerpAPI
- **Scheduling:** APScheduler
- **Geocoding:** OpenCage / Nominatim

---

## Getting Started

### 1. Clone & Set Up Environment

```bash
git clone <repository-url>
cd Event_cart
python -m venv venv
venv\Scripts\Activate.ps1      # Windows PowerShell
# source venv/bin/activate     # macOS/Linux
pip install -r requirements.txt
```

### 2. Configure Environment Variables

```bash
Copy-Item .env.example .env    # Windows
# cp .env.example .env         # macOS/Linux
```

Then fill in your `.env` file with your keys (SerpAPI, email SMTP, etc.) — see `.env.example` for the full list.

### 3. Set Up the Database

```bash
python manage.py migrate
```

The collector database (`data/events.db`) is created automatically on first run.

### 4. Run the Server

```bash
python manage.py runserver
```

Visit:
- **App:** [http://127.0.0.1:8000/](http://127.0.0.1:8000/)
- **Admin:** [http://127.0.0.1:8000/admin/login/](http://127.0.0.1:8000/admin/login/)

---

## Project Structure

```
Event_cart/
├── manage.py               # Django entry point
├── collector/               # Scraping, enrichment, views, and admin logic
├── eventsite/                # Django settings and URLs
├── templates/ / static/      # Frontend files
├── data/                      # Collector database + saved HTML snapshots
└── recommendation_model/    # Early prototype notebook/model (experimental)
```

---

## Notes

- This project is in active/prototype development.
- API keys should always be kept in `.env`, never committed to source control.

---

## License

Add your license here (MIT, Apache 2.0, etc.)
