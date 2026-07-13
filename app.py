from __future__ import annotations

import base64
import hashlib
import html
import hmac
import io
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import gspread
import requests
import streamlit as st
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload


SHEET_ID = "1Mt-Y09-azOOQ9r6nqELCQbeoeNtE-Z3JJaJ5WiMzP0A"
WORKSHEET_NAME = "Dump"
TIMEZONE = ZoneInfo("Asia/Karachi")
UPLOAD_DIR = Path("uploads")

PARTNER_NAME_BY_CODE = {
    "D0573": "CBL",
    "D70002202": "Olpers KHI",
    "D70002246": "Olpers LHR",
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
HEADERS = BASE_HEADERS + ["Store Code", "Username"]

st.set_page_config(
    page_title="Daily Market Visit",
    page_icon="📍",
    layout="centered",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
      .stApp { background: #f5f7f4; }
      .block-container { max-width: 780px; padding-top: 3rem; padding-bottom: 4rem; position: relative; }
      .hero {
        padding: 1.6rem 1.7rem; border-radius: 18px; color: white;
        background: linear-gradient(135deg, #12372a 0%, #2f6b4f 100%);
        box-shadow: 0 12px 30px rgba(18,55,42,.18); margin-bottom: 1.3rem;
      }
      .hero h1 { margin: 0; font-size: 2rem; letter-spacing: -.02em; }
      .hero p { margin: .45rem 0 0; color: #dcebe3; }
      [data-testid="stForm"] {
        background: white; border: 1px solid #dfe7e1; border-radius: 18px;
        padding: 1.4rem 1.4rem .7rem; box-shadow: 0 8px 25px rgba(34,55,42,.06);
      }
      /* The reactive visit card uses a bordered container instead of st.form. */
      [data-testid="stVerticalBlockBorderWrapper"],
      .st-key-market_visit_card,
      .st-key-market_visit_card [data-testid="stVerticalBlockBorderWrapper"] {
        background: #ffffff !important;
        background-color: #ffffff !important;
        opacity: 1 !important;
        backdrop-filter: none !important;
        border-color: #dfe7e1 !important;
        border-radius: 18px !important;
      }
      .st-key-market_visit_card {
        box-shadow: 0 16px 38px rgba(18,55,42,.14) !important;
        overflow: visible !important;
      }
      /* Keep every editable surface white while the page itself stays gray. */
      [data-baseweb="input"],
      [data-baseweb="select"] > div,
      [data-baseweb="textarea"],
      [data-testid="stFileUploaderDropzone"],
      [data-testid="stNumberInputContainer"] {
        background-color: #ffffff !important;
      }
      [data-baseweb="input"] input,
      [data-baseweb="textarea"] textarea,
      [data-testid="stNumberInputContainer"] input {
        background-color: #ffffff !important;
      }
      .shop-details {
        margin: .35rem 0 1rem; padding: 1rem 1.1rem; border-radius: 12px;
        background: #f7faf8; border: 1px solid #dfe7e1;
      }
      .shop-details-title { color: #1f5c43; font-weight: 750; margin-bottom: .7rem; }
      .shop-details-grid {
        display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .7rem 1.2rem;
      }
      .shop-detail-wide { grid-column: 1 / -1; }
      .shop-detail-label { color: #68756d; font-size: .76rem; text-transform: uppercase; letter-spacing: .03em; }
      .shop-detail-value { color: #16251d; font-size: .94rem; overflow-wrap: anywhere; }
      .shop-detail-value a { color: #1f5c43; font-weight: 650; }
      .st-key-header_logout {
        position: absolute !important; top: 4.15rem; right: 2.7rem;
        width: auto !important; z-index: 1000;
      }
      .st-key-header_logout button {
        min-height: 2.35rem; padding: .35rem .85rem; border-radius: 9px;
        color: #ffffff !important; background: rgba(255,255,255,.12) !important;
        border: 1px solid rgba(255,255,255,.42) !important;
      }
      .st-key-header_logout button:hover {
        background: rgba(255,255,255,.22) !important;
        border-color: rgba(255,255,255,.7) !important;
      }
      .st-key-header_account {
        position: absolute !important; top: 6.75rem; right: 2.7rem;
        width: auto !important; max-width: 18rem; z-index: 1000;
        text-align: right;
      }
      .st-key-header_account p {
        color: rgba(255,255,255,.88) !important; margin: 0 !important;
        font-size: .78rem !important;
      }
      @media (max-width: 640px) {
        .hero { padding-top: 5.5rem; }
        .shop-details-grid { grid-template-columns: 1fr; }
        .shop-detail-wide { grid-column: auto; }
        .st-key-header_logout { top: 4rem; right: 1.5rem; }
        .st-key-header_account { top: 6.55rem; right: 1.5rem; max-width: 16rem; }
        .st-key-header_logout button { padding: .3rem .65rem; }
      }
      div[data-testid="stFormSubmitButton"] button {
        width: 100%; border-radius: 10px; min-height: 3rem; font-weight: 700;
        background: #1f5c43; color: white; border: none;
      }
      div[data-testid="stFormSubmitButton"] button:hover {
        background: #174a35; color: white; border: none;
      }
      .section-label { color: #1f5c43; font-weight: 750; margin: .2rem 0 .4rem; }
      .footnote { text-align: center; color: #68756d; font-size: .82rem; margin-top: 1rem; }
    </style>
    <div class="hero">
      <h1>Daily Market Visit</h1>
      <p>Record shop details, market observations, and your visit photo.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

# A new form version is created only after a confirmed save. Changing every widget
# key guarantees that browser-side values (including file uploads) are reset.
form_version = int(st.session_state.get("_market_visit_form_version", 0))
form_prefix = f"market_visit_{form_version}_"
for state_key in list(st.session_state.keys()):
    if state_key.startswith("market_visit_") and not state_key.startswith(form_prefix):
        st.session_state.pop(state_key, None)


def form_key(name: str) -> str:
    return f"{form_prefix}{name}"

success_message = st.session_state.pop("_market_visit_success", None)
if success_message:
    st.success(success_message)
    st.balloons()


def _secret(name: str, default: Any = None) -> Any:
    try:
        return st.secrets.get(name, default)
    except FileNotFoundError:
        return default


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


def _configured_users() -> dict[str, dict[str, Any]]:
    users = _secret("users", {})
    return {str(username): dict(settings) for username, settings in users.items()}


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
            range_name=f"N1:O1", values=[["Store Code", "Username"]]
        )
    elif first_row == BASE_HEADERS + ["Store Code"]:
        worksheet.update_cell(1, len(HEADERS), "Username")
    elif first_row == BASE_HEADERS + ["Visit Date"]:
        worksheet.insert_cols(
            [["Store Code", "Username"]], col=len(BASE_HEADERS) + 1
        )
    elif first_row == BASE_HEADERS + ["Store Code", "Visit Date"]:
        worksheet.insert_cols([["Username"]], col=len(HEADERS))
    elif first_row == HEADERS + ["Visit Date"]:
        # Accept sheets briefly created with the now-removed Visit Date column.
        pass
    elif first_row != HEADERS:
        raise RuntimeError(
            "The Dump worksheet has different columns. Clear its first row or "
            "make it match the headers listed in README.md."
        )
    return worksheet


@st.cache_data(ttl=300, show_spinner=False)
def _universe_partner_locations() -> dict[str, dict[str, dict[str, list[dict[str, str]]]]]:
    """Return Universe shops grouped by distributor, locality, and sub-locality."""
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

    return {
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


def _partner_label(code: str) -> str:
    if not code:
        return "Select partner"
    return PARTNER_NAME_BY_CODE.get(code, code)


@st.cache_data(ttl=60, show_spinner=False)
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
        # Google Sheets and dropdown labels can contain visually identical runs
        # of one or more spaces. Collapse them before comparing shop dimensions.
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
        # The stored PKT abbreviation is not recognized consistently by
        # datetime.strptime on every platform, so parse the stable prefix.
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


def _save_photo(uploaded_file: Any, submission_id: str, credentials: Credentials) -> str:
    extension = Path(uploaded_file.name).suffix.lower() or ".jpg"
    filename = f"{submission_id}{extension}"
    content = uploaded_file.getvalue()
    apps_script_url = _secret("apps_script_upload_url", "")
    apps_script_token = _secret("apps_script_upload_token", "")
    drive_folder_id = _secret("drive_folder_id", "")

    # Apps Script runs as the human owner of a personal Drive folder, avoiding
    # the service account's zero-storage limitation.
    if apps_script_url:
        if not apps_script_token:
            raise RuntimeError(
                "apps_script_upload_token is missing from Streamlit secrets."
            )
        try:
            response = requests.post(
                apps_script_url,
                json={
                    "token": apps_script_token,
                    "filename": filename,
                    "mimeType": uploaded_file.type or "image/jpeg",
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
            mimetype=uploaded_file.type or "image/jpeg",
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
                    f"shared with {service_email}. Open the folder in Google Drive, "
                    "share it with that email as Editor, and verify drive_folder_id "
                    "in .streamlit/secrets.toml."
                ) from exc
            if exc.resp.status == 403 and (
                "storageQuotaExceeded" in error_text
                or "storage quota" in error_text.lower()
            ):
                raise RuntimeError(
                    "This service account has no Google Drive storage quota and "
                    "cannot upload into a normal My Drive folder. Use a Google "
                    "Workspace Shared Drive folder, or upload through OAuth/Google "
                    "Apps Script as a human Google account."
                ) from exc
            if exc.resp.status == 403:
                raise RuntimeError(
                    "Google Drive denied the photo upload. Confirm that the Drive "
                    "API is enabled, the service account has sufficient access, "
                    f"and the destination is a Shared Drive. Drive response: {error_text}"
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


users_configured = _configured_users()
if not users_configured:
    st.error(
        "Login users are not configured. Add [users.<username>] sections to "
        ".streamlit/secrets.toml as shown in secrets.toml.example."
    )
    st.stop()

current_user = st.session_state.get("_authenticated_user")
if not current_user:
    st.markdown('<div class="section-label">Sign in</div>', unsafe_allow_html=True)
    with st.form("login_form", clear_on_submit=False):
        login_username = st.text_input(
            "Username", autocomplete="username", placeholder="Enter username"
        )
        login_password = st.text_input(
            "Password",
            type="password",
            autocomplete="current-password",
            placeholder="Enter password",
        )
        login_submitted = st.form_submit_button(
            "Sign in", type="primary", use_container_width=True
        )

    if login_submitted:
        authenticated_user = _authenticate(login_username, login_password)
        if authenticated_user:
            st.session_state["_authenticated_user"] = authenticated_user
            st.rerun()
        else:
            st.error("Invalid username or password.")
    st.stop()

if st.button("Log out", key="header_logout"):
    st.session_state.clear()
    st.rerun()

role_label = "Administrator" if current_user["role"] == "admin" else "Partner"
with st.container(key="header_account"):
    st.caption(f"Signed in as **{current_user['display_name']}** · {role_label}")

st.markdown('<div class="section-label">Visit details</div>', unsafe_allow_html=True)

try:
    partner_locations = _universe_partner_locations()
except Exception as exc:
    partner_locations = {}
    st.error(
        f"Could not load Partner Names, Areas, and Sub Areas from Universe: {exc}"
    )

if current_user["role"] == "partner":
    assigned_partner_codes = current_user["partner_codes"]
    missing_partner_codes = [
        code for code in assigned_partner_codes if code not in partner_locations
    ]
    if missing_partner_codes:
        st.error(
            "These assigned partner codes were not found in Universe: "
            + ", ".join(missing_partner_codes)
        )
        st.stop()
    partner_locations = {
        code: partner_locations[code] for code in assigned_partner_codes
    }

with st.container(border=True, key="market_visit_card"):
    partner_col, area_col = st.columns(2)
    with partner_col:
        partner_options = list(partner_locations)
        lock_partner = (
            current_user["role"] == "partner" and len(partner_options) == 1
        )
        if current_user["role"] == "admin" or len(partner_options) > 1:
            partner_options = [""] + partner_options
        partner_code = st.selectbox(
            "Partner Name *",
            options=partner_options,
            format_func=_partner_label,
            key=form_key("partner_name"),
            disabled=lock_partner,
        )
        partner_name = PARTNER_NAME_BY_CODE.get(partner_code, partner_code)
    with area_col:
        area_options = list(partner_locations.get(partner_code, {}))
        area = st.selectbox(
            "Area *",
            options=[""] + area_options,
            format_func=lambda value: value or "Select area",
            key=form_key(f"area_{partner_code or 'none'}"),
            disabled=not partner_code,
        )

    sub_area_col, booker_col = st.columns(2)
    with sub_area_col:
        sub_area_options = list(
            partner_locations.get(partner_code, {}).get(area, {})
        )
        sub_area = st.selectbox(
            "Sub Area *",
            options=[""] + sub_area_options,
            format_func=lambda value: value or "Select sub area",
            key=form_key(f"sub_area_{partner_code or 'none'}_{area or 'none'}"),
            disabled=not area,
        )
    with booker_col:
        booker_name = st.text_input("Booker Name *", placeholder="Enter booker name", key=form_key("booker_name"))

    shop_records = (
        partner_locations.get(partner_code, {})
        .get(area, {})
        .get(sub_area, [])
    )
    shops_by_id = {shop["selection_id"]: shop for shop in shop_records}
    selected_shop_id = st.selectbox(
        "Shop Name *",
        options=[""] + list(shops_by_id),
        format_func=lambda selection_id: (
            (
                f"{shops_by_id[selection_id]['store_name']} "
                f"[{shops_by_id[selection_id]['store_code']}]"
                if shops_by_id[selection_id]["store_code"]
                else shops_by_id[selection_id]["store_name"]
            )
            if selection_id
            else "Select shop"
        ),
        key=form_key(
            f"shop_name_{partner_code or 'none'}_{area or 'none'}_"
            f"{sub_area or 'none'}"
        ),
        disabled=not sub_area,
    )
    selected_shop = shops_by_id.get(selected_shop_id, {})
    shop_name = selected_shop.get("store_name", "")

    if selected_shop:
        try:
            previous_visit_date = _last_recorded_visit(
                partner_name,
                area,
                sub_area,
                shop_name,
                selected_shop.get("store_code", ""),
            )
        except Exception:
            previous_visit_date = None

        if previous_visit_date:
            st.info(f"Last visit date: {previous_visit_date}", icon="🗓️")

        def safe_detail(name: str) -> str:
            return html.escape(selected_shop.get(name, "") or "—")

        latitude = selected_shop.get("latitude", "")
        longitude = selected_shop.get("longitude", "")
        map_link = ""
        if latitude and longitude:
            map_url = "https://www.google.com/maps?q=" + quote_plus(
                f"{latitude},{longitude}"
            )
            map_link = (
                f'<a href="{map_url}" target="_blank" rel="noopener noreferrer">'
                "Open in Google Maps ↗</a>"
            )

        st.markdown(
            f"""
            <div class="shop-details">
              <div class="shop-details-title">Selected shop details</div>
              <div class="shop-details-grid">
                <div><div class="shop-detail-label">Store Name</div><div class="shop-detail-value">{safe_detail('store_name')}</div></div>
                <div><div class="shop-detail-label">Store Code</div><div class="shop-detail-value">{safe_detail('store_code')}</div></div>
                <div><div class="shop-detail-label">Channel Classification</div><div class="shop-detail-value">{safe_detail('channel_classification')}</div></div>
                <div><div class="shop-detail-label">Owner / Contact</div><div class="shop-detail-value">{safe_detail('owner_name')} &nbsp;|&nbsp; {safe_detail('owner_contact')}</div></div>
                <div class="shop-detail-wide"><div class="shop-detail-label">Address</div><div class="shop-detail-value">{safe_detail('address')}</div></div>
                {f'<div class="shop-detail-wide"><div class="shop-detail-value">{map_link}</div></div>' if map_link else ''}
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    st.markdown('<div class="section-label">Market observations</div>', unsafe_allow_html=True)

    st.markdown(r"**Shop Picture + Selfie \***")
    camera_open_key = form_key("camera_open")
    if not st.session_state.get(camera_open_key, False):
        if st.button(
            "📷 Open camera",
            key=form_key("open_camera"),
            use_container_width=True,
        ):
            st.session_state[camera_open_key] = True
            st.rerun()

    photo = None
    if st.session_state.get(camera_open_key, False):
        photo = st.camera_input(
            "Take shop picture + selfie",
            help="Take one clear photo showing you and the shop.",
            key=form_key("visit_photo"),
            label_visibility="collapsed",
        )

    monthly_sales = st.number_input(
        "Shop Avg Monthly Sales (PKR) *",
        min_value=0,
        step=1000,
        format="%d",
        help="Enter the estimated average monthly sales in PKR.",
        key=form_key("monthly_sales"),
    )

    visited_before = st.checkbox("The order booker has visited this shop before", key=form_key("visited_before"))
    last_visit: date | None = None
    if visited_before:
        last_visit = st.date_input(
            "Last Order Booker Visit *",
            value=date.today(),
            max_value=date.today(),
            key=form_key("last_visit"),
        )

    competitor_brands = st.multiselect(
        "Competitor Brand Availability",
        ["Milkpak", "Dairy Omung", "Haleeb", "Dostea", "Good Milk", "Other"],
        placeholder="Select all available competitor brands",
        key=form_key("competitor_brands"),
    )
    competitor_other = ""
    if "Other" in competitor_brands:
        competitor_other = st.text_input("Other competitor brand", placeholder="Enter brand name", key=form_key("competitor_other"))

    top_brands = st.multiselect(
        "Top Brand Availability *",
        ["Olper's Milk", "Tarang", "TBA","Others"],
        placeholder="Select all available brands",
        key=form_key("top_brands"),
    )
    remarks = st.text_area(
        "Remarks",
        placeholder="Add visibility, stock, pricing, retailer feedback, or follow-up notes…",
        height=110,
        key=form_key("remarks"),
    )

    submitted = st.button(
        "Submit Market Visit",
        type="primary",
        use_container_width=True,
        key=form_key("submit"),
    )


if submitted:
    errors: list[str] = []
    required_text = {
        "Partner Name": partner_name,
        "Shop Name": shop_name,
        "Area": area,
        "Sub Area": sub_area,
        "Booker Name": booker_name,
    }
    errors.extend(f"{label} is required." for label, value in required_text.items() if not value.strip())
    if monthly_sales <= 0:
        errors.append("Shop Avg Monthly Sales must be greater than zero.")
    if photo is None:
        errors.append("Shop Picture + Selfie is required.")
    if not top_brands:
        errors.append("Select at least one top brand, or TBA.")
    if "Other" in competitor_brands and not competitor_other.strip():
        errors.append("Enter the other competitor brand name.")

    if errors:
        st.error("Please fix the following:\n\n- " + "\n- ".join(errors))
    else:
        now = datetime.now(TIMEZONE)
        submission_id = _submission_id(shop_name.strip(), now)
        competitors = [brand for brand in competitor_brands if brand != "Other"]
        if competitor_other.strip():
            competitors.append(competitor_other.strip())

        try:
            with st.spinner("Saving your market visit…"):
                credentials = _credentials()
                photo_location = _save_photo(photo, submission_id, credentials)
                row = [
                    submission_id,
                    now.strftime("%Y-%m-%d %H:%M:%S %Z"),
                    partner_name.strip(),
                    shop_name.strip(),
                    photo_location,
                    area.strip(),
                    sub_area.strip(),
                    booker_name.strip(),
                    int(monthly_sales),
                    last_visit.isoformat() if last_visit else "Never / Unknown",
                    ", ".join(competitors) if competitors else "None observed",
                    ", ".join(top_brands),
                    remarks.strip(),
                    selected_shop.get("store_code", ""),
                    current_user["username"],
                ]
                _worksheet(credentials).append_row(row, value_input_option="USER_ENTERED")
                _last_recorded_visit.clear()
            st.session_state["_market_visit_success"] = (
                f"Market visit saved successfully. Reference: {submission_id}"
            )
            st.session_state["_market_visit_form_version"] = form_version + 1
            st.rerun()
        except Exception as exc:
            st.error(f"Could not save this visit: {exc}")


if not _secret("apps_script_upload_url", "") and not _secret("drive_folder_id", ""):
    st.info(
        "Photo storage is currently local. Configure an Apps Script upload URL or "
        "a Workspace Shared Drive folder before cloud deployment."
    )

st.markdown(
    '<div class="footnote">Fields marked * are required · Times are saved in Pakistan Standard Time</div>',
    unsafe_allow_html=True,
)
