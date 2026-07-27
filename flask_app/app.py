from __future__ import annotations

import base64
import hashlib
import html
import hmac
import io
import os
import re
import sys
import threading
import time
from datetime import date, datetime
from functools import wraps
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import gspread
import requests
from PIL import Image, ImageOps
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    jsonify,
    flash,
)

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib

SHEET_ID = "1Mt-Y09-azOOQ9r6nqELCQbeoeNtE-Z3JJaJ5WiMzP0A"
WORKSHEET_NAME = "Dump"
TIMEZONE = ZoneInfo("Asia/Karachi")
UPLOAD_DIR = Path("uploads")

PARTNER_NAME_BY_CODE = {
    "D0573": "CBL",
    "D70002202": "Olpers KHI",
    "D70002246": "Olpers LHR",
    "Tapal":"Tapal",
}

COMPETITOR_BRANDS_BY_PARTNER = {
    "D70002202": ["Milkpak", "Dairy Omung", "Haleeb", "Dostea", "Good Milk", "Other"],
    "D70002246": ["Milkpak", "Dairy Omung", "Haleeb", "Dostea", "Good Milk", "Other"],
    "D0573": ["LU", "Bisconi", "Cookinia", "Other"],
    "Tapal": ["Meezan", "Lipton", "Vital", "Other"],
}

TOP_BRANDS_BY_PARTNER = {
    "D70002202": ["Olper's Milk", "Tarang", "TBA", "Others"],
    "D70002246": ["Olper's Milk", "Tarang", "TBA", "Others"],
    "Tapal": ["Tezdum", "Tapal", "Other"],
    "D0573": ["Oreo", "Gala", "Prince", "Other"],
}

BASE_HEADERS = [
    "Submission ID",
    "Submitted At",
    "Partner Name",
    "Shop Name",
    "Shop Picture + Selfie",
    "Area",
    "Sub Area",
    "Booker Name",
    "Shop Avg Monthly Sales",
    "Last Order Booker Visit",
    "Competitor Brands Available",
    "Top Brands Available",
    "Remarks",
]
LEGACY_HEADERS = BASE_HEADERS + ["Store Code", "Username"]
PAYMENT_HEADERS = [
    "Payment Gateways Available",
    "QR Code Payment Available",
    "QR Monthly Turnover",
]
FORM_HEADERS = LEGACY_HEADERS + PAYMENT_HEADERS
LOCATION_HEADERS = [
    "User Latitude",
    "User Longitude",
    "Location Accuracy (m)",
]
HEADERS = FORM_HEADERS + LOCATION_HEADERS


