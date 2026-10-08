import assert from 'node:assert/strict';
import {
  columnsFromGraph,
  graphFromTarget,
} from '../../study_runner/plugins/destinations/notion_upload/ui/configurator-node-view.js';

const catalog = [
  { source: 'session.participant_id', label: 'Participant ID', value_type: 'string' },
  { source: 'card.stream.brainbit_eeg.channel.alpha.mean', label: 'brainbit / alpha / mean', value_type: 'number', per_card: true },
];

// A plain column (no transform) round-trips to the same source/type.
{
  const target = { columns: { Participant: { type: 'rich_text', source: 'session.participant_id', reducer: null } } };
  const graph = graphFromTarget(target, catalog);
  assert.equal(graph.nodes.filter((n) => n.kind === 'source').length, 1);
  assert.equal(graph.nodes.filter((n) => n.kind === 'column').length, 1);
  assert.equal(graph.edges.length, 1);
  const columns = columnsFromGraph(graph);
  assert.deepEqual(columns.Participant, { type: 'rich_text', source: 'session.participant_id', reducer: null });
}

// A reducer sits as its own node between the source and the column, and
// decompiles back onto the column exactly as it was.
{
  const target = { columns: {
    Alpha: { type: 'number', source: 'card.stream.brainbit_eeg.channel.alpha.mean', reducer: 'mean' },
  } };
  const graph = graphFromTarget(target, catalog);
  const reducerNode = graph.nodes.find((n) => n.kind === 'reducer');
  assert.equal(reducerNode.reducer, 'mean');
  const columns = columnsFromGraph(graph);
  assert.equal(columns.Alpha.source, 'card.stream.brainbit_eeg.channel.alpha.mean');
  assert.equal(columns.Alpha.reducer, 'mean');
}

// Round and default-if-empty chain after the reducer, same order
// mapping.py's _apply_transforms runs them in.
{
  const target = { columns: {
    Alpha: {
      type: 'number', source: 'card.stream.brainbit_eeg.channel.alpha.mean', reducer: 'mean',
      round_decimals: 2,
    },
  } };
  const graph = graphFromTarget(target, catalog);
  assert.ok(graph.nodes.some((n) => n.kind === 'round' && n.decimals === 2));
  const columns = columnsFromGraph(graph);
  assert.equal(columns.Alpha.round_decimals, 2);
}

// Two columns sharing one source share one source node - not duplicated.
{
  const target = { columns: {
    A: { type: 'rich_text', source: 'session.participant_id', reducer: null },
    B: { type: 'rich_text', source: 'session.participant_id', reducer: null },
  } };
  const graph = graphFromTarget(target, catalog);
  assert.equal(graph.nodes.filter((n) => n.kind === 'source').length, 1);
  assert.equal(graph.nodes.filter((n) => n.kind === 'column').length, 2);
}

// A column node with no incoming wire compiles to an empty source, exactly
// like an unmapped row in the simple column list.
{
  const graph = { nodes: [{ id: 'col:X', kind: 'column', columnName: 'X', columnType: 'rich_text' }], edges: [] };
  assert.deepEqual(columnsFromGraph(graph).X, { type: 'rich_text', source: '', reducer: null });
}

console.log('notion-configurator-node-view: ok');
