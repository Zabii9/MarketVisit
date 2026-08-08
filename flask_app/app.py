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
from urllib.parse import quote_plus, unquote
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
    send_from_directory,
    send_file,
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
    "D70002202": ["Olper's Milk", "Tarang", "TBA","Flavoured Milk","Tarka","Dairy Omung","ProCal","Powder Milk", "Others"],
    "D70002246": ["Olper's Milk", "Tarang", "TBA","Flavoured Milk","Tarka","Dairy Omung","ProCal","Powder Milk", "Others"],
    "Tapal": ["Tezdum", "Tapal","Danedar","Green Tea","Family Mixture","Mezban","Chenak", "Other"],
    "D0573": ["Prince", "Tuc", "Zeera Plus","Candi","Candi","Oreo","Tiger","Bakeri","Cadbury","Wheatable","Milco Lu","Plus","Belvita", "Other"],
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


_USERS_CACHE: tuple[float, dict[str, dict[str, Any]]] = (0.0, {})


def _users_worksheet(credentials: Credentials) -> gspread.Worksheet:
    client = gspread.authorize(credentials)
    sh = client.open_by_key(SHEET_ID)
    try:
        ws = sh.worksheet("Users")
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title="Users", rows=100, cols=10)
        ws.append_row(
            ["Username", "Display Name", "Password Hash", "Role", "Partner Codes"],
            value_input_option="RAW",
        )
        ws.freeze(rows=1)
    return ws


def _get_all_users(force_refresh: bool = False) -> dict[str, dict[str, Any]]:
    global _USERS_CACHE
    now_ts = time.time()
    if not force_refresh and _USERS_CACHE[1] and (now_ts - _USERS_CACHE[0] < 120):
        return _USERS_CACHE[1]

    users_dict: dict[str, dict[str, Any]] = {}
    try:
        ws = _users_worksheet(_credentials())
        records = ws.get_all_records()
        for record in records:
            uname = str(record.get("Username", "")).strip()
            if not uname:
                continue
            display_name = str(record.get("Display Name", uname)).strip() or uname
            password_hash = str(record.get("Password Hash", "")).strip().lower()
            role = str(record.get("Role", "partner")).strip().lower()
            raw_codes = str(record.get("Partner Codes", "")).strip()
            if raw_codes.startswith("[") and raw_codes.endswith("]"):
                try:
                    import json
                    partner_codes = json.loads(raw_codes)
                except Exception:
                    partner_codes = [c.strip() for c in raw_codes.strip("[]'\"").split(",") if c.strip()]
            else:
                partner_codes = [c.strip() for c in raw_codes.split(",") if c.strip()]

            users_dict[uname] = {
                "display_name": display_name,
                "password_hash": password_hash,
                "role": role,
                "partner_codes": partner_codes,
            }
    except Exception as exc:
        print("Warning: Could not read Users from Google Sheet:", exc)

    # Load local fallback users_data.json if present
    local_json = Path("users_data.json")
    if local_json.is_file():
        try:
            import json
            with open(local_json, "r", encoding="utf-8") as f:
                file_users = json.load(f)
                for u_k, u_v in file_users.items():
                    if u_k not in users_dict:
                        users_dict[u_k] = u_v
        except Exception as e:
            print("Warning: Could not read local users_data.json:", e)

    # Initial seed from SECRETS if worksheet was empty
    if not users_dict:
        secrets_users = _secret("users", {})
        initial_users = {str(username): dict(settings) for username, settings in secrets_users.items()}
        if initial_users:
            users_dict = initial_users
            _save_all_users(initial_users)

    # Always ensure 'test' viewer user exists and has viewer role
    test_key = next((k for k in users_dict if k.casefold() == "test"), None)
    if test_key:
        users_dict[test_key]["role"] = "viewer"
    else:
        users_dict["test"] = {
            "display_name": "Test User (Viewer)",
            "password_hash": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
            "role": "viewer",
            "partner_codes": ["D0573", "D70002202", "D70002246", "Tapal"],
        }

    _USERS_CACHE = (now_ts, users_dict)
    return users_dict


def _save_all_users(users_dict: dict[str, dict[str, Any]]) -> None:
    global _USERS_CACHE
    try:
        ws = _users_worksheet(_credentials())
        rows = [["Username", "Display Name", "Password Hash", "Role", "Partner Codes"]]
        for uname, udata in sorted(users_dict.items(), key=lambda x: x[0].casefold()):
            display_name = udata.get("display_name", uname)
            password_hash = udata.get("password_hash", "")
            role = udata.get("role", "partner")
            codes = udata.get("partner_codes", udata.get("partner_code", []))
            if isinstance(codes, list):
                codes_str = ", ".join(codes)
            else:
                codes_str = str(codes)
            rows.append([uname, display_name, password_hash, role, codes_str])

        ws.clear()
        ws.update(range_name=f"A1:E{len(rows)}", values=rows)
    except Exception as exc:
        print("Error saving Users to Google Sheet:", exc)

    _USERS_CACHE = (time.time(), users_dict)


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
    if role not in {"admin", "partner", "viewer"}:
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
    if role == "viewer" and not partner_codes:
        partner_codes = list(PARTNER_NAME_BY_CODE.keys())
    elif role == "partner" and not partner_codes:
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
            "Photo URL": record.get("Shop Picture + Selfie", ""),
            "Photo": "Available" if record.get("Shop Picture + Selfie") else "None",
            "Competitor Brands": record.get("Competitor Brands Available", ""),
            "Top Brands": record.get("Top Brands Available", ""),
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

    upload_dir = Path("/tmp/uploads") if (os.getenv("VERCEL") or os.getenv("AWS_LAMBDA_FUNCTION_NAME")) else UPLOAD_DIR
    try:
        upload_dir.mkdir(parents=True, exist_ok=True)
        path = upload_dir / filename
        path.write_bytes(content)
        return f"/uploads/{filename}"
    except Exception:
        return f"/uploads/{filename}"


