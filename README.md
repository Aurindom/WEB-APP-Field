# Field Scope

A mobile-first Progressive Web App for field agents to estimate property repair costs during walkthroughs and export a ZIP containing an Excel cost breakdown and all photos.

**Live app:** https://aurindom.github.io/WEB-APP-Field/

---

## Features

- Room-by-room repair checklist across 7 room types (Bathroom, Bedroom, Living Area, Kitchen, Interior, Systems, Exterior)
- Multi-instance rooms. Add as many bathrooms, bedrooms, or living areas as the property has.
- Per-item quantity inputs with mobile-friendly steppers
- Photo capture per group, stored in IndexedDB (no size cap)
- AI group suggestion. Describe damage in plain language and AI maps it to the right repair groups.
- AI serial number OCR. Scan appliance data plates and a two-pass Claude jury extracts the serial.
- Per-group notes, per-project and global price overrides, custom line items
- Export: styled Excel workbook + all photos in a ZIP
- Works fully offline after first load. AI features degrade gracefully to manual input.

---

## Try It

Open https://aurindom.github.io/WEB-APP-Field/ on any device. No install required. On mobile, use "Add to Home Screen" for the full PWA experience.

AI features require an Anthropic API key. Tap ☰ then tap Anthropic API Key and paste your key from [console.anthropic.com](https://console.anthropic.com). The key is stored only on your device and sent directly to the backend. It is never logged or persisted server-side.

---

## Running Locally

### Prerequisites

- Python 3.10 or newer
- An Anthropic API key (for AI features, though the app works fully without it)

### Backend setup

```bash
cd backend
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

Create `backend/.env`:

```
ALLOWED_ORIGIN=http://localhost:5050
```

This file is gitignored and never committed.

### Run

Two terminals:

**Terminal 1 - Backend:**
```bash
cd backend
uvicorn main:app --host 127.0.0.1 --port 8000
```

**Terminal 2 - Frontend:**
```bash
python -m http.server 5050
```

Open http://localhost:5050, tap ☰ and then tap Anthropic API Key, and paste your key.

---

## Deployment

| Service | Purpose |
|---|---|
| Railway | FastAPI backend (`backend/` directory) |
| GitHub Pages | Static frontend (`index.html` at repo root) |

Railway environment variable required:
- `ALLOWED_ORIGIN`: set to the GitHub Pages URL (`https://aurindom.github.io`)

---

## After Code Changes

Bump `CACHE_VERSION` in `sw.js` and hard refresh with `Ctrl+Shift+R`.

---

## Tech Stack

| Layer | Choice |
|---|---|
| Frontend | Single HTML file, no build step |
| Storage | localStorage (state) + IndexedDB (photos) |
| Offline | Service Worker + Web App Manifest |
| Excel | xlsx-js-style (inlined) |
| ZIP | JSZip (inlined) |
| Backend | FastAPI + slowapi (hosted on Railway) |
| AI (serial OCR) | claude-sonnet-4-6 (two-pass jury with Levenshtein matching) |
| AI (group suggestion) | claude-haiku-4-5 (text classification over 19 fixed groups) |
