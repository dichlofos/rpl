const mapElement = document.querySelector("#track-map");

if (mapElement) {
  const map = L.map(mapElement);
  L.tileLayer(mapElement.dataset.tileUrl, {
    attribution: mapElement.dataset.tileAttribution,
    maxZoom: 19,
  }).addTo(map);

  fetch(mapElement.dataset.geojsonUrl, { headers: { Accept: "application/geo+json, application/json" } })
    .then((response) => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    })
    .then((feature) => {
      const layer = L.geoJSON(feature, { style: { color: "#dc3545", weight: 5, opacity: 0.9 } });
      const bounds = layer.getBounds();
      if (!bounds.isValid()) throw new Error("У маршрута нет корректных координат");
      map.fitBounds(bounds, { padding: [24, 24] });
      layer.addTo(map);
      const coordinates = feature.geometry.type === "LineString"
        ? feature.geometry.coordinates
        : feature.geometry.coordinates.flat();
      const first = coordinates[0];
      const last = coordinates[coordinates.length - 1];
      L.circleMarker([first[1], first[0]], { radius: 7, color: "#198754", fillOpacity: 1 }).bindTooltip("Старт").addTo(map);
      L.circleMarker([last[1], last[0]], { radius: 7, color: "#212529", fillOpacity: 1 }).bindTooltip("Финиш").addTo(map);
    })
    .catch((error) => {
      map.setView([55.75, 37.62], 5);
      const alert = document.querySelector("#map-error");
      alert.textContent = `Не удалось загрузить маршрут: ${error.message}`;
      alert.classList.remove("d-none");
    });
}