def _load_secrets() -> dict[str, Any]:
    secrets_data: dict[str, Any] = {}

    # 1. Try reading TOML files
    secrets_paths = [
        Path(".streamlit/secrets.toml"),
        Path("secrets.toml"),
        Path("../.streamlit/secrets.toml"),
        Path("../secrets.toml"),
    ]
    for path in secrets_paths:
        if path.is_file():
            try:
                with open(path, "rb") as f:
                    secrets_data.update(tomllib.load(f))
                    break
            except Exception:
                pass

    # 2. Try reading SECRETS_TOML environment variable
    secrets_env = os.getenv("SECRETS_TOML")
    if secrets_env:
        try:
            secrets_data.update(tomllib.loads(secrets_env))
        except Exception:
            pass

    # 3. Handle GCP Service Account from env vars (JSON string or flat keys)
    if "gcp_service_account" not in secrets_data:
        gcp_json = os.getenv("GCP_SERVICE_ACCOUNT")
        if gcp_json:
            try:
                import json
                secrets_data["gcp_service_account"] = json.loads(gcp_json)
            except Exception:
                pass
        elif os.getenv("private_key") or os.getenv("PRIVATE_KEY"):
            private_key = os.getenv("private_key") or os.getenv("PRIVATE_KEY") or ""
            if "\\n" in private_key and "\n" not in private_key:
                private_key = private_key.replace("\\n", "\n")
            secrets_data["gcp_service_account"] = {
                "type": os.getenv("type") or os.getenv("TYPE") or "service_account",
                "project_id": os.getenv("project_id") or os.getenv("PROJECT_ID") or "",
                "private_key_id": os.getenv("private_key_id") or os.getenv("PRIVATE_KEY_ID") or "",
                "private_key": private_key,
                "client_email": os.getenv("client_email") or os.getenv("CLIENT_EMAIL") or "",
                "client_id": os.getenv("client_id") or os.getenv("CLIENT_ID") or "",
                "auth_uri": os.getenv("auth_uri") or "https://accounts.google.com/o/oauth2/auth",
                "token_uri": os.getenv("token_uri") or "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": os.getenv("auth_provider_x509_cert_url") or "https://www.googleapis.com/oauth2/v1/certs",
                "client_x509_cert_url": os.getenv("client_x509_cert_url") or "",
            }

    # 4. Handle Users from USERS_JSON environment variable
    if "users" not in secrets_data:
        users_json = os.getenv("USERS_JSON")
        if users_json:
            try:
                import json
                secrets_data["users"] = json.loads(users_json)
            except Exception:
                pass

    # 5. Handle flat keys (lowercase or uppercase)
    for key in ["apps_script_upload_url", "apps_script_upload_token", "drive_folder_id", "flask_secret_key"]:
        if key not in secrets_data:
            val = os.getenv(key) or os.getenv(key.upper())
            if val:
                secrets_data[key] = val

    return secrets_data


SECRETS = _load_secrets()


def _secret(name: str, default: Any = None) -> Any:
    return SECRETS.get(name, default)


def _credentials() -> Credentials:
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    service_account = _secret("gcp_service_account")
    if service_account:
        return Credentials.from_service_account_info(dict(service_account), scopes=scopes)

    credentials_file = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if credentials_file:
        return Credentials.from_service_account_file(credentials_file, scopes=scopes)

    raise RuntimeError(
        "Google credentials are not configured. Add [gcp_service_account] to "
        ".streamlit/secrets.toml or set GOOGLE_APPLICATION_CREDENTIALS."
    )


USERS_FILE = Path("users_data.json")


def _get_all_users() -> dict[str, dict[str, Any]]:
    if USERS_FILE.is_file():
        try:
            import json
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    # Initial seed from SECRETS
    secrets_users = _secret("users", {})
    initial_users = {str(username): dict(settings) for username, settings in secrets_users.items()}
    if initial_users:
        _save_all_users(initial_users)
    return initial_users


def _save_all_users(users_dict: dict[str, dict[str, Any]]) -> None:
    import json
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users_dict, f, indent=2)


def _configured_users() -> dict[str, dict[str, Any]]:
    return _get_all_users()


def _authenticate(username: str, password: str) -> dict[str, Any] | None:
    users = _configured_users()
    entered_username = username.strip().casefold()
    matched_user = next(
        (
            (configured_username, configured_settings)
            for configured_username, configured_settings in users.items()
            if configured_username.casefold() == entered_username
        ),
        None,
    )
    if not matched_user:
        return None
    configured_username, settings = matched_user

    supplied_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
    expected_hash = str(settings.get("password_hash", "")).strip().lower()
    if (
        len(expected_hash) != 64
        or any(character not in "0123456789abcdef" for character in expected_hash)
        or not hmac.compare_digest(supplied_hash, expected_hash)
    ):
        return None

    role = str(settings.get("role", "")).strip().lower()
    if role not in {"admin", "partner"}:
        return None

    configured_partner_codes = settings.get(
        "partner_codes", settings.get("partner_code", "")
    )
    if isinstance(configured_partner_codes, str):
        partner_codes = [configured_partner_codes.strip()] if configured_partner_codes.strip() else []
    else:
        partner_codes = [
            str(code).strip() for code in configured_partner_codes if str(code).strip()
        ]
    partner_codes = list(dict.fromkeys(partner_codes))
    if role == "partner" and not partner_codes:
        return None

    return {
        "username": configured_username,
        "display_name": str(
            settings.get("display_name", configured_username)
        ).strip(),
        "role": role,
        "partner_codes": partner_codes,
    }