def _submission_id(shop_name: str, submitted_at: datetime) -> str:
    raw = f"{shop_name}|{submitted_at.isoformat()}".encode("utf-8")
    return f"MV-{submitted_at:%Y%m%d}-{hashlib.sha1(raw).hexdigest()[:8].upper()}"


# Initialize Flask App
app = Flask(__name__, static_folder="static", static_url_path="/static")
secret_key_val = _secret("flask_secret_key") or os.getenv("FLASK_SECRET_KEY") or os.getenv("SECRET_KEY") or "market-visit-secure-key-2026-cbl-tapal-olpers"
app.secret_key = secret_key_val
app.config["SESSION_PERMANENT"] = True
app.config["PERMANENT_SESSION_LIFETIME"] = 86400 * 30



@app.route("/favicon.ico")
def favicon():
    return send_from_directory(app.static_folder, "favicon.ico", mimetype="image/x-icon")


@app.route("/uploads/<path:filename>")
def serve_upload(filename: str):
    upload_dir = Path("/tmp/uploads") if (os.getenv("VERCEL") or os.getenv("AWS_LAMBDA_FUNCTION_NAME")) else UPLOAD_DIR
    clean_name = Path(filename).name
    if (upload_dir / clean_name).exists():
        return send_from_directory(upload_dir, clean_name)
    return redirect(url_for("photo_proxy", file_ref=clean_name))


@app.route("/api/photo_proxy/<path:file_ref>")
def photo_proxy(file_ref: str):
    file_ref = unquote(file_ref).strip()
    upload_dir = Path("/tmp/uploads") if (os.getenv("VERCEL") or os.getenv("AWS_LAMBDA_FUNCTION_NAME")) else UPLOAD_DIR
    clean_name = Path(file_ref).name

    # 1. Check local upload directory first
    local_path = upload_dir / clean_name
    if local_path.exists():
        return send_from_directory(upload_dir, clean_name)

    # 2. Query Google Drive API via service account (by ID or by filename)
    try:
        credentials = _credentials()
        drive_service = build("drive", "v3", credentials=credentials)
        target_id = None

        if "." not in file_ref and len(file_ref) >= 20:
            target_id = file_ref
        else:
            # Search Google Drive by filename
            query = f"name = '{clean_name}' and trashed = false"
            res = drive_service.files().list(
                q=query,
                fields="files(id, name)",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ).execute()
            files = res.get("files", [])
            if files:
                target_id = files[0]["id"]

        if target_id:
            request_media = drive_service.files().get_media(fileId=target_id)
            buffer = io.BytesIO()
            downloader = MediaIoBaseDownload(buffer, request_media)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            buffer.seek(0)
            return send_file(buffer, mimetype="image/jpeg")
    except Exception as exc:
        print("Photo proxy error:", exc)

    # 3. Fallback redirect if valid drive ID
    if "." not in file_ref and len(file_ref) >= 20:
        return redirect(f"https://drive.google.com/thumbnail?id={file_ref}&sz=w1000")

    # 4. Fallback 404 response
    return jsonify({"error": "Photo not found"}), 404


