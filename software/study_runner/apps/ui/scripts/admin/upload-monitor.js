/**
 * Finalization notice: the corner widget that says a session is being
 * finalized, or that one needs attention.
 *
 * Local commit, recording freeze, XDF validation/merge, statistics and every
 * destination are one durable state machine. Clicking the notice opens that
 * session's detail view, whose progress rail shows every step with retry
 * buttons (session-progress-rail.js), and marks the notice as seen: it stays
 * away until something new happens to the job (a new failure, a finished
 * run). Legacy upload jobs remain a fallback for sessions created by an older
 * Study Runner version.
 */
import { getJson, postJson } from '../shared/api-client.js';
import { t } from '../shared/i18n.js';
import {
  finalizationProgress,
  pickFinalizationFocus,
} from '../shared/finalization-view-model.js';

const POLL_INTERVAL_MS = 3000;
let callbacks = {};
let initialized = false;
let widget = null;
let pollInFlight = false;
let knownFinalizationStatuses = null;
let knownCompletionIds = null;
let focusSession = null;
let latestFinalizationJobs = new Map();
const knownFinalizationSessionIds = new Set();
// Legacy sessions have no server-side acknowledgement; remember them here.
const seenLegacySignatures = new Map();

export function initializeUploadMonitor(options = {}) {
  callbacks = options;
  if (initialized) return;
  initialized = true;

  widget = buildWidget();
  document.body.appendChild(widget);

  void poll();
  setInterval(() => void poll(), POLL_INTERVAL_MS);
}

function buildWidget() {
  const el = document.createElement('button');
  el.type = 'button';
  el.className = 'upload-widget';
  el.hidden = true;
  el.innerHTML = `
    <i class="iconoir-refresh" data-finalization-widget-icon></i>
    <div class="upload-widget-body">
      <div class="upload-widget-title" id="upload-widget-title" aria-live="polite"></div>
      <div class="upload-progress" id="upload-widget-progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"><div class="upload-progress-fill" id="upload-widget-fill"></div></div>
    </div>
  `;
  el.addEventListener('click', () => {
    if (focusSession) void openFocusedSession(focusSession);
  });
  return el;
}

async function poll() {
  if (pollInFlight) return;
  pollInFlight = true;
  try {
    const [finalizationStatus, uploadStatus, completedSessions] = await Promise.all([
      loadStatus('/api/finalization/status', 'finalization'),
      loadStatus('/api/uploads/status', 'uploads'),
      loadStatus('/api/admin/sessions', 'completed sessions'),
    ]);
    if (!finalizationStatus && !uploadStatus && !completedSessions) return;

    const finalizationJobs = (Array.isArray(finalizationStatus?.jobs) ? finalizationStatus.jobs : [])
      .map((job) => ({ ...job, is_finalization: true }));
    latestFinalizationJobs = new Map(finalizationJobs.map((job) => [job.job_id, job]));
    finalizationJobs.forEach((job) => knownFinalizationSessionIds.add(sessionIdentityKey(job)));
    const uploadSessions = (Array.isArray(uploadStatus?.sessions) ? uploadStatus.sessions : [])
      .filter((session) => !knownFinalizationSessionIds.has(sessionIdentityKey(session)));
    const localSessions = (Array.isArray(completedSessions) ? completedSessions : [])
      .filter((session) => !knownFinalizationSessionIds.has(sessionIdentityKey(session)));

    if (knownFinalizationStatuses === null && finalizationStatus) {
      // Quiet baseline: reopening the admin page must not replay history.
      knownFinalizationStatuses = new Map(finalizationJobs.map((job) => [job.job_id, job.status]));
    } else if (finalizationStatus) {
      surfaceFinalizationChanges(finalizationJobs);
    }
    if (knownCompletionIds === null && completedSessions) {
      knownCompletionIds = new Set(localSessions.map(completionId));
    } else if (completedSessions) {
      localSessions
        .filter((session) => !knownCompletionIds.has(completionId(session)))
        .forEach((session) => callbacks.onLocalCompletion?.(session));
      localSessions.forEach((session) => knownCompletionIds.add(completionId(session)));
    }

    focusSession = pickFinalizationFocus(finalizationJobs) || pickLegacyFocus(uploadSessions, localSessions);
    renderWidget();
  } finally {
    pollInFlight = false;
  }
}

async function loadStatus(url, label) {
  try {
    return await getJson(url, { timeoutMs: 4000 });
  } catch (error) {
    console.error(`[finalization] Could not load ${label}:`, error);
    return null;
  }
}