def _worksheet(credentials: Credentials) -> gspread.Worksheet:
    client = gspread.authorize(credentials)
    worksheet = client.open_by_key(SHEET_ID).worksheet(WORKSHEET_NAME)
    first_row = worksheet.row_values(1)
    if not first_row:
        worksheet.append_row(HEADERS, value_input_option="RAW")
        worksheet.freeze(rows=1)
    elif first_row == BASE_HEADERS:
        worksheet.update(
            range_name="N1:U1",
            values=[
                ["Store Code", "Username", *PAYMENT_HEADERS, *LOCATION_HEADERS]
            ],
        )
    elif first_row == BASE_HEADERS + ["Store Code"]:
        worksheet.update(
            range_name="O1:U1",
            values=[["Username", *PAYMENT_HEADERS, *LOCATION_HEADERS]],
        )
    elif first_row == BASE_HEADERS + ["Visit Date"]:
        worksheet.insert_cols(
            [["Store Code", "Username", *PAYMENT_HEADERS, *LOCATION_HEADERS]],
            col=len(BASE_HEADERS) + 1,
        )
    elif first_row == BASE_HEADERS + ["Store Code", "Visit Date"]:
        worksheet.insert_cols(
            [["Username", *PAYMENT_HEADERS, *LOCATION_HEADERS]],
            col=len(BASE_HEADERS) + 2,
        )
    elif first_row == LEGACY_HEADERS:
        worksheet.update(
            range_name="P1:U1", values=[[*PAYMENT_HEADERS, *LOCATION_HEADERS]]
        )
    elif first_row == LEGACY_HEADERS + ["Visit Date"]:
        worksheet.insert_cols(
            [[*PAYMENT_HEADERS, *LOCATION_HEADERS]],
            col=len(LEGACY_HEADERS) + 1,
        )
    elif first_row == FORM_HEADERS:
        worksheet.update(range_name="S1:U1", values=[LOCATION_HEADERS])
    elif first_row == FORM_HEADERS + ["Visit Date"]:
        worksheet.insert_cols([LOCATION_HEADERS], col=len(FORM_HEADERS) + 1)
    elif first_row == HEADERS + ["Visit Date"]:
        pass
    elif first_row != HEADERS:
        raise RuntimeError(
            "The Dump worksheet has different columns. Clear its first row or "
            "make it match the headers listed in README.md."
        )
    return worksheet


_UNIVERSE_CACHE: tuple[float, Any] = (0.0, None)