def _get_dummy_analytics() -> dict[str, Any]:
    """Generate realistic dummy data for viewer/test user demo mode."""
    dummy_records = [
        {
            "partner": "CBL",
            "shop": "Al-Fateh Supermarket",
            "code": "D0573-101",
            "area": "Gulberg",
            "sub_area": "Main Market",
            "sales": 150000.0,
            "top_brands": "Prince, Tuc, Zeera Plus",
            "competitors": "LU, Bisconi",
            "payment_gateways": "Cash, Easypaisa, JazzCash",
            "qr_payment": "Yes",
            "qr_turnover": 45000.0,
            "booker": "Muhammad Ali",
            "username": "test",
            "submitted_at": "2026-02-08 11:30:00 PKT",
            "date": "2026-02-08",
            "lat": 31.5204,
            "lng": 74.3587,
        },
        {
            "partner": "Olpers KHI",
            "shop": "Agha's Supermarket",
            "code": "D70002202-005",
            "area": "Clifton",
            "sub_area": "Block 5",
            "sales": 220000.0,
            "top_brands": "Olper's Milk, Tarang, ProCal",
            "competitors": "Milkpak, Dairy Omung",
            "payment_gateways": "Cash, Bank Transfer, QR",
            "qr_payment": "Yes",
            "qr_turnover": 75000.0,
            "booker": "Usman Tariq",
            "username": "test",
            "submitted_at": "2026-02-08 14:15:00 PKT",
            "date": "2026-02-08",
            "lat": 24.8138,
            "lng": 67.0302,
        },
        {
            "partner": "Tapal",
            "shop": "Imtiaz Super Market",
            "code": "Tapal-202",
            "area": "DHA",
            "sub_area": "Phase 5",
            "sales": 180000.0,
            "top_brands": "Danedar, Tezdum, Green Tea",
            "competitors": "Lipton, Vital",
            "payment_gateways": "Cash, Cards, QR",
            "qr_payment": "Yes",
            "qr_turnover": 60000.0,
            "booker": "Hamza Khan",
            "username": "test",
            "submitted_at": "2026-02-07 10:45:00 PKT",
            "date": "2026-02-07",
            "lat": 31.4704,
            "lng": 74.3787,
        },
        {
            "partner": "Olpers LHR",
            "shop": "Rahim Store",
            "code": "D70002246-044",
            "area": "Model Town",
            "sub_area": "Block C",
            "sales": 95000.0,
            "top_brands": "Olper's Milk, TBA, Tarka",
            "competitors": "Milkpak, Haleeb",
            "payment_gateways": "Cash, Easypaisa",
            "qr_payment": "No",
            "qr_turnover": 0.0,
            "booker": "Zubair Ahmed",
            "username": "test",
            "submitted_at": "2026-02-07 16:20:00 PKT",
            "date": "2026-02-07",
            "lat": 31.4854,
            "lng": 74.3214,
        },
        {
            "partner": "CBL",
            "shop": "Naheed Superstore",
            "code": "D0573-088",
            "area": "Bahadurabad",
            "sub_area": "Main Commercial",
            "sales": 260000.0,
            "top_brands": "Candi, Oreo, Tiger, Bakeri",
            "competitors": "Bisconi, Cookinia",
            "payment_gateways": "Cash, Card, QR",
            "qr_payment": "Yes",
            "qr_turnover": 90000.0,
            "booker": "Muhammad Ali",
            "username": "test",
            "submitted_at": "2026-02-06 12:10:00 PKT",
            "date": "2026-02-06",
            "lat": 24.8785,
            "lng": 67.0694,
        },
        {
            "partner": "Olpers KHI",
            "shop": "Bismillah General Store",
            "code": "D70002202-019",
            "area": "FB Area",
            "sub_area": "Block 14",
            "sales": 65000.0,
            "top_brands": "Flavoured Milk, Powder Milk",
            "competitors": "Good Milk, Dostea",
            "payment_gateways": "Cash, JazzCash",
            "qr_payment": "Yes",
            "qr_turnover": 25000.0,
            "booker": "Usman Tariq",
            "username": "test",
            "submitted_at": "2026-02-06 15:40:00 PKT",
            "date": "2026-02-06",
            "lat": 24.9285,
            "lng": 67.0794,
        },
        {
            "partner": "Tapal",
            "shop": "Kashif Mart",
            "code": "Tapal-109",
            "area": "Johar Town",
            "sub_area": "G3 Block",
            "sales": 110000.0,
            "top_brands": "Family Mixture, Mezban",
            "competitors": "Meezan, Vital",
            "payment_gateways": "Cash, Easypaisa",
            "qr_payment": "No",
            "qr_turnover": 0.0,
            "booker": "Hamza Khan",
            "username": "test",
            "submitted_at": "2026-02-05 09:30:00 PKT",
            "date": "2026-02-05",
            "lat": 31.4685,
            "lng": 74.2794,
        },
        {
            "partner": "Olpers LHR",
            "shop": "Metro Cash & Carry",
            "code": "D70002246-001",
            "area": "Thokar Niaz Baig",
            "sub_area": "Raiwind Road",
            "sales": 320000.0,
            "top_brands": "Olper's Milk, Tarang, ProCal, TBA",
            "competitors": "Milkpak, Dairy Omung, Good Milk",
            "payment_gateways": "Cash, Bank Transfer, QR",
            "qr_payment": "Yes",
            "qr_turnover": 120000.0,
            "booker": "Zubair Ahmed",
            "username": "test",
            "submitted_at": "2026-02-05 13:50:00 PKT",
            "date": "2026-02-05",
            "lat": 31.4725,
            "lng": 74.2414,
        }
    ]

    total_visits = len(dummy_records)
    total_sales = sum(r["sales"] for r in dummy_records)
    qr_count = sum(1 for r in dummy_records if r["qr_payment"] == "Yes")
    qr_turnover_sum = sum(r["qr_turnover"] for r in dummy_records)

    return {
        "total_visits": total_visits,
        "total_sales": total_sales,
        "avg_sales": total_sales / total_visits if total_visits > 0 else 0,
        "unique_shops_count": 8,
        "unique_bookers_count": 4,
        "qr_count": qr_count,
        "qr_adoption_rate": round((qr_count / total_visits * 100), 1) if total_visits > 0 else 0,
        "qr_turnover_sum": qr_turnover_sum,
        "total_universe_shops": 12,
        "universe_shops_by_partner": {
            "CBL": 3,
            "Olpers KHI": 3,
            "Olpers LHR": 3,
            "Tapal": 3,
        },
        "partner_counts": {
            "CBL": 2,
            "Olpers KHI": 2,
            "Olpers LHR": 2,
            "Tapal": 2,
        },
        "top_brands_counts": {
            "Olper's Milk": 4,
            "Tarang": 3,
            "Prince": 2,
            "Tuc": 2,
            "Candi": 2,
            "Tezdum": 2,
            "Danedar": 2,
            "ProCal": 2,
        },
        "competitor_counts": {
            "Milkpak": 4,
            "LU": 2,
            "Dairy Omung": 2,
            "Bisconi": 2,
            "Lipton": 2,
            "Vital": 2,
        },
        "payment_counts": {
            "Cash": 8,
            "Easypaisa": 4,
            "JazzCash": 3,
            "QR": 6,
            "Bank Transfer": 2,
        },
        "area_summary": [
            {"area": "Gulberg", "sub_area": "Main Market", "visits": 1, "sales": 150000.0},
            {"area": "Clifton", "sub_area": "Block 5", "visits": 1, "sales": 220000.0},
            {"area": "DHA", "sub_area": "Phase 5", "visits": 1, "sales": 180000.0},
            {"area": "Model Town", "sub_area": "Block C", "visits": 1, "sales": 95000.0},
            {"area": "Bahadurabad", "sub_area": "Main Commercial", "visits": 1, "sales": 260000.0},
            {"area": "FB Area", "sub_area": "Block 14", "visits": 1, "sales": 65000.0},
            {"area": "Johar Town", "sub_area": "G3 Block", "visits": 1, "sales": 110000.0},
            {"area": "Thokar Niaz Baig", "sub_area": "Raiwind Road", "visits": 1, "sales": 320000.0},
        ],
        "raw_scoped_records": dummy_records,
        "user_time_spending": [
            {
                "date": "2026-02-08",
                "month": "2026-02",
                "user": "Muhammad Ali",
                "username": "test",
                "booker": "Muhammad Ali",
                "partner": "CBL",
                "areas": ["Gulberg"],
                "first_visit_time": "11:30 AM",
                "last_visit_time": "02:15 PM",
                "span_minutes": 165,
                "span_hours": 2.75,
                "span_formatted": "2h 45m",
                "active_minutes": 75,
                "active_hours": 1.25,
                "active_formatted": "1h 15m",
                "visit_count": 2,
                "avg_interval_minutes": 82.5,
            },
            {
                "date": "2026-02-07",
                "month": "2026-02",
                "user": "Hamza Khan",
                "username": "test",
                "booker": "Hamza Khan",
                "partner": "Tapal",
                "areas": ["DHA"],
                "first_visit_time": "10:45 AM",
                "last_visit_time": "04:20 PM",
                "span_minutes": 335,
                "span_hours": 5.58,
                "span_formatted": "5h 35m",
                "active_minutes": 120,
                "active_hours": 2.0,
                "active_formatted": "2h 0m",
                "visit_count": 2,
                "avg_interval_minutes": 167.5,
            }
        ],
    }


