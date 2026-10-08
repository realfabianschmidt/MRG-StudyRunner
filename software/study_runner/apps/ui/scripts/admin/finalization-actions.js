/**
 * Operator actions on a durable finalization job, shared by the live
 * finalization notice and the session detail view so both behave the same.
 */
import { getJson, postJson } from '../shared/api-client.js';
import { t } from '../shared/i18n.js';
import { escapeHtml } from '../shared/dom-utils.js';
import { createModal } from '../shared/modal.js';

const jobUrl = (jobId, action) => `/api/finalization/${encodeURIComponent(jobId)}/${action}`;

/**
 * Ask which target to retry with when the destination's settings moved
 * since the session ended (a changed Notion page, for example).
 *
 * Resolves "current", "snapshot", or null if the operator cancelled.
 */
function confirmRetryTarget(fields) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      modal.destroy();
      resolve(value);
    };
    const modal = createModal({
      title: t('finalization.retryTargetTitle', 'The upload target changed'),
      closeLabel: t('finalization.retryTargetCancel', 'Cancel'),
      onClose: () => finish(null),
    });
    const rows = fields.map((field) => `
      <tr>
        <td>${escapeHtml(t(field.label_key, field.name))}</td>
        <td class="retry-target-then">${escapeHtml(field.snapshot || '—')}</td>
        <td class="retry-target-now">${escapeHtml(field.current || '—')}</td>
      </tr>`).join('');
    modal.body.innerHTML = `
      <p class="settings-hint">${escapeHtml(t(
        'finalization.retryTargetHint',
        'This session finished with different settings than the study has now. Choose which one the retry should use.',
      ))}</p>
      <table class="retry-target-table">
        <thead><tr>
          <th>${escapeHtml(t('finalization.retryTargetField', 'Setting'))}</th>
          <th>${escapeHtml(t('finalization.retryTargetThen', 'Then'))}</th>
          <th>${escapeHtml(t('finalization.retryTargetNow', 'Now'))}</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <div class="dashboard-actions confirm-modal-actions">
        <button type="button" class="btn-secondary" data-retry-target="snapshot">
          ${escapeHtml(t('finalization.retryTargetKeepOriginal', 'Keep original target'))}
        </button>
        <button type="button" class="btn-primary" data-retry-target="current">
          ${escapeHtml(t('finalization.retryTargetUseCurrent', 'Use current target'))}
        </button>
      </div>`;
    modal.body.querySelectorAll('[data-retry-target]').forEach((button) => {
      button.addEventListener('click', () => finish(button.dataset.retryTarget));
    });
    modal.open();
  });
}

export async function retryFinalizationStep(jobId, stepKey, { showToast, onDone } = {}) {
  let target = 'snapshot';
  try {
    const comparison = await getJson(jobUrl(jobId, 'retry-target') + (
      stepKey ? `?step=${encodeURIComponent(stepKey)}` : ''
    ));
    if (comparison?.differs && Array.isArray(comparison.fields) && comparison.fields.length) {
      target = await confirmRetryTarget(comparison.fields);
      if (!target) return; // the operator cancelled the comparison dialog
    }
  } catch (error) {
    // The comparison is a courtesy, not a precondition: if it cannot be
    // loaded, retry with the original target exactly as before.
    console.error('[finalization] Could not compare the retry target:', error);
  }
  try {
    await postJson(jobUrl(jobId, 'retry'), { step: stepKey, target });
    showToast?.(t('finalization.retryStarted', 'Trying again…'), 'success');
  } catch (error) {
    console.error('[finalization] Step retry failed:', error);
    showToast?.(t('finalization.retryFailed', 'The finalization step could not be retried'), 'error');
  }
  await onDone?.();
}

export async function confirmDegradedFinalization(jobId, reason, { showToast, onDone } = {}) {
  const explanation = String(reason || '').trim();
  if (!explanation) return;
  try {
    const response = await postJson(jobUrl(jobId, 'confirm-degraded'), { reason: explanation, confirmed_by: 'admin' });
    const continued = response?.job?.degraded_confirmation?.continued_processing;
    showToast?.(
      continued
        ? t('finalization.continueStarted', 'Processing continues')
        : t('finalization.degradedConfirmed', 'Degraded completion confirmed'),
      'success',
    );
  } catch (error) {
    console.error('[finalization] Degraded confirmation failed:', error);
    showToast?.(t('finalization.degradedFailed', 'Degraded completion could not be confirmed'), 'error');
  }
  await onDone?.();
}

export async function continueFinalization(jobId, { showToast, onDone } = {}) {
  try {
    await postJson(jobUrl(jobId, 'continue'), { confirmed_by: 'admin' });
    showToast?.(t('finalization.continueStarted', 'Processing continues'), 'success');
  } catch (error) {
    console.error('[finalization] Continue processing failed:', error);
    showToast?.(error?.message || t('finalization.continueFailed', 'Processing could not be continued'), 'error');
  }
  await onDone?.();
}

export async function openFinalizationFolder(jobId, { showToast } = {}) {
  try {
    await postJson(jobUrl(jobId, 'open-folder'), {});
  } catch (error) {
    console.error('[finalization] Could not open session folder:', error);
    showToast?.(t('finalization.openFolderFailed', 'Could not open the session folder'), 'error');
  }
}
