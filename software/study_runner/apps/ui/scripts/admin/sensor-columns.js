/**
 * Two column stacks instead of a grid for the sensor tiles: each tile keeps
 * its own height and the next tile follows right below it, whatever stands
 * beside it. Tile *i* always lives in column *i % 2*, so tiles never hop
 * between columns when live data changes their height. On narrow screens
 * the columns dissolve (CSS) and each tile's `order` keeps the plugin order.
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

/** Put tile *index* in its column at its place, moving it only when it is not there yet. */
export function placeInColumn(columns, tile, keys, index) {
  const count = columns.length;
  const column = columns[index % count];
  const position = Math.floor(index / count);
  tile.style.order = String(index);
  if (tile.parentElement === column && column.children[position] === tile) return;
  const expected = keys.filter((_, i) => i % count === index % count);
  const next = [...column.children].find((node) => expected.indexOf(node.dataset.pluginTile) > position);
  column.insertBefore(tile, next || null);
}
