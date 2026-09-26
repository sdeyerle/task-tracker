# Task Tracker

A little web app for tracking what you're working on: add tasks, break them
into subtasks, and track percentage complete. Data is stored in a local
SQLite database (`tasks.db`, created automatically on first run).

## Run locally

```bash
pip install -r requirements.txt
python app.py
```

Then open http://localhost:5000.

## Deploy

The app reads the `PORT` environment variable, so it works on Render,
Railway, Fly.io, Heroku, etc.

- **Render:** this repo includes `render.yaml` — create a new Blueprint and
  point it at the repo. Note: Render's free tier has no persistent disk, so
  the SQLite database resets when the service restarts. For data that
  survives restarts, use a host with a persistent volume (e.g. Fly.io with
  a mounted volume) or swap in Postgres.
- **Procfile** is included for Heroku-style hosts.

## Features

- Add / rename / delete tasks
- Subtasks with checkboxes — checking them off auto-updates the task's %
- Manual progress slider (0–100%) per task
- Filter: Active / All / Done
- Mobile-friendly, follows system dark mode
