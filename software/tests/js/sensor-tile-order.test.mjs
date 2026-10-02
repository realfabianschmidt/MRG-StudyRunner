import assert from 'node:assert/strict';
import test from 'node:test';
import {
  defaultLayout,
  isDefaultLayout,
  mergeLayout,
  moveTile,
  rowMajorOrder,
  sameLayout,
  tilePlace,
} from '../../study_runner/apps/ui/scripts/admin/sensor-tile-order.js';
import { keyboardTarget } from '../../study_runner/apps/ui/scripts/admin/sensor-tile-drag.js';

const KEYS = ['brainbit', 'am_hub', 'camera_emotion', 'osc', 'mini_radar'];

test('the default puts the catalog order alternately left and right', () => {
  assert.deepEqual(defaultLayout(KEYS).columns, [['brainbit', 'camera_emotion', 'mini_radar'], ['am_hub', 'osc']]);
  assert.deepEqual(rowMajorOrder(defaultLayout(KEYS)), KEYS);
});

test('without a saved arrangement the default applies', () => {
  assert.ok(sameLayout(mergeLayout(null, KEYS), defaultLayout(KEYS)));
  assert.ok(sameLayout(mergeLayout({ columns: [['am_hub']] }, KEYS), defaultLayout(KEYS)));
  assert.ok(isDefaultLayout(mergeLayout(null, KEYS), KEYS));
});

test('a saved arrangement wins; unknown keys drop out and new plugins join the shorter column', () => {
  const saved = { columns: [['mini_radar', 'gone', 'brainbit'], ['am_hub', 'mini_radar']] };
  const layout = mergeLayout(saved, KEYS);
  // mini_radar is kept where it appears first; camera_emotion and osc are new.
  assert.deepEqual(layout.columns, [['mini_radar', 'brainbit', 'osc'], ['am_hub', 'camera_emotion']]);
  assert.equal(isDefaultLayout(layout, KEYS), false);
});

test('moving a tile keeps every other tile in its column', () => {
  const start = defaultLayout(KEYS);
  const moved = moveTile(start, 'mini_radar', 1, 0);
  assert.deepEqual(moved.columns, [['brainbit', 'camera_emotion'], ['mini_radar', 'am_hub', 'osc']]);
  assert.deepEqual(start.columns[0], ['brainbit', 'camera_emotion', 'mini_radar']);  // not mutated
  assert.deepEqual(moveTile(start, 'brainbit', 0, 99).columns[0], ['camera_emotion', 'mini_radar', 'brainbit']);
  assert.deepEqual(tilePlace(moved, 'mini_radar'), { column: 1, position: 0 });
  assert.equal(tilePlace(moved, 'missing'), null);
});

test('the arrow keys move a tile up, down and across', () => {
  const layout = defaultLayout(KEYS);
  const place = tilePlace(layout, 'camera_emotion');  // column 0, position 1
  assert.deepEqual(keyboardTarget(layout, place, 'ArrowUp'), { column: 0, position: 0 });
  assert.deepEqual(keyboardTarget(layout, place, 'ArrowDown'), { column: 0, position: 2 });
  assert.deepEqual(keyboardTarget(layout, place, 'ArrowRight'), { column: 1, position: 1 });
  assert.equal(keyboardTarget(layout, place, 'ArrowLeft'), null);
  assert.equal(keyboardTarget(layout, tilePlace(layout, 'brainbit'), 'ArrowUp'), null);
  assert.equal(keyboardTarget(layout, tilePlace(layout, 'osc'), 'ArrowDown'), null);
  assert.equal(keyboardTarget(layout, place, 'Enter'), null);
  // Into a shorter column: no further down than its end.
  assert.deepEqual(keyboardTarget(layout, tilePlace(layout, 'mini_radar'), 'ArrowRight'), { column: 1, position: 2 });
});
