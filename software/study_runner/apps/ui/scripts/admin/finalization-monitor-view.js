/** Render one durable finalization job; polling and network actions stay in the monitor. */
import { t } from '../shared/i18n.js';
import { escapeHtml, formatDateTime, formatFileSize } from '../shared/dom-utils.js';
import {
  finalizationProgress,
  finalizationStepLabel,
} from '../shared/finalization-view-model.js';

export function renderFinalizationJob(container, job, actions = {}) {
  const previousReason = container.querySelector('[data-degraded-reason]');
  const reasonDraft = previousReason?.value || '';
  const restoreReasonFocus = previousReason === document.activeElement;
  const selectionStart = previousReason?.selectionStart;
  const selectionEnd = previousReason?.selectionEnd;
  const progress = finalizationProgress(job);
  const warnings = Array.isArray(job.warnings) ? job.warnings.filter(Boolean) : [];
  const artifacts = Array.isArray(job.artifacts) ? job.artifacts : [];
  const status = finalizationStatusDisplay(job.status);
  const progressLabel = t('finalization.progress', '{done} of {total} steps completed')
    .replace('{done}', String(progress.done)).replace('{total}', String(progress.total));

  container.innerHTML = `
    <div class="finalization-summary finalization-summary--${escapeHtml(status.tone)}">
      <div>
        <div class="finalization-status-label"><i class="${status.icon}"></i> ${escapeHtml(status.text)}</div>
        <div class="upload-job-status">${escapeHtml(t('finalization.sessionMeta', 'Session {session} · started {time}')
          .replace('{session}', job.session_id || '-')
          .replace('{time}', formatDateTime(job.created_at)))}</div>
      </div>
      <span class="finalization-quality">${escapeHtml(qualityLabel(job.quality_status))}</span>
    </div>
    <div class="finalization-progress-row">
      <span>${escapeHtml(progressLabel)}</span>
      <strong>${progress.percent}%</strong>
    </div>
    <div class="upload-progress finalization-progress" role="progressbar" aria-label="${escapeHtml(progressLabel)}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${progress.percent}"><div class="upload-progress-fill" style="width: ${progress.percent}%"></div></div>
    ${warnings.length ? renderWarnings(warnings) : ''}
    <div class="upload-job-list finalization-step-list">
      ${(job.steps || []).map(renderFinalizationStep).join('')}
    </div>
    <section class="finalization-artifacts">
      <div class="finalization-section-title">${escapeHtml(t('finalization.artifacts', 'Artifacts'))}</div>
      ${artifacts.length
        ? `<div class="upload-file-list">${artifacts.map(renderArtifact).join('')}</div>`
        : `<p class="settings-hint">${escapeHtml(t('finalization.noArtifacts', 'Artifacts will appear after they have been published locally.'))}</p>`}
    </section>
    ${job.status === 'attention_required' ? renderDegradedConfirmation(reasonDraft) : ''}
    <div class="dashboard-actions finalization-actions">
      <button class="btn-secondary" type="button" data-action="open-session-folder">
        <i class="iconoir-folder"></i> ${escapeHtml(t('finalization.openSessionFolder', 'Open session folder'))}
      </button>
    </div>
  `;

  container.querySelectorAll('[data-retry-step]').forEach((button) => {
    bindAsyncAction(button, () => actions.onRetry?.(job.job_id, button.dataset.retryStep));
  });
  bindAsyncAction(
    container.querySelector('[data-action="open-session-folder"]'),
    () => actions.onOpenFolder?.(job.job_id),
  );

  const reasonInput = container.querySelector('[data-degraded-reason]');
  const confirmButton = container.querySelector('[data-confirm-degraded]');
  if (reasonInput && confirmButton) {
    reasonInput.value = reasonDraft;
    const updateDisabled = () => { confirmButton.disabled = !reasonInput.value.trim(); };
    reasonInput.addEventListener('input', updateDisabled);
    updateDisabled();
    bindAsyncAction(confirmButton, () => actions.onConfirmDegraded?.(job.job_id, reasonInput.value));
    if (restoreReasonFocus) {
      reasonInput.focus();
      if (Number.isInteger(selectionStart) && Number.isInteger(selectionEnd)) {
        reasonInput.setSelectionRange?.(selectionStart, selectionEnd);
      }
    }
  }
}

function renderWarnings(warnings) {
  return `
    <div class="finalization-warnings" role="status">
      <div class="finalization-section-title"><i class="iconoir-warning-triangle"></i> ${escapeHtml(t('finalization.qualityWarnings', 'Quality warnings'))}</div>
      <ul>${warnings.map((warning) => `<li>${escapeHtml(String(warning))}</li>`).join('')}</ul>
    </div>`;
}

function renderFinalizationStep(step) {
  const status = stepStatusDisplay(step.status);
  const label = finalizationStepLabel(step, t);
  const retryButton = ['failed', 'retrying'].includes(step.status)
    ? `<button class="btn-icon-only" type="button" data-retry-step="${escapeHtml(step.key)}" title="${escapeHtml(t('finalization.retryStep', 'Retry this step'))}" aria-label="${escapeHtml(t('finalization.retryStep', 'Retry this step'))}"><i class="iconoir-refresh"></i></button>`
    : '';
  const errorLine = step.last_error
    ? `<div class="upload-job-error">${escapeHtml(step.last_error)}</div>`
    : '';
  return `
    <div class="upload-job-row">
      <i class="${status.icon}"></i>
      <div class="upload-job-body">
        <div class="upload-job-label">${escapeHtml(label)}</div>
        <div class="upload-job-status">${escapeHtml(status.text)}</div>
        ${errorLine}${renderStepDetails(step)}
      </div>
      ${retryButton}
    </div>`;
}

