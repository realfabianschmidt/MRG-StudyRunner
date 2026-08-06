/**
 * The Nextcloud settings page.
 *
 * Deliberately the same layout and behavior as the Notion page: setup steps,
 * status tiles, one connection card. The share link belongs to the study, the
 * optional share password stays backend-local like the Notion API key.
 */
import { getJson, postJson } from '../../api-client.js';
import { t } from '../../i18n.js';
import { byId, setText } from '../../lib/dom-utils.js';
import { normalizeStudySettings } from '../../lib/study-settings.js';
import { renderTestResult, setStepState, withBusyButton } from '../../lib/settings-page.js';

let callbacks = {};
let initialized = false;
let passwordConfigured = false;
let clearPasswordRequested = false;

export function initializeNextcloudSettings(options = {}) {
  callbacks = options;
  if (initialized) return;
  initialized = true;

  // Optional rich destination UIs are discovered by the generic study panel
  // through the btn-<plugin-key>-settings convention.  The plugin controller
  // owns what opening that trigger means.
  byId('btn-nextcloud-settings')?.addEventListener('click', () => {
    void callbacks.openStudySettingsPanel?.('nextcloud');
  });
  byId('btn-nextcloud-test')?.addEventListener('click', () => void testConnection());
  byId('btn-nextcloud-save')?.addEventListener('click', () => void save());
  byId('btn-nextcloud-clear-password')?.addEventListener('click', () => setClearPasswordRequested(!clearPasswordRequested));
  byId('nextcloud-study-enabled')?.addEventListener('change', renderStatus);
  byId('nextcloud-share-link')?.addEventListener('input', renderStatus);
}

function studySettings() {
  const config = callbacks.getStudyConfig?.() || {};
  const settings = normalizeStudySettings(config.study_settings);
  const nextcloud = settings.plugins?.nextcloud || {};
  const destinationSettings = nextcloud.settings || {};
  return {
    nextcloud_enabled: Boolean(nextcloud.enabled),
    nextcloud_share_link: String(destinationSettings.share_link || '').trim(),
  };
}

/**
 * Fill the Nextcloud fields from the loaded study.
 * Called by the study-settings shell when its Nextcloud panel is shown -
 * this page has no view of its own any more.
 */
export async function refreshNextcloudStudyFields() {
  const settings = studySettings();
  const enabledInput = byId('nextcloud-study-enabled');
  const linkInput = byId('nextcloud-share-link');
  if (enabledInput) enabledInput.checked = settings.nextcloud_enabled;
  if (linkInput) linkInput.value = settings.nextcloud_share_link;
  const passwordInput = byId('nextcloud-password');
  if (passwordInput) passwordInput.value = '';
  setClearPasswordRequested(false);
  hideTestResult();

  try {
    const hardwareConfig = await getJson('/api/hardware-config');
    passwordConfigured = Boolean(hardwareConfig?.nextcloud?.password_configured);
  } catch (error) {
    console.error('[nextcloud] Could not load config:', error);
    passwordConfigured = false;
    callbacks.showToast?.(t('nextcloud.loadFailed', 'Could not load Nextcloud settings'), 'error');
  }
  renderStatus();
}

function currentFormValues() {
  return {
    enabled: Boolean(byId('nextcloud-study-enabled')?.checked),
    shareLink: String(byId('nextcloud-share-link')?.value || '').trim(),
    password: String(byId('nextcloud-password')?.value || ''),
  };
}

