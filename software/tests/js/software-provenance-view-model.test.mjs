import assert from 'node:assert/strict';
import {
  methodsText,
  softwareNote,
  softwareRows,
} from '../../study_runner/apps/ui/scripts/shared/software-provenance-view-model.js';

const recorded = {
  study_runner_version: '1.7.0',
  plugins: {
    am_hub: { version: '3.0.1', role: 'recording' },
    brainbit: { version: '1.1.0', role: 'recording' },
    mood_meter: { version: '1.1.0', role: 'card' },
    notion: { version: '1.0.0', role: 'destination' },
  },
};

// The session view lists every plugin, grouped by what it did.
assert.deepEqual(softwareRows(recorded), [
  { label: 'Study Runner', value: '1.7.0' },
  { label: 'Recording', value: 'am_hub 3.0.1, brainbit 1.1.0' },
  { label: 'Cards', value: 'mood_meter 1.1.0' },
  { label: 'Upload', value: 'notion 1.0.0' },
]);
assert.equal(softwareNote(recorded), '');

// The methods text cites what produced data; upload destinations only copy files.
assert.equal(
  methodsText(recorded),
  'Data were recorded with Study Runner 1.7.0 (plugins: am_hub 3.0.1, brainbit 1.1.0, mood_meter 1.1.0).',
);
assert.equal(
  methodsText({ study_runner_version: '1.7.0', plugins: { notion: { version: '1.0.0', role: 'destination' } } }),
  'Data were recorded with Study Runner 1.7.0.',
);
// The API sorts keys alphabetically; sensors still come before cards.
assert.equal(
  methodsText({
    study_runner_version: '1.7.0',
    plugins: {
      affect_map: { version: '1.1.0', role: 'card' },
      am_hub: { version: '3.0.1', role: 'recording' },
      slider: { version: '1.0.0', role: 'card' },
    },
  }),
  'Data were recorded with Study Runner 1.7.0 (plugins: am_hub 3.0.1, affect_map 1.1.0, slider 1.0.0).',
);

// Translations fill the same placeholders.
const german = (key, fallback) => ({
  'sessions.software.methodsWithPlugins': 'Die Daten wurden mit Study Runner {version} erhoben (Plugins: {plugins}).',
}[key] ?? fallback);
assert.equal(
  methodsText(recorded, german),
  'Die Daten wurden mit Study Runner 1.7.0 erhoben (Plugins: am_hub 3.0.1, brainbit 1.1.0, mood_meter 1.1.0).',
);

// An older session: only its sensor plugins are known, and it says so.
const partial = { study_runner_version: null, plugins: { mr60: { version: '1.0.0', role: 'recording' } }, partial: true };
assert.deepEqual(softwareRows(partial), [
  { label: 'Study Runner', value: 'not recorded' },
  { label: 'Recording', value: 'mr60 1.0.0' },
]);
assert.match(softwareNote(partial), /only the sensor plugin versions are known/);
assert.equal(methodsText(partial), '', 'no methods text without a Study Runner version');

// Nothing recorded at all.
assert.deepEqual(softwareRows(null), []);
assert.match(softwareNote(null), /^Not recorded/);
assert.equal(methodsText(undefined), '');

console.log('software provenance view model: ok');
