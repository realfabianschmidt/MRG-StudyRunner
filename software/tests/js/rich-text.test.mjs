import assert from 'node:assert/strict';
import test from 'node:test';
import { renderMediaLayout, renderRichText } from '../../study_runner/apps/ui/scripts/shared/rich-text.js';

const ASSET = `${'a'.repeat(64)}.png`;

test('study text is escaped before the small markdown subset is applied', () => {
  const html = renderRichText('# Welcome\n\nRead **this** and *that*\nnext line\n\n- one\n- two <script>alert(1)</script>\n\n<img src=x onerror=alert(1)>');
  assert.match(html, /^<h2>Welcome<\/h2>/);
  assert.match(html, /<p>Read <strong>this<\/strong> and <em>that<\/em><br>next line<\/p>/);
  assert.match(html, /<ul><li>one<\/li><li>two &lt;script&gt;alert\(1\)&lt;\/script&gt;<\/li><\/ul>/);
  assert.doesNotMatch(html, /<script|<img/);
});

test('media layout places the image left or right, and ignores it for text-only', () => {
  const left = renderMediaLayout({ title: 'T', text: 'x', image_asset: ASSET, image_alt: 'A "cat"', layout: 'image-left' });
  assert.match(left, /media-layout--image-left"><figure/);
  assert.match(left, /alt="A &quot;cat&quot;"/);
  const right = renderMediaLayout({ title: 'T', text: 'x', image_asset: ASSET, layout: 'image-right' });
  assert.match(right, /<\/div><figure class="media-layout-figure"><img src="\/api\/study-assets\/a+\.png"/);
  const textOnly = renderMediaLayout({ title: 'T', text: 'x', image_asset: ASSET, layout: 'text' });
  assert.doesNotMatch(textOnly, /<img/);
  assert.doesNotMatch(renderMediaLayout({ title: 'T', layout: 'image-left' }), /<img/);
});