def _get_dummy_submissions() -> list[dict[str, Any]]:
    """Return dummy market visit submissions for test/viewer user."""
    return [
        {
            "_Submission ID": "MV-20260208-TEST0001",
            "Submitted At": "2026-02-08 11:30:00 PKT",
            "Partner": "CBL",
            "Store Code": "D0573-101",
            "Shop Name": "Al-Fateh Supermarket",
            "Area": "Gulberg",
            "Sub Area": "Main Market",
            "Booker Name": "Muhammad Ali",
            "Monthly Sales": 150000,
            "Photo URL": "/static/favicon.ico",
            "Photo": "Available",
            "Competitor Brands": "LU, Bisconi",
            "Top Brands": "Prince, Tuc, Zeera Plus",
            "Payment Gateways": "Cash, Easypaisa, JazzCash",
            "QR Payment": "Yes",
            "QR Monthly Turnover": 45000,
            "User Latitude": "31.5204",
            "User Longitude": "74.3587",
            "Location Accuracy (m)": "12",
            "Remarks": "Demo submission for viewer mode. Excellent stock placement.",
        },
        {
            "_Submission ID": "MV-20260208-TEST0002",
            "Submitted At": "2026-02-08 14:15:00 PKT",
            "Partner": "Olpers KHI",
            "Store Code": "D70002202-005",
            "Shop Name": "Agha's Supermarket",
            "Area": "Clifton",
            "Sub Area": "Block 5",
            "Booker Name": "Usman Tariq",
            "Monthly Sales": 220000,
            "Photo URL": "/static/favicon.ico",
            "Photo": "Available",
            "Competitor Brands": "Milkpak, Dairy Omung",
            "Top Brands": "Olper's Milk, Tarang, ProCal",
            "Payment Gateways": "Cash, Bank Transfer",
            "QR Payment": "Yes",
            "QR Monthly Turnover": 75000,
            "User Latitude": "24.8138",
            "User Longitude": "67.0302",
            "Location Accuracy (m)": "8",
            "Remarks": "High daily turnover. All key SKUs displayed on front shelf.",
        },
    ]


