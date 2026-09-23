/**
 * Operator-supplied branding, shared by the participant slide and the hub.
 *
 * Both surfaces need the same two things - fetch the manifest, turn a slot into
 * an <img> - so they get one implementation rather than a copy each.
 *
 * Logos are rendered through <img src>, never inlined: the bytes are operator
 * uploads, and an SVG dropped into innerHTML could carry script.
 */

const MANIFEST_URL = '/api/branding';

/** Where a slot's bytes live. Also used by the settings panel for previews. */
export function brandingAssetUrl(slot, cacheBust = '') {
  const url = `/api/branding/asset/${encodeURIComponent(slot)}`;
  return cacheBust ? `${url}?v=${encodeURIComponent(cacheBust)}` : url;
}

/**
 * Read the manifest. Branding is decoration: if the call fails the caller gets
 * an empty manifest and the page renders without logos rather than breaking.
 */
export async function loadBranding() {
  try {
    const response = await fetch(MANIFEST_URL, { headers: { Accept: 'application/json' } });
    if (!response.ok) return emptyBranding();
    const payload = await response.json();
    return payload?.branding || emptyBranding();
  } catch {
    return emptyBranding();
  }
}

/**
 * Put the group mark into `target`, or leave it hidden when none is configured.
 * Returns true when something was rendered.
 */
export function renderGroupLogo(target, branding) {
  if (!target) return false;
  target.replaceChildren();
  const group = branding?.group;
  if (!group) {
    target.hidden = true;
    return false;
  }
  target.appendChild(logoImage(group.slot, group.alt));
  target.hidden = false;
  return true;
}

/** Same for the funder row. Order follows the manifest. */
export function renderFunderLogos(target, branding) {
  if (!target) return false;
  target.replaceChildren();
  const funders = branding?.funders || [];
  if (!funders.length) {
    target.hidden = true;
    return false;
  }
  for (const funder of funders) {
    target.appendChild(logoImage(funder.slot, funder.alt));
  }
  target.hidden = false;
  return true;
}

function logoImage(slot, alt) {
  const image = document.createElement('img');
  image.src = brandingAssetUrl(slot);
  // Empty alt on a decorative mark keeps screen readers from announcing a
  // filename; a real alt is announced when the operator supplied one.
  image.alt = alt || '';
  image.loading = 'lazy';
  image.decoding = 'async';
  return image;
}

function emptyBranding() {
  return { group: null, funders: [] };
}

// ---- Fonts -------------------------------------------------------------------

const FONT_STACKS = {
  geist: "'Geist', -apple-system, BlinkMacSystemFont, \"Segoe UI\", Roboto, Helvetica, Arial, sans-serif",
  system: "system-ui, -apple-system, BlinkMacSystemFont, \"Segoe UI\", Roboto, Helvetica, Arial, sans-serif",
  serif: "Georgia, Cambria, \"Times New Roman\", Times, serif",
};
const FONT_VARIABLES = { heading: '--font-heading', body: '--font-body' };

export async function loadFonts() {
  try {
    const response = await fetch('/api/branding/fonts', { headers: { Accept: 'application/json' } });
    if (!response.ok) return null;
    return (await response.json())?.fonts || null;
  } catch {
    return null;
  }
}

/**
 * Apply the operator's font choices by overriding the CSS font variables.
 * "default" keeps the stylesheet's own stack; an uploaded font falls back to
 * the default stack while it loads or if it cannot be read.
 */
export async function applyFonts(fonts = null) {
  const manifest = fonts || await loadFonts();
  if (!manifest) return;
  const root = document.documentElement;
  for (const [slot, variable] of Object.entries(FONT_VARIABLES)) {
    const entry = manifest[slot] || {};
    root.style.removeProperty(variable);
    if (entry.choice === 'uploaded' && entry.has_upload) {
      const family = `StudyRunner${slot === 'heading' ? 'Heading' : 'Body'}${entry.version.replace(/\W/g, '')}`;
      try {
        const face = new FontFace(family, `url(/api/branding/font/${slot}?v=${encodeURIComponent(entry.version)})`);
        await face.load();
        document.fonts.add(face);
        root.style.setProperty(variable, `'${family}', ${FONT_STACKS.geist}`);
      } catch (error) {
        console.warn(`[fonts] Could not load the ${slot} font:`, error);
      }
    } else if (FONT_STACKS[entry.choice]) {
      root.style.setProperty(variable, FONT_STACKS[entry.choice]);
    }
  }
}
