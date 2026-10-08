/**
 * A small, purpose-made node canvas: pan/zoom, draggable nodes, bezier
 * wires between typed ports. No dependency - see
 * docs/notion-plugin-configurator-plan.md, Phase 3, for why: every
 * maintained vanilla-JS node editor needs a framework and a build step,
 * which this project's rules exclude, and the one that does not
 * (Drawflow) has been unmaintained since 2024.
 *
 * This file knows nothing about Notion or export mappings. It renders a
 * graph {nodes: [{id, kind, label, x, y, inputs: [{id,type}], outputs}],
 * edges: [{id, from: {node,port}, to: {node,port}}]} and reports changes;
 * `configurator-node-view.js` is what turns that into export-mapping
 * columns and back.
 */

const PORT_COLORS = {
  string: '#4b8bf5', number: '#2fa968', date: '#b5892a',
  list: '#8b5fd6', boolean: '#d6556f', any: '#8a8f98',
};

export function createNodeCanvas(container, { onChange } = {}) {
  const state = { nodes: [], edges: [], selected: null, scale: 1, panX: 0, panY: 0 };
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('class', 'node-canvas-wires');
  const nodeLayer = document.createElement('div');
  nodeLayer.className = 'node-canvas-nodes';
  const world = document.createElement('div');
  world.className = 'node-canvas-world';
  world.append(svg, nodeLayer);
  container.className = 'node-canvas';
  container.innerHTML = '';
  container.appendChild(world);

  let dragWire = null; // { fromNode, fromPort, fromKind: 'output'|'input', previewEl }
  let dragNode = null; // { id, offsetX, offsetY }
  let panDrag = null;

  function emitChange() {
    onChange?.(getGraph());
  }

  function applyTransform() {
    world.style.transform = `translate(${state.panX}px, ${state.panY}px) scale(${state.scale})`;
  }

  function portElement(nodeId, portId, direction) {
    return nodeLayer.querySelector(
      `[data-node-id="${nodeId}"] [data-port-id="${portId}"][data-port-direction="${direction}"]`,
    );
  }

  function portCenter(el) {
    const nodeRect = world.getBoundingClientRect();
    const portRect = el.getBoundingClientRect();
    return {
      x: (portRect.left + portRect.width / 2 - nodeRect.left) / state.scale,
      y: (portRect.top + portRect.height / 2 - nodeRect.top) / state.scale,
    };
  }

  function wirePath(a, b) {
    const dx = Math.max(40, Math.abs(b.x - a.x) / 2);
    return `M ${a.x} ${a.y} C ${a.x + dx} ${a.y}, ${b.x - dx} ${b.y}, ${b.x} ${b.y}`;
  }

  function redrawWires() {
    svg.innerHTML = '';
    for (const edge of state.edges) {
      const fromEl = portElement(edge.from.node, edge.from.port, 'output');
      const toEl = portElement(edge.to.node, edge.to.port, 'input');
      if (!fromEl || !toEl) continue;
      const path = document.createElementNS(svg.namespaceURI, 'path');
      path.setAttribute('d', wirePath(portCenter(fromEl), portCenter(toEl)));
      path.setAttribute('class', `node-canvas-wire${state.selected?.kind === 'edge' && state.selected.id === edge.id ? ' is-selected' : ''}`);
      path.dataset.edgeId = edge.id;
      path.addEventListener('click', (event) => {
        event.stopPropagation();
        state.selected = { kind: 'edge', id: edge.id };
        render();
      });
      svg.appendChild(path);
    }
    if (dragWire?.previewPoint) {
      const fromEl = portElement(dragWire.fromNode, dragWire.fromPort, dragWire.fromKind);
      if (fromEl) {
        const path = document.createElementNS(svg.namespaceURI, 'path');
        path.setAttribute('class', 'node-canvas-wire node-canvas-wire--preview');
        const a = portCenter(fromEl);
        path.setAttribute('d', dragWire.fromKind === 'output'
          ? wirePath(a, dragWire.previewPoint)
          : wirePath(dragWire.previewPoint, a));
        svg.appendChild(path);
      }
    }
  }

  function renderNode(node) {
    const el = document.createElement('div');
    el.className = `node-canvas-node${state.selected?.kind === 'node' && state.selected.id === node.id ? ' is-selected' : ''}`;
    el.dataset.nodeId = node.id;
    el.style.left = `${node.x}px`;
    el.style.top = `${node.y}px`;
    const port = (p, direction) => `
      <div class="node-canvas-port node-canvas-port--${direction}" data-port-id="${p.id}" data-port-direction="${direction}"
           style="--port-color:${PORT_COLORS[p.type] || PORT_COLORS.any}" title="${p.label || p.type}">
      </div>`;
    el.innerHTML = `
      <div class="node-canvas-node-header">${escapeHtml(node.label)}</div>
      ${node.detail ? `<div class="node-canvas-node-detail">${escapeHtml(node.detail)}</div>` : ''}
      <div class="node-canvas-node-ports">
        <div class="node-canvas-port-column">${(node.inputs || []).map((p) => `<div class="node-canvas-port-row">${port(p, 'input')}<span>${escapeHtml(p.label || p.type)}</span></div>`).join('')}</div>
        <div class="node-canvas-port-column node-canvas-port-column--outputs">${(node.outputs || []).map((p) => `<div class="node-canvas-port-row">${escapeHtml(p.label || p.type)}${port(p, 'output')}</div>`).join('')}</div>
      </div>`;
    el.addEventListener('pointerdown', (event) => {
      if (event.target.closest('.node-canvas-port')) return;
      state.selected = { kind: 'node', id: node.id };
      dragNode = { id: node.id, startX: event.clientX, startY: event.clientY, originX: node.x, originY: node.y };
      event.stopPropagation();
      render();
    });
    el.querySelectorAll('.node-canvas-port').forEach((portEl) => {
      portEl.addEventListener('pointerdown', (event) => {
        event.stopPropagation();
        const direction = portEl.dataset.portDirection;
        dragWire = {
          fromNode: node.id, fromPort: portEl.dataset.portId, fromKind: direction,
          previewPoint: toWorldPoint(event),
        };
        redrawWires();
      });
      portEl.addEventListener('pointerup', (event) => {
        if (!dragWire) return;
        event.stopPropagation();
        finishWire(node.id, portEl.dataset.portId, portEl.dataset.portDirection);
      });
    });
    return el;
  }

  function toWorldPoint(event) {
    const rect = world.getBoundingClientRect();
    return { x: (event.clientX - rect.left) / state.scale, y: (event.clientY - rect.top) / state.scale };
  }

  function finishWire(toNodeId, toPortId, toDirection) {
    if (dragWire && dragWire.fromKind !== toDirection && dragWire.fromNode !== toNodeId) {
      const output = dragWire.fromKind === 'output'
        ? { node: dragWire.fromNode, port: dragWire.fromPort }
        : { node: toNodeId, port: toPortId };
      const input = dragWire.fromKind === 'input'
        ? { node: dragWire.fromNode, port: dragWire.fromPort }
        : { node: toNodeId, port: toPortId };
      if (portType(output, 'output') === portType(input, 'input') || portType(input, 'input') === 'any' || portType(output, 'output') === 'any') {
        // Only one wire may feed a given input - a column has one source.
        state.edges = state.edges.filter((edge) => !(edge.to.node === input.node && edge.to.port === input.port));
        state.edges.push({ id: `e${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`, from: output, to: input });
        emitChange();
      }
    }
    dragWire = null;
    render();
  }

  function portType(ref, direction) {
    const node = state.nodes.find((n) => n.id === ref.node);
    const list = direction === 'output' ? node?.outputs : node?.inputs;
    return list?.find((p) => p.id === ref.port)?.type || 'any';
  }

  function render() {
    nodeLayer.innerHTML = '';
    state.nodes.forEach((node) => nodeLayer.appendChild(renderNode(node)));
    redrawWires();
  }

  container.addEventListener('pointerdown', (event) => {
    if (event.target === container || event.target === world || event.target === svg) {
      state.selected = null;
      panDrag = { startX: event.clientX, startY: event.clientY, originX: state.panX, originY: state.panY };
      render();
    }
  });
  window.addEventListener('pointermove', (event) => {
    if (dragNode) {
      const node = state.nodes.find((n) => n.id === dragNode.id);
      if (node) {
        node.x = dragNode.originX + (event.clientX - dragNode.startX) / state.scale;
        node.y = dragNode.originY + (event.clientY - dragNode.startY) / state.scale;
        render();
      }
    } else if (panDrag) {
      state.panX = panDrag.originX + (event.clientX - panDrag.startX);
      state.panY = panDrag.originY + (event.clientY - panDrag.startY);
      applyTransform();
    } else if (dragWire) {
      dragWire.previewPoint = toWorldPoint(event);
      redrawWires();
    }
  });
  window.addEventListener('pointerup', () => {
    if (dragNode) emitChange();
    dragNode = null;
    panDrag = null;
    if (dragWire) { dragWire = null; render(); }
  });
  container.addEventListener('wheel', (event) => {
    event.preventDefault();
    const next = Math.min(2, Math.max(0.4, state.scale * (event.deltaY < 0 ? 1.1 : 1 / 1.1)));
    state.scale = next;
    applyTransform();
    render();
  }, { passive: false });
  window.addEventListener('keydown', (event) => {
    if (!container.isConnected) return;
    if ((event.key === 'Delete' || event.key === 'Backspace') && state.selected
        && document.activeElement?.tagName !== 'INPUT' && document.activeElement?.tagName !== 'TEXTAREA') {
      if (state.selected.kind === 'node') removeNode(state.selected.id);
      else if (state.selected.kind === 'edge') removeEdge(state.selected.id);
    }
  });

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (ch) => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]
    ));
  }

  function addNode(node) {
    state.nodes.push({ x: 0, y: 0, inputs: [], outputs: [], ...node });
    render();
    emitChange();
    return node.id;
  }

  function removeNode(id) {
    state.nodes = state.nodes.filter((n) => n.id !== id);
    state.edges = state.edges.filter((e) => e.from.node !== id && e.to.node !== id);
    state.selected = null;
    render();
    emitChange();
  }

  function removeEdge(id) {
    state.edges = state.edges.filter((e) => e.id !== id);
    state.selected = null;
    render();
    emitChange();
  }

  function setGraph(graph) {
    state.nodes = (graph?.nodes || []).map((n) => ({ ...n }));
    state.edges = (graph?.edges || []).map((e) => ({ ...e }));
    state.selected = null;
    render();
  }

  function getGraph() {
    return { nodes: state.nodes.map((n) => ({ ...n })), edges: state.edges.map((e) => ({ ...e })) };
  }

  function zoomToFit() {
    if (!state.nodes.length) { state.panX = 0; state.panY = 0; state.scale = 1; applyTransform(); return; }
    const minX = Math.min(...state.nodes.map((n) => n.x));
    const maxX = Math.max(...state.nodes.map((n) => n.x + 220));
    const minY = Math.min(...state.nodes.map((n) => n.y));
    const maxY = Math.max(...state.nodes.map((n) => n.y + 90));
    const rect = container.getBoundingClientRect();
    const scale = Math.min(1.2, Math.max(0.4, Math.min(rect.width / (maxX - minX + 80), rect.height / (maxY - minY + 80)) || 1));
    state.scale = scale;
    state.panX = -minX * scale + 40;
    state.panY = -minY * scale + 40;
    applyTransform();
    render();
  }

  applyTransform();
  return { addNode, removeNode, removeEdge, setGraph, getGraph, zoomToFit, render };
}