def _get_trimmed_dummy_universe(partner_locations: dict[str, Any]) -> dict[str, Any]:
    """Trim universe for test/viewer user so each partner only shows 1 area with 2-3 shops."""
    default_dummy = {
        "D0573": {
            "Gulberg": {
                "Main Market": [
                    {
                        "selection_id": "D0573-101::Al-Fateh Supermarket",
                        "store_code": "D0573-101",
                        "store_name": "Al-Fateh Supermarket",
                        "channel_classification": "Supermarket",
                        "owner_name": "Kashif Ali",
                        "owner_contact": "03001234567",
                        "address": "Main Market, Gulberg, Lahore",
                        "latitude": "31.5204",
                        "longitude": "74.3587"
                    },
                    {
                        "selection_id": "D0573-102::Naheed Mart",
                        "store_code": "D0573-102",
                        "store_name": "Naheed Mart",
                        "channel_classification": "Departmental Store",
                        "owner_name": "Tariq Mahmood",
                        "owner_contact": "03219876543",
                        "address": "Block 2, Gulberg, Lahore",
                        "latitude": "31.5215",
                        "longitude": "74.3590"
                    },
                    {
                        "selection_id": "D0573-103::Crown Cash & Carry",
                        "store_code": "D0573-103",
                        "store_name": "Crown Cash & Carry",
                        "channel_classification": "Cash & Carry",
                        "owner_name": "Usman Malik",
                        "owner_contact": "03334567890",
                        "address": "Main Boulevard, Gulberg, Lahore",
                        "latitude": "31.5230",
                        "longitude": "74.3610"
                    }
                ]
            }
        },
        "D70002202": {
            "Clifton": {
                "Block 5": [
                    {
                        "selection_id": "D70002202-001::Agha's Supermarket",
                        "store_code": "D70002202-001",
                        "store_name": "Agha's Supermarket",
                        "channel_classification": "Supermarket",
                        "owner_name": "Agha Salman",
                        "owner_contact": "03012345678",
                        "address": "Block 5, Clifton, Karachi",
                        "latitude": "24.8138",
                        "longitude": "67.0302"
                    },
                    {
                        "selection_id": "D70002202-002::Bismillah General Store",
                        "store_code": "D70002202-002",
                        "store_name": "Bismillah General Store",
                        "channel_classification": "Retail Shop",
                        "owner_name": "Muhammad Bilal",
                        "owner_contact": "03123456789",
                        "address": "Boat Basin, Clifton, Karachi",
                        "latitude": "24.8190",
                        "longitude": "67.0280"
                    },
                    {
                        "selection_id": "D70002202-003::Ocean Mart",
                        "store_code": "D70002202-003",
                        "store_name": "Ocean Mart",
                        "channel_classification": "Mart",
                        "owner_name": "Hamza Raza",
                        "owner_contact": "03456789012",
                        "address": "Khayaban-e-Iqbal, Clifton, Karachi",
                        "latitude": "24.8150",
                        "longitude": "67.0320"
                    }
                ]
            }
        },
        "D70002246": {
            "Model Town": {
                "Block C": [
                    {
                        "selection_id": "D70002246-001::Rahim Store",
                        "store_code": "D70002246-001",
                        "store_name": "Rahim Store",
                        "channel_classification": "General Store",
                        "owner_name": "Abdul Rahim",
                        "owner_contact": "03023456789",
                        "address": "Block C, Model Town, Lahore",
                        "latitude": "31.4854",
                        "longitude": "74.3214"
                    },
                    {
                        "selection_id": "D70002246-002::Metro Cash & Carry",
                        "store_code": "D70002246-002",
                        "store_name": "Metro Cash & Carry",
                        "channel_classification": "Hypermarket",
                        "owner_name": "Imran Khan",
                        "owner_contact": "03224567890",
                        "address": "Model Town Link Road, Lahore",
                        "latitude": "31.4780",
                        "longitude": "74.3250"
                    },
                    {
                        "selection_id": "D70002246-003::Standard Retailers",
                        "store_code": "D70002246-003",
                        "store_name": "Standard Retailers",
                        "channel_classification": "Retail Shop",
                        "owner_name": "Zia Ur Rehman",
                        "owner_contact": "03345678901",
                        "address": "Central Commercial, Model Town, Lahore",
                        "latitude": "31.4830",
                        "longitude": "74.3200"
                    }
                ]
            }
        },
        "Tapal": {
            "DHA": {
                "Phase 5": [
                    {
                        "selection_id": "Tapal-001::Imtiaz Super Market",
                        "store_code": "Tapal-001",
                        "store_name": "Imtiaz Super Market",
                        "channel_classification": "Supermarket",
                        "owner_name": "Imtiaz Abbasi",
                        "owner_contact": "03034567890",
                        "address": "CCA Phase 5, DHA, Lahore",
                        "latitude": "31.4704",
                        "longitude": "74.3787"
                    },
                    {
                        "selection_id": "Tapal-002::Kashif Mart",
                        "store_code": "Tapal-002",
                        "store_name": "Kashif Mart",
                        "channel_classification": "Mart",
                        "owner_name": "Kashif Shah",
                        "owner_contact": "03135678901",
                        "address": "Main Commercial, DHA Phase 5, Lahore",
                        "latitude": "31.4720",
                        "longitude": "74.3800"
                    },
                    {
                        "selection_id": "Tapal-003::Green Tea House",
                        "store_code": "Tapal-003",
                        "store_name": "Green Tea House",
                        "channel_classification": "Specialty Shop",
                        "owner_name": "Zubair Ahmad",
                        "owner_contact": "03236789012",
                        "address": "Phase 5 Market, DHA, Lahore",
                        "latitude": "31.4750",
                        "longitude": "74.3820"
                    }
                ]
            }
        }
    }

    if not partner_locations:
        return default_dummy

    trimmed = {}
    for code, areas in partner_locations.items():
        if not areas:
            continue
        first_area_name = next(iter(areas))
        sub_areas = areas[first_area_name]
        if not sub_areas:
            continue
        first_sub_name = next(iter(sub_areas))
        shops = sub_areas[first_sub_name][:3]
        trimmed[code] = {
            first_area_name: {
                first_sub_name: shops
            }
        }

    return trimmed if trimmed else default_dummy


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
    if role not in {"admin", "partner", "viewer"}:
        return jsonify({"ok": False, "error": "Role must be 'admin', 'partner', or 'viewer'."}), 400
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
        "partner_codes": partner_codes if role in {"partner", "viewer"} else [],
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
    if role not in {"admin", "partner", "viewer"}:
        return jsonify({"ok": False, "error": "Role must be 'admin', 'partner', or 'viewer'."}), 400
    if role == "partner" and not partner_codes:
        return jsonify({"ok": False, "error": "Select at least one partner code for partner users."}), 400

    all_users = _get_all_users()
    target_key = next((u for u in all_users if u.casefold() == username.casefold()), None)
    if not target_key:
        return jsonify({"ok": False, "error": f"User '{username}' not found."}), 404

    user_entry = all_users[target_key]
    user_entry["display_name"] = display_name
    user_entry["role"] = role
    user_entry["partner_codes"] = partner_codes if role in {"partner", "viewer"} else []

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