def _universe_partner_locations() -> dict[str, dict[str, dict[str, list[dict[str, str]]]]]:
    """Return Universe shops grouped by distributor, locality, and sub-locality (cached 5m)."""
    global _UNIVERSE_CACHE
    now_ts = time.time()
    if _UNIVERSE_CACHE[1] is not None and (now_ts - _UNIVERSE_CACHE[0] < 300):
        return _UNIVERSE_CACHE[1]

    client = gspread.authorize(_credentials())
    worksheet = client.open_by_key(SHEET_ID).worksheet("Universe")
    values = worksheet.get_all_values()
    if not values:
        return {}
    headers = [header.strip().lower() for header in values[0]]
    required_columns = [
        "distributor_code",
        "locality_name",
        "sub_locality_name",
        "store_code",
        "store_name",
        "channel_classification",
        "owner_name",
        "owner_contact",
        "address",
        "latitude",
        "longitude",
    ]
    try:
        column = {name: headers.index(name) for name in required_columns}
    except ValueError as exc:
        raise RuntimeError(
            "The Universe worksheet is missing one or more required shop-detail columns."
        ) from exc

    partner_locations: dict[
        str, dict[str, dict[str, dict[str, dict[str, str]]]]
    ] = {}
    for row in values[1:]:
        def cell(name: str) -> str:
            index = column[name]
            return row[index].strip() if len(row) > index else ""

        code = cell("distributor_code")
        locality = cell("locality_name")
        sub_locality = cell("sub_locality_name")
        store_code = cell("store_code")
        store_name = cell("store_name")
        if code and locality and sub_locality and store_name:
            selection_id = f"{store_code}::{store_name}"
            shops = (
                partner_locations.setdefault(code, {})
                .setdefault(locality, {})
                .setdefault(sub_locality, {})
            )
            shops[selection_id] = {
                "selection_id": selection_id,
                "store_code": store_code,
                "store_name": store_name,
                "channel_classification": cell("channel_classification"),
                "owner_name": cell("owner_name"),
                "owner_contact": cell("owner_contact"),
                "address": cell("address"),
                "latitude": cell("latitude"),
                "longitude": cell("longitude"),
            }

    res = {
        code: {
            locality: {
                sub_locality: sorted(
                    shops.values(), key=lambda shop: shop["store_name"].casefold()
                )
                for sub_locality, shops in sorted(
                    sub_locations.items(), key=lambda item: item[0].casefold()
                )
            }
            for locality, sub_locations in sorted(
                locations.items(), key=lambda item: item[0].casefold()
            )
        }
        for code, locations in sorted(
            partner_locations.items(),
            key=lambda item: PARTNER_NAME_BY_CODE.get(item[0], item[0]),
        )
    }
    _UNIVERSE_CACHE = (now_ts, res)
    return res


def _last_recorded_visit(
    partner_name: str,
    area: str,
    sub_area: str,
    shop_name: str,
    store_code: str,
) -> str | None:
    """Return the latest saved market-visit date for the selected shop."""
    worksheet = _worksheet(_credentials())
    records = worksheet.get_all_records()

    def normalized(value: Any) -> str:
        return " ".join(str(value or "").split()).casefold()

    expected = tuple(map(normalized, (partner_name, area, sub_area, shop_name)))
    timestamps: list[datetime] = []
    for record in records:
        saved_store_code = normalized(record.get("Store Code"))
        if normalized(store_code) and saved_store_code:
            if saved_store_code != normalized(store_code):
                continue
        else:
            actual = tuple(
                normalized(record.get(column))
                for column in ("Partner Name", "Area", "Sub Area", "Shop Name")
            )
            if actual != expected:
                continue

        submitted_at = str(record.get("Submitted At", "")).strip()
        timestamp_candidates = (submitted_at[:19], submitted_at[:10])
        for timestamp_value, date_format in zip(
            timestamp_candidates,
            ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"),
        ):
            try:
                timestamps.append(datetime.strptime(timestamp_value, date_format))
                break
            except ValueError:
                continue

    if not timestamps:
        return None
    return max(timestamps).strftime("%d %b %Y")


def _user_submissions(username: str) -> list[dict[str, Any]]:
    """Return submissions owned by the authenticated username, newest first."""
    records = _worksheet(_credentials()).get_all_records()
    normalized_username = username.strip().casefold()
    owned_records = [
        record
        for record in records
        if str(record.get("Username", "")).strip().casefold()
        == normalized_username
    ]
    owned_records.sort(
        key=lambda record: str(record.get("Submitted At", "")), reverse=True
    )
    return [
        {
            "_Submission ID": record.get("Submission ID", ""),
            "Submitted At": record.get("Submitted At", ""),
            "Partner": record.get("Partner Name", ""),
            "Store Code": record.get("Store Code", ""),
            "Shop Name": record.get("Shop Name", ""),
            "Area": record.get("Area", ""),
            "Sub Area": record.get("Sub Area", ""),
            "Booker Name": record.get("Booker Name", ""),
            "Monthly Sales": record.get("Shop Avg Monthly Sales", ""),
            "Photo": "Available" if record.get("Shop Picture + Selfie") else "",
            "Payment Gateways": record.get("Payment Gateways Available", ""),
            "QR Payment": record.get("QR Code Payment Available", ""),
            "QR Monthly Turnover": record.get("QR Monthly Turnover", ""),
            "User Latitude": record.get("User Latitude", ""),
            "User Longitude": record.get("User Longitude", ""),
            "Location Accuracy (m)": record.get("Location Accuracy (m)", ""),
            "Remarks": record.get("Remarks", ""),
        }
        for record in owned_records
    ]


