"""
api_server.py  —  Backend API Server
--------------------------------------
Flask server that sits between the dashboard and the pipeline.
Deploy this on Render (or any server) alongside the other scripts.

Endpoints:
    POST /api/scrape          — start a scrape job
    GET  /api/scrape/status   — poll job progress
    GET  /api/leads           — fetch leads from DB (with optional ?city= ?domain=)
    POST /api/leads           — add a lead manually
    PUT  /api/leads/<id>      — edit a lead
    DELETE /api/leads/<id>    — delete a lead
    GET  /api/domains         — list all lead tables (domains) in DB
    GET  /api/cities          — list cities for a given ?domain=

Install deps:
    pip install flask flask-cors pymysql

Run locally:
    python api_server.py

On Render:
    Start command: python api_server.py
    Or with gunicorn: gunicorn api_server:app
"""

import os
import re
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone

from flask import Flask, jsonify, request, render_template
from flask_cors import CORS

from db import get_connection, quote_identifier

app = Flask(
    __name__,
    template_folder="templates",
    static_folder="static"
)
CORS(app)  # allow dashboard (different origin) to call this API

# ── In-memory job store ───────────────────────────────────────────────────────
# Keyed by job_id → { status, log, started_at, finished_at, params }
# For production you'd persist this in Redis/DB, but in-memory is fine here.
_jobs: dict = {}
_jobs_lock = threading.Lock()

TABLE_NAME = "leads"




# ── Job runner (runs in background thread) ────────────────────────────────────

def _run_pipeline_job(job_id: str, domain: str, location: str,
                      sources: list, count: int):
    def log(msg: str):
        timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        line = f"[{timestamp}] {msg}"
        print(line)
        with _jobs_lock:
            _jobs[job_id]["log"].append(line)

    with _jobs_lock:
        _jobs[job_id]["status"] = "running"

    try:
        log(f"Starting scrape: domain={domain}, location={location}, sources={sources}, count={count}")

        cmd = [
            sys.executable, "run_pipeline.py",
            "--domain",   domain,
            "--location", location,
            "--sources",  *sources,
            "--count",    str(count),
        ]
        log(f"Running: {' '.join(cmd)}")

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        # Stream output line by line into the job log
        for line in process.stdout:
            log(line.rstrip())

        process.wait()

        if process.returncode == 0:
            with _jobs_lock:
                _jobs[job_id]["status"] = "done"
            log("✅ Pipeline finished successfully")
        else:
            with _jobs_lock:
                _jobs[job_id]["status"] = "error"
            log(f"❌ Pipeline exited with code {process.returncode}")

    except Exception as e:
        with _jobs_lock:
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["log"].append(f"[ERROR] {e}")

    with _jobs_lock:
        _jobs[job_id]["finished_at"] = datetime.now(timezone.utc).isoformat()


# ── /api/scrape ───────────────────────────────────────────────────────────────

@app.get("/")
@app.get("/dashboard")
def dashboard():
    return render_template("dashboard.html")

@app.post("/api/scrape")
def start_scrape():
    """
    POST /api/scrape
    Body: { "domain": "restaurant", "location": "Bhopal",
            "sources": ["google", "linkedin"], "count": 50 }
    Returns: { "job_id": "...", "status": "queued" }
    """
    body     = request.get_json(force=True, silent=True) or {}
    domain   = str(body.get("domain",   "")).strip()
    location = str(body.get("location", "")).strip()
    sources  = body.get("sources", ["google"])
    count    = int(body.get("count", 50))

    if not domain or not location:
        return jsonify({"error": "domain and location are required"}), 400

    valid_sources = {"google", "linkedin", "facebook", "reddit", "justdial"}
    sources = [s for s in sources if s in valid_sources] or ["google"]
    count   = max(1, min(count, 200))  # cap at 200

    job_id = str(uuid.uuid4())[:8]
    with _jobs_lock:
        _jobs[job_id] = {
            "job_id":      job_id,
            "status":      "queued",
            "log":         [],
            "started_at":  datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "params":      {"domain": domain, "location": location,
                            "sources": sources, "count": count},
        }

    thread = threading.Thread(
        target=_run_pipeline_job,
        args=(job_id, domain, location, sources, count),
        daemon=True,
    )
    thread.start()

    return jsonify({"job_id": job_id, "status": "queued"})


@app.get("/api/scrape/status")
def scrape_status():
    """
    GET /api/scrape/status?job_id=abc123
    Returns job status + log lines for live progress display.
    """
    job_id = request.args.get("job_id", "")
    with _jobs_lock:
        job = _jobs.get(job_id)

    if not job:
        # Return latest job if no id given
        with _jobs_lock:
            if not _jobs:
                return jsonify({"status": "idle", "log": []})
            job = sorted(_jobs.values(), key=lambda j: j["started_at"])[-1]

    return jsonify(job)


