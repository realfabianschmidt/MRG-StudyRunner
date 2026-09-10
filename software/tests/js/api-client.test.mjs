import assert from 'node:assert/strict';
import { requestJson } from '../../study_runner/apps/ui/scripts/shared/api-client.js';

globalThis.window = {
  setTimeout,
  clearTimeout,
};

const payload = {
  ok: false,
  error: 'Study cannot start.',
  readiness: { ready: false, start_blocked: true, blockers: [] },
};
globalThis.fetch = async () => ({
  ok: false,
  status: 409,
  headers: { get: () => 'application/json' },
  json: async () => payload,
});

await assert.rejects(
  requestJson('/api/admin/study-run/start'),
  (error) => {
    assert.equal(error.message, payload.error);
    assert.equal(error.status, 409);
    assert.deepEqual(error.payload, payload);
    return true;
  },
);
