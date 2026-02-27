# PacketVisualizer3D Architecture

This document explains the architecture and design of the `PacketVisualizer3D` component, which renders a real-time 3D force-directed graph visualization of mesh network packet traffic using Three.js and `d3-force-3d`.

## Overview

The visualizer displays:

- **Nodes**: Network participants (self, repeaters, clients) as colored spheres
- **Links**: Connections between nodes based on observed packet paths
- **Particles**: Animated colored dots traveling along links representing packets in transit

## Architecture

### Data Layer (`useVisualizerData3D` hook)

The custom hook manages all graph state and simulation logic:

```
Packets → Parse → Aggregate by key → Observation window → Publish → Animate
```

**Key responsibilities:**

- Maintains node and link maps (`nodesRef`, `linksRef`)
- Runs `d3-force-3d` simulation for 3D layout (`.numDimensions(3)`)
- Processes incoming packets with deduplication
- Aggregates packet repeats across multiple paths
- Manages particle queue and animation timing

**State:**

- `nodesRef`: Map of node ID → GraphNode
- `linksRef`: Map of link key → GraphLink
- `particlesRef`: Array of active Particle objects
- `simulationRef`: d3-force-3d simulation instance
- `pendingRef`: Packets in observation window awaiting animation
- `timersRef`: Per-packet publish timers

### Rendering Layer (Three.js)

- `THREE.WebGLRenderer` + `CSS2DRenderer` (text labels overlaid on 3D scene)
- `OrbitControls` for camera interaction (orbit, pan, zoom)
- `THREE.Mesh` with `SphereGeometry` per node + `CSS2DObject` labels
- `THREE.LineSegments` for links (persistent geometries; position buffers updated in-place each frame)
- `THREE.Points` with vertex colors for particles (persistent geometry + circular sprite texture)
- `THREE.Raycaster` for hover/click detection on node spheres

### Shared Utilities (`utils/visualizerUtils.ts`)

Types, constants, and pure functions shared across the codebase:

- Types: `NodeType`, `PacketLabel`, `Particle`, `ObservedPath`, `PendingPacket`, `ParsedPacket`, `TrafficObservation`, `RepeaterTrafficData`, `RepeaterSplitAnalysis`
- Constants: `COLORS`, `PARTICLE_COLOR_MAP`, `PARTICLE_SPEED`, `DEFAULT_OBSERVATION_WINDOW_SEC`, traffic thresholds, `PACKET_LEGEND_ITEMS`
- Functions: `simpleHash`, `parsePacket`, `getPacketLabel`, `generatePacketKey`, `getLinkId`, `getNodeType`, `dedupeConsecutive`, `analyzeRepeaterTraffic`, `recordTrafficObservation`

`GraphNode` and `GraphLink` are defined locally in the component — they extend `SimulationNodeDatum3D` and `SimulationLinkDatum` from `d3-force-3d`.

### Type Declarations (`types/d3-force-3d.d.ts`)

Minimal `.d.ts` file for `d3-force-3d` which has no bundled TypeScript types. Declares `SimulationNodeDatum3D` extending `SimulationNodeDatum` with `z`/`vz`/`fz` properties.

## Packet Processing Pipeline

### 1. Packet Arrival

When a new packet arrives from the WebSocket:

```typescript
packets.forEach((packet) => {
  if (processedRef.current.has(packet.id)) return; // Skip duplicates
  processedRef.current.add(packet.id);

  const parsed = parsePacket(packet.data);
  const key = generatePacketKey(parsed, packet);
  // ...
});
```

### 2. Key Generation

Packets are grouped by a unique key to aggregate repeats:

| Packet Type    | Key Format                                             |
| -------------- | ------------------------------------------------------ |
| Advertisement  | `ad:{pubkey_prefix_12}`                                |
| Group Text     | `gt:{channel}:{sender}:{message_hash_or_data_hash}`    |
| Direct Message | `dm:{src_hash}:{dst_hash}:{message_hash_or_data_hash}` |
| Other          | `other:{message_hash_or_data_hash}`                    |

`parsePacket()` exposes decoder `messageHash` (path-insensitive). `generatePacketKey()` prefers that hash, falling back to a local data hash for malformed/unsupported packets.

### 3. Observation Window

