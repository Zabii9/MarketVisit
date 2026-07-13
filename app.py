from __future__ import annotations

import hashlib
import io
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import gspread
import streamlit as st
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload


SHEET_ID = "1Mt-Y09-azOOQ9r6nqELCQbeoeNtE-Z3JJaJ5WiMzP0A"
WORKSHEET_NAME = "Dump"
TIMEZONE = ZoneInfo("Asia/Karachi")
UPLOAD_DIR = Path("uploads")

HEADERS = [
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

FORM_WIDGET_KEYS = [
    "partner_name", "shop_name", "area", "sub_area", "booker_name",
    "monthly_sales", "visit_photo", "visited_before", "last_visit",
    "competitor_brands", "competitor_other", "top_brands", "remarks",
]


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
      .block-container { max-width: 780px; padding-top: 2rem; padding-bottom: 4rem; }
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

# Preserve values after validation/API errors. Reset only after a confirmed save.
if st.session_state.pop("_reset_market_visit_form", False):
    for widget_key in FORM_WIDGET_KEYS:
        st.session_state.pop(widget_key, None)

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


def _worksheet(credentials: Credentials) -> gspread.Worksheet:
    client = gspread.authorize(credentials)
    worksheet = client.open_by_key(SHEET_ID).worksheet(WORKSHEET_NAME)
    first_row = worksheet.row_values(1)
    if not first_row:
        worksheet.append_row(HEADERS, value_input_option="RAW")
        worksheet.freeze(rows=1)
    elif first_row == HEADERS + ["Visit Date"]:
        # Accept sheets briefly created with the now-removed Visit Date column.
        pass
    elif first_row != HEADERS:
        raise RuntimeError(
            "The Dump worksheet has different columns. Clear its first row or "
            "make it match the headers listed in README.md."
        )
    return worksheet


def _save_photo(uploaded_file: Any, submission_id: str, credentials: Credentials) -> str:
    extension = Path(uploaded_file.name).suffix.lower() or ".jpg"
    filename = f"{submission_id}{extension}"
    content = uploaded_file.getvalue()
    drive_folder_id = _secret("drive_folder_id", "")

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


st.markdown('<div class="section-label">Visit details</div>', unsafe_allow_html=True)

with st.form("market_visit", clear_on_submit=False):
    col1, col2 = st.columns(2)
    with col1:
        partner_name = st.text_input("Partner Name *", placeholder="Enter partner name", key="partner_name")
        shop_name = st.text_input("Shop Name *", placeholder="Enter shop name", key="shop_name")
        area = st.text_input("Area *", placeholder="e.g. Gulshan", key="area")
    with col2:
        sub_area = st.text_input("Sub Area *", placeholder="e.g. Block 5", key="sub_area")
        booker_name = st.text_input("Booker Name *", placeholder="Enter booker name", key="booker_name")
        monthly_sales = st.number_input(
            "Shop Avg Monthly Sales (PKR) *",
            min_value=0,
            step=1000,
            format="%d",
            help="Enter the estimated average monthly sales in PKR.",
            key="monthly_sales",
        )

    photo = st.file_uploader(
        "Shop Picture + Selfie *",
        type=["jpg", "jpeg", "png", "webp"],
        help="Take or upload one clear photo showing you and the shop.",
        key="visit_photo",
    )

    st.markdown('<div class="section-label">Market observations</div>', unsafe_allow_html=True)
    visited_before = st.checkbox("The order booker has visited this shop before", key="visited_before")
    last_visit: date | None = None
    if visited_before:
        last_visit = st.date_input(
            "Last Order Booker Visit *",
            value=date.today(),
            max_value=date.today(),
            key="last_visit",
        )

    competitor_brands = st.multiselect(
        "Competitor Brand Availability",
        ["Milkpak", "Dairy Omung", "Haleeb", "Dostea", "Good Milk", "Other"],
        placeholder="Select all available competitor brands",
        key="competitor_brands",
    )
    competitor_other = ""
    if "Other" in competitor_brands:
        competitor_other = st.text_input("Other competitor brand", placeholder="Enter brand name", key="competitor_other")

    top_brands = st.multiselect(
        "Top Brand Availability *",
        ["Olper's Milk", "Tarang", "TBA","Others"],
        placeholder="Select all available brands",
        key="top_brands",
    )
    remarks = st.text_area(
        "Remarks",
        placeholder="Add visibility, stock, pricing, retailer feedback, or follow-up notes…",
        height=110,
        key="remarks",
    )

    submitted = st.form_submit_button("Submit Market Visit", type="primary")


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
                ]
                _worksheet(credentials).append_row(row, value_input_option="USER_ENTERED")
            st.session_state["_market_visit_success"] = (
                f"Market visit saved successfully. Reference: {submission_id}"
            )
            st.session_state["_reset_market_visit_form"] = True
            st.rerun()
        except Exception as exc:
            st.error(f"Could not save this visit: {exc}")


if not _secret("drive_folder_id", ""):
    st.info(
        "Photo storage is currently local. Add `drive_folder_id` to Streamlit secrets "
        "before cloud deployment so photo links remain available."
    )

st.markdown(
    '<div class="footnote">Fields marked * are required · Times are saved in Pakistan Standard Time</div>',
    unsafe_allow_html=True,
)
