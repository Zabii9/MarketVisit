# Daily Market Visit

A mobile-friendly Streamlit form that saves daily market visits to the `Dump`
worksheet in Google Sheets. Visit photos can be uploaded to a shared Google Drive
folder and their links are saved with each response.

## Security first

The service-account key shared in chat must be considered compromised. In Google
Cloud Console, delete/revoke that key, create a replacement, and use only the new
key in Streamlit secrets. Never put a service-account JSON file in this repository.

## Google setup

1. Enable **Google Sheets API** and **Google Drive API** in the Google Cloud project.
2. Share the spreadsheet with the replacement service account's `client_email` as
   **Editor**. The app uses spreadsheet ID
   `1Mt-Y09-azOOQ9r6nqELCQbeoeNtE-Z3JJaJ5WiMzP0A` and worksheet `Dump`.
3. Recommended: create a folder inside a Google Workspace **Shared Drive** for visit
   photos and add the service account as a member with **Content manager** access.
   Copy the folder ID from its URL. A folder in a user's normal **My Drive** does
   not work because service accounts have no storage quota and cannot own files.
4. Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml`, paste the
   replacement service-account values, and add the Drive folder ID.

The app creates this header row when `Dump` is empty:

`Submission ID, Submitted At, Partner Name, Shop Name, Shop Picture + Selfie, Area, Sub Area, Booker Name, Shop Avg Monthly Sales, Last Order Booker Visit, Competitor Brands Available, Top Brands Available, Remarks`

If the sheet already contains content with different headers, move or clear it
before the first submission.

## Run locally

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

Without `drive_folder_id`, photos are stored in the local `uploads/` folder. This
is suitable for local testing only; cloud deployments need Drive storage because
their local filesystem may be temporary.

If Drive reports that the folder was not found even though its ID is correct, it
has not been shared with the service-account `client_email`. Google returns 404
for folders the authenticated account cannot access. A 403 storage-quota error
means the destination is a normal My Drive folder; use a Workspace Shared Drive or
an OAuth/Apps Script uploader that runs as a human account.

## Deploy on Streamlit Community Cloud

Push the project without `.streamlit/secrets.toml`, create a Streamlit app pointing
to `app.py`, and paste the contents of your local secrets file into the app's
**Settings → Secrets** panel.