@app.route("/login", methods=["GET", "POST"])
def login():
    if "_authenticated_user" in session:
        user = session.get("_authenticated_user", {})
        if user.get("role") == "admin":
            return redirect(url_for("dashboard"))
        return redirect(url_for("index"))

    error = None
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        user = _authenticate(username, password)
        if user:
            session["_authenticated_user"] = user
            if user.get("role") == "admin":
                return redirect(url_for("dashboard"))
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
    if user.get("role") == "admin" and not request.args.get("form"):
        return redirect(url_for("dashboard"))

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
    if user.get("role") == "viewer" or user.get("username") == "test":
        try:
            full_locations = _universe_partner_locations()
        except Exception:
            full_locations = {}
        return jsonify(_get_trimmed_dummy_universe(full_locations))

    try:
        partner_locations = _universe_partner_locations()
    except Exception as exc:
        err_msg = str(exc)
        if "oauth2.googleapis.com" in err_msg or "getaddrinfo failed" in err_msg:
            err_msg = "Network Error: Unable to connect to Google APIs. Please check your internet connection."
        return jsonify({"error": err_msg}), 500

    if user.get("role") == "partner":
        assigned_partner_codes = [str(c).strip().casefold() for c in user.get("partner_codes", [])]
        partner_locations = {
            code: data
            for code, data in partner_locations.items()
            if code.strip().casefold() in assigned_partner_codes
        }

    return jsonify(partner_locations)


@app.route("/api/last-visit")
@login_required
def api_last_visit():
    user = session.get("_authenticated_user", {})
    if user.get("role") == "viewer" or user.get("username") == "test":
        return jsonify({"last_visit_date": "08 Feb 2026"})
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


@app.route("/dashboard")
@login_required
def dashboard():
    user = session.get("_authenticated_user", {})
    return render_template(
        "dashboard.html",
        user=user,
        PARTNER_NAME_BY_CODE=PARTNER_NAME_BY_CODE,
    )


