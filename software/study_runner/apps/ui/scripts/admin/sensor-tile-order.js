/**
 * The arrangement of the dashboard's plugin tiles: two columns of plugin
 * keys. Pure functions, so the rules are easy to test:
 *
 * - The default puts the catalog's plugins alternately left and right (the
 *   order before anyone arranged anything).
 * - An operator's saved arrangement wins for the plugins it names; a plugin
 *   it does not name (a new one) joins the shorter column; a key without a
 *   plugin is dropped.
 * - Moving a tile puts it into a column at a position; the other tiles keep
 *   their columns, so nothing hops sideways.
 */

export const COLUMN_COUNT = 2;

/** Catalog order, alternately left and right. */
export function defaultLayout(keys) {
  const columns = Array.from({ length: COLUMN_COUNT }, () => []);
  keys.forEach((key, index) => columns[index % COLUMN_COUNT].push(key));
  return { columns };
}

/** The saved arrangement applied to the plugins there are now. */
export function mergeLayout(saved, keys) {
  const savedColumns = Array.isArray(saved?.columns) && saved.columns.length === COLUMN_COUNT ? saved.columns : null;
  if (!savedColumns) return defaultLayout(keys);
  const known = new Set(keys);
  const placed = new Set();
  const columns = savedColumns.map((column) => (Array.isArray(column) ? column : []).filter((key) => {
    if (!known.has(key) || placed.has(key)) return false;
    placed.add(key);
    return true;
  }));
  keys.filter((key) => !placed.has(key)).forEach((key) => {
    const shortest = columns.reduce((best, column, index) => (column.length < columns[best].length ? index : best), 0);
    columns[shortest].push(key);
  });
  return { columns };
}

/** The layout with ``key`` moved into ``column`` at ``position`` (0 = top). */
export function moveTile(layout, key, column, position) {
  const columns = layout.columns.map((keys) => keys.filter((existing) => existing !== key));
  const target = Math.max(0, Math.min(COLUMN_COUNT - 1, Number(column) || 0));
  const index = Math.max(0, Math.min(columns[target].length, Number(position) || 0));
  columns[target].splice(index, 0, key);
  return { columns };
}

/** One list, row by row (left, right, left, ...), for the single-column view. */
export function rowMajorOrder(layout) {
  const order = [];
  const rows = Math.max(0, ...layout.columns.map((column) => column.length));
  for (let row = 0; row < rows; row += 1) {
    layout.columns.forEach((column) => {
      if (row < column.length) order.push(column[row]);
    });
  }
  return order;
}

/** Where a tile is: ``{column, position}`` or null. */
export function tilePlace(layout, key) {
  for (let column = 0; column < layout.columns.length; column += 1) {
    const position = layout.columns[column].indexOf(key);
    if (position >= 0) return { column, position };
  }
  return null;
}

export function sameLayout(left, right) {
  return JSON.stringify(left?.columns || null) === JSON.stringify(right?.columns || null);
}

/** Whether the layout is just the default order for these plugins. */
export function isDefaultLayout(layout, keys) {
  return sameLayout(layout, defaultLayout(keys));
}
