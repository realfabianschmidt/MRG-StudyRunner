/**
 * Operator actions on a durable finalization job, shared by the live
 * finalization notice and the session detail view so both behave the same.
 */
import { postJson } from '../shared/api-client.js';
import { t } from '../shared/i18n.js';

const jobUrl = (jobId, action) => `/api/finalization/${encodeURIComponent(jobId)}/${action}`;

export async function retryFinalizationStep(jobId, stepKey, { showToast, onDone } = {}) {
  try {
    await postJson(jobUrl(jobId, 'retry'), { step: stepKey });
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
    await postJson(jobUrl(jobId, 'confirm-degraded'), { reason: explanation, confirmed_by: 'admin' });
    showToast?.(t('finalization.degradedConfirmed', 'Degraded completion confirmed'), 'success');
  } catch (error) {
    console.error('[finalization] Degraded confirmation failed:', error);
    showToast?.(t('finalization.degradedFailed', 'Degraded completion could not be confirmed'), 'error');
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
