// Cards operated by touch show a gently tapping finger instead of a "touch
// here" sentence; the sentence stays the accessible name, so screen readers
// still read it. That the first touch takes it away is held for every card in
// card-preview-contract.test.mjs.
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import test from 'node:test';

import { touchHint } from '../../study_runner/apps/ui/scripts/cards/card-motion.js';

test('the finger is named by the escaped sentence', () => {
  const markup = touchHint({ tag: 'p', attrs: 'class="mm-field-hint"', label: 'Drag the light <here> & "now"' });
  assert.match(markup, /^<p class="mm-field-hint" role="img" aria-label="Drag the light &lt;here&gt; &amp; &quot;now&quot;" data-touch-hint>/);
  assert.match(markup, /<i class="iconoir-one-finger-select-hand-gesture touch-hint" aria-hidden="true"><\/i><\/p>$/);
  assert.match(touchHint({ label: 'Touch' }), /^<span /);
});

test('no card writes a touch instruction as a sentence', () => {
  // A card text that starts with a gesture verb is the label of the finger.
  const gestureSentence = /\bt\('[^']+',\s*'(Drag|Tap|Touch|Swipe|Press|Hold|Pinch|Slide)\b/;
  const cards = new URL('../../study_runner/plugins/cards/', import.meta.url);
  const instructions = [];
  for (const plugin of readdirSync(cards, { withFileTypes: true }).filter((entry) => entry.isDirectory())) {
    for (const file of readdirSync(new URL(`${plugin.name}/`, cards)).filter((name) => name.endsWith('.js'))) {
      const lines = readFileSync(new URL(`${plugin.name}/${file}`, cards), 'utf8').split('\n');
      for (const line of lines.filter((text) => gestureSentence.test(text))) instructions.push([`${plugin.name}/${file}`, line]);
    }
  }
  assert.ok(instructions.length >= 5, 'the affect map, mood meter and word cloud instructions are found');
  for (const [file, line] of instructions) assert.ok(line.includes('touchHint('), `${file}: ${line.trim()}`);
});

test('the finger fades out once touched and keeps still for less motion', () => {
  const css = readFileSync(new URL('../../study_runner/apps/ui/styles/main.css', import.meta.url), 'utf8');
  const start = css.indexOf('SHARED - touch hints');
  assert.ok(start > 0);
  const block = css.slice(start);
  assert.match(block, /\[data-touch-hint="done"\] \{ opacity: 0; \}/);
  assert.match(block, /@media \(prefers-reduced-motion: reduce\) \{\s*\.touch-hint \{ animation: none; \}/);
});
