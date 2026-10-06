/** Reconcile an ambiguous browser timeout with the server's durable run state. */
export async function submitStartRequest({ postJson, getJson, payload, previousRunId }) {
  try {
    return await postJson('/api/admin/study-run/start', payload, { timeoutMs: 12000 });
  } catch (error) {
    if (!String(error.message || '').includes('timed out')) throw error;
    // Cancelling fetch does not roll back a server-side start.
    try {
      const current = await getJson('/api/admin/study-run', { timeoutMs: 8000 });
      if (current?.run_state?.status === 'running' && current.run_state.run_id !== previousRunId) {
        return current;
      }
    } catch {
      // Polling will expose the result later; never retry the POST here.
    }
    throw new Error('Start outcome is not confirmed yet. Check the run status before trying again.');
  }
}