/** A share link must look like https://host/s/<token>. */
function isShareLinkShaped(value) {
  return /^https?:\/\/[^\s/]+\/s\/[^\s/?#]+/i.test(value);
}

function renderStatus() {
  const { enabled, shareLink } = currentFormValues();
  const linkShaped = isShareLinkShaped(shareLink);
  const studyName = callbacks.getCurrentStudyName?.() || t('admin.unnamedStudy', 'Untitled study');

  setText('nextcloud-active-study-name', studyName);
  setText('nextcloud-study-status', enabled ? t('nextcloud.on', 'on') : t('nextcloud.off', 'off'));
  setText('nextcloud-study-hint', enabled
    ? t('nextcloud.onHint', 'Completed sessions of this study are copied to Nextcloud.')
    : t('nextcloud.offHint', 'This study does not upload anything to Nextcloud.'));

  setText('nextcloud-link-status', !shareLink
    ? t('nextcloud.linkMissing', 'missing')
    : linkShaped ? t('nextcloud.linkSet', 'set') : t('nextcloud.linkUnclear', 'check link'));
  setText('nextcloud-link-hint', !shareLink
    ? t('nextcloud.linkMissingHint', 'Without a share link nothing can be uploaded.')
    : linkShaped
      ? t('nextcloud.linkSetHint', 'The link has the expected shape. Use "Test connection" to be sure.')
      : t('nextcloud.linkUnclearHint', 'Expected something like https://cloud.example.com/s/AbCdEf123.'));

  setText('nextcloud-password-status', passwordConfigured
    ? t('nextcloud.passwordStored', 'stored')
    : t('nextcloud.passwordNone', 'none'));
  setText('nextcloud-password-hint', passwordConfigured
    ? t('nextcloud.passwordStoredHint', 'A password is stored on this computer. Leave the field empty to keep it.')
    : t('nextcloud.passwordNoneHint', 'Only needed for password-protected shares.'));
  setText('nextcloud-password-state', clearPasswordRequested
    ? t('nextcloud.clearPasswordPending', 'The stored password will be removed when you save.')
    : passwordConfigured
      ? t('nextcloud.passwordKeepHint', 'Leave empty to keep the stored password.')
      : '');

  renderSteps({ enabled, linkShaped });
}

function renderSteps({ enabled, linkShaped }) {
  const ready = t('nextcloud.stepReady', 'ready');
  const missing = t('nextcloud.stepMissing', 'missing');
  // Step 1 happens inside Nextcloud, which cannot report back here.
  setStepState('nextcloud-step-1-state', 'optional', t('nextcloud.stepExternal', 'in Nextcloud'));
  setStepState('nextcloud-step-2-state', linkShaped ? 'ready' : 'missing', linkShaped ? ready : missing);
  setStepState(
    'nextcloud-step-3-state',
    'optional',
    passwordConfigured ? t('nextcloud.passwordStored', 'stored') : t('nextcloud.stepOptional', 'optional'),
  );
  setStepState('nextcloud-step-4-state', enabled && linkShaped ? 'ready' : 'missing', enabled && linkShaped ? ready : missing);
}

async function testConnection() {
  const { shareLink, password } = currentFormValues();
  const icon = byId('nextcloud-test-icon');
  hideTestResult();
  if (icon) icon.className = 'iconoir-refresh';

  await withBusyButton('btn-nextcloud-test', t('nextcloud.testing', 'Testing...'), async () => {
    try {
      // Send the password only when the operator typed one, so an empty field
      // means "use the stored password" instead of "no password".
      const payload = { share_link: shareLink };
      if (password) payload.password = password;
      const result = await postJson('/api/nextcloud/test', payload);
      renderTestResult('nextcloud-test-result', result, {
        fallbackErrorLabel: t('nextcloud.testFailed', 'Connection failed'),
      });
      if (icon) icon.className = result.ok ? 'iconoir-ev-plug' : 'iconoir-ev-plug-xmark';
    } catch (error) {
      console.error('[nextcloud] Test failed:', error);
      renderTestResult('nextcloud-test-result', { ok: false, error: error.message }, {
        fallbackErrorLabel: t('nextcloud.testFailed', 'Connection failed'),
      });
      if (icon) icon.className = 'iconoir-ev-plug-xmark';
    }
  });
}

async function save() {
  const { enabled, shareLink, password } = currentFormValues();

  await withBusyButton('btn-nextcloud-save', t('nextcloud.saving', 'Saving...'), async () => {
    try {
      if (password || clearPasswordRequested) {
        const hardwareConfig = await getJson('/api/hardware-config');
        hardwareConfig.nextcloud = {
          ...(hardwareConfig.nextcloud || {}),
          ...(password ? { password } : {}),
          ...(clearPasswordRequested ? { clear_password: true } : {}),
        };
        await postJson('/api/hardware-config', hardwareConfig);
        passwordConfigured = clearPasswordRequested ? false : true;
      }

      const current = normalizeStudySettings(callbacks.getStudyConfig?.().study_settings);
      const plugins = { ...current.plugins };
      const previous = plugins.nextcloud || {};
      plugins.nextcloud = {
        enabled,
        required: false,
        settings: { ...(previous.settings || {}), share_link: shareLink },
      };
      callbacks.setStudySettings?.({ ...current, plugins });
      const saved = await callbacks.saveStudyConfig?.({
        successMessage: t('nextcloud.savedFull', 'Study including Nextcloud settings saved.'),
      });
      if (saved === false) return;

      const passwordInput = byId('nextcloud-password');
      if (passwordInput) passwordInput.value = '';
      setClearPasswordRequested(false);
      callbacks.showToast?.(t('nextcloud.saved', 'Nextcloud settings saved'), 'success');
      renderStatus();
    } catch (error) {
      console.error('[nextcloud] Save failed:', error);
      callbacks.showToast?.(t('nextcloud.saveFailed', 'Nextcloud save failed'), 'error');
    }
  });
}

function setClearPasswordRequested(value) {
  clearPasswordRequested = Boolean(value);
  const button = byId('btn-nextcloud-clear-password');
  if (button) button.classList.toggle('is-armed', clearPasswordRequested);
  renderStatus();
}

function hideTestResult() {
  const container = byId('nextcloud-test-result');
  if (container) {
    container.hidden = true;
    container.innerHTML = '';
  }
}
