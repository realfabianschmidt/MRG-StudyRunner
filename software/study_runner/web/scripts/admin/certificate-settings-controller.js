/**
 * The certificate settings page.
 *
 * Same shape as the Notion and Nextcloud pages: setup steps that show what is
 * still missing, a status block, and one card per action. The QR code has a
 * single job - the tablet scans it and downloads the certificate.
 */
import { getJson } from '../api-client.js';
import { t } from '../i18n.js';
import { byId, escapeHtml, formatDateTime, setText } from '../lib/dom-utils.js';
import { renderTestResult, setStepState, wireSettingsPage, withBusyButton } from '../lib/settings-page.js';
import { createQrSvg } from '../qr-code.js';

let callbacks = {};
let initialized = false;
let latestStatus = null;

export function initializeCertificateSettings(options = {}) {
  callbacks = options;
  if (initialized) return;
  initialized = true;

  wireSettingsPage({
    viewId: 'view-certificate-settings',
    openButtonId: 'btn-certificate-settings',
    backButtonId: 'btn-certificate-back',
    switchView: callbacks.switchView,
    onOpen: refreshStatus,
  });

  byId('btn-certificate-export')?.addEventListener('click', () => void exportCertificate());
  byId('btn-certificate-import')?.addEventListener('click', () => byId('certificate-import-file')?.click());
  byId('certificate-import-file')?.addEventListener('change', (event) => void importCertificate(event));
}

async function refreshStatus() {
  try {
    latestStatus = await getJson('/api/admin/certificate/status');
    renderStatus(latestStatus);
  } catch (error) {
    console.error('[certificate] Could not load status:', error);
    callbacks.showToast?.(t('certificate.loadFailed', 'Could not load the certificate status'), 'error');
  }
}

function renderStatus(status) {
  if (!status) return;

  const isLocalCa = status.mode === 'generated';
  setText('certificate-https-value', httpsLabel(status));
  setText('certificate-https-hint', httpsHint(status));

  setText('certificate-expiry-value', status.root_ca_expires_at ? formatDateTime(status.root_ca_expires_at) : '-');
  setText('certificate-expiry-hint', status.root_ca_expires_at
    ? t('certificate.validUntilHint', 'Tablets keep trusting it until this date.')
    : t('certificate.noLocalCertificate', 'No local certificate on this computer.'));

  const addresses = [...(status.ip_addresses || []), ...(status.dns_names || [])];
  setText('certificate-addresses-value', addresses.length ? addresses.join(', ') : '-');
  setText('certificate-addresses-hint', t('certificate.addressesHint', 'The certificate is valid for these addresses of this computer.'));

  setText('certificate-fingerprint-value', status.root_ca_fingerprint_sha256 || '-');

  renderDownload(status);
  renderSteps(status, isLocalCa);
}

function httpsLabel(status) {
  if (!status.https_active) return t('certificate.httpsOff', 'off');
  if (status.mode === 'generated') return t('certificate.httpsLocalCa', 'on, own certificate');
  if (status.mode === 'configured') return t('certificate.httpsConfigured', 'on, provided certificate');
  if (status.mode === 'adhoc') return t('certificate.httpsAdhoc', 'on, temporary certificate');
  return t('certificate.httpsUnknown', 'unknown');
}

function httpsHint(status) {
  if (!status.https_active) {
    return t('certificate.httpsOffHint', 'Without a secure connection the tablet camera cannot be used.');
  }
  if (status.mode === 'adhoc') {
    return t('certificate.httpsAdhocHint', 'A temporary certificate is in use. Tablets will refuse it - restart Study Runner.');
  }
  if (status.mode === 'configured') {
    return t('certificate.httpsConfiguredHint', 'A certificate provided by you is in use, so no tablet setup is needed here.');
  }
  return t('certificate.httpsLocalCaHint', 'Tablets need this certificate once, then the camera works.');
}

function renderDownload(status) {
  const container = byId('certificate-qr');
  const urlEl = byId('certificate-download-url');
  const openLink = byId('btn-certificate-open-download');
  const url = status.download_url || '';

  if (urlEl) urlEl.textContent = url || t('certificate.noDownload', 'not available');
  if (openLink) {
    openLink.href = url || '#';
    openLink.hidden = !url;
  }

  if (container) {
    container.innerHTML = '';
    if (url) {
      try {
        container.innerHTML = createQrSvg(url, { size: 220, margin: 4 });
      } catch (error) {
        // The offline encoder only reaches version 6 (~106 characters); a very
        // long address cannot be encoded, so show it as text instead.
        console.error('[certificate] Could not draw the QR code:', error);
        container.textContent = t('certificate.qrFailed', 'The code could not be drawn. Type the address on the tablet instead.');
      }
    }
  }

  setText('certificate-download-hint', downloadHint(status));
}