def _save_photo(uploaded_file_name: str, file_bytes: bytes, mime_type: str, submission_id: str, credentials: Credentials) -> str:
    extension = Path(uploaded_file_name).suffix.lower() or ".jpg"
    filename = f"{submission_id}{extension}"
    try:
        img = Image.open(io.BytesIO(file_bytes))
        buf = io.BytesIO()
        img_format = "PNG" if extension == ".png" else "JPEG"
        img.save(buf, format=img_format, quality=90)
        content = buf.getvalue()
    except Exception:
        content = file_bytes

    apps_script_url = _secret("apps_script_upload_url", "")
    apps_script_token = _secret("apps_script_upload_token", "")
    drive_folder_id = _secret("drive_folder_id", "")

    if apps_script_url:
        if not apps_script_token:
            raise RuntimeError(
                "apps_script_upload_token is missing from secrets."
            )
        try:
            response = requests.post(
                apps_script_url,
                json={
                    "token": apps_script_token,
                    "filename": filename,
                    "mimeType": mime_type or "image/jpeg",
                    "data": base64.b64encode(content).decode("ascii"),
                },
                timeout=60,
            )
            response.raise_for_status()
            result = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise RuntimeError(
                "The Google Apps Script photo uploader could not be reached. "
                "Confirm the deployment URL and that access is set to Anyone."
            ) from exc
        if not result.get("ok"):
            raise RuntimeError(
                f"Google Apps Script rejected the photo: {result.get('error', 'Unknown error')}"
            )
        return str(result["url"])

    if drive_folder_id:
        drive = build("drive", "v3", credentials=credentials, cache_discovery=False)
        media = MediaIoBaseUpload(
            io.BytesIO(content),
            mimetype=mime_type or "image/jpeg",
            resumable=False,
        )
        try:
            created = (
                drive.files()
                .create(
                    body={"name": filename, "parents": [drive_folder_id]},
                    media_body=media,
                    fields="id,webViewLink",
                    supportsAllDrives=True,
                )
                .execute()
            )
        except HttpError as exc:
            error_text = exc.content.decode("utf-8", errors="ignore")
            if exc.resp.status == 404:
                service_email = credentials.service_account_email
                raise RuntimeError(
                    "The configured Google Drive folder was not found or is not "
                    f"shared with {service_email}."
                ) from exc
            if exc.resp.status == 403 and (
                "storageQuotaExceeded" in error_text
                or "storage quota" in error_text.lower()
            ):
                raise RuntimeError(
                    "This service account has no Google Drive storage quota."
                ) from exc
            if exc.resp.status == 403:
                raise RuntimeError(
                    f"Google Drive denied the photo upload. Response: {error_text}"
                ) from exc
            raise
        return created.get(
            "webViewLink", f"https://drive.google.com/file/d/{created['id']}/view"
        )

    UPLOAD_DIR.mkdir(exist_ok=True)
    path = UPLOAD_DIR / filename
    path.write_bytes(content)
    return str(path.resolve())


def _submission_id(shop_name: str, submitted_at: datetime) -> str:
    raw = f"{shop_name}|{submitted_at.isoformat()}".encode("utf-8")
    return f"MV-{submitted_at:%Y%m%d}-{hashlib.sha1(raw).hexdigest()[:8].upper()}"


# Initialize Flask App
app = Flask(__name__)
app.secret_key = _secret("flask_secret_key", os.urandom(24))


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "_authenticated_user" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)

    return decorated_function


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "_authenticated_user" not in session:
            return redirect(url_for("login"))
        user = session.get("_authenticated_user", {})
        if user.get("role") != "admin":
            flash("Access denied: Administrator privileges required.")
            return redirect(url_for("index"))
        return f(*args, **kwargs)

    return decorated_function


