/**
 * Everything that happened to one session, left to right: started, ended,
 * then every durable finalization step (save, merge, statistics, each upload
 * destination, clean-up). Shown at the top of the session detail view, which
 * is also where a click on the finalization notice leads.
 *
 * Steps come from the finalization job as-is, so a new destination plugin
 * appears here without any change to this file.
 */
import { t } from '../shared/i18n.js';
import { escapeHtml, formatDateTime } from '../shared/dom-utils.js';
import { finalizationStepLabel } from '../shared/finalization-view-model.js';

const STEP_ICONS = {
  done: 'iconoir-check-circle',
  running: 'iconoir-refresh',
  retrying: 'iconoir-refresh',
  failed: 'iconoir-xmark-circle',
  skipped: 'iconoir-minus-circle',
  pending: 'iconoir-clock',
};

export function renderSessionProgressRail(container, session, job, actions = {}) {
  if (!container) return;
  const previousReason = container.querySelector('[data-degraded-reason]');
  const reasonDraft = previousReason?.value || '';
  // The rail refreshes while work is pending; keep a reason being typed.
  const restoreFocus = previousReason && previousReason === document.activeElement;
  const selection = [previousReason?.selectionStart, previousReason?.selectionEnd];
  const result = session?.result || {};
  const steps = [
    {
      key: 'study_started',
      label: t('sessions.rail.started', 'Study started'),
      status: result.timestamp_start ? 'done' : 'pending',
      hint: result.timestamp_start ? formatDateTime(result.timestamp_start) : '',
    },
    {
      key: 'study_ended',
      label: t('sessions.rail.ended', 'Study ended'),
      status: result.timestamp_end ? 'done' : 'pending',
      hint: result.timestamp_end ? formatDateTime(result.timestamp_end) : '',
    },
    ...(job?.steps || []).map((step) => ({
      key: step.key,
      label: finalizationStepLabel(step, t),
      status: step.status || 'pending',
      hint: stepHint(step),
      error: step.last_error || '',
      retryable: ['failed', 'retrying'].includes(step.status),
    })),
  ];
  if (!job) {
    steps.push({
      key: 'saved',
      label: t('sessions.rail.saved', 'Saved locally'),
      status: 'done',
      hint: session?.saved_at ? formatDateTime(session.saved_at) : '',
    });
  }

  const problems = steps.filter((step) => step.error || step.retryable);
  const warnings = Array.isArray(job?.warnings) ? job.warnings.filter(Boolean) : [];

  container.innerHTML = `
    <ol class="progress-rail" aria-label="${escapeHtml(t('sessions.rail.title', 'Session progress'))}">
      ${steps.map(renderStep).join('')}
    </ol>
    ${problems.length ? `<div class="progress-rail-problems">${problems.map(renderProblem).join('')}</div>` : ''}
    ${warnings.length ? `
      <div class="finalization-warnings" role="status">
        <div class="finalization-section-title"><i class="iconoir-warning-triangle"></i> ${escapeHtml(t('finalization.qualityWarnings', 'Quality warnings'))}</div>
        <ul>${warnings.map((warning) => `<li>${escapeHtml(String(warning))}</li>`).join('')}</ul>
      </div>` : ''}
    ${job?.status === 'attention_required' ? renderDegradedConfirmation(reasonDraft) : ''}
    ${job ? `
      <div class="dashboard-actions finalization-actions">
        <button class="btn-secondary" type="button" data-action="open-session-folder">
          <i class="iconoir-folder"></i> ${escapeHtml(t('finalization.openSessionFolder', 'Open session folder'))}
        </button>
      </div>` : ''}
  `;

  container.querySelectorAll('[data-retry-step]').forEach((button) => {
    bindAsyncAction(button, () => actions.onRetry?.(job.job_id, button.dataset.retryStep));
  });
  bindAsyncAction(container.querySelector('[data-action="open-session-folder"]'), () => actions.onOpenFolder?.(job.job_id));
  const reasonInput = container.querySelector('[data-degraded-reason]');
  const confirmButton = container.querySelector('[data-confirm-degraded]');
  if (reasonInput && confirmButton) {
    reasonInput.value = reasonDraft;
    const updateDisabled = () => { confirmButton.disabled = !reasonInput.value.trim(); };
    reasonInput.addEventListener('input', updateDisabled);
    updateDisabled();
    bindAsyncAction(confirmButton, () => actions.onConfirmDegraded?.(job.job_id, reasonInput.value));
    if (restoreFocus) {
      reasonInput.focus();
      if (selection.every(Number.isInteger)) reasonInput.setSelectionRange?.(...selection);
    }
  }
}

function stepHint(step) {
  if (step.status === 'retrying' && step.next_attempt_at) {
    return t('sessions.rail.nextAttempt', 'next try {time}').replace('{time}', formatDateTime(step.next_attempt_at));
  }
  if (step.completed_at) return formatDateTime(step.completed_at);
  return statusText(step.status);
}

function statusText(status) {
  const labels = {
    done: t('finalization.stepStatus.done', 'Done'),
    running: t('finalization.stepStatus.running', 'Running ...'),
    retrying: t('finalization.stepStatus.retrying', 'Retry scheduled'),
    failed: t('finalization.stepStatus.failed', 'Failed'),
    skipped: t('finalization.stepStatus.skipped', 'Skipped'),
  };
  return labels[status] || t('finalization.stepStatus.pending', 'Pending');
}

function renderStep(step) {
  const status = STEP_ICONS[step.status] ? step.status : 'pending';
  const title = [step.label, statusText(status), step.error].filter(Boolean).join(' · ');
  return `
    <li class="progress-rail-step progress-rail-step--${status}" title="${escapeHtml(title)}">
      <span class="progress-rail-dot"><i class="${STEP_ICONS[status]}"></i></span>
      <span class="progress-rail-label">${escapeHtml(step.label)}</span>
      <span class="progress-rail-hint">${escapeHtml(step.hint || '')}</span>
    </li>`;
}

function renderProblem(step) {
  const retry = step.retryable
    ? `<button class="btn-secondary" type="button" data-retry-step="${escapeHtml(step.key)}"><i class="iconoir-refresh"></i> ${escapeHtml(t('finalization.retryStep', 'Retry this step'))}</button>`
    : '';
  return `
    <div class="progress-rail-problem">
      <div>
        <strong>${escapeHtml(step.label)}</strong> · ${escapeHtml(statusText(step.status))}
        ${step.error ? `<div class="upload-job-error">${escapeHtml(step.error)}</div>` : ''}
      </div>
      ${retry}
    </div>`;
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
