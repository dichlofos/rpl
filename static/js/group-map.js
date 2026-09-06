import { showWaypointName, waypointIcon, waypointLabel } from "./waypoints.mjs?v=labels-1";

const element = document.querySelector("#group-map");
const waypointNamesKey = "rpl-show-waypoint-names";
if (element) {
  const map = L.map(element);
  const namesToggle = document.querySelector("[data-waypoint-names]");
  const allTracksToggle = document.querySelector("[data-all-tracks-visible]");
  const waypointMarkers = [];
  const trackLayers = [];
  const syncAllTracksToggle = () => {
    const visibleCount = trackLayers.filter(({ checkbox }) => checkbox.checked).length;
    allTracksToggle.disabled = !trackLayers.length;
    allTracksToggle.checked = visibleCount === trackLayers.length;
    allTracksToggle.indeterminate = visibleCount > 0 && visibleCount < trackLayers.length;
  };
  allTracksToggle.addEventListener("change", () => {
    trackLayers.forEach(({ checkbox, layer }) => {
      checkbox.checked = allTracksToggle.checked;
      if (allTracksToggle.checked) layer.addTo(map);
      else layer.remove();
    });
    syncAllTracksToggle();
  });
  try {
    namesToggle.checked = window.localStorage.getItem(waypointNamesKey) === "true";
  } catch {
    namesToggle.checked = false;
  }
  namesToggle.addEventListener("change", () => {
    waypointMarkers.forEach(({ marker, point }) => {
      showWaypointName(marker, point, namesToggle.checked);
    });
    try {
      window.localStorage.setItem(waypointNamesKey, String(namesToggle.checked));
    } catch {
      // The toggle still works when browser storage is unavailable.
    }
  });
  L.tileLayer(element.dataset.tileUrl, {
    attribution: element.dataset.tileAttribution, maxZoom: 19,
  }).addTo(map);
  fetch(element.dataset.geojsonUrl, { cache: "no-store" })
    .then(response => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    })
    .then(collection => {
      const bounds = L.latLngBounds([]);
      for (const feature of collection.features) {
        const row = document.querySelector(`[data-group-track="${feature.id}"]`);
        if (!row) continue;
        const link = document.createElement("a");
        link.href = feature.properties.url;
        link.textContent = `${feature.properties.position}. ${feature.properties.name}`;
        const layer = L.geoJSON(feature, {
          style: { color: feature.properties.color, weight: 4, opacity: 0.85 },
        }).bindPopup(link).addTo(map);
        for (const point of feature.waypoints || []) {
          const marker = L.marker([point.coordinates[1], point.coordinates[0]], {
            icon: waypointIcon(feature.properties.color), title: point.name || "Путевая точка",
          }).bindPopup(waypointLabel(point)).addTo(layer);
          showWaypointName(marker, point, namesToggle.checked);
          waypointMarkers.push({ marker, point });
        }
        const trackBounds = layer.getBounds();
        if (!trackBounds.isValid()) continue;
        bounds.extend(trackBounds);
        const checkbox = row.querySelector("[data-track-visible]");
        const focus = row.querySelector("[data-track-focus]");
        checkbox.disabled = false;
        focus.disabled = false;
        trackLayers.push({ checkbox, layer });
        checkbox.addEventListener("change", () => {
          if (checkbox.checked) layer.addTo(map);
          else layer.remove();
          syncAllTracksToggle();
        });
        focus.addEventListener("click", () => {
          checkbox.checked = true;
          layer.addTo(map);
          syncAllTracksToggle();
          map.fitBounds(trackBounds, { padding: [24, 24], maxZoom: 17 });
          element.scrollIntoView({ behavior: "smooth", block: "center" });
        });
      }
      syncAllTracksToggle();
      if (bounds.isValid()) map.fitBounds(bounds, { padding: [24, 24], maxZoom: 17 });
      else map.setView([55.75, 37.62], 5);
    })
    .catch(error => {
      map.setView([55.75, 37.62], 5);
      const alert = document.querySelector("#group-map-error");
      alert.textContent = `Не удалось загрузить треки группы: ${error.message}`;
      alert.classList.remove("d-none");
    });
}
