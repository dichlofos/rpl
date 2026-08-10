const storageKey = "rpl-track-list-view";
const buttons = document.querySelectorAll("[data-track-view]");
const panels = document.querySelectorAll("[data-track-view-panel]");

function setView(view) {
  const selectedView = view === "table" ? "table" : "cards";

  buttons.forEach((button) => {
    const isActive = button.dataset.trackView === selectedView;
    button.classList.toggle("active", isActive);
    button.setAttribute("aria-pressed", String(isActive));
  });
  panels.forEach((panel) => {
    panel.hidden = panel.dataset.trackViewPanel !== selectedView;
  });
}

let savedView;
try {
  savedView = window.localStorage.getItem(storageKey);
} catch {
  savedView = null;
}
setView(savedView);

buttons.forEach((button) => {
  button.addEventListener("click", () => {
    const view = button.dataset.trackView;
    setView(view);
    try {
      window.localStorage.setItem(storageKey, view);
    } catch {
      // The switch still works when browser storage is unavailable.
    }
  });
});
