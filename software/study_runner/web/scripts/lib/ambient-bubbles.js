/**
 * Soft morphing blobs for the participant waiting slide.
 *
 * Self-contained on purpose: it imports nothing from the app, owns no state
 * beyond the element it created, and every value worth changing sits in CONFIG
 * below. Drop it, retune it, or swap it for something else without touching a
 * controller. Styles live next to it in styles/ambient-bubbles.css.
 *
 * Two exports: startAmbientBubbles(host, overrides) paints the field into an
 * element, stopAmbientBubbles() removes it again.
 */

/** Everything tunable. Nothing below this block needs editing to restyle it. */
const CONFIG = {
  count: 5,
  // Share of the viewport's smaller side, so the blobs scale with the screen.
  sizeRange: [0.35, 0.7],
  // One drift cycle, seconds. Slow enough to read as "alive", not as motion.
  driftSeconds: [26, 44],
  // One shape-morph cycle, seconds. Deliberately out of step with the drift so
  // the two never loop together into a visible rhythm.
  morphSeconds: [18, 30],
  opacity: 0.6,
  blurPx: 70,
  // Neutral greys, a step darker than the page so the field is perceptible
  // without ever competing with the text in front of it.
  colors: ['#D7D7DE', '#E1E1E7', '#CFCFD8'],
};

const HOST_CLASS = 'ambient-bubbles';

let container = null;

/**
 * Render the blob field into `host`. Calling it twice replaces the first field
 * rather than stacking a second one.
 *
 * @param {HTMLElement} host        element to append the field to
 * @param {object}      [overrides] partial CONFIG, merged shallowly
 */
export function startAmbientBubbles(host, overrides = {}) {
  if (!host) return null;
  stopAmbientBubbles();

  const config = { ...CONFIG, ...overrides };
  container = document.createElement('div');
  container.className = HOST_CLASS;
  container.setAttribute('aria-hidden', 'true');

  for (let index = 0; index < config.count; index += 1) {
    container.appendChild(createBlob(index, config));
  }
  host.appendChild(container);
  return container;
}

/** Remove the field. Safe to call when nothing is running. */
export function stopAmbientBubbles() {
  container?.remove();
  container = null;
}

function createBlob(index, config) {
  const blob = document.createElement('span');
  blob.className = `${HOST_CLASS}__blob`;

  const size = randomBetween(config.sizeRange[0], config.sizeRange[1]) * 100;
  const style = {
    '--blob-size': `${size.toFixed(2)}vmin`,
    '--blob-color': config.colors[index % config.colors.length],
    '--blob-opacity': String(config.opacity),
    '--blob-blur': `${config.blurPx}px`,
    '--blob-drift': `${randomBetween(...config.driftSeconds).toFixed(1)}s`,
    '--blob-morph': `${randomBetween(...config.morphSeconds).toFixed(1)}s`,
    // Negative delays start each blob mid-cycle, so the field looks settled on
    // the first frame instead of every blob launching from the same pose.
    '--blob-delay': `-${randomBetween(0, 30).toFixed(1)}s`,
    '--blob-top': `${randomBetween(-15, 75).toFixed(1)}%`,
    '--blob-left': `${randomBetween(-15, 75).toFixed(1)}%`,
  };
  for (const [property, value] of Object.entries(style)) {
    blob.style.setProperty(property, value);
  }
  return blob;
}

function randomBetween(min, max) {
  return min + Math.random() * (max - min);
}
