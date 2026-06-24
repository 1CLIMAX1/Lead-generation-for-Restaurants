from datetime import date, datetime
from decimal import Decimal

from flask import Flask, jsonify, render_template, request
import pymysql
import os

from flask_cors import CORS          # ← add this import

app = Flask(__name__, template_folder="templates")
CORS(app, origins=["https://leadgenerati.netlify.app"])  # ← add this



# MySQL connection details
def get_connection():
    return pymysql.connect(
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ.get("MYSQL_PORT", 4000)),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        database=os.environ["MYSQL_DB"],
        cursorclass=pymysql.cursors.DictCursor,
        ssl_verify_cert=True,
        ssl_verify_identity=True
    )

TABLE_NAME = "Restaurant_information"

FIELD_ALIASES = {
    "name": ("name", "restaurant_name", "business_name"),
    "official_url": ("official_url", "website", "url"),
    "has_official_website": ("has_official_website", "has_website", "hasWeb"),
    "lead_score": ("lead_score", "score"),
    "lead_category": ("lead_category", "cat", "category"),
    "extracted_phone": ("extracted_phone", "phone", "Contact No.", "contact_no"),
    "google_search_url": ("google_search_url", "search_url"),
    "scrape_status": ("scrape_status", "status"),
}

MUTABLE_FIELDS = (
    "name",
    "official_url",
    "has_official_website",
    "lead_score",
    "lead_category",
    "extracted_phone",
    "google_search_url",
    "scrape_status",
)


def quote_identifier(identifier):
    return f"`{str(identifier).replace('`', '``')}`"


def fetchall_dict(cursor):
    return cursor.fetchall()


def json_value(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def get_columns():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(f"SHOW COLUMNS FROM {quote_identifier(TABLE_NAME)}")
    rows = fetchall_dict(cursor)
    cursor.close()
    conn.close()
    return rows


def get_primary_key(columns=None):
    return "name"


def ensure_primary_key():
    return "name"

def resolve_fields(columns):
    column_names = {column["Field"] for column in columns}
    resolved = {}

    for logical_name, aliases in FIELD_ALIASES.items():
        resolved[logical_name] = next(
            (alias for alias in aliases if alias in column_names), None
        )

    return resolved


def normalize_bool(value):
    if isinstance(value, bytes):
        return int.from_bytes(value, byteorder="big") != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def clean_payload_value(logical_name, value):
    if logical_name == "has_official_website":
        return 1 if normalize_bool(value) else 0
    if logical_name == "lead_score":
        if value in ("", None):
            return 0
        return max(0, min(100, int(value)))
    if value is None:
        return ""
    return str(value).strip()


def payload_to_columns(payload, columns, field_map):
    column_names = {column["Field"] for column in columns}
    values = {}

    for logical_name in MUTABLE_FIELDS:
        column_name = field_map.get(logical_name)
        if column_name and column_name in column_names and logical_name in payload:
            values[column_name] = clean_payload_value(logical_name, payload[logical_name])

    return values


def normalize_lead(row, primary_key, field_map):
    normalized = {key: json_value(value) for key, value in row.items()}

    for logical_name, column_name in field_map.items():
        normalized[logical_name] = json_value(row.get(column_name)) if column_name else ""

    normalized["_id"] = json_value(row.get(primary_key)) if primary_key else None
    normalized["has_official_website"] = normalize_bool(
        normalized.get("has_official_website")
    )

    try:
        normalized["lead_score"] = int(normalized.get("lead_score") or 0)
    except (TypeError, ValueError):
        normalized["lead_score"] = 0

    return normalized


def get_lead(primary_key, lead_id, field_map):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        f"SELECT * FROM {quote_identifier(TABLE_NAME)} "
        f"WHERE {quote_identifier(primary_key)} = %s",
        (lead_id,),
    )
    rows = fetchall_dict(cursor)
    cursor.close()
    conn.close()
    return normalize_lead(rows[0], primary_key, field_map) if rows else None


@app.get("/")
@app.get("/dashboard")
def dashboard():
    return render_template("restaurant_leads_dashboard.html")


@app.get("/api/leads")
def list_leads():
    primary_key = ensure_primary_key()
    columns = get_columns()
    field_map = resolve_fields(columns)

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(f"SELECT * FROM {quote_identifier(TABLE_NAME)}")
    rows = fetchall_dict(cursor)
    cursor.close()
    conn.close()

    leads = [normalize_lead(row, primary_key, field_map) for row in rows]

    return jsonify(
        {
            "leads": leads,
            "primary_key": primary_key,
            "fields": field_map,
            "columns": [column["Field"] for column in columns],
        }
    )


@app.post("/api/leads")
def create_lead():
    primary_key = ensure_primary_key()
    columns = get_columns()
    field_map = resolve_fields(columns)
    payload = request.get_json(silent=True) or {}
    values = payload_to_columns(payload, columns, field_map)

    if not values:
        return jsonify({"error": "No valid lead fields were provided."}), 400

    column_sql = ", ".join(quote_identifier(column) for column in values)
    placeholder_sql = ", ".join(["%s"] * len(values))

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        f"INSERT INTO {quote_identifier(TABLE_NAME)} ({column_sql}) "
        f"VALUES ({placeholder_sql})",
        tuple(values.values()),
    )
    conn.commit()
    lead_id = cursor.lastrowid
    cursor.close()
    conn.close()

    lead = get_lead(primary_key, lead_id, field_map)
    return jsonify({"lead": lead}), 201


@app.put("/api/leads/<path:lead_id>")
def update_lead(lead_id):
    primary_key = ensure_primary_key()
    columns = get_columns()
    field_map = resolve_fields(columns)
    payload = request.get_json(silent=True) or {}
    values = payload_to_columns(payload, columns, field_map)

    if not values:
        return jsonify({"error": "No valid lead fields were provided."}), 400

    set_sql = ", ".join(f"{quote_identifier(column)} = %s" for column in values)

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        f"UPDATE {quote_identifier(TABLE_NAME)} SET {set_sql} "
        f"WHERE {quote_identifier(primary_key)} = %s",
        (*values.values(), lead_id),
    )
    conn.commit()
    affected = cursor.rowcount
    cursor.close()
    conn.close()

    if not affected:
        return jsonify({"error": "Lead not found."}), 404

    return jsonify({"lead": get_lead(primary_key, lead_id, field_map)})


@app.delete("/api/leads/<path:lead_id>")
def delete_lead(lead_id):
    primary_key = ensure_primary_key()

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        f"DELETE FROM {quote_identifier(TABLE_NAME)} "
        f"WHERE {quote_identifier(primary_key)} = %s",
        (lead_id,),
    )
    conn.commit()
    affected = cursor.rowcount
    cursor.close()
    conn.close()

    if not affected:
        return jsonify({"error": "Lead not found."}), 404

    return jsonify({"ok": True})

@app.get("/test-db")
def test_db():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1")
    result = cursor.fetchone()
    cursor.close()
    conn.close()
    return jsonify(result)

if __name__ == "__main__":
    import os
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
