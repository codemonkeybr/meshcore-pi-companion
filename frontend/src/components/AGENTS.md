# PacketVisualizer Architecture

This document explains the architecture and design of the PacketVisualizer component, which renders a real-time force-directed graph visualization of mesh network packet traffic.

## Overview

The PacketVisualizer displays:

- **Nodes**: Network participants (self, repeaters, clients)
- **Links**: Connections between nodes based on observed packet paths
- **Particles**: Animated dots traveling along links representing packets in transit

## Architecture: Data Layer vs Rendering Layer

The component is split into two distinct layers to enable future rendering engine swaps (e.g., WebGL, Three.js):

### Data Layer (`useVisualizerData` hook)

The custom hook manages all graph state and simulation logic:

```
Packets → Parse → Aggregate by key → Observation window → Publish → Animate
```

**Key responsibilities:**

- Maintains node and link maps (`nodesRef`, `linksRef`)
- Runs D3 force simulation for layout
- Processes incoming packets with deduplication
- Aggregates packet repeats across multiple paths
- Manages particle queue and animation timing

**State:**

- `nodesRef`: Map of node ID → GraphNode
- `linksRef`: Map of link key → GraphLink
- `particlesRef`: Array of active Particle objects
- `simulationRef`: D3 force simulation instance
- `pendingRef`: Packets in observation window awaiting animation
- `timersRef`: Per-packet publish timers

### Rendering Layer (canvas drawing functions)

Separate pure functions handle all canvas rendering:

- `renderLinks()`: Draws connections between nodes
- `renderParticles()`: Draws animated packets with labels
- `renderNodes()`: Draws node circles with emojis/text

The main component orchestrates rendering via `requestAnimationFrame`.

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

| Packet Type    | Key Format                                |
| -------------- | ----------------------------------------- |
| Advertisement  | `ad:{pubkey_prefix_12}`                   |
| Group Text     | `gt:{channel}:{sender}:{content_hash}`    |
| Direct Message | `dm:{src_hash}:{dst_hash}:{content_hash}` |
| Other          | `other:{data_hash}`                       |

### 3. Observation Window

Same packets arriving via different paths are aggregated:

