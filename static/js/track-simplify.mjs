import { distanceMeters } from "./track-distance.mjs";

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