@app.route("/api/analytics")
@login_required
def api_analytics():
    user = session.get("_authenticated_user", {})
    if user.get("role") == "viewer" or user.get("username") == "test":
        return jsonify(_get_dummy_analytics())
    role = user.get("role", "partner")
    assigned_codes = [str(c).strip().casefold() for c in user.get("partner_codes", [])]
    assigned_names = [PARTNER_NAME_BY_CODE.get(code, code).casefold() for code in user.get("partner_codes", [])] + assigned_codes

    try:
        worksheet = _worksheet(_credentials())
        all_records = worksheet.get_all_records()
    except Exception as exc:
        err_msg = str(exc)
        if "oauth2.googleapis.com" in err_msg or "getaddrinfo failed" in err_msg:
            err_msg = "Network Error: Unable to connect to Google APIs. Please check your internet connection."
        return jsonify({"error": err_msg}), 500

    # Calculate Total Shops in Google Sheet Universe
    try:
        partner_locations = _universe_partner_locations()
        if role == "partner":
            partner_locations = {
                code: data
                for code, data in partner_locations.items()
                if code.strip().casefold() in assigned_codes
            }

        total_universe_shops = 0
        universe_shops_by_partner: dict[str, int] = {}
        for p_code, area_dict in partner_locations.items():
            p_name = PARTNER_NAME_BY_CODE.get(p_code, p_code)
            count = 0
            for sub_dict in area_dict.values():
                for shops_list in sub_dict.values():
                    count += len(shops_list)
            total_universe_shops += count
            universe_shops_by_partner[p_name] = universe_shops_by_partner.get(p_name, 0) + count
    except Exception:
        total_universe_shops = 0
        universe_shops_by_partner = {}

    # Scoped Data Visibility based on User Role and Assigned Partner Codes
    scoped_records = []
    for r in all_records:
        partner_name = str(r.get("Partner Name", "")).strip()
        record_user = str(r.get("Username", "")).strip().casefold()

        if role == "admin":
            scoped_records.append(r)
        else:
            if (
                partner_name.casefold() in assigned_names
                or record_user == str(user.get("username", "")).strip().casefold()
            ):
                scoped_records.append(r)

    total_visits = len(scoped_records)
    total_sales = 0.0
    unique_shops = set()
    unique_bookers = set()
    qr_count = 0
    qr_turnover_sum = 0.0

    partner_counts: dict[str, int] = {}
    top_brands_counts: dict[str, int] = {}
    competitor_counts: dict[str, int] = {}
    payment_counts: dict[str, int] = {}
    area_summary: dict[str, dict[str, Any]] = {}

    for r in scoped_records:
        try:
            sales = float(r.get("Shop Avg Monthly Sales", 0) or 0)
        except (ValueError, TypeError):
            sales = 0.0
        total_sales += sales

        shop = str(r.get("Shop Name", "")).strip()
        code = str(r.get("Store Code", "")).strip()
        if shop or code:
            unique_shops.add(f"{shop}|{code}")
        booker = str(r.get("Booker Name", "")).strip()
        if booker:
            unique_bookers.add(booker)

        qr_status = str(r.get("QR Code Payment Available", "")).strip()
        if qr_status.casefold() == "yes":
            qr_count += 1
            try:
                turnover = float(r.get("QR Monthly Turnover", 0) or 0)
            except (ValueError, TypeError):
                turnover = 0.0
            qr_turnover_sum += turnover

        p_name = str(r.get("Partner Name", "Unknown")).strip() or "Unknown"
        partner_counts[p_name] = partner_counts.get(p_name, 0) + 1

        tb_str = str(r.get("Top Brands Available", "")).strip()
        if tb_str and tb_str != "—":
            for b in tb_str.split(","):
                b_clean = b.strip()
                if b_clean:
                    top_brands_counts[b_clean] = top_brands_counts.get(b_clean, 0) + 1

        cb_str = str(r.get("Competitor Brands Available", "")).strip()
        if cb_str and cb_str != "—":
            for b in cb_str.split(","):
                b_clean = b.strip()
                if b_clean:
                    competitor_counts[b_clean] = competitor_counts.get(b_clean, 0) + 1

        pg_str = str(r.get("Payment Gateways Available", "")).strip()
        if pg_str and pg_str != "—":
            for g in pg_str.split(","):
                g_clean = g.strip()
                if g_clean:
                    payment_counts[g_clean] = payment_counts.get(g_clean, 0) + 1

        area = str(r.get("Area", "Unspecified")).strip() or "Unspecified"
        sub_area = str(r.get("Sub Area", "Unspecified")).strip() or "Unspecified"
        area_key = f"{area} → {sub_area}"
        if area_key not in area_summary:
            area_summary[area_key] = {"area": area, "sub_area": sub_area, "visits": 0, "sales": 0.0}
        area_summary[area_key]["visits"] += 1
        area_summary[area_key]["sales"] += sales

    area_summary_list = sorted(area_summary.values(), key=lambda x: x["visits"], reverse=True)

    sanitized_scoped = []
    for r in scoped_records:
        try:
            s_val = float(r.get("Shop Avg Monthly Sales", 0) or 0)
        except Exception:
            s_val = 0.0
        try:
            qr_t = float(r.get("QR Monthly Turnover", 0) or 0)
        except Exception:
            qr_t = 0.0

        sub_at = str(r.get("Submitted At", "")).strip()
        date_only = sub_at[:10] if len(sub_at) >= 10 else ""

        lat_raw = r.get("User Latitude", r.get("Latitude", r.get("lat", "")))
        lng_raw = r.get("User Longitude", r.get("Longitude", r.get("lng", "")))

        try:
            lat_val = float(str(lat_raw).strip()) if str(lat_raw).strip() not in ("", "0", "None", "null") else None
        except Exception:
            lat_val = None

        try:
            lng_val = float(str(lng_raw).strip()) if str(lng_raw).strip() not in ("", "0", "None", "null") else None
        except Exception:
            lng_val = None

        sanitized_scoped.append({
            "partner": str(r.get("Partner Name", "")).strip(),
            "shop": str(r.get("Shop Name", "")).strip(),
            "code": str(r.get("Store Code", "")).strip(),
            "area": str(r.get("Area", "")).strip(),
            "sub_area": str(r.get("Sub Area", "")).strip(),
            "sales": s_val,
            "top_brands": str(r.get("Top Brands Available", "")).strip(),
            "competitors": str(r.get("Competitor Brands Available", "")).strip(),
            "payment_gateways": str(r.get("Payment Gateways Available", "")).strip(),
            "qr_payment": str(r.get("QR Code Payment Available", "")).strip(),
            "qr_turnover": qr_t,
            "booker": str(r.get("Booker Name", "")).strip(),
            "username": str(r.get("Username", "")).strip(),
            "submitted_at": sub_at,
            "date": date_only,
            "lat": lat_val,
            "lng": lng_val,
        })

    user_time_spending = _calculate_user_time_spending(scoped_records)

    return jsonify({
        "total_visits": total_visits,
        "total_sales": total_sales,
        "avg_sales": (total_sales / total_visits) if total_visits > 0 else 0,
        "unique_shops_count": len(unique_shops),
        "unique_bookers_count": len(unique_bookers),
        "qr_count": qr_count,
        "qr_adoption_rate": round((qr_count / total_visits * 100), 1) if total_visits > 0 else 0,
        "qr_turnover_sum": qr_turnover_sum,
        "total_universe_shops": total_universe_shops,
        "universe_shops_by_partner": universe_shops_by_partner,
        "partner_counts": partner_counts,
        "top_brands_counts": top_brands_counts,
        "competitor_counts": competitor_counts,
        "payment_counts": payment_counts,
        "area_summary": area_summary_list[:15],
        "raw_scoped_records": sanitized_scoped,
        "user_time_spending": user_time_spending,
    })


