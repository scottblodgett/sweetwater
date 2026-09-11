// The ranch map is not drawn yet, and the panel says why rather than quietly calling upstream.
// Sensor coordinates live in the ranch's own catalog (ranch://sensors/map). The read API never calls
// the ranch, and neither does a browser. Until the loop persists a catalog snapshot to sw_ops and the
// API exposes it (docs/open-issues.md, needs Scott's yes on the migration), there is no source to draw from.
export function MapPlaceholder() {
  return (
    <div className="map-placeholder">
      <div><b>Ranch map: no source yet.</b></div>
      <div className="dim">Coordinates are in the ranch catalog, which only the orchestrator reads. This window reads <code>sw_ops</code> through the read API and nothing else, so the map waits on the catalog snapshot migration in <code>docs/open-issues.md</code>. It does not call the Sensor API from a browser to fill the gap.</div>
    </div>
  );
}
