import { distanceMeters } from "./track-distance.mjs";

export const buildElevationProfile = (segments) => {
  const runs = [];
  const points = [];
  let distance = 0;
  let minElevation = Infinity;
  let maxElevation = -Infinity;
  for (const segment of segments) {
    let previous = null;
    let run = null;
    for (const coordinate of segment) {
      if (previous) distance += distanceMeters(previous, coordinate);
      previous = coordinate;
      const elevation = coordinate[2];
      if (!Number.isFinite(elevation)) {
        run = null;
        continue;
      }
      if (!run) {
        run = [];
        runs.push(run);
      }
      const point = { distance, elevation, coordinate };
      run.push(point);
      points.push(point);
      minElevation = Math.min(minElevation, elevation);
      maxElevation = Math.max(maxElevation, elevation);
    }
  }
  return {
    runs, points, distance,
    minElevation: points.length ? minElevation : null,
    maxElevation: points.length ? maxElevation : null,
  };
};

const svgNode = (name, attributes, text) => {
  const node = document.createElementNS("http://www.w3.org/2000/svg", name);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  if (text !== undefined) node.textContent = text;
  return node;
};
const number = (value, digits = 0) => value.toLocaleString("ru-RU", {
  maximumFractionDigits: digits,
});

export const createElevationChart = (container, onPoint = () => {}) => {
  const svg = container.querySelector("svg");
  const message = container.querySelector("[data-elevation-message]");
  const readout = container.querySelector("[data-elevation-readout]");
  let profile = null;
  let selected = -1;
  let cursor;
  let dot;
  let x;
  let y;
  let width;
  const left = 60;
  const right = 20;
  const top = 24;
  const bottom = 196;
  const height = 240;

  const clearSelection = () => {
    selected = -1;
    cursor?.setAttribute("visibility", "hidden");
    dot?.setAttribute("visibility", "hidden");
    readout.textContent = "Наведите на график или используйте стрелки ← →, чтобы посмотреть высоту точки.";
    onPoint(null);
  };

  const select = (index) => {
    if (!profile?.points.length) return;
    selected = index;
    const point = profile.points[index];
    cursor.setAttribute("x1", x(point.distance));
    cursor.setAttribute("x2", x(point.distance));
    cursor.setAttribute("visibility", "visible");
    dot.setAttribute("cx", x(point.distance));
    dot.setAttribute("cy", y(point.elevation));
    dot.setAttribute("visibility", "visible");
    readout.textContent = `${number(point.distance / 1000, 2)} км · ${number(point.elevation)} м`;
    onPoint(point.coordinate);
  };

  const draw = () => {
    if (!profile) return;
    clearSelection();
    svg.replaceChildren();
    const hasElevation = profile.points.length > 0;
    svg.classList.toggle("d-none", !hasElevation);
    readout.classList.toggle("d-none", !hasElevation);
    message.textContent = hasElevation
      ? `Высота: ${number(profile.minElevation)}–${number(profile.maxElevation)} м.`
      : "В этом треке нет данных о высоте.";
    if (!hasElevation) return;

    width = Math.max(320, container.clientWidth);
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.append(svgNode("title", {}, "Высотный профиль трека"));
    const padding = Math.max(5, (profile.maxElevation - profile.minElevation) * 0.1);
    const min = profile.minElevation - padding;
    const max = profile.maxElevation + padding;
    const distanceMax = profile.distance || 1;
    x = (distance) => left + distance / distanceMax * (width - left - right);
    y = (elevation) => bottom - (elevation - min) / (max - min) * (bottom - top);
    const ticks = width < 500 ? 2 : 4;
    for (let i = 0; i <= 4; i += 1) {
      const elevation = min + (max - min) * i / 4;
      svg.append(svgNode("line", { x1: left, x2: width - right, y1: y(elevation), y2: y(elevation), class: "elevation-grid" }));
      svg.append(svgNode("text", { x: left - 8, y: y(elevation) + 4, "text-anchor": "end" }, number(elevation)));
    }
    for (let i = 0; i <= ticks; i += 1) {
      const distance = distanceMax * i / ticks;
      svg.append(svgNode("text", { x: x(distance), y: bottom + 20, "text-anchor": "middle" }, number(distance / 1000, 1)));
    }
    svg.append(svgNode("text", { x: left, y: 14 }, "Высота, м"));
    svg.append(svgNode("text", { x: width - right, y: height - 2, "text-anchor": "end" }, "Расстояние, км"));

    for (const run of profile.runs) {
      const path = run.map((point, index) => `${index ? "L" : "M"}${x(point.distance).toFixed(2)},${y(point.elevation).toFixed(2)}`).join(" ");
      svg.append(svgNode("path", {
        d: `${path} L${x(run.at(-1).distance)},${bottom} L${x(run[0].distance)},${bottom} Z`,
        class: "elevation-fill",
      }));
      svg.append(svgNode("path", { d: path, class: "elevation-line" }));
      if (run.length === 1) svg.append(svgNode("circle", {
        cx: x(run[0].distance), cy: y(run[0].elevation), r: 3, class: "elevation-dot",
      }));
    }
    cursor = svgNode("line", { y1: top, y2: bottom, class: "elevation-cursor", visibility: "hidden" });
    dot = svgNode("circle", { r: 4, class: "elevation-dot", visibility: "hidden" });
    svg.append(cursor, dot);
  };

  svg.addEventListener("pointermove", (event) => {
    if (!profile?.points.length) return;
    const bounds = svg.getBoundingClientRect();
    const position = (event.clientX - bounds.left) * width / bounds.width;
    const distance = Math.max(0, Math.min(1, (position - left) / (width - left - right))) * profile.distance;
    let low = 0;
    let high = profile.points.length - 1;
    while (low < high) {
      const middle = Math.floor((low + high) / 2);
      if (profile.points[middle].distance < distance) low = middle + 1;
      else high = middle;
    }
    if (low > 0 && distance - profile.points[low - 1].distance < profile.points[low].distance - distance) low -= 1;
    select(low);
  });
  svg.addEventListener("pointerleave", clearSelection);
  svg.addEventListener("blur", clearSelection);
  svg.addEventListener("keydown", (event) => {
    if (!profile?.points.length || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    if (event.key === "Home") select(0);
    else if (event.key === "End") select(profile.points.length - 1);
    else select(Math.max(0, Math.min(profile.points.length - 1, selected + (event.key === "ArrowLeft" ? -1 : 1))));
  });
  let observedWidth = 0;
  new ResizeObserver(([entry]) => {
    if (entry.contentRect.width === observedWidth) return;
    observedWidth = entry.contentRect.width;
    draw();
  }).observe(container);
  return {
    update(segments) {
      profile = buildElevationProfile(segments);
      draw();
    },
    showError() {
      message.textContent = "Не удалось загрузить данные высотного профиля.";
    },
  };
};
