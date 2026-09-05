import { buildElevationProfile } from "../../static/js/track-elevation.mjs";

const assert = (condition, message) => { if (!condition) throw new Error(message); };
const equal = (actual, expected, message) => assert(JSON.stringify(actual) === JSON.stringify(expected), message);

const input = [[[0, 0, 0], [0.001, 0, 20], [0.002, 0, -10]]];
const original = JSON.stringify(input);
const profile = buildElevationProfile(input);
assert(profile.distance > 222 && profile.distance < 223, "distance is measured in metres along the path");
equal(profile.points.map((point) => point.elevation), [0, 20, -10], "zero and negative heights remain valid");
equal([profile.minElevation, profile.maxElevation], [-10, 20], "height range");
equal(JSON.stringify(input), original, "source coordinates are not mutated");

const missing = buildElevationProfile([[[0, 0, 10], [0.001, 0], [0.002, 0, 30]]]);
equal(missing.runs.map((run) => run.length), [1, 1], "missing heights break the line");
assert(missing.points[1].distance > 222, "missing heights do not remove travelled distance");

const segments = buildElevationProfile([
  [[0, 0, 10], [0.001, 0, 20]],
  [[100, 0, 30], [100.001, 0, 40]],
]);
equal(segments.runs.length, 2, "segments are separate lines");
equal(segments.points[1].distance, segments.points[2].distance, "distance does not include inter-segment gaps");
assert(segments.distance > 222 && segments.distance < 223, "segment lengths are added");

for (const segments of [[], [[]], [[[0, 0], [1, 0, null], [2, 0, NaN], [3, 0, Infinity]]]]) {
  const empty = buildElevationProfile(segments);
  equal(empty.points.length, 0, "missing or non-finite heights are not plotted");
  equal([empty.minElevation, empty.maxElevation], [null, null], "empty range");
}
const stationary = buildElevationProfile([[[1, 1, 42], [1, 1, 42]]]);
equal([stationary.distance, stationary.minElevation, stationary.maxElevation], [0, 42, 42], "stationary flat track");
const single = buildElevationProfile([[[1, 1, 42]]]);
equal(single.runs[0].length, 1, "single height is retained");
const edited = buildElevationProfile([[input[0][0], input[0][2]]]);
equal(edited.maxElevation, 0, "deleted summit no longer affects the range");
console.log("Elevation profile: distance, gaps, segments, missing/flat/negative heights and edits passed.");
