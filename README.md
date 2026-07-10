# EventCart

EventCart is a Python-based event discovery and organization platform that collects technology-related events from the web, enriches them with location details, stores them in a local database, and presents them through a Django web interface. The project is designed for users who want to discover upcoming hackathons, meetups, conferences, webinars, workshops, and similar events in cities like Pune, Mumbai, and Bengaluru.

## What the project does

EventCart combines three main capabilities:

1. Event discovery
   - Searches the web for events using SerpAPI.
   - Fetches event details from public event pages.
   - Saves HTML snapshots for later reference.

2. Event enrichment
   - Extracts titles and descriptions from event pages.
   - Detects venue/location information from HTML and structured data.
   - Optionally enriches locations using Eventbrite, Meetup, and geocoding APIs.
   - Stores approximate city/area information.

3. Event browsing and personalization
   - Organizes events into folders such as Hackathons, Meetups, Conferences, Webinars, and Workshops.
   - Lets users browse events by folder and city/area.
   - Supports search, event detail pages, and nearby-event discovery.
   - Supports interest-based notifications for users who mark an event as interesting.

---

## Main use cases

EventCart can be used for:

- Discovering relevant tech events in major Indian cities.
- Saving and organizing events into thematic folders.
- Filtering events by city or area.
- Tracking events a user wants to attend or follow.
- Receiving email notifications when a matching event is added.
- Running an internal admin dashboard to manage the collector.

---

## Project overview

This repository is a hybrid project with two related implementations:

- A Django web application for user-facing browsing and administration.
- A Flask-based collector script in app.py that performs the scraping and event ingestion flow.

The current primary application is the Django version, driven by manage.py and the collector app.

---

## Architecture

### 1. Web application layer
The Django app exposes pages and APIs for:

- Public homepage
- User registration and login
- Folder browsing
- Search
- Event detail pages
- Admin dashboard
- REST-like JSON endpoints for folders, areas, and nearby events

### 2. Collector layer
The collector logic is implemented in collector/collector_logic.py and is responsible for:

- Searching for event URLs
- Downloading the corresponding pages
- Extracting metadata
- Classifying events into folders
- Saving HTML snapshots
- Storing data into SQLite

### 3. Data layer
The application uses SQLite databases for persistence:

- Main database: db.sqlite3 (for Django auth and app state)
- Event data database: data/events.db (used by the collector logic)
- HTML snapshots: data/html/

### 4. Recommendation layer
The project includes a recommendation model folder with notebooks, CSV files, and a pickled model. In the current Django views, the app uses a lightweight preference-based recommendation approach rather than the serialized model directly. The recommendation_model folder appears to be an experimental or earlier prototype.

---

## Data models and storage design

The project does not currently define Django ORM models in collector/models.py. Instead, it uses direct SQLite tables created inside the collector logic.

### Core database tables

- events
  - Stores each discovered event.
  - Includes details such as title, URL, description, fetched timestamp, location, area, and geocoded values.

- folders
  - Stores folders/categories such as Hackathons, Meetups, Workshops, etc.

- event_folders
  - Maps events to folders using a many-to-many style relationship.

- user_preferences
  - Stores user interaction preferences such as folder, area, and keyword preferences.

- user_event_interests
  - Stores which events a user marked as interesting.

- event_interest_notifications
  - Prevents duplicate notifications for the same event/user combination.

### Why these structures are used

- events: central record for every event discovered.
- folders: supports categorization and browsing.
- event_folders: allows one event to appear in multiple categories.
- user_preferences: powers simple personalized recommendations.
- user_event_interests: enables notification workflows.
- event_interest_notifications: avoids repeated emails.

---

## Main features

### Event collection
The collector searches the web for event content, fetches pages, and stores the results.

### Event classification
Events are categorized based on keywords such as:

- hackathon
- meetup
- conference
- webinar
- workshop
- training
- community

### Location enrichment
The system tries to enrich venue information with:

- HTML-based extraction
- JSON-LD structured event data
- Eventbrite venue lookup
- Meetup venue lookup
- OpenCage/Nominatim geocoding

### User experience
Users can:

- Register and log in
- Browse folders
- Search events
- Open event details
- Filter by area
- Receive interest notifications

### Admin dashboard
Admins can configure the collector and monitor counts and run status.

---

## Recommendation logic

The app includes a basic recommendation system that ranks events for logged-in users based on:

- folder preferences
- area preferences
- keyword preferences

The logic is implemented in collector/views.py and uses the user’s stored preferences to boost related events in the recommendation list.

There is also a recommendation_model folder with:

- event_recommender.pkl
- model.ipynb
- sample CSV datasets

These appear to be an experimental or earlier modeling effort and are not the current primary recommendation engine in the Django app.

---

## Project structure

```text
Event_cart/
├── app.py                      # Legacy Flask-based event collector
├── manage.py                   # Django entry point
├── requirements.txt            # Python dependencies
├── run.py                      # Small helper script
├── db.sqlite3                  # Django database
├── collector/
│   ├── admin.py
│   ├── apps.py
│   ├── collector_logic.py      # Core scraping, enrichment, and storage logic
│   ├── models.py               # Currently empty; uses direct SQLite instead of ORM models
│   ├── scheduler.py            # APScheduler-based collector scheduling
│   ├── urls.py                 # Django URL routes
│   └── views.py                # Django views and app logic
├── eventsite/
│   ├── settings.py             # Django configuration
│   ├── urls.py                 # Project URL configuration
│   └── wsgi.py/asgi.py
├── templates/                  # HTML templates for the UI
├── static/                     # Static assets such as images and CSS
├── data/
│   ├── events.db               # Collector database
│   └── html/                   # Saved event HTML snapshots
├── recommendation_model/       # Notebook, CSVs, and model prototype
└── seed_interest_test_event.py # Test script for notification flow
```

