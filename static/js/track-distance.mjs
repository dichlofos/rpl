const EARTH_RADIUS_M = 6371000;

export const distanceMeters = (first, last) => {
  const radians = Math.PI / 180;
  const latitudeDelta = (last[1] - first[1]) * radians;
  const longitudeDelta = (last[0] - first[0]) * radians;
  const a = Math.sin(latitudeDelta / 2) ** 2
    + Math.cos(first[1] * radians) * Math.cos(last[1] * radians)
    * Math.sin(longitudeDelta / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(Math.min(1, Math.max(0, a))));
};