@app.route("/users")
@admin_required
def manage_users():
    user = session.get("_authenticated_user", {})
    all_users = _get_all_users()
    users_list = []
    for uname, udata in sorted(all_users.items(), key=lambda x: x[0].casefold()):
        codes = udata.get("partner_codes", udata.get("partner_code", []))
        if isinstance(codes, str):
            codes = [codes] if codes else []
        users_list.append({
            "username": uname,
            "display_name": udata.get("display_name", uname),
            "role": udata.get("role", "partner"),
            "partner_codes": codes,
        })
    return render_template(
        "users.html",
        user=user,
        users_list=users_list,
        PARTNER_NAME_BY_CODE=PARTNER_NAME_BY_CODE,
    )


@app.route("/api/users/add", methods=["POST"])
@admin_required
def api_add_user():
    data = request.get_json(force=True) or {}
    username = str(data.get("username", "")).strip()
    display_name = str(data.get("display_name", "")).strip() or username
    password = str(data.get("password", "")).strip()
    role = str(data.get("role", "partner")).strip().lower()
    partner_codes = data.get("partner_codes", [])

    if not username:
        return jsonify({"ok": False, "error": "Username is required."}), 400
    if not password:
        return jsonify({"ok": False, "error": "Password is required."}), 400
    if role not in {"admin", "partner"}:
        return jsonify({"ok": False, "error": "Role must be 'admin' or 'partner'."}), 400
    if role == "partner" and not partner_codes:
        return jsonify({"ok": False, "error": "Select at least one partner code for partner users."}), 400

    all_users = _get_all_users()
    if any(u.casefold() == username.casefold() for u in all_users):
        return jsonify({"ok": False, "error": f"Username '{username}' already exists."}), 400

    password_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
    all_users[username] = {
        "display_name": display_name,
        "password_hash": password_hash,
        "role": role,
        "partner_codes": partner_codes if role == "partner" else [],
    }
    _save_all_users(all_users)

    return jsonify({"ok": True, "message": f"User '{username}' created successfully."})


@app.route("/api/users/update", methods=["POST"])
@admin_required
def api_update_user():
    data = request.get_json(force=True) or {}
    username = str(data.get("username", "")).strip()
    display_name = str(data.get("display_name", "")).strip() or username
    password = str(data.get("password", "")).strip()
    role = str(data.get("role", "partner")).strip().lower()
    partner_codes = data.get("partner_codes", [])

    if not username:
        return jsonify({"ok": False, "error": "Username is required."}), 400
    if role not in {"admin", "partner"}:
        return jsonify({"ok": False, "error": "Role must be 'admin' or 'partner'."}), 400
    if role == "partner" and not partner_codes:
        return jsonify({"ok": False, "error": "Select at least one partner code for partner users."}), 400

    all_users = _get_all_users()
    target_key = next((u for u in all_users if u.casefold() == username.casefold()), None)
    if not target_key:
        return jsonify({"ok": False, "error": f"User '{username}' not found."}), 404

    user_entry = all_users[target_key]
    user_entry["display_name"] = display_name
    user_entry["role"] = role
    user_entry["partner_codes"] = partner_codes if role == "partner" else []

    if password:
        user_entry["password_hash"] = hashlib.sha256(password.encode("utf-8")).hexdigest()

    _save_all_users(all_users)

    return jsonify({"ok": True, "message": f"User '{username}' updated successfully."})


@app.route("/api/users/delete", methods=["POST"])
@admin_required
def api_delete_user():
    data = request.get_json(force=True) or {}
    username = str(data.get("username", "")).strip()
    current_user = session.get("_authenticated_user", {}).get("username", "")

    if not username:
        return jsonify({"ok": False, "error": "Username is required."}), 400
    if username.casefold() == current_user.casefold():
        return jsonify({"ok": False, "error": "You cannot delete your own logged-in admin account."}), 400

    all_users = _get_all_users()
    target_key = next((u for u in all_users if u.casefold() == username.casefold()), None)
    if not target_key:
        return jsonify({"ok": False, "error": f"User '{username}' not found."}), 404

    del all_users[target_key]
    _save_all_users(all_users)

    return jsonify({"ok": True, "message": f"User '{username}' deleted successfully."})


