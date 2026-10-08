/**
 * Bridges one target's columns to node-canvas.js's graph, and back.
 *
 * A column with no transform is a two-node chain, Source -> Column - the
 * same thing the simple column list already is, just drawn. A reducer,
 * then an optional Round or Default-if-empty, insert as nodes in between.
 * This is deliberately the only shape the compiler understands: it keeps
 * decompiling a graph back into {type, source, reducer, round_decimals,
 * default_if_empty} unambiguous. See mapping.py's `_apply_transforms` for
 * where Round and Default run, after the reducer, same as here.
 */
import { createNodeCanvas } from './node-canvas.js';

const REDUCERS = ['mean', 'min', 'max', 'count', 'join', 'first', 'last'];

function valueTypeOf(source, catalog) {
  const entry = catalog.find((item) => item.source === source);
  return entry?.value_type === 'number' ? 'number' : entry?.per_card ? 'list' : (entry?.value_type || 'string');
}

function sourceLabel(source, catalog) {
  return catalog.find((item) => item.source === source)?.label || source || '(no source)';
}

function graphFromTarget(target, catalog) {
  const nodes = [];
  const edges = [];
  const edge = (from, to) => ({ id: `e${from.node}-${to.node}`, from, to });
  const sourceNodes = new Map();
  let row = 0;
  for (const [name, column] of Object.entries(target.columns)) {
    const y = 30 + row * 150;
    row += 1;
    const srcId = sourceNodes.get(column.source) || `src:${sourceNodes.size}:${Date.now().toString(36)}`;
    if (!sourceNodes.has(column.source)) {
      sourceNodes.set(column.source, srcId);
      const valueType = valueTypeOf(column.source, catalog);
      nodes.push({
        id: srcId, kind: 'source', label: sourceLabel(column.source, catalog), detail: column.source,
        x: 20, y: 30 + (sourceNodes.size - 1) * 110,
        outputs: [{ id: 'out', type: valueType === 'list' ? 'list' : valueType, label: valueType }],
      });
    }
    let fromRef = { node: srcId, port: 'out' };
    let x = 300;
    if (column.reducer) {
      const id = `red:${name}`;
      nodes.push({
        id, kind: 'reducer', label: 'Reduce', detail: column.reducer, x, y,
        inputs: [{ id: 'in', type: 'list', label: 'list' }],
        outputs: [{ id: 'out', type: 'any', label: 'value' }],
        reducer: column.reducer,
      });
      edges.push(edge(fromRef, { node: id, port: 'in' }));
      fromRef = { node: id, port: 'out' };
      x += 220;
    }
    if (Number.isInteger(column.round_decimals)) {
      const id = `round:${name}`;
      nodes.push({
        id, kind: 'round', label: 'Round', detail: `${column.round_decimals} decimals`, x, y,
        inputs: [{ id: 'in', type: 'any', label: 'number' }], outputs: [{ id: 'out', type: 'number', label: 'number' }],
        decimals: column.round_decimals,
      });
      edges.push(edge(fromRef, { node: id, port: 'in' }));
      fromRef = { node: id, port: 'out' };
      x += 220;
    } else if (column.default_if_empty !== undefined && column.default_if_empty !== null) {
      const id = `default:${name}`;
      nodes.push({
        id, kind: 'default', label: 'Default if empty', detail: String(column.default_if_empty), x, y,
        inputs: [{ id: 'in', type: 'any', label: 'value' }], outputs: [{ id: 'out', type: 'any', label: 'value' }],
        value: column.default_if_empty,
      });
      edges.push(edge(fromRef, { node: id, port: 'in' }));
      fromRef = { node: id, port: 'out' };
      x += 220;
    }
    const columnId = `col:${name}`;
    nodes.push({
      id: columnId, kind: 'column', label: name, detail: column.type, x, y,
      inputs: [{ id: 'in', type: 'any', label: column.type }], columnName: name, columnType: column.type,
    });
    edges.push(edge(fromRef, { node: columnId, port: 'in' }));
  }
  return { nodes, edges };
}