def _parse_submitted_at(sub_at_str: str) -> datetime | None:
    s = str(sub_at_str or "").strip()
    if not s:
        return None
    s_clean = s.replace("T", " ")
    if len(s_clean) >= 19:
        try:
            return datetime.strptime(s_clean[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    if len(s) >= 10:
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d")
        except ValueError:
            pass
    return None


def _calculate_user_time_spending(scoped_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}

    for r in scoped_records:
        sub_at = str(r.get("Submitted At", "")).strip()
        dt = _parse_submitted_at(sub_at)
        date_str = dt.strftime("%Y-%m-%d") if dt else (sub_at[:10] if len(sub_at) >= 10 else "")
        if not date_str or len(date_str) < 10:
            continue

        username = str(r.get("Username", "")).strip()
        booker = str(r.get("Booker Name", "")).strip()
        user_key = username or booker or "Unknown User"
        partner = str(r.get("Partner Name", "")).strip()

        key = (user_key, date_str)
        if key not in grouped:
            grouped[key] = []
        
        grouped[key].append({
            "dt": dt,
            "submitted_at": sub_at,
            "username": username,
            "booker": booker,
            "partner": partner,
            "area": str(r.get("Area", "")).strip(),
            "sub_area": str(r.get("Sub Area", "")).strip(),
            "shop": str(r.get("Shop Name", "")).strip(),
        })

    user_time_spending = []
    for (user_key, date_str), recs in grouped.items():
        valid_recs = [r for r in recs if r["dt"] is not None]
        valid_recs.sort(key=lambda x: x["dt"])

        partner_name = recs[0]["partner"] if recs else ""
        booker_name = recs[0]["booker"] if recs else ""
        username = recs[0]["username"] if recs else ""

        areas_list: list[str] = []
        for item in recs:
            a_raw = str(item.get("area", "")).strip()
            if a_raw:
                a_clean = a_raw.split("[")[0].strip()
                if a_clean and a_clean not in areas_list:
                    areas_list.append(a_clean)

        visits_count = len(recs)
        
        if valid_recs and len(valid_recs) >= 1:
            first_dt = valid_recs[0]["dt"]
            last_dt = valid_recs[-1]["dt"]
            
            first_time = first_dt.strftime("%I:%M %p")
            last_time = last_dt.strftime("%I:%M %p")
            
            span_minutes = int((last_dt - first_dt).total_seconds() / 60)
            if span_minutes < 0:
                span_minutes = 0

            active_minutes = 15
            for i in range(1, len(valid_recs)):
                gap = int((valid_recs[i]["dt"] - valid_recs[i-1]["dt"]).total_seconds() / 60)
                if gap > 0:
                    active_minutes += min(gap, 60)
                else:
                    active_minutes += 5
        else:
            first_time = "—"
            last_time = "—"
            span_minutes = 0
            active_minutes = visits_count * 15

        span_hours = round(span_minutes / 60.0, 2)
        active_hours = round(active_minutes / 60.0, 2)

        span_h = span_minutes // 60
        span_m = span_minutes % 60
        span_formatted = f"{span_h}h {span_m}m" if span_h > 0 else f"{span_m}m"

        act_h = active_minutes // 60
        act_m = active_minutes % 60
        active_formatted = f"{act_h}h {act_m}m" if act_h > 0 else f"{act_m}m"

        avg_interval = round(span_minutes / (visits_count - 1), 1) if visits_count > 1 else 0.0

        user_time_spending.append({
            "date": date_str,
            "month": date_str[:7],
            "user": user_key,
            "username": username,
            "booker": booker_name,
            "partner": partner_name,
            "areas": areas_list,
            "first_visit_time": first_time,
            "last_visit_time": last_time,
            "span_minutes": span_minutes,
            "span_hours": span_hours,
            "span_formatted": span_formatted,
            "active_minutes": active_minutes,
            "active_hours": active_hours,
            "active_formatted": active_formatted,
            "visit_count": visits_count,
            "avg_interval_minutes": avg_interval,
        })

    user_time_spending.sort(key=lambda x: (x["date"], x["user"]), reverse=True)
    return user_time_spending


@app.route("/api/submissions")
@login_required
def api_submissions():
    user = session.get("_authenticated_user", {})
    if user.get("role") == "viewer" or user.get("username") == "test":
        return jsonify({"submissions": _get_dummy_submissions()})
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
    if user.get("role") == "viewer" or user.get("username") == "test":
        return jsonify({
            "ok": False,
            "error": "🔒 Read-Only Demo Account: The 'test' viewer user cannot save, edit, or delete data."
        }), 403
    partner_code = request.form.get("partner_code", "").strip()
    if not partner_code and user.get("partner_codes"):
        partner_code = user["partner_codes"][0]
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
