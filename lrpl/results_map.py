RESULTS_MAP_JS = r"""
(() => {
  const element = document.querySelector("#results-map");
  if (!element) return;
  const message = document.querySelector("#results-map-message");
  if (typeof L === "undefined") {
    message.textContent = "Не удалось загрузить Leaflet.";
    message.hidden = false;
    return;
  }

  const map = L.map(element, { preferCanvas: true }).setView([55.75, 37.62], 5);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "© OpenStreetMap contributors",
    maxZoom: 19,
  }).addTo(map);

  const rows = [...document.querySelectorAll("[data-photo-row]")];
  const visible = new Set();
  const layer = L.layerGroup().addTo(map);
  let scheduled = false;

  const update = () => {
    scheduled = false;
    layer.clearLayers();
    const bounds = L.latLngBounds([]);
    let count = 0;
    for (const row of rows) {
      if (!visible.has(row)) continue;
      const latitude = Number(row.dataset.latitude);
      const longitude = Number(row.dataset.longitude);
      if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) continue;
      const point = [latitude, longitude];
      const marker = L.circleMarker(point, {
        radius: 7,
        color: "#fff",
        weight: 2,
        fillColor: "#d62929",
        fillOpacity: 0.95,
      });
      const popup = document.createElement("div");
      const name = document.createElement("strong");
      name.textContent = row.dataset.filename || "Фотография";
      const time = document.createElement("div");
      time.textContent = row.dataset.localTime || "";
      popup.append(name, time);
      marker.bindPopup(popup).addTo(layer);
      bounds.extend(point);
      count += 1;
    }
    message.hidden = count > 0;
    message.textContent = "У видимых строк нет вычисленных координат.";
    if (count === 1) map.setView(bounds.getCenter(), 17, { animate: false });
    else if (count > 1) map.fitBounds(bounds, { padding: [28, 28], maxZoom: 17, animate: false });
  };

  const scheduleUpdate = () => {
    if (scheduled) return;
    scheduled = true;
    window.requestAnimationFrame(update);
  };
  const observer = new IntersectionObserver(entries => {
    for (const entry of entries) {
      if (entry.isIntersecting) visible.add(entry.target);
      else visible.delete(entry.target);
    }
    scheduleUpdate();
  }, { threshold: 0.05 });
  rows.forEach(row => observer.observe(row));
  window.addEventListener("resize", () => map.invalidateSize());
  setTimeout(() => map.invalidateSize(), 0);
})();
"""