Same packets arriving via different paths are aggregated:

```typescript
if (existing && now < existing.expiresAt) {
  // Append path to existing entry
  existing.paths.push({ nodes: path, snr: packet.snr, timestamp: now });
} else {
  // Create new pending entry with observation window
  pendingPacketsRef.current.set(key, {
    key,
    label,
    paths: [{ nodes: path, ... }],
    expiresAt: now + OBSERVATION_WINDOW_MS,
  });
}
```

### 4. Publishing & Animation

When the observation window expires, all paths animate simultaneously:

```typescript
function publishPacket(pending: PendingPacket) {
  // Ensure all nodes exist in graph
  // Create links between consecutive nodes
  // Queue particles for ALL paths at once

  for (const observedPath of pending.paths) {
    for (let i = 0; i < path.length - 1; i++) {
      // Spawn particle with negative initial progress for smooth flow
      particlesRef.current.push({
        progress: -(i * HOP_DELAY), // Stagger by hop index
        // ...
      });
    }
  }
}
```

**Key insight:** Particles start with negative progress. This creates smooth flow through multi-hop paths without pausing at intermediate nodes.

## D3 Force Simulation (3D)

The layout uses `d3-force-3d` with `.numDimensions(3)`:

| Force               | Purpose                                        |
| ------------------- | ---------------------------------------------- |
| `link`              | Pulls connected nodes together (distance: 120) |
| `charge`            | Repels nodes (self node 6x stronger, max: 800) |
| `center`            | Gently pulls graph toward origin (0, 0, 0)     |
| `selfX/selfY/selfZ` | Anchors self node near origin                  |

### Shuffle Layout

The "Shuffle layout" button randomizes all node positions in a 3D sphere (radius 200, except self stays at origin) and reheats the simulation to alpha=1.

### Continuous Drift

When "Let 'em drift" is enabled, `alphaTarget(0.05)` keeps the simulation running indefinitely, allowing the graph to continuously reorganize.

### Expand/Contract ("Oooh Big Stretch!")

Temporarily increases repulsion to push nodes apart, then relaxes back. Useful for untangling dense graphs.

## Node Resolution

Nodes are resolved from various sources:

```typescript
function resolveNode(source, isRepeater, showAmbiguous): string | null {
  // source.type can be: 'pubkey', 'prefix', or 'name'
  // Use precomputed contact indexes (by 12-char prefix, by name, by shorter prefixes)
  // If found: use full 12-char prefix as node ID
  // If not found and showAmbiguous: create "?prefix" node
  // Otherwise: return null (path terminates)
}
```

### Ambiguous Nodes

When only a 1-byte prefix is known (from packet path bytes), the node is marked ambiguous and shown with a `?` prefix and gray styling. However, if the node is identified as a repeater (via advert or path hop), it shows blue regardless of ambiguity.

### Advert-Path Identity Hints

**Problem:** When multiple repeaters share a 1-byte prefix, the visualizer can't tell which physical repeater a path hop refers to.

**Solution:** The backend tracks recent unique advertisement paths per contact in `contact_advert_paths` (see root `AGENTS.md` § "Contact Advert Path Memory"). On mount (and when new contacts appear), the visualizer fetches this data via `GET /api/contacts/repeaters/advert-paths` and builds an index keyed by 12-char prefix.

**Scoring:** `pickLikelyRepeaterByAdvertPath(candidates, nextPrefix)` scores each candidate repeater by how often its stored advert paths' `next_hop` matches the packet's actual next-hop prefix. It requires:

- At least 2 matching observations (stronger-than-trivial evidence)
- The top candidate's match score must be at least 2x the runner-up's

