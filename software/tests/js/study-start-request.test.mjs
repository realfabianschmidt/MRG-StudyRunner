import assert from 'node:assert/strict';
import test from 'node:test';

import { submitStartRequest } from '../../study_runner/apps/ui/scripts/admin/study-start-request.js';

test('a timed-out Start reconciles a run the server already committed', async () => {
  let posts = 0;
  const response = await submitStartRequest({
    postJson: async () => { posts += 1; throw new Error('Request timed out after 12000 ms'); },
    getJson: async () => ({ run_state: { status: 'running', run_id: 'new-run' } }),
    payload: {}, previousRunId: 'old-run',
  });
  assert.equal(response.run_state.run_id, 'new-run');
  assert.equal(posts, 1);
});

test('an unconfirmed timeout never retries Start or reports success', async () => {
  let posts = 0;
  await assert.rejects(submitStartRequest({
    postJson: async () => { posts += 1; throw new Error('Request timed out after 12000 ms'); },
    getJson: async () => ({ run_state: { status: 'loaded', run_id: 'old-run' } }),
    payload: {}, previousRunId: 'old-run',
  }), /not confirmed/);
  assert.equal(posts, 1);
});