function renderStepDetails(step) {
  const details = step.details && typeof step.details === 'object' ? { ...step.details } : {};
  if (step.blocked_by) details.blocked_by = step.blocked_by;
  if (step.next_attempt_at) details.next_attempt_at = step.next_attempt_at;
  const attempts = Number(step.attempts || 0);
  if (!Object.keys(details).length && attempts <= 1) return '';
  let serialized = '';
  if (Object.keys(details).length) {
    try {
      serialized = JSON.stringify(details, null, 2);
    } catch (error) {
      serialized = String(details);
    }
  }
  const attemptText = attempts > 1
    ? t('finalization.attempts', '{count} attempts').replace('{count}', String(attempts))
    : '';
  return `
    <details class="finalization-step-details">
      <summary>${escapeHtml(t('finalization.details', 'Details'))}${attemptText ? ` · ${escapeHtml(attemptText)}` : ''}</summary>
      ${serialized ? `<pre>${escapeHtml(serialized)}</pre>` : ''}
    </details>`;
}

function renderArtifact(artifact) {
  const path = String(artifact.path || '');
  const size = Number(artifact.size_bytes);
  const suffix = Number.isFinite(size) ? ` · ${formatFileSize(size)}` : '';
  const remote = artifact.remote_verified ? ` · ${t('finalization.remoteVerified', 'remote verified')}` : '';
  return `<span class="upload-file-chip" title="${escapeHtml(`${artifact.role || ''}${suffix}${remote}`)}">${escapeHtml(path)}</span>`;
}

function renderDegradedConfirmation(reasonDraft) {
  return `
    <section class="finalization-degraded">
      <div class="finalization-section-title">${escapeHtml(t('finalization.degradedTitle', 'Confirm degraded completion'))}</div>
      <p class="settings-hint">${escapeHtml(t('finalization.degradedHint', 'Only confirm after reviewing the failure. Source files remain local and the quality warning is retained.'))}</p>
      <div class="field">
        <label for="finalization-degraded-reason">${escapeHtml(t('finalization.degradedReason', 'Reason'))}</label>
        <textarea id="finalization-degraded-reason" data-degraded-reason rows="3" maxlength="1000" placeholder="${escapeHtml(t('finalization.degradedPlaceholder', 'Document the accepted data loss or quality limitation.'))}">${escapeHtml(reasonDraft)}</textarea>
      </div>
      <button class="btn-secondary" type="button" data-confirm-degraded>
        <i class="iconoir-check-circle"></i> ${escapeHtml(t('finalization.confirmDegraded', 'Confirm degraded completion'))}
      </button>
    </section>`;
}

function finalizationStatusDisplay(status) {
  switch (status) {
    case 'completed':
      return { icon: 'iconoir-check-circle upload-job-icon--done', text: t('finalization.status.completed', 'Completed'), tone: 'done' };
    case 'completed_degraded':
      return { icon: 'iconoir-warning-triangle upload-job-icon--failed', text: t('finalization.status.completedDegraded', 'Completed with quality warning'), tone: 'attention' };
    case 'attention_required':
      return { icon: 'iconoir-warning-triangle upload-job-icon--failed', text: t('finalization.status.attentionRequired', 'Attention required'), tone: 'attention' };
    case 'running':
      return { icon: 'iconoir-refresh upload-job-icon--running', text: t('finalization.status.running', 'Finalizing ...'), tone: 'running' };
    default:
      return { icon: 'iconoir-clock upload-job-icon--queued', text: t('finalization.status.queued', 'Queued'), tone: 'queued' };
  }
}

function stepStatusDisplay(status) {
  switch (status) {
    case 'done': return { icon: 'iconoir-check-circle upload-job-icon--done', text: t('finalization.stepStatus.done', 'Done') };
    case 'running': return { icon: 'iconoir-refresh upload-job-icon--running', text: t('finalization.stepStatus.running', 'Running ...') };
    case 'retrying': return { icon: 'iconoir-refresh upload-job-icon--failed', text: t('finalization.stepStatus.retrying', 'Retry scheduled') };
    case 'failed': return { icon: 'iconoir-xmark-circle upload-job-icon--failed', text: t('finalization.stepStatus.failed', 'Failed') };
    case 'skipped': return { icon: 'iconoir-minus-circle upload-job-icon--queued', text: t('finalization.stepStatus.skipped', 'Skipped') };
    default: return { icon: 'iconoir-clock upload-job-icon--queued', text: t('finalization.stepStatus.pending', 'Pending') };
  }
}

function qualityLabel(status) {
  const labels = {
    valid: t('finalization.quality.valid', 'Quality: valid'),
    degraded: t('finalization.quality.degraded', 'Quality: degraded'),
    invalid: t('finalization.quality.invalid', 'Quality: invalid'),
    pending: t('finalization.quality.pending', 'Quality: pending'),
  };
  return labels[status] || t('finalization.quality.notApplicable', 'Quality: not applicable');
}

function bindAsyncAction(button, action) {
  if (!button || typeof action !== 'function') return;
  button.addEventListener('click', async () => {
    button.disabled = true;
    try {
      await action();
    } finally {
      if (button.isConnected) button.disabled = false;
    }
  });
}
