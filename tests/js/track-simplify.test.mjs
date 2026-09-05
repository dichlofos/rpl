import { retainedPointIndices } from "../../static/js/track-simplify.mjs";

const equal = (actual, expected, message) => {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`${message}: got ${JSON.stringify(actual)}, expected ${JSON.stringify(expected)}`);
  }
};
const check = (name, segments, windowSize, distance, expected) => {
  const original = JSON.stringify(segments);
  equal(retainedPointIndices(segments, windowSize, distance), expected, name);
  equal(JSON.stringify(segments), original, `${name}: input is unchanged`);
};

check("empty input", [], 3, 0, []);
check("short segments", [[[0, 0]], [[0, 0], [0, 0]]], 3, 0, [0, 1, 2]);
check("window larger than track", [[[0, 0], [1, 0], [0, 0]]], 4, 0, [0, 1, 2]);
check("closed excursion is removed", [[[0, 0, 12], [1, 0, 20], [0, 0, 15]]], 3, 0, [0, 2]);
check("distance above threshold", [[[0, 0], [1, 0], [0.001, 0]]], 3, 100, [0, 1, 2]);
check("distance below threshold", [[[0, 0], [1, 0], [0.001, 0]]], 3, 112, [0, 2]);
check("longitude depends on latitude", [[[0, 60], [1, 60], [0.001, 60]]], 3, 60, [0, 2]);
check("date line crossing", [[[179.9999, 0], [170, 0], [-179.9999, 0]]], 3, 23, [0, 2]);
check("failed window slides one point", [[[1, 0], [0, 0], [2, 0], [0, 0]]], 3, 0, [0, 1, 3]);
check("successful windows share endpoints", [Array.from({ length: 7 }, () => [0, 0])], 3, 0, [0, 2, 4, 6]);
check("partial tail stays intact", [Array.from({ length: 6 }, () => [0, 0])], 4, 0, [0, 3, 4, 5]);
check("segments processed independently", [
  [[0, 0], [1, 0], [0, 0]], [[0, 0], [2, 0], [0, 0]],
], 3, 0, [0, 2, 3, 5]);
check("windows never span segment boundary", [
  [[0, 0], [1, 0]], [[0, 0], [1, 0]],
], 3, 1000000, [0, 1, 2, 3]);

for (const [windowSize, distance] of [
  [2, 0], [3.5, 0], [NaN, 0], [Infinity, 0], ["3", 0],
  [Number.MAX_SAFE_INTEGER + 1, 0], [3, -1], [3, NaN], [3, Infinity], [3, "5"],
]) {
  let rejected = false;
  try {
    retainedPointIndices([[[0, 0], [1, 0], [0, 0]]], windowSize, distance);
  } catch {
    rejected = true;
  }
  equal(rejected, true, `invalid parameters ${windowSize}, ${distance}`);
}
console.log("Track simplification: 13 scenarios and 10 invalid parameter cases passed.");
