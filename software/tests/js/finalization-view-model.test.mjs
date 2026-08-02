import assert from 'node:assert/strict';
import {
  finalizationProgress,
  finalizationSessionKey,
  finalizationStepLabel,
  pickFinalizationFocus,
  retryableSteps,
} from '../../study_runner/web/scripts/lib/finalization-view-model.js';

const queued = {
  job_id: 'queued',
  status: 'queued',
  created_epoch: 3,
  steps: [
    { key: 'commit_submission', status: 'done' },
    { key: 'freeze_recording', status: 'pending' },
    { key: 'publish_notion', status: 'skipped' },
  ],
};
const attention = {
  job_id: 'attention',
  status: 'attention_required',
  created_epoch: 1,
  steps: [
    { key: 'commit_submission', status: 'done' },
    { key: 'validate_sources', status: 'failed' },
    { key: 'publish_nextcloud', status: 'retrying' },
  ],
};
const completed = { job_id: 'done', status: 'completed', created_epoch: 9, steps: [] };
const degradedPublishing = {
  job_id: 'degraded',
  status: 'completed_degraded',
  created_epoch: 2,
  steps: [{ key: 'publish_notion', status: 'retrying' }],
};

assert.deepEqual(finalizationProgress(queued), { done: 2, total: 3, percent: 67 });
assert.equal(pickFinalizationFocus([completed, queued, attention]), attention);
assert.equal(pickFinalizationFocus([completed, degradedPublishing]), degradedPublishing);
assert.deepEqual(retryableSteps(attention), ['validate_sources', 'publish_nextcloud']);
assert.equal(finalizationSessionKey(attention), 'finalization:attention');
assert.equal(
  finalizationStepLabel(
    { key: 'publish_future_archive', label: 'Publish research archive' },
    (key, fallback) => key === 'finalization.step.publishFutureArchive' ? 'Archiv publizieren' : fallback,
  ),
  'Archiv publizieren',
);
assert.equal(
  finalizationStepLabel({ key: 'validate_future_format' }),
  'Validate Future Format',
);