def _preload_universe_cache():
    try:
        _universe_partner_locations()
    except Exception:
        pass


@app.route("/login", methods=["GET", "POST"])
def login():
    if "_authenticated_user" in session:
        return redirect(url_for("index"))

    error = None
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        user = _authenticate(username, password)
        if user:
            session["_authenticated_user"] = user
            threading.Thread(target=_preload_universe_cache, daemon=True).start()
            return redirect(url_for("index"))
        else:
            error = "Invalid username or password."

    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    user = session.get("_authenticated_user", {})
    return render_template(
        "index.html",
        user=user,
        PARTNER_NAME_BY_CODE=PARTNER_NAME_BY_CODE,
        COMPETITOR_BRANDS_BY_PARTNER=COMPETITOR_BRANDS_BY_PARTNER,
        TOP_BRANDS_BY_PARTNER=TOP_BRANDS_BY_PARTNER,
    )


@app.route("/api/universe")
@login_required
def api_universe():
    user = session.get("_authenticated_user", {})
    try:
        partner_locations = _universe_partner_locations()
    except Exception as exc:
        err_msg = str(exc)
        if "oauth2.googleapis.com" in err_msg or "getaddrinfo failed" in err_msg:
            err_msg = "Network Error: Unable to connect to Google APIs. Please check your internet connection."
        return jsonify({"error": err_msg}), 500

    if user.get("role") == "partner":
        assigned_partner_codes = user.get("partner_codes", [])
        partner_locations = {
            code: partner_locations[code]
            for code in assigned_partner_codes
            if code in partner_locations
        }

    return jsonify(partner_locations)


@app.route("/api/last-visit")
@login_required
def api_last_visit():
    partner_name = request.args.get("partner_name", "")
    area = request.args.get("area", "")
    sub_area = request.args.get("sub_area", "")
    shop_name = request.args.get("shop_name", "")
    store_code = request.args.get("store_code", "")
    try:
        last_visit = _last_recorded_visit(
            partner_name, area, sub_area, shop_name, store_code
        )
        return jsonify({"last_visit_date": last_visit})
    except Exception as exc:
        return jsonify({"last_visit_date": None, "error": str(exc)})


@app.route("/api/submissions")
@login_required
def api_submissions():
    user = session.get("_authenticated_user", {})
    try:
        submissions = _user_submissions(user["username"])
        return jsonify({"submissions": submissions})
    except Exception as exc:
        err_msg = str(exc)
        if "oauth2.googleapis.com" in err_msg or "getaddrinfo failed" in err_msg:
            err_msg = "Network Error: Unable to connect to Google APIs. Please check your internet connection."
        return jsonify({"error": err_msg}), 500


