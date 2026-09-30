// Shared core of the mood meter views: quadrants, where every word sits on the
// energy x pleasantness plane, colors, the morphing shape, springs and the
// animation loop. No state lives here -- everything per session is kept in
// cardState() by card.js, everything visual on the DOM.

// Brackett's Mood Meter quadrants. Order matters: Red -> Yellow -> Green ->
// Blue (clockwise, used for prev/next navigation in the word space).
export const QUADRANTS = [
  {
    id: 'red',
    label: 'High Energy - Unpleasant',
    color: '#B4402E', colorDark: '#7B241C',
    dirX: -1, dirY: -1,
    examples: ['Stressed', 'Anxious', 'Frustrated'],
  },
  {
    id: 'yellow',
    label: 'High Energy - Pleasant',
    color: '#BD7A1E', colorDark: '#9A6108',
    dirX: 1, dirY: -1,
    examples: ['Excited', 'Happy', 'Enthusiastic'],
  },
  {
    id: 'green',
    label: 'Low Energy - Pleasant',
    color: '#2F7A4D', colorDark: '#145A32',
    dirX: 1, dirY: 1,
    examples: ['Calm', 'Content', 'Serene'],
  },
  {
    id: 'blue',
    label: 'Low Energy - Unpleasant',
    color: '#2A6FA0', colorDark: '#1A5276',
    dirX: -1, dirY: 1,
    examples: ['Sad', 'Tired', 'Hopeless'],
  },
];

// The quadrant's words, falling back to the study defaults per quadrant.
export function localizedQuadrants(t) {
  return QUADRANTS.map((quadrant) => ({
    ...quadrant,
    label: t(`cards.moodMeter.quadrant.${quadrant.id}`, quadrant.label),
    examples: t(`cards.moodMeter.examples.${quadrant.id}`, quadrant.examples.join('|')).split('|'),
  }));
}

export function wordLists(question, defaults, t = (_key, fallback) => fallback) {
  return localizedQuadrants(t).map((quadrant) => ({
    ...quadrant,
    words: question?.word_lists?.[quadrant.id] ?? defaults?.word_lists?.[quadrant.id] ?? [],
  }));
}

const COLUMNS_PER_QUADRANT = 5;

/**
 * Every word's place on the plane, pleasantness 0 (left) -> 1 (right) and
 * energy 0 (bottom) -> 1 (top). The default lists are Brackett's original
 * 10 x 10 Mood Meter grid read row by row, five words per quadrant row
 * ("Enraged" is the top-left corner), so the defaults land exactly where the
 * Mood Meter puts them; custom lists follow the same row-by-row rule.
 */
export function wordCoordinates(quads) {
  const coordinates = [];
  for (const quadrant of quads) {
    const words = quadrant.words || [];
    const columns = Math.min(COLUMNS_PER_QUADRANT, Math.max(1, words.length));
    const rows = Math.max(1, Math.ceil(words.length / COLUMNS_PER_QUADRANT));
    words.forEach((word, index) => {
      const column = index % COLUMNS_PER_QUADRANT;
      const row = Math.floor(index / COLUMNS_PER_QUADRANT);
      const localP = ((column + 0.5) / columns) * 0.5;
      const localE = ((row + 0.5) / rows) * 0.5;
      const pleasant = quadrant.dirX > 0;
      const high = quadrant.dirY < 0;
      coordinates.push({
        word,
        quadrant: quadrant.id,
        pleasantness: pleasant ? 0.5 + localP : localP,
        energy: high ? 1 - localE : 0.5 - localE,
      });
    });
  }
  return coordinates;
}

export function quadrantAt(pleasantness, energy) {
  if (energy >= 0.5) return pleasantness < 0.5 ? 'red' : 'yellow';
  return pleasantness < 0.5 ? 'blue' : 'green';
}

export function nearestWords(coordinates, pleasantness, energy, count) {
  return coordinates
    .map((entry) => ({ ...entry, distance: Math.hypot(entry.pleasantness - pleasantness, entry.energy - energy) }))
    .sort((left, right) => left.distance - right.distance)
    .slice(0, count);
}

// A word's place on the orbit wheel (unit circle, y down): its angle comes from its (pleasantness,
// energy) direction, its radius from how far it sits from neutral.
export function orbitPlace(entry) {
  const x = entry.pleasantness - 0.5;
  const y = entry.energy - 0.5;
  const reach = Math.max(Math.abs(x), Math.abs(y)) / 0.5; // 0.1 .. 0.9 for the grid
  const radius = 0.3 + 0.66 * reach;
  const angle = Math.atan2(y, x);
  return { x: Math.cos(angle) * radius, y: -Math.sin(angle) * radius, radius, angle };
}

