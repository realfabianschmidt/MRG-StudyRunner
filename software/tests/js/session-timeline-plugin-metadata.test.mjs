import assert from 'node:assert/strict';

import { renderSessionTimeline } from '../../study_runner/web/scripts/admin/session-timeline.js';
import { configurePluginCatalog } from '../../study_runner/web/scripts/lib/plugin-catalog.js';

const labels = {
  nothingRecorded: 'nothing',
  chartLabel: 'chart',
  markersLabel: 'markers',
  channels: {},
};

configurePluginCatalog({
  api_version: 3,
  plugins: [
    {
      plugin_key: 'fixture_sensor',
      status: 'valid',
      ui: {
        timeline: {
          lane_aliases: ['fixture_sidecar'],
          preferred_channels: ['payload.signal'],
        },
      },
    },
  ],
});

const preferredContainer = { innerHTML: '' };
const preferredResult = renderSessionTimeline(preferredContainer, {
  lanes: [{
    sensor: 'fixture_sidecar',
    mode: 'raw',
    points: [
      { server_received_epoch: 1, payload: { signal: 10, ignored: 30 } },
      { server_received_epoch: 2, payload: { signal: 12, ignored: 31 } },
    ],
  }],
  markers: [],
  labels,
});

assert.equal(preferredResult.laneCount, 1);
assert.match(preferredContainer.innerHTML, />signal</);
assert.doesNotMatch(preferredContainer.innerHTML, />ignored</);

const fallbackContainer = { innerHTML: '' };
const fallbackResult = renderSessionTimeline(fallbackContainer, {
  lanes: [{
    sensor: 'new_sensor_without_core_changes',
    mode: 'raw',
    points: [
      { server_received_epoch: 1, temperature: 20, sequence_number: 1 },
      { server_received_epoch: 2, temperature: 21, sequence_number: 2 },
    ],
  }],
  markers: [],
  labels,
});

assert.equal(fallbackResult.laneCount, 1);
assert.match(fallbackContainer.innerHTML, />temperature</);
assert.doesNotMatch(fallbackContainer.innerHTML, />sequence number</);
