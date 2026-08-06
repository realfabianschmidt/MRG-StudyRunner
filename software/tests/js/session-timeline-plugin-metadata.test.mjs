/**
 * The timeline must stay sensor-agnostic.
 *
 * Two supported cases, and both have to keep working: a plugin that curates
 * its channels through its manifest, and a plugin that declares nothing at all
 * and is discovered entirely from the stream it recorded.
 */
import assert from 'node:assert/strict';

import { renderSessionTimeline } from '../../study_runner/web/scripts/admin/session-timeline.js';
import { configurePluginCatalog } from '../../study_runner/web/scripts/lib/plugin-catalog.js';

const labels = {
  nothingRecorded: 'nothing',
  chartLabel: 'chart',
  markersLabel: 'markers',
  zoomFull: 'full',
  zoomPartial: '{shown} of {total}',
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

// A container stub: the renderer writes innerHTML, then queries what it wrote.
const container = () => ({
  innerHTML: '',
  querySelector: () => null,
  querySelectorAll: () => [],
});

const preferredContainer = container();
const preferredResult = renderSessionTimeline(preferredContainer, {
  streams: [{
    stream_key: 'fixture_sidecar',
    stream_name: 'Fixture sensor',
    mode: 'raw',
    points: [
      { _epoch: 1, payload: { signal: 10, ignored: 30 } },
      { _epoch: 2, payload: { signal: 12, ignored: 31 } },
    ],
  }],
  markers: [],
  labels,
});

assert.equal(preferredResult.trackCount, 1, 'a curated plugin shows only what it declared');
assert.match(preferredContainer.innerHTML, /payload\.signal/);
assert.doesNotMatch(preferredContainer.innerHTML, /payload\.ignored/);

const fallbackContainer = container();
const fallbackResult = renderSessionTimeline(fallbackContainer, {
  streams: [{
    stream_key: 'new_sensor_without_core_changes',
    stream_name: 'Brand new sensor',
    mode: 'raw',
    points: [
      { _epoch: 1, temperature: 20, sequence_number: 1 },
      { _epoch: 2, temperature: 21, sequence_number: 2 },
    ],
  }],
  markers: [],
  labels,
});

assert.equal(fallbackResult.trackCount, 1, 'an undeclared sensor is still discovered');
assert.match(fallbackContainer.innerHTML, /temperature/);
assert.doesNotMatch(fallbackContainer.innerHTML, /sequence_number/);
assert.match(fallbackContainer.innerHTML, /Brand new sensor/, 'the stream names itself');