@app.route("/api/submit", methods=["POST"])
@login_required
def api_submit():
    user = session.get("_authenticated_user", {})
    partner_code = request.form.get("partner_code", "").strip()
    partner_name = PARTNER_NAME_BY_CODE.get(partner_code, partner_code)
    area = request.form.get("area", "").strip()
    sub_area = request.form.get("sub_area", "").strip()
    booker_name = request.form.get("booker_name", "").strip()
    shop_id = request.form.get("shop_id", "").strip()

    monthly_sales_str = request.form.get("monthly_sales", "0")
    try:
        monthly_sales = float(monthly_sales_str)
    except ValueError:
        monthly_sales = 0

    visited_before = request.form.get("visited_before") == "on"
    last_visit_str = request.form.get("last_visit", "").strip()
    last_visit = None
    if visited_before and last_visit_str:
        try:
            last_visit = date.fromisoformat(last_visit_str)
        except ValueError:
            pass

    competitor_brands = request.form.getlist("competitor_brands")
    competitor_other = request.form.get("competitor_other", "").strip()

    top_brands = request.form.getlist("top_brands")

    payment_gateways = request.form.getlist("payment_gateways")
    other_payment_gateway = request.form.get("other_payment_gateway", "").strip()

    qr_payment_available = request.form.get("qr_payment_available", "No").strip()
    qr_turnover_str = request.form.get("qr_monthly_turnover", "0")
    try:
        qr_monthly_turnover = float(qr_turnover_str)
    except ValueError:
        qr_monthly_turnover = 0

    remarks = request.form.get("remarks", "").strip()
    user_latitude = request.form.get("user_latitude", "").strip()
    user_longitude = request.form.get("user_longitude", "").strip()
    location_accuracy = request.form.get("location_accuracy", "").strip()

    # Extract shop name and store code from shop_id (format: store_code::store_name)
    store_code = ""
    shop_name = shop_id
    if "::" in shop_id:
        parts = shop_id.split("::", 1)
        store_code = parts[0]
        shop_name = parts[1]

    # Validation
    errors: list[str] = []
    if not partner_name:
        errors.append("Partner Name is required.")
    if not area:
        errors.append("Area is required.")
    if not sub_area:
        errors.append("Sub Area is required.")
    if not booker_name:
        errors.append("Booker Name is required.")
    if not shop_name:
        errors.append("Shop Name is required.")
    if monthly_sales <= 0:
        errors.append("Shop Avg Monthly Sales must be greater than zero.")
    if not top_brands:
        errors.append("Select at least one top brand, or TBA.")
    if "Other" in competitor_brands and not competitor_other:
        errors.append("Enter the other competitor brand name.")
    if not payment_gateways:
        errors.append("Select at least one available payment gateway.")
    if "Other" in payment_gateways and not other_payment_gateway:
        errors.append("Enter the other payment gateway name.")
    if qr_monthly_turnover < 0:
        errors.append("QR Monthly Turnover must be non-negative.")

    photo_file = request.files.get("photo")
    if not photo_file or not photo_file.filename:
        errors.append("Shop Picture + Selfie is required.")

    if errors:
        return jsonify({"ok": False, "error": " ".join(errors)}), 400

    now = datetime.now(TIMEZONE)
    submission_id = _submission_id(shop_name, now)

    competitors = [brand for brand in competitor_brands if brand != "Other"]
    if competitor_other:
        competitors.append(competitor_other)

    saved_payment_gateways = [g for g in payment_gateways if g != "Other"]
    if other_payment_gateway:
        saved_payment_gateways.append(other_payment_gateway)

    try:
        credentials = _credentials()
        file_bytes = photo_file.read()
        photo_location = _save_photo(
            photo_file.filename, file_bytes, photo_file.mimetype, submission_id, credentials
        )

        row = [
            submission_id,
            now.strftime("%Y-%m-%d %H:%M:%S %Z"),
            partner_name,
            shop_name,
            photo_location,
            area,
            sub_area,
            booker_name,
            int(monthly_sales),
            last_visit.isoformat() if last_visit else "Never / Unknown",
            ", ".join(competitors) if competitors else "None observed",
            ", ".join(top_brands),
            remarks,
            store_code,
            user["username"],
            ", ".join(saved_payment_gateways),
            qr_payment_available,
            int(qr_monthly_turnover),
            user_latitude,
            user_longitude,
            location_accuracy,
        ]

        _worksheet(credentials).append_row(row, value_input_option="USER_ENTERED")

        return jsonify(
            {
                "ok": True,
                "message": f"Market visit saved successfully. Reference: {submission_id}",
                "submission_id": submission_id,
            }
        )
    except Exception as exc:
        err_msg = str(exc)
        if "oauth2.googleapis.com" in err_msg or "getaddrinfo failed" in err_msg:
            err_msg = "Network Error: Unable to connect to Google APIs. Please check your internet connection."
        return jsonify({"ok": False, "error": err_msg}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
