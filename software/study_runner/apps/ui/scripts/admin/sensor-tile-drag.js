/**
 * Moving a plugin tile by its title bar, with the mouse, a pen, a finger or
 * the keyboard. The tile follows the pointer only after it travelled a few
 * pixels, so a click on the title stays a click; controls never start a drag
 * (the runtime switch has its own pointer handling). A line shows where the
 * tile will land. Keyboard, on the focused title: Arrow Up/Down move it
 * within its column, Arrow Left/Right to the other column.
 *
 * Only the drop is reported (``onMove(key, column, position)``); the caller
 * changes the arrangement and saves it.
 */
import { tilePlace } from './sensor-tile-order.js';

const THRESHOLD_PX = 6;
const CONTROLS = 'button, a, input, select, textarea, summary, label, [data-runtime-switch]';

export function bindSensorTileDrag(root, { getLayout, onMove, enabled = () => true }) {
  let drag = null;

  root.addEventListener('pointerdown', (event) => {
    if (event.button !== 0 || !enabled()) return;
    const handle = event.target.closest('[data-tile-handle]');
    if (!handle || event.target.closest(CONTROLS)) return;
    const tile = handle.closest('[data-plugin-tile]');
    if (!tile) return;
    drag = { tile, key: tile.dataset.pluginTile, x: event.clientX, y: event.clientY, pointerId: event.pointerId, active: false, target: null };
  });

  window.addEventListener('pointermove', (event) => {
    if (!drag || event.pointerId !== drag.pointerId) return;
    const dx = event.clientX - drag.x;
    const dy = event.clientY - drag.y;
    if (!drag.active) {
      if (Math.hypot(dx, dy) < THRESHOLD_PX) return;
      drag.active = true;
      drag.tile.classList.add('sensor-tile--dragging');
      root.classList.add('sensor-columns--dragging');
    }
    event.preventDefault();
    drag.tile.style.transform = `translate(${dx}px, ${dy}px)`;
    drag.target = dropTarget(root, drag.tile, event.clientX, event.clientY);
    showDropLine(root, drag.target);
  });

  const finish = (event, cancelled) => {
    if (!drag || (event && event.pointerId !== drag.pointerId)) return;
    const { active, target, key, tile } = drag;
    drag = null;
    tile.style.transform = '';
    tile.classList.remove('sensor-tile--dragging');
    root.classList.remove('sensor-columns--dragging');
    showDropLine(root, null);
    if (active && !cancelled && target) onMove(key, target.column, target.position);
  };
  window.addEventListener('pointerup', (event) => finish(event, false));
  window.addEventListener('pointercancel', (event) => finish(event, true));

  root.addEventListener('keydown', (event) => {
    const handle = event.target.closest?.('[data-tile-handle]');
    if (!handle || !enabled()) return;
    const key = handle.closest('[data-plugin-tile]')?.dataset.pluginTile;
    const layout = getLayout();
    const place = key && layout ? tilePlace(layout, key) : null;
    if (!place) return;
    const next = keyboardTarget(layout, place, event.key);
    if (!next) return;
    event.preventDefault();
    onMove(key, next.column, next.position);
  });

  return { isDragging: () => Boolean(drag?.active) };
}

/** Where an arrow key moves a tile, or null when it does not move. */
export function keyboardTarget(layout, place, arrow) {
  const { column, position } = place;
  if (arrow === 'ArrowUp') return position > 0 ? { column, position: position - 1 } : null;
  if (arrow === 'ArrowDown') {
    return position < layout.columns[column].length - 1 ? { column, position: position + 1 } : null;
  }
  if (arrow === 'ArrowLeft' || arrow === 'ArrowRight') {
    const other = arrow === 'ArrowLeft' ? column - 1 : column + 1;
    if (other < 0 || other >= layout.columns.length) return null;
    return { column: other, position: Math.min(position, layout.columns[other].length) };
  }
  return null;
}

/** The column under the pointer (or nearest) and the place among its other tiles. */
function dropTarget(root, dragged, x, y) {
  const columns = [...root.querySelectorAll(':scope > .sensor-column')];
  if (!columns.length) return null;
  const distance = (rect) => (x < rect.left ? rect.left - x : x > rect.right ? x - rect.right : 0);
  let column = 0;
  columns.forEach((node, index) => {
    if (distance(node.getBoundingClientRect()) < distance(columns[column].getBoundingClientRect())) column = index;
  });
  const others = [...columns[column].children].filter((node) => node !== dragged && node.dataset.pluginTile);
  const position = others.filter((node) => {
    const rect = node.getBoundingClientRect();
    return rect.top + rect.height / 2 < y;
  }).length;
  return { column, position, before: others[position] || null, columnNode: columns[column] };
}

function showDropLine(root, target) {
  root.querySelectorAll('.sensor-tile--drop-before').forEach((node) => node.classList.remove('sensor-tile--drop-before'));
  root.querySelectorAll('.sensor-column--drop-end').forEach((node) => node.classList.remove('sensor-column--drop-end'));
  if (!target) return;
  if (target.before) target.before.classList.add('sensor-tile--drop-before');
  else target.columnNode.classList.add('sensor-column--drop-end');
}