```typescript
if (existing && now < existing.expiresAt) {
  // Append path to existing entry
  existing.paths.push({ nodes: path, snr: packet.snr, timestamp: now });
} else {
  // Create new pending entry with 2-second observation window
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

## D3 Force Simulation

The layout uses D3's force simulation with these forces:

| Force         | Purpose                                              |
| ------------- | ---------------------------------------------------- |
| `link`        | Pulls connected nodes together                       |
| `charge`      | Repels nodes from each other (self node 6x stronger) |
| `center`      | Gently pulls graph toward center                     |
| `collide`     | Prevents node overlap                                |
| `selfX/selfY` | Anchors self node near center                        |

### Shuffle Layout

The "Shuffle layout" button randomizes all node positions (except self, which stays centered) and reheats the simulation to alpha=1. This lets users try different random starting configurations to find a cleaner layout.

### Continuous Drift

When "Let 'em drift" is enabled, `alphaTarget(0.05)` keeps the simulation running indefinitely, allowing the graph to continuously reorganize into better layouts.

## Node Resolution

Nodes are resolved from various sources:

```typescript
function resolveNode(source, isRepeater, showAmbiguous): string | null {
  // source.type can be: 'pubkey', 'prefix', or 'name'
  // Try to find matching contact
  // If found: use full 12-char prefix as node ID
  // If not found and showAmbiguous: create "?prefix" node
  // Otherwise: return null (path terminates)
}
```

### Ambiguous Nodes

When only a 1-byte prefix is known (from packet path bytes), the node is marked ambiguous and shown with a `?` prefix and gray styling.

### Traffic Pattern Splitting (Experimental)

**Problem:** Multiple physical repeaters can share the same 1-byte prefix (collision). Since packet paths only contain 1-byte hashes, we can't directly distinguish them. However, traffic patterns provide a heuristic.

**Key Insight:** If packets from different sources all route through prefix `32` to the same next hop, it's likely the same physical node. But if `32` routes to different next hops depending on the source, those are likely different physical nodes.

**Example:**

```
ae -> 32 -> ba -> self
c1 -> 32 -> ba -> self
d1 -> 32 -> 60 -> self
d1 -> 32 -> 60 -> self
```

Here we can deduce:

- The `32` that routes to `ba` is likely one physical repeater
- The `32` that routes to `60` is likely a DIFFERENT physical repeater

**Algorithm:** When "Split by traffic pattern" is enabled:

1. **Intermediate repeaters** (has next hop in path): Node ID includes the next hop suffix
   - `?32:>ba` - the `32` that routes to `ba`
   - `?32:>60` - the `32` that routes to `60`

2. **Final repeaters** (no next hop, connects directly to destination): No suffix added
   - Stays as simple `?ba`, `?60` etc.
   - Rationale: The last repeater before you is clearly a single physical node regardless of where traffic originates

**Why only NEXT hop matters:**

- We DON'T key on the previous node because multiple sources going through the same repeater to the same destination = same physical node
- We DO key on next hop because a repeater routing to different destinations suggests prefix collision

**Node ID format:**

- Without splitting: `?XX` (e.g., `?32`)
- With splitting (intermediate): `?XX:>YY` (e.g., `?32:>ba`)
- With splitting (final): `?XX` (unchanged, no suffix)

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

Different packet types provide different levels of sender identification:

| Packet Type    | Sender Info Available          | Resolution                     |
| -------------- | ------------------------------ | ------------------------------ |
| Advertisement  | Full 32-byte public key        | Exact contact match            |
| AnonRequest    | Full 32-byte public key        | Exact contact match            |
| Group Text     | Sender name (after decryption) | Name lookup                    |
| Direct Message | 1-byte source hash             | Ambiguous (may match multiple) |
| Request        | 1-byte source hash             | Ambiguous                      |
| Other          | None                           | Path bytes only                |

**AnonRequest packets** are particularly useful because they include the sender's full public key (unlike regular Request packets which only have a 1-byte hash). This allows exact identification of who is making the request.

## Canvas Rendering

### Coordinate Transformation

Pan and zoom are applied via transform matrix:

```typescript
ctx.setTransform(dpr * scale, 0, 0, dpr * scale, dpr * (x + panX), dpr * (y + panY));
```

### Render Order

1. Clear canvas with background
2. Draw links (gray lines)
3. Draw particles (colored dots with labels)
4. Draw nodes (circles with emojis)
5. Draw hover tooltip if applicable

## Configuration Options

| Option                     | Default | Description                                               |
| -------------------------- | ------- | --------------------------------------------------------- |
| Ambiguous path repeaters   | On      | Show nodes when only partial prefix known                 |
| Ambiguous sender/recipient | Off     | Show placeholder nodes for unknown senders                |
| Split by traffic pattern   | Off     | Split ambiguous repeaters by next-hop routing (see above) |
| Hide repeaters >48hrs      | Off     | Filter out old repeaters                                  |
| Observation window         | 15 sec  | Wait time for duplicate packets before animating (1-60s)  |
| Let 'em drift              | On      | Continuous layout optimization                            |
| Repulsion                  | 200     | Force strength (50-2500)                                  |
| Packet speed               | 2x      | Particle animation speed multiplier (1x-5x)               |
| Shuffle layout             | -       | Button to randomize node positions and reheat sim         |
| Oooh Big Stretch!          | -       | Button to temporarily increase repulsion then relax       |
| Hide UI                    | Off     | Hide legends and most controls for cleaner view           |
| Full screen                | Off     | Hide the packet feed panel (desktop only)                 |

## File Structure

```
PacketVisualizer.tsx
├── TYPES (GraphNode, GraphLink, Particle, etc.)
├── CONSTANTS (colors, timing, legend items)
├── UTILITY FUNCTIONS
│   ├── simpleHash()
│   ├── parsePacket()
│   ├── getPacketLabel()
│   ├── generatePacketKey()
│   ├── findContactBy*()
│   └── dedupeConsecutive()
├── DATA LAYER HOOK (useVisualizerData)
│   ├── Refs (nodes, links, particles, simulation, pending, timers)
│   ├── Simulation initialization
│   ├── Node/link management (addNode, addLink, syncSimulation)
│   ├── Path building (resolveNode, buildPath)
│   └── Packet processing & publishing
├── RENDERING FUNCTIONS
│   ├── renderLinks()
│   ├── renderParticles()
│   └── renderNodes()
└── MAIN COMPONENT (PacketVisualizer)
    ├── State (dimensions, options, transform, hover)
    ├── Event handlers (mouse, wheel)
    ├── Animation loop
    └── JSX (canvas, legend, settings panel)
```

## Performance Considerations

- **Observation window**: 2 seconds balances latency vs. path aggregation
- **Max links**: Capped at 100 to prevent graph explosion
- **Particle culling**: Particles removed when progress > 1
- **Node filtering**: Old repeaters can be hidden to reduce clutter
- **requestAnimationFrame**: Render loop tied to display refresh rate

## Future Improvements

The data/rendering split enables:

- WebGL rendering for larger graphs
- 3D visualization
- Different layout algorithms
- Export to other formats