function surfaceFinalizationChanges(jobs) {
  jobs.forEach((job) => {
    const before = knownFinalizationStatuses.get(job.job_id);
    if (before && before !== 'attention_required' && job.status === 'attention_required') {
      callbacks.showToast?.(
        t('finalization.toastAttention', 'A session needs attention: {study} / {participant}')
          .replace('{study}', job.study_id || '-')
          .replace('{participant}', job.participant_id || '-'),
        'warning',
      );
    }
    if (before && !['completed', 'completed_degraded'].includes(before)
      && ['completed', 'completed_degraded'].includes(job.status)) {
      callbacks.onLocalCompletion?.(job);
    }
    knownFinalizationStatuses.set(job.job_id, job.status);
  });
}

function pickLegacyFocus(uploadSessions, localSessions) {
  const localById = new Map(localSessions.map((session) => [session.session_id, session]));
  const session = uploadSessions.find((candidate) => (
    (candidate.jobs || []).some((job) => job.status !== 'done')
    && seenLegacySignatures.get(candidate.session_id) !== legacySignature(candidate)
  ));
  if (!session) return null;
  return { ...session, session_path: session.session_path || localById.get(session.session_id)?.session_path || '' };
}

function legacySignature(session) {
  return (session.jobs || []).map((job) => `${job.job_id}:${job.status}`).sort().join('|');
}

/** Go to the session and mark its notice as seen until something new happens. */
async function openFocusedSession(session) {
  const folder = String(session.session_path || '').split(/[\\/]/).filter(Boolean).pop() || '';
  if (session.is_finalization) {
    try {
      const response = await postJson(`/api/finalization/${encodeURIComponent(session.job_id)}/acknowledge`, {});
      if (response?.job) {
        latestFinalizationJobs.set(response.job.job_id, { ...response.job, is_finalization: true });
      }
    } catch (error) {
      console.error('[finalization] Could not mark the notice as seen:', error);
    }
    focusSession = pickFinalizationFocus([...latestFinalizationJobs.values()]);
  } else {
    seenLegacySignatures.set(session.session_id, legacySignature(session));
    focusSession = null;
  }
  renderWidget();
  await callbacks.onOpenSession?.({
    study_id: session.study_id,
    participant_id: session.participant_id,
    session_id: session.session_id,
    session_folder: folder,
  });
}

function completionId(session) {
  return [session.study_id || '', session.participant_id || '', session.session_id || '', session.result_file || ''].join('::');
}

function sessionIdentityKey(session) {
  return [session.study_id || '', session.participant_id || '', session.session_id || ''].join('::');
}

function renderWidget() {
  if (!widget) return;
  if (!focusSession) {
    widget.hidden = true;
    return;
  }

  let percent;
  let needsAttention;
  let title;
  if (focusSession.is_finalization) {
    const progress = finalizationProgress(focusSession);
    percent = progress.percent;
    needsAttention = focusSession.status === 'attention_required'
      || (focusSession.steps || []).some((step) => ['failed', 'retrying'].includes(step.status));
    const uploadOnly = focusSession.status === 'completed' && (focusSession.upload_failures || []).length > 0;
    title = uploadOnly
      ? t('finalization.widgetUploadFailed', 'Saved locally - an upload failed')
      : needsAttention
      ? t('finalization.widgetAttention', 'Finalization needs attention')
      : t('finalization.widgetProgress', 'Finalizing {done}/{total}')
        .replace('{done}', String(progress.done)).replace('{total}', String(progress.total));
  } else {
    const jobs = focusSession.jobs || [];
    const done = jobs.filter((job) => job.status === 'done').length;
    const total = jobs.length || 1;
    percent = Math.round((done / total) * 100);
    needsAttention = jobs.some((job) => job.status === 'failed');
    title = needsAttention
      ? t('uploads.widgetFailed', 'Upload needs attention')
      : t('uploads.widgetProgress', 'Uploading {done}/{total}').replace('{done}', String(done)).replace('{total}', String(total));
  }

  widget.hidden = false;
  widget.classList.toggle('upload-widget--failed', needsAttention);
  widget.setAttribute('aria-label', `${title} – ${t('finalization.widgetOpen', 'open the session')}`);
  const iconEl = widget.querySelector('[data-finalization-widget-icon]');
  if (iconEl) iconEl.className = needsAttention ? 'iconoir-warning-triangle' : 'iconoir-refresh';
  const titleEl = widget.querySelector('#upload-widget-title');
  if (titleEl) titleEl.textContent = title;
  const progressEl = widget.querySelector('#upload-widget-progress');
  if (progressEl) {
    progressEl.setAttribute('aria-label', title);
    progressEl.setAttribute('aria-valuenow', String(percent));
  }
  const fillEl = widget.querySelector('#upload-widget-fill');
  if (fillEl) fillEl.style.width = `${percent}%`;
}
