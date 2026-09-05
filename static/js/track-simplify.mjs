const EARTH_RADIUS_M = 6371000;

const distanceMeters = (first, last) => {
  const radians = Math.PI / 180;
  const latitudeDelta = (last[1] - first[1]) * radians;
  const longitudeDelta = (last[0] - first[0]) * radians;
  const a = Math.sin(latitudeDelta / 2) ** 2
    + Math.cos(first[1] * radians) * Math.cos(last[1] * radians)
    * Math.sin(longitudeDelta / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(Math.min(1, Math.max(0, a))));
};

// Indices refer to the flattened input, never to a progressively shortened track.
// Successful windows share an endpoint; failed windows advance by one point.
export const retainedPointIndices = (segments, windowSize, maxDistance) => {
  if (!Number.isSafeInteger(windowSize) || windowSize < 3) {
    throw new Error("W должно быть целым числом не меньше 3.");
  }
  if (!Number.isFinite(maxDistance) || maxDistance < 0) {
    throw new Error("d должно быть конечным числом не меньше 0 метров.");
  }
  const retained = [];
  let offset = 0;
  for (const segment of segments) {
    let start = 0;
    while (start < segment.length) {
      retained.push(offset + start);
      const end = start + windowSize - 1;
      if (end < segment.length && distanceMeters(segment[start], segment[end]) <= maxDistance) {
        start = end;
      } else {
        start += 1;
      }
    }
    offset += segment.length;
  }
  return retained;
};