function hexToRgb(hex) {
  const value = Number.parseInt(hex.slice(1), 16);
  return [(value >> 16) & 255, (value >> 8) & 255, value & 255];
}

// The four quadrant colors blended at a point (bilinear over the corners).
export function colorAt(pleasantness, energy, colors = null) {
  const corner = (id) => hexToRgb(colors?.[id] || QUADRANTS.find((quadrant) => quadrant.id === id).color);
  const top = corner('red').map((channel, index) => channel + (corner('yellow')[index] - channel) * pleasantness);
  const bottom = corner('blue').map((channel, index) => channel + (corner('green')[index] - channel) * pleasantness);
  const rgb = bottom.map((channel, index) => Math.round(channel + (top[index] - channel) * energy));
  return `rgb(${rgb.join(', ')})`;
}

export function clamp01(value) {
  return Math.min(1, Math.max(0, value));
}

function smoothstep(edge0, edge1, value) {
  const x = clamp01((value - edge0) / (edge1 - edge0));
  return x * x * (3 - 2 * x);
}

/**
 * How the shape feels at a point, after How We Feel and Apple's State of
 * Mind: jagged for high-energy unpleasant, flowering for high-energy
 * pleasant, drooping for low-energy unpleasant, soft and round when calm.
 * Each weight fades out towards the middle, so crossing the plane morphs
 * the shape continuously.
 */
export function shapeAt(pleasantness, energy) {
  return {
    spikes: smoothstep(0.45, 0.95, energy) * smoothstep(0.45, 0.95, 1 - pleasantness),
    bloom: smoothstep(0.45, 0.95, energy) * smoothstep(0.45, 0.95, pleasantness),
    droop: smoothstep(0.45, 0.95, 1 - energy) * smoothstep(0.45, 0.95, 1 - pleasantness),
  };
}

// Per-quadrant shape characters for the blob overview.
export const QUADRANT_SHAPES = Object.freeze({
  red: { spikes: 1, bloom: 0, droop: 0 },
  yellow: { spikes: 0, bloom: 1, droop: 0 },
  green: { spikes: 0, bloom: 0, droop: 0 },
  blue: { spikes: 0, bloom: 0, droop: 1 },
});

const TAU = Math.PI * 2;

/**
 * A closed SVG path around (cx, cy): a circle of radius r bent by the shape
 * weights, and gently breathing with time t (seconds). Pure function.
 */
export function blobPath(cx, cy, r, shape, t = 0, points = 120) {
  const { spikes = 0, bloom = 0, droop = 0 } = shape || {};
  let path = '';
  for (let index = 0; index < points; index += 1) {
    const angle = (index / points) * TAU;
    const triangle = (2 / Math.PI) * Math.asin(Math.sin(angle * 7 + t * 0.7));
    const petals = Math.pow(0.5 + 0.5 * Math.cos(angle * 6 + t * 0.35), 2.2);
    const breathing = 0.035 * Math.sin(angle * 3 + t * 1.1) + 0.025 * Math.sin(angle * 5 - t * 0.8);
    const radius = r * (1 - 0.12 * bloom + 0.26 * spikes * triangle + 0.42 * bloom * petals + breathing * (1 - 0.6 * spikes));
    let x = Math.cos(angle) * radius;
    let y = Math.sin(angle) * radius;
    if (droop > 0) {
      // Heavy at the bottom, narrow on top: a hanging drop.
      y *= y > 0 ? 1 + 0.22 * droop : 1 - 0.14 * droop;
      x *= 1 - 0.08 * droop * (y < 0 ? 1 : 0);
      y += r * 0.08 * droop;
    }
    path += `${index ? 'L' : 'M'}${(cx + x).toFixed(1)} ${(cy + y).toFixed(1)}`;
  }
  return `${path}Z`;
}

// A critically damped spring towards a moving target; call step(dt) per frame.
export function createSpring(value, { stiffness = 170 } = {}) {
  const damping = 2 * Math.sqrt(stiffness);
  const spring = { value: { ...value }, velocity: { x: 0, y: 0 }, target: { ...value } };
  spring.step = (dt) => {
    for (const axis of ['x', 'y']) {
      const force = stiffness * (spring.target[axis] - spring.value[axis]) - damping * spring.velocity[axis];
      spring.velocity[axis] += force * dt;
      spring.value[axis] += spring.velocity[axis] * dt;
    }
    return Math.abs(spring.velocity.x) + Math.abs(spring.velocity.y) > 0.0005
      || Math.abs(spring.target.x - spring.value.x) + Math.abs(spring.target.y - spring.value.y) > 0.0005;
  };
  return spring;
}

export function haptic(ms = 8) {
  try {
    if (typeof navigator !== 'undefined') navigator.vibrate?.(ms);
  } catch {
    // Haptics are a nicety; many tablets have none.
  }
}