function downloadHint(status) {
  if (status.download_status === 'ready') {
    return t('certificate.downloadHint', 'The tablet must be on the same network as this computer.');
  }
  if (status.download_status === 'failed') {
    return t('certificate.downloadFailedHint', 'The download helper could not start. The study itself is unaffected.');
  }
  if (status.download_status === 'disabled') {
    return t('certificate.downloadDisabledHint', 'No download is needed: this computer does not use its own certificate.');
  }
  return t('certificate.downloadUnknownHint', 'Start Study Runner with a secure connection to enable the download.');
}

function renderSteps(status, isLocalCa) {
  const downloadReady = status.download_status === 'ready';
  setStepState(
    'certificate-step-1-state',
    downloadReady ? 'ready' : 'missing',
    downloadReady ? t('certificate.stepReady', 'ready') : t('certificate.stepMissing', 'missing'),
  );
  // Steps 2 and 3 happen on the tablet, which cannot report back to the server.
  const tabletLabel = t('certificate.stepOnTablet', 'on tablet');
  setStepState('certificate-step-2-state', 'optional', tabletLabel);
  setStepState('certificate-step-3-state', 'optional', tabletLabel);
  setStepState(
    'certificate-step-4-state',
    isLocalCa || status.mode === 'configured' ? 'ready' : 'missing',
    isLocalCa || status.mode === 'configured' ? t('certificate.stepReady', 'ready') : t('certificate.stepMissing', 'missing'),
  );
}

async function exportCertificate() {
  await withBusyButton('btn-certificate-export', t('certificate.exporting', 'Saving...'), async () => {
    try {
      const response = await fetch('/api/admin/certificate/export');
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.error || `HTTP ${response.status}`);
      }
      const blob = await response.blob();
      triggerBrowserDownload(blob, 'study-runner-root-ca-backup.json');
      callbacks.showToast?.(t('certificate.exported', 'Certificate file saved'), 'success');
    } catch (error) {
      console.error('[certificate] Export failed:', error);
      renderTestResult('certificate-transfer-result', { ok: false, error: error.message }, {
        fallbackErrorLabel: t('certificate.exportFailed', 'Saving failed'),
      });
    }
  });
}

async function importCertificate(event) {
  const input = event.target;
  const file = input?.files?.[0];
  if (!file) return;

  await withBusyButton('btn-certificate-import', t('certificate.importing', 'Loading...'), async () => {
    try {
      const body = new FormData();
      body.append('file', file);
      const response = await fetch('/api/admin/certificate/import', { method: 'POST', body });
      const result = await response.json().catch(() => ({ ok: false }));
      if (!response.ok || !result.ok) {
        throw new Error(result.error || `HTTP ${response.status}`);
      }
      renderImportSuccess(result);
      callbacks.showToast?.(
        result.unchanged
          ? t('certificate.importUnchanged', 'This certificate was already in use')
          : t('certificate.imported', 'Certificate loaded - restart Study Runner'),
        'success',
      );
      await refreshStatus();
    } catch (error) {
      console.error('[certificate] Import failed:', error);
      renderTestResult('certificate-transfer-result', { ok: false, error: error.message }, {
        fallbackErrorLabel: t('certificate.importFailed', 'Loading failed'),
      });
    } finally {
      // Allow re-picking the same file after a failed attempt.
      if (input) input.value = '';
    }
  });
}

function renderImportSuccess(result) {
  const container = byId('certificate-transfer-result');
  if (!container) return;
  const message = result.unchanged
    ? t('certificate.importUnchangedBody', 'The file contains the certificate this computer already uses. Nothing changed.')
    : t('certificate.importedBody', 'Restart Study Runner so the new certificate is used. Tablets that already trust it need no new setup.');
  container.innerHTML = `
    <div class="test-result-box test-result-box--ok">
      <div class="test-row"><i class="iconoir-check-circle"></i><span>${escapeHtml(message)}</span></div>
      <div class="test-row"><i class="iconoir-fingerprint"></i><span>${escapeHtml(result.fingerprint_sha256 || '')}</span></div>
    </div>
  `;
  container.hidden = false;
}

function triggerBrowserDownload(blob, filename) {
  // Browser download instead of a native save dialog: identical on Windows and macOS.
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
