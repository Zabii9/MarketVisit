/**
 * Personal Google Drive upload endpoint for the Market Visit app.
 * Configure FOLDER_ID and UPLOAD_TOKEN in Apps Script properties.
 */
function doPost(e) {
  const output = (payload) => ContentService
    .createTextOutput(JSON.stringify(payload))
    .setMimeType(ContentService.MimeType.JSON);

  try {
    const properties = PropertiesService.getScriptProperties();
    const expectedToken = properties.getProperty('UPLOAD_TOKEN');
    const folderId = properties.getProperty('FOLDER_ID');
    const payload = JSON.parse(e.postData.contents || '{}');

    const missingProperties = [];
    if (!folderId) missingProperties.push('FOLDER_ID');
    if (!expectedToken) missingProperties.push('UPLOAD_TOKEN');
    if (missingProperties.length) {
      return output({
        ok: false,
        error: 'Missing Apps Script properties: ' + missingProperties.join(', ')
      });
    }
    if (!payload.token || payload.token !== expectedToken) {
      return output({ ok: false, error: 'Invalid upload token.' });
    }
    if (!payload.filename || !payload.data) {
      return output({ ok: false, error: 'Photo filename or data is missing.' });
    }

    const bytes = Utilities.base64Decode(payload.data);
    const blob = Utilities.newBlob(
      bytes,
      payload.mimeType || 'image/jpeg',
      payload.filename
    );
    const file = DriveApp.getFolderById(folderId).createFile(blob);

    return output({ ok: true, id: file.getId(), url: file.getUrl() });
  } catch (error) {
    return output({ ok: false, error: String(error.message || error) });
  }
}
