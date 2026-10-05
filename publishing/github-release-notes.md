# Changelog: Velox v0.1.0 (1.21.1)

First release of **Velox**: dropped items stop costing the server a full physics pass each. Their movement is computed in batches by the **Celeris** physics engine and applied inside the normal item tick, so pickup, merging, despawn and everything else behave as before.

---

## Key Features & Changes

* **Batched Item Physics**: Every item in a dimension is simulated in one pass before entities tick, on Celeris's native SIMD kernel and worker threads.
* **Vanilla Behaviour**: Items on plain terrain follow vanilla's movement rules; near fluids, partial or special blocks, boats, minecarts or the world border they run vanilla code for that tick.
* **Server-Side**: Clients do not need Velox to join a server running it.

---

## Performance

* Requires Celeris 0.2.0. Works without JVM flags on Celeris's pure-Java kernel; `--enable-preview --enable-native-access=ALL-UNNAMED` switches it to the faster native kernel.

---

## Downloads

### Minecraft 1.21.1

| File | For |
|---|---|
| `velox-0.1.0+1.21.1.jar` | Every system (recommended) |
| `velox-0.1.0+1.21.1-sources.jar` | Source code, for developers |

Verify downloads with `SHA256SUMS.txt`.