# ── /api/leads ────────────────────────────────────────────────────────────────

@app.get("/api/leads")
def get_leads():
    """
    GET /api/leads?domain=restaurant&city=Bhopal&limit=200
    If domain not specified, queries all leads_ tables and merges.
    """
    domain  = request.args.get("domain",  "").strip()
    city    = request.args.get("city",    "").strip()
    limit   = min(int(request.args.get("limit", 200)), 500)

    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            query = f"SELECT * FROM {quote_identifier(TABLE_NAME)} WHERE 1=1"
            params = []

            if domain:
                query += " AND LOWER(domain)=LOWER(%s)"
                params.append(domain)

            if city:
                query += " AND LOWER(city)=LOWER(%s)"
                params.append(city)

            query += " ORDER BY lead_score DESC LIMIT %s"
            params.append(limit)

            cursor.execute(query, params)
            leads = cursor.fetchall()

            for row in leads:
                row["_id"] = row["id"]

    finally:
        conn.close()

    return jsonify({"leads": leads[:limit]})


@app.post("/api/leads")
def add_lead():
    body = request.get_json(force=True, silent=True) or {}

    cols = [c for c in body if c not in ("_id", "_table")]

    if "name" not in cols:
        return jsonify({"error": "name is required"}), 400

    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            col_sql = ", ".join(quote_identifier(c) for c in cols)
            val_sql = ", ".join(["%s"] * len(cols))
            values = [body[c] for c in cols]

            cursor.execute(
                f"INSERT INTO {quote_identifier(TABLE_NAME)} ({col_sql}) VALUES ({val_sql})",
                values,
            )

        conn.commit()

    finally:
        conn.close()

    return jsonify({"ok": True})



@app.put("/api/leads/<int:lead_id>")
def update_lead(lead_id):

    body = request.get_json(force=True, silent=True) or {}

    update_cols = {
        k: v for k, v in body.items()
        if k not in ("id", "_id", "_table")
    }

    if not update_cols:
        return jsonify({"error": "nothing to update"}), 400

    set_sql = ", ".join(
        f"{quote_identifier(k)}=%s"
        for k in update_cols
    )

    values = list(update_cols.values())
    values.append(lead_id)

    conn = get_connection()

    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE {quote_identifier(TABLE_NAME)}
                SET {set_sql}
                WHERE id=%s
                """,
                values,
            )

        conn.commit()

    finally:
        conn.close()

    return jsonify({"ok": True})


@app.delete("/api/leads/<int:lead_id>")
def delete_lead(lead_id):

    conn = get_connection()

    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"DELETE FROM {quote_identifier(TABLE_NAME)} WHERE id=%s",
                (lead_id,),
            )

        conn.commit()

    finally:
        conn.close()

    return jsonify({"ok": True})


# ── /api/domains & /api/cities ────────────────────────────────────────────────

@app.get("/api/domains")
def get_domains():

    conn = get_connection()

    try:
        with conn.cursor() as cursor:

            cursor.execute("""
                SELECT DISTINCT domain
                FROM leads
                WHERE domain IS NOT NULL
                ORDER BY domain
            """)

            domains = [
                r["domain"]
                for r in cursor.fetchall()
            ]

    finally:
        conn.close()

    return jsonify({"domains": domains})


@app.get("/api/cities")
def get_cities():

    domain = request.args.get("domain", "").strip()

    conn = get_connection()

    try:
        with conn.cursor() as cursor:

            if domain:

                cursor.execute(
                    """
                    SELECT DISTINCT city
                    FROM leads
                    WHERE LOWER(domain)=LOWER(%s)
                    ORDER BY city
                    """,
                    (domain,),
                )

            else:

                cursor.execute("""
                    SELECT DISTINCT city
                    FROM leads
                    ORDER BY city
                """)

            cities = [
                r["city"]
                for r in cursor.fetchall()
                if r["city"]
            ]

    finally:
        conn.close()

    return jsonify({"cities": cities})


# ── Health check ──────────────────────────────────────────────────────────────

@app.get("/test-db")
def test_db():

    conn = get_connection()

    try:
        with conn.cursor() as cursor:

            cursor.execute(
                "SELECT COUNT(*) AS total FROM leads"
            )

            return jsonify(cursor.fetchone())

    finally:
        conn.close()


@app.get("/health")
def health():
    return jsonify({"ok": True, "time": datetime.now(timezone.utc).isoformat()})


# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
