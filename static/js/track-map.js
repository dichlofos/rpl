import { retainedPointIndices } from "./track-simplify.mjs";
import { createElevationChart } from "./track-elevation.mjs?v=3";
import { routeSegments, routeGeometry } from "./track-geometry.mjs";
import { createWaypointEditor } from "./waypoints.mjs";

const mapElement = document.querySelector("#track-map");

if (mapElement) {
  const map = L.map(mapElement);
  const editor = document.querySelector("#track-editor");
  const errorAlert = document.querySelector("#map-error");
  let elevationMarker = null;
  const elevationChart = createElevationChart(document.querySelector("#track-elevation"), (coordinate) => {
    if (!coordinate) {
      elevationMarker?.remove();
      elevationMarker = null;
      return;
    }
    const latlng = [coordinate[1], coordinate[0]];
    if (elevationMarker) elevationMarker.setLatLng(latlng);
    else elevationMarker = L.circleMarker(latlng, {
      radius: 7, color: "#0d6efd", weight: 3, fillColor: "#fff", fillOpacity: 1, interactive: false,
    }).addTo(map);
  });
  let feature;
  let trackLayer;
  let endpointLayers = [];
  let pointMarkers = [];
  let selectedMarker = null;
  let editing = false;
  let originalFeature;
  let startIndex = 0;
  let endIndex = 0;
  let areaRectangle = null;
  let areaStart = null;
  let selectingArea = false;
  let addingWaypoint = false;
  const defaultEditorHint = "Выберите или перетащите точку. Правый клик откроет меню.";
  const trimTooltip = "Сначала выберите точку на треке";

  L.tileLayer(mapElement.dataset.tileUrl, {
    attribution: mapElement.dataset.tileAttribution,
    maxZoom: 19,
  }).addTo(map);

  const showError = (message) => {
    errorAlert.textContent = message;
    errorAlert.classList.remove("d-none");
  };

  const hideError = () => errorAlert.classList.add("d-none");
  const segments = () => routeSegments(feature.geometry);
  const coordinates = () => segments().flat();
  const waypointEditor = createWaypointEditor(map, () => feature, () => editing, () => coordinates().length, showError);

  const drawTrack = (fit = false) => {
    elevationChart.update(segments());
    if (trackLayer) {
      trackLayer.clearLayers();
      trackLayer.addData(feature);
    } else {
      trackLayer = L.geoJSON(feature, { style: { color: "#dc3545", weight: 5, opacity: 0.9 } }).addTo(map);
    }
    const bounds = trackLayer.getBounds();
    for (const point of feature.waypoints || []) bounds.extend([point.coordinates[1], point.coordinates[0]]);
    if (fit && bounds.isValid()) map.fitBounds(bounds, { padding: [24, 24], maxZoom: 17 });
    const points = coordinates();
    document.querySelector("#track-elevation").classList.toggle("d-none", !points.length);
    if (editor) {
      editor.querySelector("[data-editor-tools]").classList.toggle("d-none", !editing || !points.length);
      editor.querySelector("[data-simplify-tools]").classList.toggle("d-none", !editing || points.length < 3);
    }
    if (!points.length) {
      endpointLayers.forEach(layer => layer.remove());
      endpointLayers = [];
      return;
    }
    if (endpointLayers.length) {
      endpointLayers[0].setLatLng([points[0][1], points[0][0]]);
      endpointLayers[1].setLatLng([points.at(-1)[1], points.at(-1)[0]]);
    } else {
      endpointLayers = [
        L.circleMarker([points[0][1], points[0][0]], { radius: 7, color: "#198754", fillOpacity: 1 }).bindTooltip("Старт").addTo(map),
        L.circleMarker([points.at(-1)[1], points.at(-1)[0]], { radius: 7, color: "#212529", fillOpacity: 1 }).bindTooltip("Финиш").addTo(map),
      ];
    }
  };

  const setEditorState = (active) => {
    editing = active;
    addingWaypoint = false;
    editor.querySelector('[data-editor-action="start"]').classList.toggle("d-none", active);
    editor.querySelector("[data-editor-tools]").classList.toggle("d-none", !active || !coordinates().length);
    editor.querySelector("[data-simplify-tools]").classList.toggle("d-none", !active || coordinates().length < 3);
    editor.querySelector("[data-simplify-summary]").textContent = "";
    editor.querySelector('[data-editor-action="save"]').classList.toggle("d-none", !active);
    editor.querySelector('[data-editor-action="cancel"]').classList.toggle("d-none", !active);
    editor.querySelector("[data-editor-hint]").classList.toggle("d-none", !active);
    editor.querySelector('[data-editor-action="add-waypoint"]').classList.toggle("d-none", !active);
    waypointEditor.redraw();
  };

  const selectMarker = (marker) => {
    if (selectedMarker) selectedMarker.getElement()?.classList.remove("is-selected");
    selectedMarker = marker;
    marker.getElement()?.classList.add("is-selected");
    editor.querySelectorAll('[data-editor-action^="trim-"]').forEach((button) => { button.disabled = false; });
    editor.querySelectorAll("[data-trim-tooltip]").forEach((tooltip) => { tooltip.removeAttribute("title"); });
  };

  const clearMarkerSelection = () => {
    selectedMarker?.getElement()?.classList.remove("is-selected");
    selectedMarker = null;
    editor.querySelectorAll('[data-editor-action^="trim-"]').forEach((button) => { button.disabled = true; });
    editor.querySelectorAll("[data-trim-tooltip]").forEach((tooltip) => { tooltip.title = trimTooltip; });
  };

  const clearMarkers = () => {
    clearMarkerSelection();
    pointMarkers.forEach((marker) => marker.remove());
    pointMarkers = [];
  };

  const clearArea = () => {
    if (areaRectangle) areaRectangle.remove();
    areaRectangle = null;
    areaStart = null;
    selectingArea = false;
    addingWaypoint = false;
    map.dragging.enable();
    mapElement.classList.remove("is-selecting-area");
    editor.querySelector("[data-area-tools]").classList.add("d-none");
    editor.querySelector("[data-area-summary]").classList.add("d-none");
    editor.querySelector("[data-editor-hint]").textContent = defaultEditorHint;
  };

  const showAreaSummary = () => {
    if (!areaRectangle) return;
    const inside = pointMarkers.filter((marker) => areaRectangle.getBounds().contains(marker.getLatLng())).length;
    const summary = editor.querySelector("[data-area-summary]");
    summary.textContent = `В области: ${inside} из ${pointMarkers.length} точек`;
    summary.classList.remove("d-none");
    editor.querySelector("[data-area-tools]").classList.remove("d-none");
  };

  const rebuildGeometry = () => {
    const grouped = [];
    pointMarkers.forEach((marker) => {
      const segmentIndex = marker.options.segmentIndex;
      if (!grouped[segmentIndex]) grouped[segmentIndex] = [];
      grouped[segmentIndex].push(marker.options.coordinate);
    });
    const nonEmpty = grouped.filter((segment) => segment?.length);
    feature.geometry = routeGeometry(nonEmpty);
    drawTrack();
  };

  const makeMarkers = () => {
    clearMarkers();
    let index = startIndex;
    segments().forEach((segment, segmentIndex) => {
      segment.forEach((coordinate) => {
        const marker = L.marker([coordinate[1], coordinate[0]], {
          draggable: true,
          icon: L.divIcon({ className: "track-point-marker", html: "<span></span>", iconSize: [12, 12], iconAnchor: [6, 6] }),
          coordinate,
          originalIndex: index,
          segmentIndex,
        }).addTo(map);
        index += 1;
        marker.on("click", () => selectMarker(marker));
        marker.on("dragstart", () => selectMarker(marker));
        marker.on("drag", () => {
          const latlng = marker.getLatLng();
          coordinate[0] = latlng.lng;
          coordinate[1] = latlng.lat;
          rebuildGeometry();
        });
        marker.on("contextmenu", () => {
          selectMarker(marker);
          L.popup()
            .setLatLng(marker.getLatLng())
            .setContent('<div class="d-grid gap-2"><button class="btn btn-sm btn-outline-secondary" data-popup-action="before">Отрезать до этой точки</button><button class="btn btn-sm btn-outline-secondary" data-popup-action="after">Отрезать после этой точки</button></div>')
            .openOn(map);
        });
        pointMarkers.push(marker);
      });
    });
  };

  const trim = (side) => {
    if (!selectedMarker) return;
    const selectedIndex = selectedMarker.options.originalIndex;
    const retained = pointMarkers.filter((marker) => side === "before"
      ? marker.options.originalIndex >= selectedIndex
      : marker.options.originalIndex <= selectedIndex);
    if (!retained.length && !feature.waypoints.length) {
      showError("Должна остаться хотя бы одна точка трека или путевая точка.");
      return;
    }
    hideError();
    if (side === "before") startIndex = selectedIndex;
    else endIndex = selectedIndex + 1;
    pointMarkers.filter((marker) => !retained.includes(marker)).forEach((marker) => marker.remove());
    pointMarkers = retained;
    clearMarkerSelection();
    map.closePopup();
    rebuildGeometry();
    if (areaRectangle) showAreaSummary();
  };

  const startAreaSelection = () => {
    clearArea();
    selectingArea = true;
    map.dragging.disable();
    map.closePopup();
    mapElement.classList.add("is-selecting-area");
    editor.querySelector("[data-editor-hint]").textContent = "Протяните мышью по карте, чтобы выделить прямоугольную область. Escape — отмена.";
  };

  const deleteByArea = (side) => {
    if (!areaRectangle) return;
    const bounds = areaRectangle.getBounds();
    const retained = pointMarkers.filter((marker) => {
      const inside = bounds.contains(marker.getLatLng());
      return side === "inside" ? !inside : inside;
    });
    if (!retained.length && !feature.waypoints.length) {
      showError("Должна остаться хотя бы одна точка трека или путевая точка.");
      return;
    }
    hideError();
    pointMarkers.filter((marker) => !retained.includes(marker)).forEach((marker) => marker.remove());
    pointMarkers = retained;
    clearMarkerSelection();
    clearArea();
    rebuildGeometry();
  };

  const simplifyTrack = () => {
    const windowInput = editor.querySelector("[data-simplify-window]");
    const distanceInput = editor.querySelector("[data-simplify-distance]");
    if (!windowInput.reportValidity() || !distanceInput.reportValidity()) return;
    try {
      const retainedIndices = new Set(retainedPointIndices(
        segments(), windowInput.valueAsNumber, distanceInput.valueAsNumber,
      ));
      const before = pointMarkers.length;
      pointMarkers = pointMarkers.filter((marker, index) => {
        if (retainedIndices.has(index)) return true;
        marker.remove();
        return false;
      });
      hideError();
      clearMarkerSelection();
      clearArea();
      map.closePopup();
      rebuildGeometry();
      editor.querySelector("[data-simplify-summary]").textContent =
        `Удалено точек: ${before - pointMarkers.length}. Осталось: ${pointMarkers.length}. Изменения пока не сохранены.`;
    } catch (error) {
      showError(error.message);
    }
  };

  const startEditing = () => {
    originalFeature = structuredClone(feature);
    startIndex = 0;
    endIndex = coordinates().length;
    setEditorState(true);
    makeMarkers();
  };

  const cancelEditing = () => {
    feature = originalFeature;
    clearArea();
    clearMarkers();
    setEditorState(false);
    hideError();
    drawTrack();
  };

  const updateStats = (stats) => {
    const values = {
      distance_km: `${stats.distance_km.toFixed(1)} км`,
      elevation_gain_m: `${stats.elevation_gain_m} м`,
      elevation_loss_m: `${stats.elevation_loss_m} м`,
      duration: stats.duration,
      points_count: stats.points_count,
      waypoints_count: stats.waypoints_count,
    };
    Object.entries(values).forEach(([name, value]) => {
      document.querySelector(`[data-track-stat="${name}"]`).textContent = value;
    });
  };

  const saveEditing = async () => {
    if (!waypointEditor.commit()) return;
    const saveButton = editor.querySelector('[data-editor-action="save"]');
    saveButton.disabled = true;
    hideError();
    try {
      const response = await fetch(editor.dataset.editUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": editor.querySelector('[name="csrfmiddlewaretoken"]').value },
        body: JSON.stringify({
          start_index: startIndex,
          end_index: endIndex,
          retained_indices: pointMarkers.map((marker) => marker.options.originalIndex),
          coordinates: pointMarkers.map((marker) => marker.options.coordinate.slice(0, 2)),
          waypoints: feature.waypoints,
        }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
      feature = result.geojson;
      clearArea();
      clearMarkers();
      setEditorState(false);
      drawTrack();
      updateStats(result.stats);
    } catch (error) {
      showError(`Не удалось сохранить маршрут: ${error.message}`);
    } finally {
      saveButton.disabled = false;
    }
  };

  editor?.addEventListener("click", (event) => {
    const action = event.target.closest("[data-editor-action]")?.dataset.editorAction;
    if (action === "start") startEditing();
    if (action === "cancel") cancelEditing();
    if (action === "save") saveEditing();
    if (action === "trim-before") trim("before");
    if (action === "trim-after") trim("after");
    if (action === "select-area") startAreaSelection();
    if (action === "delete-inside") deleteByArea("inside");
    if (action === "delete-outside") deleteByArea("outside");
    if (action === "simplify") simplifyTrack();
    if (action === "add-waypoint") {
      clearArea();
      addingWaypoint = true;
      editor.querySelector("[data-editor-hint]").textContent = "Нажмите на карту, чтобы добавить путевую точку. Escape — отмена.";
    }
  });
  map.on("click", event => {
    if (!addingWaypoint) return;
    addingWaypoint = false;
    editor.querySelector("[data-editor-hint]").textContent = defaultEditorHint;
    waypointEditor.add(event.latlng);
  });
  map.on("mousedown", (event) => {
    if (!selectingArea) return;
    areaStart = event.latlng;
    areaRectangle = L.rectangle(L.latLngBounds(areaStart, areaStart), {
      color: "#0d6efd", weight: 2, fillOpacity: 0.12, dashArray: "6 4",
    }).addTo(map);
  });
  map.on("mousemove", (event) => {
    if (selectingArea && areaStart && areaRectangle) {
      areaRectangle.setBounds(L.latLngBounds(areaStart, event.latlng));
    }
  });
  map.on("mouseup", () => {
    if (!selectingArea || !areaStart || !areaRectangle) return;
    selectingArea = false;
    areaStart = null;
    map.dragging.enable();
    mapElement.classList.remove("is-selecting-area");
    editor.querySelector("[data-editor-hint]").textContent = "Изменения пока не сохранены. Можно выбрать другую область или нажать «Отмена».";
    showAreaSummary();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && (areaRectangle || addingWaypoint)) clearArea();
  });
  map.on("popupopen", (event) => {
    event.popup.getElement().querySelector('[data-popup-action="before"]')?.addEventListener("click", () => trim("before"));
    event.popup.getElement().querySelector('[data-popup-action="after"]')?.addEventListener("click", () => trim("after"));
  });

  fetch(mapElement.dataset.geojsonUrl, {
    cache: "no-store",
    headers: { Accept: "application/geo+json, application/json" },
  })
    .then((response) => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    })
    .then((loadedFeature) => {
      feature = loadedFeature;
      feature.waypoints ||= [];
      waypointEditor.redraw();
      drawTrack(true);
    })
    .catch((error) => {
      elevationChart.showError();
      map.setView([55.75, 37.62], 5);
      showError(`Не удалось загрузить маршрут: ${error.message}`);
    });
}
