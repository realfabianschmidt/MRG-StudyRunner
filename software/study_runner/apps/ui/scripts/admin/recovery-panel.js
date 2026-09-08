/**
 * Hub banner for study sessions orphaned by a crash or closed tab.
 *
 * Stays hidden whenever there is nothing to recover. Each candidate offers
 * "Finalize" (write it into saved_results as a normal, browsable session)
 * or "Discard" (archive it, never delete) - both call the same confirm()
 * pattern already used for deleting a study elsewhere in this file.
 */
import { getJson, postJson } from '../shared/api-client.js';
import { t } from '../shared/i18n.js';
import { byId, escapeHtml, formatDateTime } from '../shared/dom-utils.js';

let callbacks = {};
let initialized = false;

export function initializeRecoveryPanel(options = {}) {
  callbacks = options;
  if (initialized) return;
  initialized = true;
}

export async function loadRecoveryCandidates() {
  const banner = byId('recovery-banner');
  const list = byId('recovery-list');
  if (!banner || !list) return;

  let candidates = [];
  try {
    const response = await getJson('/api/admin/recovery');
    candidates = Array.isArray(response?.candidates) ? response.candidates : [];
  } catch (error) {
    console.error('[recovery] Could not load recovery candidates:', error);
    banner.hidden = true;
    return;
  }

  if (!candidates.length) {
    banner.hidden = true;
    list.innerHTML = '';
    return;
  }

  banner.hidden = false;
  list.innerHTML = candidates.map(renderCandidate).join('');

  list.querySelectorAll('[data-action="finalize"]').forEach((button) => {
    button.addEventListener('click', () => void finalizeCandidate(button.dataset.recoveryId));
  });
  list.querySelectorAll('[data-action="discard"]').forEach((button) => {
    button.addEventListener('click', () => void discardCandidate(button.dataset.recoveryId));
  });
}

function renderCandidate(candidate) {
  const sensors = candidate.sensors_flushed?.length
    ? candidate.sensors_flushed.join(', ')
    : t('recovery.noSensorData', 'no sensor data saved');
  const meta = t('recovery.itemMeta', '{time} - {count} answers - {sensors}')
    .replace('{time}', formatDateTime(candidate.last_activity))
    .replace('{count}', String(candidate.answers_count ?? 0))
    .replace('{sensors}', sensors);

  return `
    <div class="recovery-item" data-recovery-id="${escapeHtml(candidate.recovery_id)}">
      <div class="recovery-item-main">
        <div class="recovery-item-title">${escapeHtml(candidate.study_id)} / ${escapeHtml(candidate.participant_hint || '?')}</div>
        <div class="recovery-item-meta">${escapeHtml(meta)}</div>
      </div>
      <div class="recovery-item-actions">
        <button class="btn-secondary" type="button" data-action="discard" data-recovery-id="${escapeHtml(candidate.recovery_id)}">
          <i class="iconoir-trash"></i> ${escapeHtml(t('recovery.discard', 'Discard'))}
        </button>
        <button class="btn-primary" type="button" data-action="finalize" data-recovery-id="${escapeHtml(candidate.recovery_id)}">
          <i class="iconoir-check"></i> ${escapeHtml(t('recovery.finalize', 'Finalize'))}
        </button>
      </div>
    </div>`;
}

async function finalizeCandidate(recoveryId) {
  if (!confirm(t('recovery.finalizeConfirm', 'Save this interrupted session as a normal result? You can browse it under Completed studies afterwards.'))) {
    return;
  }
  try {
    await postJson('/api/admin/recovery/finalize', { recovery_id: recoveryId });
    callbacks.showToast?.(t('recovery.finalized', 'Session saved'), 'success');
    await loadRecoveryCandidates();
    await callbacks.onFinalized?.();
  } catch (error) {
    console.error('[recovery] Finalize failed:', error);
    callbacks.showToast?.(t('recovery.finalizeFailed', 'Could not save this session'), 'error');
  }
}

async function discardCandidate(recoveryId) {
  if (!confirm(t('recovery.discardConfirm', 'Discard this interrupted session? Its files are kept but will no longer appear here.'))) {
    return;
  }
  try {
    await postJson('/api/admin/recovery/discard', { recovery_id: recoveryId });
    callbacks.showToast?.(t('recovery.discarded', 'Session discarded'), 'success');
    await loadRecoveryCandidates();
  } catch (error) {
    console.error('[recovery] Discard failed:', error);
    callbacks.showToast?.(t('recovery.discardFailed', 'Could not discard this session'), 'error');
  }
}
