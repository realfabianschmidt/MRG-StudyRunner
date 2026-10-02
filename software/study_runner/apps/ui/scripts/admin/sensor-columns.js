/**
 * Two column stacks instead of a grid for the sensor tiles: each tile keeps
 * its own height and the next tile follows right below it, whatever stands
 * beside it. Which column a tile lives in comes from the tile arrangement
 * (sensor-tile-order.js), never from its height, so tiles never hop between
 * columns when live data changes. On narrow screens the columns dissolve
 * (CSS) and each tile's `order` keeps the arrangement row by row.
 */
export function sensorColumns(target) {
  let columns = [...target.children].filter((node) => node.classList?.contains('sensor-column'));
  if (columns.length !== 2) {
    target.innerHTML = '<div class="sensor-column"></div><div class="sensor-column"></div>';
    target.classList.add('sensor-columns');
    columns = [...target.children];
  }
  return columns;
}

/**
 * Text that does not fit its bar scrolls through slowly, like a station
 * display: pause, run to the end, pause, back. Text that fits stands still.
 */
export function updateMarquees(root) {
  root.querySelectorAll('[data-marquee]').forEach((box) => {
    const text = box.querySelector('.marquee-text');
    if (!text) return;
    const overflow = text.scrollWidth - box.clientWidth;
    const running = overflow > 2;
    box.classList.toggle('is-scrolling', running);
    if (running) {
      box.style.setProperty('--marquee-shift', `-${overflow + 12}px`);
      // About 30 px per second, plus the pauses at both ends.
      box.style.setProperty('--marquee-duration', `${Math.max(6, overflow / 30 * 2 + 4).toFixed(1)}s`);
    }
  });
}

/**
 * Put a tile at ``position`` in ``column``, moving it only when it is not
 * there yet (moving a node would close an open dropdown in it). ``order`` is
 * its place in the single-column view. Place a column's tiles top to bottom.
 */
export function placeTile(column, tile, position, order) {
  tile.style.order = String(order);
  if (tile.parentElement === column && column.children[position] === tile) return;
  column.insertBefore(tile, column.children[position] || null);
}