---

## Technologies used

- Python 3
- Django 5.1
- Flask (legacy collector path)
- SQLite
- Requests
- BeautifulSoup
- APScheduler
- SerpAPI
- OpenCage / Nominatim geocoding (optional)
- Django authentication system

---

## Installation and Setup

Follow these steps to set up the project locally:

### 1. Clone the Repository
```bash
git clone <repository-url>
cd Event_cart
```

### 2. Create and Activate a Virtual Environment
It is highly recommended to use a virtual environment to manage dependencies:

* **Unix/macOS**:
  ```bash
  python3 -m venv venv
  source venv/bin/activate
  ```
* **Windows (Command Prompt)**:
  ```cmd
  python -m venv venv
  venv\Scripts\activate.bat
  ```
* **Windows (PowerShell)**:
  ```powershell
  python -m venv venv
  venv\Scripts\Activate.ps1
  ```

### 3. Install Dependencies
Install all the required Python packages:
```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables
Copy the template `.env.example` file to `.env`:
* **Unix/macOS**:
  ```bash
  cp .env.example .env
  ```
* **Windows (PowerShell)**:
  ```powershell
  Copy-Item .env.example .env
  ```
* **Windows (Command Prompt)**:
  ```cmd
  copy .env.example .env
  ```

Open the newly created `.env` file and configure the settings. Below are the supported environment variables:
* `DJANGO_SECRET_KEY`: Custom secret key for Django security.
* `DJANGO_DEBUG`: Set to `1` (enabled) or `0` (disabled). Defaults to `1`.
* `ALLOWED_HOSTS`: Comma-separated list of allowed hostnames (e.g. `127.0.0.1,localhost`).
* `SERPAPI_KEY`: Required if you want to run the SerpAPI-based event scraper.
* `EVENTBRITE_TOKEN`, `MEETUP_TOKEN`: Optional API keys to enrich events via Meetup or Eventbrite.
* `OPENCAGE_KEY`: Optional API key to fetch geographic coordinates for address geocoding.
* `EVENTBOT_ADMIN_USER` / `EVENTBOT_ADMIN_PASS`: Credentials used to log in to the admin dashboard (default: `admin` / `admin123`).
* `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USER`, `EMAIL_PASS`, `EMAIL_USE_TLS`, `DEFAULT_FROM_EMAIL`: Settings used for SMTP server integration (for email notifications to users).

### 5. Initialize the Databases
The application utilizes two distinct SQLite databases:
1. **Django Database (`db.sqlite3`)**: Keeps track of user accounts, sessions, and default Django features. You must run migrations to initialize it:
   ```bash
   python manage.py migrate
   ```
2. **Collector Database (`data/events.db`)**: Holds scraped events, folders, and preferences. It is **automatically created and initialized** by `collector/collector_logic.py` upon starting the application or running scripts.

---

## Running the Application

### Option A: Django Web Application (Recommended)
This starts the full interactive user application.

1. Start the Django development server:
   ```bash
   python manage.py runserver
   ```
2. Open your web browser and navigate to:
   * **Homepage**: [http://127.0.0.1:8000/](http://127.0.0.1:8000/) - Browse folders, search, mark interests, and request nearby events.
   * **User Login / Registration**: [http://127.0.0.1:8000/login/](http://127.0.0.1:8000/login/) / [http://127.0.0.1:8000/register/](http://127.0.0.1:8000/register/)
   * **Admin Dashboard**: [http://127.0.0.1:8000/admin/login/](http://127.0.0.1:8000/admin/login/) - Log in with credentials specified in your `.env` (default is `admin` / `admin123`) to configure and trigger the scraper scheduler.

### Option B: Running the Legacy Flask Collector
If you wish to run the legacy Flask interface and background collector:
```bash
python app.py
```
This runs a separate Flask dashboard to view and trigger collector jobs.

---

## Running inside Docker
You can containerize and run the application using Docker:

1. **Build the Docker Image**:
   ```bash
   docker build -t eventcart .
   ```
2. **Run the Container**:
   ```bash
   docker run -d -p 8000:8000 --env-file .env eventcart
   ```
   The Django application will be accessible at [http://localhost:8000/](http://localhost:8000/).

---

## Running Utility & Test Scripts

### Seed and Test Interest Notifications
To verify that the email notification flow functions correctly:
1. Make sure SMTP variables (`EMAIL_USER`, `EMAIL_PASS`, etc.) are configured in `.env`.
2. Run the seed script:
   ```bash
   python seed_interest_test_event.py
   ```
   This will insert a mock test event and trigger the email dispatch logic for matched users.

### Run Automated Selenium Tests
To run UI automation tests via Selenium and LambdaTest:
1. Verify the LambdaTest credentials (`username` and `access_key`) inside `test_lambdatest.py`.
2. Execute the test suite:
   ```bash
   python test_lambdatest.py
   ```

---

## Notes and caveats

- The repository contains hard-coded API keys in the current code. For production use, these should be moved to environment variables and kept secret.
- The project is still in a prototype or early-stage form and uses direct SQLite operations rather than a full Django ORM model layer.
- Some features depend on external APIs and may work best when the relevant tokens are configured.
- The HTML snapshot storage is useful for offline inspection and debugging.

---

## Summary

EventCart is an event discovery and organization platform focused on technology events. It scrapes public event sources, enriches them with location data, stores them in a local database, categorizes them into folders, and enables users to browse and receive notifications about events relevant to them.

It is useful for:

- event discovery
- tech community engagement
- personal event tracking
- lightweight event recommendation
- local event organization workflows
