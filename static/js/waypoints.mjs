export const waypointIcon = (color = "#b65c00") => L.divIcon({
  className: "waypoint-marker", html: `<span style="background-color:${color}">◆</span>`,
  iconSize: [22, 22], iconAnchor: [11, 11],
});

export const waypointLabel = (point) => {
  const box = document.createElement("div");
  const name = document.createElement("strong");
  name.textContent = point.name || "Путевая точка";
  box.append(name);
  if (point.description) {
    const description = document.createElement("p");
    description.className = "mb-0 text-break";
    description.textContent = point.description;
    box.append(description);
  }
  if (Number.isFinite(point.elevation)) {
    const height = document.createElement("div");
    height.textContent = `${Math.round(point.elevation)} м`;
    box.append(height);
  }
  return box;
};

export const createWaypointEditor = (map, getFeature, isEditing, trackPointCount, showError) => {
  let markers = [];
  let pending = null;
  const commit = () => {
    if (!pending) return true;
    const { form, point, marker } = pending;
    if (!form.reportValidity()) return false;
    const value = (name) => form.querySelector(`[data-waypoint-field="${name}"]`);
    point.name = value("name").value;
    point.description = value("description").value;
    point.elevation = value("elevation").value === "" ? null : value("elevation").valueAsNumber;
    point.coordinates = [value("longitude").valueAsNumber, value("latitude").valueAsNumber];
    marker.setLatLng([point.coordinates[1], point.coordinates[0]]);
    return true;
  };
  const popup = (point, marker) => {
    if (!isEditing()) return waypointLabel(point);
    const form = document.createElement("form");
    form.className = "waypoint-form";
    const fields = [
      ["name", "Название", "text", point.name],
      ["description", "Описание", "textarea", point.description],
      ["latitude", "Широта", "number", point.coordinates[1], -90, 90],
      ["longitude", "Долгота", "number", point.coordinates[0], -180, 180],
      ["elevation", "Высота, м", "number", point.elevation],
    ];
    for (const [name, title, type, value, min, max] of fields) {
      const label = document.createElement("label");
      label.className = "d-block small mb-2";
      label.textContent = title;
      const input = document.createElement(type === "textarea" ? "textarea" : "input");
      if (type !== "textarea") input.type = type;
      else input.rows = 2;
      input.className = "form-control form-control-sm";
      input.dataset.waypointField = name;
      input.value = value ?? "";
      if (type === "number") input.step = "any";
      if (min !== undefined) {
        input.min = min;
        input.max = max;
        input.required = true;
      }
      label.append(input);
      form.append(label);
    }
    const apply = document.createElement("button");
    apply.type = "submit";
    apply.className = "btn btn-sm btn-primary mb-2";
    apply.textContent = "Применить к точке";
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "btn btn-sm btn-outline-danger";
    remove.textContent = "Удалить точку";
    remove.dataset.waypointDelete = "";
    form.append(apply, remove);
    pending = { form, point, marker };
    form.addEventListener("submit", event => {
      event.preventDefault();
      if (commit()) map.closePopup();
    });
    // Save valid field changes even when the user clicks another marker.
    form.addEventListener("change", commit);
    remove.addEventListener("click", () => {
      const feature = getFeature();
      if (trackPointCount() + feature.waypoints.length <= 1) {
        showError("Должна остаться хотя бы одна точка трека или путевая точка.");
        return;
      }
      feature.waypoints = feature.waypoints.filter(item => item !== point);
      pending = null;
      redraw();
    });
    return form;
  };
  const redraw = () => {
    pending = null;
    map.closePopup();
    markers.forEach(marker => marker.remove());
    markers = (getFeature().waypoints || []).map(point => {
      const marker = L.marker([point.coordinates[1], point.coordinates[0]], {
        draggable: isEditing(), icon: waypointIcon(), title: point.name || "Путевая точка",
      }).addTo(map);
      marker.bindPopup(() => popup(point, marker), { maxWidth: 280 });
      marker.on("dragstart", () => { pending = null; map.closePopup(); });
      marker.on("dragend", () => {
        const latlng = marker.getLatLng();
        point.coordinates = [latlng.lng, latlng.lat];
      });
      return marker;
    });
  };
  map.on("popupclose", () => { pending = null; });
  return {
    redraw, commit,
    add(latlng) {
      if (!commit()) return;
      const point = { index: null, name: "", description: "", elevation: null, coordinates: [latlng.lng, latlng.lat] };
      getFeature().waypoints.push(point);
      redraw();
      markers.at(-1).openPopup();
    },
  };
};