/** The inverse of `graphFromTarget`: every "column" node's incoming chain
 * becomes one `{type, source, reducer, round_decimals, default_if_empty}`. */
function columnsFromGraph(graph) {
  const byId = new Map(graph.nodes.map((node) => [node.id, node]));
  const incomingByTarget = new Map(graph.edges.map((e) => [`${e.to.node}:${e.to.port}`, e]));
  const columns = {};
  for (const node of graph.nodes) {
    if (node.kind !== 'column') continue;
    const column = { type: node.columnType, source: '', reducer: null };
    let cursor = node;
    let guard = 0;
    while (guard++ < 8) {
      const incoming = incomingByTarget.get(`${cursor.id}:in`);
      if (!incoming) break;
      const upstream = byId.get(incoming.from.node);
      if (!upstream) break;
      if (upstream.kind === 'source') { column.source = upstream.detail; break; }
      if (upstream.kind === 'reducer') column.reducer = upstream.reducer;
      if (upstream.kind === 'round') column.round_decimals = upstream.decimals;
      if (upstream.kind === 'default') column.default_if_empty = upstream.value;
      cursor = upstream;
    }
    columns[node.columnName] = column;
  }
  return columns;
}

/**
 * Mounts a node canvas for one target into `container` and keeps it in
 * sync with `target.columns`. `onCommit(columns)` fires on every graph edit
 * (debounced by node-canvas's own change event), so the caller can mark the
 * mapping dirty the same way the simple column list does.
 */
export function mountNodeView(container, target, catalog, { onCommit } = {}) {
  const canvas = createNodeCanvas(container, {
    onChange: (graph) => onCommit?.(columnsFromGraph(graph)),
  });
  canvas.setGraph(graphFromTarget(target, catalog));
  canvas.zoomToFit();

  function addSourceNode(source) {
    const valueType = valueTypeOf(source, catalog);
    canvas.addNode({
      id: `src:${Date.now().toString(36)}`, kind: 'source', label: sourceLabel(source, catalog), detail: source,
      x: 20, y: 20, outputs: [{ id: 'out', type: valueType === 'list' ? 'list' : valueType, label: valueType }],
    });
  }
  function addReducerNode(reducer) {
    canvas.addNode({
      id: `red:${Date.now().toString(36)}`, kind: 'reducer', label: 'Reduce', detail: reducer, x: 300, y: 20,
      inputs: [{ id: 'in', type: 'list', label: 'list' }], outputs: [{ id: 'out', type: 'any', label: 'value' }],
      reducer,
    });
  }
  function addRoundNode(decimals) {
    canvas.addNode({
      id: `round:${Date.now().toString(36)}`, kind: 'round', label: 'Round', detail: `${decimals} decimals`, x: 500, y: 20,
      inputs: [{ id: 'in', type: 'any', label: 'number' }], outputs: [{ id: 'out', type: 'number', label: 'number' }],
      decimals,
    });
  }
  function addDefaultNode(value) {
    canvas.addNode({
      id: `default:${Date.now().toString(36)}`, kind: 'default', label: 'Default if empty', detail: value, x: 500, y: 20,
      inputs: [{ id: 'in', type: 'any', label: 'value' }], outputs: [{ id: 'out', type: 'any', label: 'value' }],
      value,
    });
  }
  function addColumnNode(name, type) {
    canvas.addNode({
      id: `col:${name}`, kind: 'column', label: name, detail: type, x: 740, y: 20,
      inputs: [{ id: 'in', type: 'any', label: type }], columnName: name, columnType: type,
    });
  }

  return { addSourceNode, addReducerNode, addRoundNode, addDefaultNode, addColumnNode, zoomToFit: canvas.zoomToFit };
}

// Pure data transforms, exported for tests: neither touches the DOM.
export { REDUCERS, graphFromTarget, columnsFromGraph };
