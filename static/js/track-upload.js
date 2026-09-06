(() => {
  const dialog = document.querySelector("[data-new-group-dialog]");
  const openButton = document.querySelector("[data-new-group-open]");
  if (!dialog || !openButton) return;

  const group = document.querySelector("#id_group");
  const name = document.querySelector("#id_new_group_name");
  const description = document.querySelector("#id_new_group_description");
  const summary = document.querySelector("[data-new-group-summary]");

  const updateSummary = () => {
    const value = name.value.trim();
    summary.textContent = value ? `Будет создана группа «${value}».` : "";
    summary.classList.toggle("d-none", !value);
  };

  const cancelNewGroup = () => {
    name.value = "";
    description.value = "";
    updateSummary();
    dialog.close();
  };

  openButton.addEventListener("click", () => dialog.showModal());
  dialog.querySelector("[data-new-group-cancel]").addEventListener("click", cancelNewGroup);
  dialog.querySelector("[data-new-group-save]").addEventListener("click", () => {
    if (!name.value.trim()) {
      name.focus();
      return;
    }
    group.value = "";
    updateSummary();
    dialog.close();
  });
  group.addEventListener("change", () => {
    if (!group.value) return;
    name.value = "";
    description.value = "";
    updateSummary();
  });
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) cancelNewGroup();
  });
  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    cancelNewGroup();
  });
  updateSummary();
  if (dialog.open) {
    dialog.close();
    dialog.showModal();
  }
})();
