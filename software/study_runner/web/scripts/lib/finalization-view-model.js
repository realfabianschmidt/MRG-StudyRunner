export const TERMINAL_FINALIZATION_STATUSES = new Set(['completed', 'completed_degraded']);

const STEP_COMPLETE = new Set(['done', 'skipped']);

export function finalizationProgress(job) {
  const steps = Array.isArray(job?.steps) ? job.steps : [];
  const total = steps.length;
  const done = steps.filter((step) => STEP_COMPLETE.has(step?.status)).length;
  return {
    done,
    total,
    percent: total ? Math.round((done / total) * 100) : 0,
  };
}

export function isFinalizationActive(job) {
  if (!job?.job_id || job.status === 'completed') return false;
  if (job.status === 'completed_degraded') {
    return (Array.isArray(job.steps) ? job.steps : []).some((step) => (
      (step?.phase === 'publication' || String(step?.key || '').startsWith('publish_'))
      && !STEP_COMPLETE.has(step?.status)
    ));
  }
  return true;
}

export function pickFinalizationFocus(jobs) {
  const active = (Array.isArray(jobs) ? jobs : []).filter(isFinalizationActive);
  const priority = { attention_required: 0, completed_degraded: 1, running: 2, queued: 3 };
  return active.sort((left, right) => {
    const statusOrder = (priority[left.status] ?? 9) - (priority[right.status] ?? 9);
    if (statusOrder) return statusOrder;
    return Number(right.created_epoch || 0) - Number(left.created_epoch || 0);
  })[0] || null;
}

export function retryableSteps(job) {
  return (Array.isArray(job?.steps) ? job.steps : [])
    .filter((step) => ['failed', 'retrying'].includes(step?.status))
    .map((step) => step.key)
    .filter(Boolean);
}

export function finalizationSessionKey(job) {
  return `finalization:${job?.job_id || ''}`;
}

/** Resolve known translations while allowing arbitrary future worker steps. */
export function finalizationStepLabel(step, translate = (_key, fallback) => fallback) {
  const key = String(step?.key || '').trim();
  const fallback = String(step?.label || humanize(key) || key);
  if (!key) return fallback;
  const translationSuffix = key.replace(/_([a-z0-9])/g, (_match, letter) => letter.toUpperCase());
  return translate(`finalization.step.${translationSuffix}`, fallback);
}

function humanize(value) {
  return String(value || '')
    .replace(/[._-]+/g, ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}
