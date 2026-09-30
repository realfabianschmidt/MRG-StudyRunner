import assert from 'node:assert/strict';
import test from 'node:test';

import { loadShippedCards, snapshot } from './card-test-support.mjs';
import { cardState, resetAllCardState } from '../../study_runner/apps/ui/scripts/cards/session-state.js';
import { colorAt } from '../../study_runner/plugins/cards/affect_map/mood-core.js';

const cards = await loadShippedCards();
const affect = cards['affect-map'];
const mood = cards['mood-meter'];
const defaults = snapshot.defaults.affect_map['affect-map'];

test('Affect Map offers only Field and Orbit and previews selected colors', () => {
  const html = affect.renderEditor(defaults);
  assert.equal((html.match(/class="am-ed-variant"/g) || []).length, 2);
  assert.ok(!html.includes('value="classic"'));
  assert.ok(!html.includes('value="blobs"'));
  assert.equal((html.match(/class="am-ed-color-input"/g) || []).length, 4);
  assert.match(html, /#B4402E/);
  const gray = affect.renderEditor({ ...defaults, colors_enabled: false });
  assert.match(gray, /#777777/);
  assert.ok(!gray.includes('stop-color="#B4402E"'));
});

test('custom palette and neutral mode affect color without changing coordinates', () => {
  const custom = affect.effectiveColors({ region_colors: { red: '#123456' } });
  assert.equal(custom.red, '#123456');
  assert.equal(colorAt(0, 1, custom), 'rgb(18, 52, 86)');
  assert.equal(affect.effectiveColors({ colors_enabled: false }).red, '#777777');
  const fieldHtml = affect.renderStudy({ ...defaults, colors_enabled: false }, 2);
  assert.match(fieldHtml, /--mm-cloud:#777777/);
  assert.ok(!fieldHtml.includes('--mm-cloud:#B4402E'));
  const orbitHtml = affect.renderStudy({ ...defaults, variant: 'orbit', colors_enabled: false }, 3);
  assert.match(orbitHtml, /--mm-ring:conic-gradient\(rgb\(/);
  assert.ok(!orbitHtml.includes('--mm-word-color:rgb(180, 64, 46)'));
});

test('the editor updates both previews when a region color or mode changes', () => {
  const callbacks = new Map();
  const svg = { innerHTML: '' };
  const variants = ['field', 'orbit'].map((value) => ({ value, closest: () => ({ querySelector: () => svg }) }));
  const enabled = { checked: true, addEventListener: (_event, callback) => callbacks.set('mode', callback) };
  const red = { dataset: { region: 'red' }, value: '#123456', addEventListener: (_event, callback) => callbacks.set('red', callback) };
  const colors = [red, ...Object.entries(defaults.region_colors).filter(([key]) => key !== 'red').map(([region, value]) => ({ dataset: { region }, value, addEventListener() {} }))];
  const el = {
    querySelector: () => enabled,
    querySelectorAll: (selector) => selector === '.am-ed-color-input' ? colors : variants,
  };
  affect.bindEditorEvents(el);
  callbacks.get('red')();
  assert.match(svg.innerHTML, /#123456/);
  enabled.checked = false;
  callbacks.get('mode')();
  assert.match(svg.innerHTML, /#777777/);
  assert.ok(!svg.innerHTML.includes('#123456'));
});

test('Affect Map and Mood Meter keep answers in separate session stores', () => {
  const own = cardState('affect-map', 4, () => ({ selected: new Set(), position: null, question: null }));
  own.question = defaults;
  own.selected.add('Calm');
  assert.equal(affect.collectAnswer(4), null);
  own.position = { pleasantness: .8, energy: .2 };
  assert.deepEqual(affect.collectAnswer(4), { words: ['Calm'], pleasantness: .8, energy: .2 });
  assert.equal(mood.collectAnswer(4), null);
  resetAllCardState();
  assert.equal(affect.collectAnswer(4), null);
});