When a winner is found, the ambiguous node gets a `probableIdentity` label (the likely repeater's name) and the display name updates accordingly. The remaining candidates are listed as "Other possible" in the tooltip.

**Interaction with traffic splitting:** Advert-path hints run first. If a probable identity is found, the display name is set. Traffic splitting can still produce separate node IDs (`?XX:>YY`), but won't overwrite the advert-path display name.

**Toggle:** "Use repeater advert-path identity hints" checkbox (enabled by default, disabled when ambiguous repeaters are hidden).

### Traffic Pattern Splitting (Experimental)

**Problem:** Multiple physical repeaters can share the same 1-byte prefix (collision). Since packet paths only contain 1-byte hashes, we can't directly distinguish them. However, traffic patterns provide a heuristic.

**Key Insight:** A single physical repeater (even acting as a hub) will have the same sources routing through it regardless of next-hop. But if prefix `32` has completely disjoint sets of sources for different next-hops, those are likely different physical nodes sharing the same prefix.

**Example:**

```
ae -> 32 -> ba -> self   (source: ae)
c1 -> 32 -> ba -> self   (source: c1)
d1 -> 32 -> 60 -> self   (source: d1)
e2 -> 32 -> 60 -> self   (source: e2)
```

Analysis:

- Sources {ae, c1} always route through `32` to `ba`
- Sources {d1, e2} always route through `32` to `60`
- These source sets are **disjoint** (no overlap)
- Conclusion: Likely two different physical repeaters sharing prefix `32`

Counter-example (same physical hub):

```
ae -> 32 -> ba -> self
ae -> 32 -> 60 -> self   (same source 'ae' routes to different next-hops!)
```

Here source `ae` routes through `32` to BOTH `ba` and `60`. This proves `32` is a single physical hub node with multiple downstream paths. No splitting should occur.

**Algorithm:** When "Heuristically group repeaters by traffic pattern" is enabled:

1. **Record observations** for each ambiguous repeater: `(packetSource, nextHop)` tuples
2. **Analyze disjointness**: Group sources by their next-hop, check for overlap
3. **Split conservatively**: Only split when:
   - Multiple distinct next-hop groups exist
   - Source sets are completely disjoint (no source appears in multiple groups)
   - Each group has at least 20 unique sources (conservative threshold)
4. **Final repeaters** (no next hop, connects directly to self): Never split

**Node ID format:**

- Without splitting (default): `?XX` (e.g., `?32`)
- With splitting (after evidence threshold met): `?XX:>YY` (e.g., `?32:>ba`)
- Final repeater: `?XX` (unchanged, no suffix)

## Path Building

Paths are constructed from packet data:

```typescript
function buildPath(parsed, packet, myPrefix): string[] {
  const path = [];

  // 1. Add source node (from advert pubkey, DM src hash, or group text sender)
  // 2. Add repeater path (from path bytes in packet header)
  // 3. Add destination (self for incoming, or DM dst hash for outgoing)

  return dedupeConsecutive(path); // Remove consecutive duplicates
}
```

## Packet Types & Colors

| Label | Type           | Color            |
| ----- | -------------- | ---------------- |
| AD    | Advertisement  | Amber (#f59e0b)  |
| GT    | Group Text     | Cyan (#06b6d4)   |
| DM    | Direct Message | Purple (#8b5cf6) |
| ACK   | Acknowledgment | Green (#22c55e)  |
| TR    | Trace          | Orange (#f97316) |
| RQ    | Request        | Pink (#ec4899)   |
| RS    | Response       | Teal (#14b8a6)   |
| ?     | Unknown        | Gray (#6b7280)   |

### Sender Extraction by Packet Type

| Packet Type    | Sender Info Available          | Resolution                     |
| -------------- | ------------------------------ | ------------------------------ |
| Advertisement  | Full 32-byte public key        | Exact contact match            |
| AnonRequest    | Full 32-byte public key        | Exact contact match            |
| Group Text     | Sender name (after decryption) | Name lookup                    |
| Direct Message | 1-byte source hash             | Ambiguous (may match multiple) |
| Request        | 1-byte source hash             | Ambiguous                      |
| Other          | None                           | Path bytes only                |

## Node Colors

| Color   | Hex       | Meaning                                        |
| ------- | --------- | ---------------------------------------------- |
| Green   | `#22c55e` | Self node (larger sphere)                      |
| Blue    | `#3b82f6` | Repeater                                       |
| White   | `#ffffff` | Client                                         |
| Gray    | `#9ca3af` | Unknown/ambiguous (not identified as repeater) |
| Gold    | `#ffd700` | Active (hovered or pinned) node                |
| Lt Gold | `#fff0b3` | Neighbors of active node                       |

## Mouse Interactions

| Action                     | Behavior                                    |
| -------------------------- | ------------------------------------------- |
| Left-click on node         | Pin: highlight node + links + neighbors     |
| Left-click pinned node     | Unpin: remove highlights                    |
| Left-click empty space     | Unpin any pinned node                       |
| Hover over node            | Shows tooltip with node details + neighbors |
| Orbit (left-drag on space) | Rotate camera around scene                  |
| Pan (right-drag)           | Pan the camera                              |
| Scroll wheel               | Zoom in/out                                 |

**Click-to-pin:** When a node is pinned, hovering other nodes does not change the highlight. The tooltip shows "Traffic exchanged with:" listing all connected neighbors with their possible names.

## Configuration Options

| Option                     | Default | Description                                               |
| -------------------------- | ------- | --------------------------------------------------------- |
| Ambiguous repeaters        | On      | Show nodes when only partial prefix known                 |
| Ambiguous sender/recipient | Off     | Show placeholder nodes for unknown senders                |
| Advert-path identity hints | On      | Use stored advert paths to label ambiguous repeaters      |
| Split by traffic pattern   | Off     | Split ambiguous repeaters by next-hop routing (see above) |
| Observation window         | 15 sec  | Wait time for duplicate packets before animating (1-60s)  |
| Let 'em drift              | On      | Continuous layout optimization                            |
| Repulsion                  | 200     | Force strength (50-2500)                                  |
| Packet speed               | 2x      | Particle animation speed multiplier (1x-5x)               |
| Shuffle layout             | -       | Button to randomize node positions and reheat sim         |
| Oooh Big Stretch!          | -       | Button to temporarily increase repulsion then relax       |
| Clear & Reset              | -       | Button to clear all nodes, links, and packets             |
| Hide UI                    | Off     | Hide legends and most controls for cleaner view           |
| Full screen                | Off     | Hide the packet feed panel (desktop only)                 |

## File Structure

```
PacketVisualizer3D.tsx
├── TYPES (GraphNode extends SimulationNodeDatum3D, GraphLink)
├── CONSTANTS (NODE_COLORS, NODE_LEGEND_ITEMS)
├── DATA LAYER HOOK (useVisualizerData3D)
│   ├── Refs (nodes, links, particles, simulation, pending, timers, trafficPatterns, stretchRaf)
│   ├── d3-force-3d simulation initialization (.numDimensions(3))
│   ├── Contact indexing (byPrefix12 / byName / byPrefix)
│   ├── Node/link management (addNode, addLink, syncSimulation)
│   ├── Path building (resolveNode, buildPath)
│   ├── Traffic pattern analysis (for repeater disambiguation)
│   └── Packet processing & publishing
└── MAIN COMPONENT (PacketVisualizer3D)
    ├── Three.js scene setup (WebGLRenderer, CSS2DRenderer, OrbitControls)
    ├── Node mesh management (SphereGeometry + CSS2DObject labels)
    ├── Link rendering (persistent LineSegments + dynamic position buffer updates)
    ├── Particle rendering (persistent Points + dynamic position/color buffer updates)
    ├── Raycasting (hover detection, click-to-pin)
    ├── State (options, pinned/hovered node, neighbors; change-detected UI updates)
    └── JSX (container, legend overlay, options panel, tooltip)

utils/visualizerUtils.ts
├── Types (NodeType, PacketLabel, Particle, PendingPacket, ParsedPacket, etc.)
├── Constants (COLORS, PARTICLE_COLOR_MAP, PARTICLE_SPEED, PACKET_LEGEND_ITEMS)
└── Functions (parsePacket, generatePacketKey, analyzeRepeaterTraffic, etc.)

types/d3-force-3d.d.ts
└── Type declarations for d3-force-3d (SimulationNodeDatum3D, Simulation3D, forces)
```

## Performance Considerations

- **Observation window**: Configurable (default 15s) to balance latency vs. path aggregation
- **Persistent geometries**: Links/highlights/particles are created once; buffers are updated per frame to reduce GC/GPU churn
- **Particle culling**: Particles removed when progress > 1
- **Change-detected React updates**: Hover + neighbor UI state updates only when values change
- **requestAnimationFrame**: Render loop tied to display refresh rate
- **CSS2DRenderer z-index**: Set to 1 so UI overlays (z-10) render above node labels
