import assert from 'node:assert/strict';
import test from 'node:test';

import { renderClients, PARTICIPANT_TILE_KEY } from '../../study_runner/apps/ui/scripts/admin/participant-devices-panel.js';
import { mergeLayout, moveTile } from '../../study_runner/apps/ui/scripts/admin/sensor-tile-order.js';

const connection = (clientId, code) => ({
  client_id: clientId, display_id: code, status: 'active', study_id: 'study-a',
  waiting_for_admin_start: true, age_seconds: 0, plugin_status: {},
});

test('device panel identifies and selects a waiting connection', () => {
  const target = { innerHTML: '' };
  renderClients(target, {
    clients: [connection('tablet-1', 'D-AAAAA'), connection('tablet-2', 'D-BBBBB')],
    single_tablet: { selected_client_id: 'tablet-2' },
  }, { study_id: 'study-a', status: 'loaded', selected_client_id: 'tablet-2' });
  assert.match(target.innerHTML, /D-AAAAA/);
  assert.match(target.innerHTML, /D-BBBBB/);
  assert.match(target.innerHTML, /data-participant-target="tablet-2"/);
  assert.match(target.innerHTML, /Selected for Start/);
});

test('release and acknowledgement are different statuses', () => {
  const target = { innerHTML: '' };
  const status = { clients: [connection('tablet-2', 'D-BBBBB')], single_tablet: { observed: false } };
  const run = { study_id: 'study-a', status: 'running', active_client_id: 'tablet-2' };
  renderClients(target, status, run);
  assert.match(target.innerHTML, /Waiting for device acknowledgement/);
  status.single_tablet.observed = true;
  renderClients(target, status, run);
  assert.match(target.innerHTML, /Start seen on device/);
});

test('older plugin layout accepts and moves the new participant tile', () => {
  const layout = mergeLayout({ columns: [['brainbit'], ['am_hub']] }, ['brainbit', 'am_hub', PARTICIPANT_TILE_KEY]);
  assert.ok(layout.columns.flat().includes(PARTICIPANT_TILE_KEY));
  const moved = moveTile(layout, PARTICIPANT_TILE_KEY, 0, 0);
  assert.equal(moved.columns[0][0], PARTICIPANT_TILE_KEY);
});
