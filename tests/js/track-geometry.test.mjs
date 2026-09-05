import { routeGeometry, routeSegments } from "../../static/js/track-geometry.mjs";

for (const segments of [[], [[[1, 2]]], [[[1, 2], [3, 4]]], [[[1, 2]], [[3, 4], [5, 6]]], [[[1, 2], [3, 4]], [[5, 6], [7, 8]]]]) {
  const result = routeSegments(routeGeometry(segments));
  if (JSON.stringify(result) !== JSON.stringify(segments)) throw Error("Route geometry roundtrip failed");
}
if (routeGeometry([]) !== null) throw Error("Waypoint-only route should have null geometry");
console.log("Route geometry: empty, single point, line and mixed segments passed.");
