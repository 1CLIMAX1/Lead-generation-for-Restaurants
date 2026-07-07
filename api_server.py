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

from flask import Flask, jsonify, request
from flask_cors import CORS

from db import get_connection, quote_identifier

app = Flask(__name__)
CORS(app)  # allow dashboard (different origin) to call this API

# ── In-memory job store ───────────────────────────────────────────────────────
# Keyed by job_id → { status, log, started_at, finished_at, params }
# For production you'd persist this in Redis/DB, but in-memory is fine here.
_jobs: dict = {}
_jobs_lock = threading.Lock()


def table_name_for_domain(domain: str) -> str:
    safe = re.sub(r"[^\w]", "_", domain.strip().lower())
    safe = re.sub(r"_+", "_", safe).strip("_")
    return f"leads_{safe}"


def get_all_lead_tables(cursor) -> list:
    cursor.execute(
        "SELECT TABLE_NAME FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME LIKE 'leads_%'"
    )
    return [row["TABLE_NAME"] for row in cursor.fetchall()]


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
            all_tables = get_all_lead_tables(cursor)
            if not all_tables:
                return jsonify({"leads": []})

            if domain:
                tbl = table_name_for_domain(domain)
                tables_to_query = [tbl] if tbl in all_tables else []
            else:
                tables_to_query = all_tables

            if not tables_to_query:
                return jsonify({"leads": []})

            leads = []
            for tbl in tables_to_query:
                if city:
                    cursor.execute(
                        f"SELECT * FROM {quote_identifier(tbl)} "
                        f"WHERE LOWER(city) = LOWER(%s) "
                        f"ORDER BY lead_score DESC LIMIT %s",
                        (city, limit)
                    )
                else:
                    cursor.execute(
                        f"SELECT * FROM {quote_identifier(tbl)} "
                        f"ORDER BY lead_score DESC LIMIT %s",
                        (limit,)
                    )
                rows = cursor.fetchall()
                for row in rows:
                    row["_table"] = tbl
                    # Ensure _id exists for dashboard compatibility
                    row["_id"] = row.get("name", "")
                leads.extend(rows)

            # Re-sort merged results
            leads.sort(key=lambda r: int(r.get("lead_score") or 0), reverse=True)

    finally:
        conn.close()

    return jsonify({"leads": leads[:limit]})


@app.post("/api/leads")
def add_lead():
    body = request.get_json(force=True, silent=True) or {}
    domain = body.get("domain", "business")
    tbl    = table_name_for_domain(domain)

    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            # ensure_table imported logic inline to avoid circular import issues
            cursor.execute(
                "SELECT COUNT(*) AS count FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s", (tbl,)
            )
            if cursor.fetchone()["count"] == 0:
                return jsonify({"error": f"Table {tbl} does not exist yet. Run a scrape first."}), 400

            cols = [c for c in body if c not in ("_id", "_table", "domain")]
            if "name" not in cols:
                return jsonify({"error": "name is required"}), 400

            col_sql   = ", ".join(quote_identifier(c) for c in cols)
            val_sql   = ", ".join(["%s"] * len(cols))
            values    = [body[c] for c in cols]
            cursor.execute(
                f"INSERT INTO {quote_identifier(tbl)} ({col_sql}) VALUES ({val_sql})",
                values
            )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"ok": True})


@app.put("/api/leads/<path:lead_id>")
def update_lead(lead_id: str):
    body   = request.get_json(force=True, silent=True) or {}
    domain = body.get("domain", "business")
    tbl    = table_name_for_domain(domain)

    update_cols = {k: v for k, v in body.items()
                   if k not in ("name", "_id", "_table", "domain")}
    if not update_cols:
        return jsonify({"error": "nothing to update"}), 400

    set_sql = ", ".join(f"{quote_identifier(k)} = %s" for k in update_cols)
    values  = list(update_cols.values()) + [lead_id]

    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"UPDATE {quote_identifier(tbl)} SET {set_sql} WHERE name = %s",
                values
            )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"ok": True})


@app.delete("/api/leads/<path:lead_id>")
def delete_lead(lead_id: str):
    domain = request.args.get("domain", "business")
    tbl    = table_name_for_domain(domain)

    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"DELETE FROM {quote_identifier(tbl)} WHERE name = %s",
                (lead_id,)
            )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"ok": True})


# ── /api/domains & /api/cities ────────────────────────────────────────────────

@app.get("/api/domains")
def get_domains():
    """Returns list of domains that have data, e.g. ['restaurant', 'gym']"""
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            tables = get_all_lead_tables(cursor)
    finally:
        conn.close()

    domains = [t.replace("leads_", "", 1) for t in tables]
    return jsonify({"domains": domains})


@app.get("/api/cities")
def get_cities():
    """
    GET /api/cities?domain=restaurant
    Returns distinct cities for a domain (or all domains if not specified).
    """
    domain = request.args.get("domain", "").strip()

    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            all_tables = get_all_lead_tables(cursor)
            if domain:
                tbl = table_name_for_domain(domain)
                tables = [tbl] if tbl in all_tables else []
            else:
                tables = all_tables

            cities = set()
            for tbl in tables:
                cursor.execute(
                    f"SELECT DISTINCT city FROM {quote_identifier(tbl)} "
                    f"WHERE city IS NOT NULL AND city != ''"
                )
                for row in cursor.fetchall():
                    cities.add(row["city"].strip())
    finally:
        conn.close()

    return jsonify({"cities": sorted(cities)})


# ── Health check ──────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return jsonify({"ok": True, "time": datetime.now(timezone.utc).isoformat()})


# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
