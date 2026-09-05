export const routeSegments = (geometry) => {
  if (!geometry) return [];
  if (geometry.type === "Point") return [[geometry.coordinates]];
  if (geometry.type === "LineString") return [geometry.coordinates];
  if (geometry.type === "MultiLineString") return geometry.coordinates;
  return (geometry.geometries || []).flatMap(routeSegments);
};

export const routeGeometry = (segments) => {
  const nonEmpty = segments.filter(segment => segment.length);
  const geometries = nonEmpty.map(segment => segment.length === 1
    ? { type: "Point", coordinates: segment[0] }
    : { type: "LineString", coordinates: segment });
  if (!geometries.length) return null;
  if (geometries.length === 1) return geometries[0];
  if (geometries.every(geometry => geometry.type === "LineString")) {
    return { type: "MultiLineString", coordinates: nonEmpty };
  }
  return { type: "GeometryCollection", geometries };
};
