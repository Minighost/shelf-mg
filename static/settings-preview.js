const PREVIEW_VARS = {
    custom_bg_color: "--bg-color",
    custom_surface_color: "--surface-color",
    custom_text_color: "--text-color",
    custom_text_muted: "--text-muted",
    custom_border_color: "--border-color",
    custom_accent_color: "--accent-color",
    custom_accent_hover: "--accent-hover",
};

function previewTheme(theme) {
    const customFields = document.getElementById("custom-color-fields");
    customFields.style.display = theme === "custom" ? "flex" : "none";

    const preview = document.getElementById("settings-preview");
    if (theme === "custom") {
        previewCustomColors();
    } else {
        // Clear any custom-color overrides so the preview box falls back
        // to whatever [data-theme="light"/"dark"] already defines.
        for (const cssVar of Object.values(PREVIEW_VARS)) {
            preview.style.removeProperty(cssVar);
        }
        preview.setAttribute("data-theme", theme);
    }
}

function previewCustomColors() {
    const preview = document.getElementById("settings-preview");
    preview.removeAttribute("data-theme");
    for (const [fieldId, cssVar] of Object.entries(PREVIEW_VARS)) {
        const input = document.getElementById(fieldId);
        preview.style.setProperty(cssVar, input.value);
    }
}

function previewFont(fontKey) {
    const select = document.getElementById("font_family");
    const option = select.querySelector(`option[value="${fontKey}"]`);
    if (option) {
        document.getElementById("settings-preview").style.setProperty("--reader-font-family", option.dataset.css);
    }
}

function previewFontSize(size) {
    document.getElementById("settings-preview").style.setProperty("--reader-font-size-base", `${size}rem`);
    document.getElementById("font-size-label").textContent = size;
}

function previewContentMaxWidth(pct) {
    document.documentElement.style.setProperty("--content-max-width-vw", `${pct}vw`);
    document.getElementById("content-max-width-label").textContent = pct;
}

document.addEventListener("DOMContentLoaded", () => {
    const themeSelect = document.getElementById("theme");
    if (themeSelect) previewTheme(themeSelect.value);
});

function toggleReaderFontFields(show) {
    document.getElementById("reader-font-fields").style.display = show ? "block" : "none";
    document.getElementById("reader-font-preview").style.display = show ? "block" : "none";
}

function previewReaderFont(fontKey) {
    const select = document.getElementById("reader_font_family");
    const option = select.querySelector(`option[value="${fontKey}"]`);
    if (option) {
        document.getElementById("reader-font-preview").style.setProperty("--reader-font-family", option.dataset.css);
    }
}

function previewReaderFontSize(size) {
    document.getElementById("reader-font-preview").style.setProperty("--reader-font-size-base", `${size}rem`);
    document.getElementById("reader-font-size-label").textContent = size;
}

function activeFilterKeys() {
    return new Set(
        Array.from(document.querySelectorAll("#active-filters-list .active-filter-chip"))
            .map((chip) => chip.dataset.key)
    );
}

function addActiveFilter(field) {
    const list = document.getElementById("active-filters-list");

    const li = document.createElement("li");
    li.className = "active-filter-chip";
    li.dataset.key = field.key;

    const handle = document.createElement("span");
    handle.className = "filter-drag-handle";
    handle.setAttribute("aria-hidden", "true");
    handle.textContent = "⠿";

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.name = "enabled_filters";
    checkbox.value = field.key;
    checkbox.checked = true;
    checkbox.hidden = true;

    const label = document.createElement("span");
    label.textContent = field.label;

    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "remove-filter-btn";
    removeBtn.setAttribute("aria-label", `Remove ${field.label}`);
    removeBtn.textContent = "×";
    removeBtn.onclick = () => removeActiveFilter(removeBtn);

    li.append(handle, checkbox, label, removeBtn);
    list.appendChild(li);

    const search = document.getElementById("filter-search");
    search.value = "";
    renderFilterSuggestions("");
}

function removeActiveFilter(button) {
    button.closest(".active-filter-chip").remove();
    renderFilterSuggestions(document.getElementById("filter-search").value);
}

function renderFilterSuggestions(query) {
    const dataEl = document.getElementById("all-filter-fields-data");
    const suggestionsList = document.getElementById("filter-suggestions");
    if (!dataEl || !suggestionsList) return;

    const allFields = JSON.parse(dataEl.textContent);
    const active = activeFilterKeys();
    const normalizedQuery = query.trim().toLowerCase();

    const matches = allFields.filter(
        (field) => !active.has(field.key) && field.label.toLowerCase().includes(normalizedQuery)
    );

    suggestionsList.innerHTML = "";
    for (const field of matches) {
        const li = document.createElement("li");
        li.className = "filter-suggestion";
        li.textContent = field.label;
        li.onclick = () => addActiveFilter(field);
        suggestionsList.appendChild(li);
    }

    suggestionsList.style.display = matches.length > 0 ? "block" : "none";
}

document.addEventListener("DOMContentLoaded", () => {
    const searchInput = document.getElementById("filter-search");
    if (!searchInput) return;

    document.addEventListener("click", (event) => {
        const control = document.querySelector(".add-filter-control");
        if (control && !control.contains(event.target)) {
            document.getElementById("filter-suggestions").style.display = "none";
        }
    });
});

// Drag-to-reorder for active filter chips. Uses Pointer Events (not the
// HTML5 Drag and Drop API) so the same code path handles mouse and touch —
// this app is mobile-first, and native HTML5 DnD has no touch support.
// Reordering the <li> elements is sufficient: the form submits
// "enabled_filters" checkboxes in DOM order, which becomes the saved order.
function initFilterDragDrop() {
    const list = document.getElementById("active-filters-list");
    if (!list) return;

    list.addEventListener("pointerdown", (event) => {
        const handle = event.target.closest(".filter-drag-handle");
        if (!handle) return;
        const chip = handle.closest(".active-filter-chip");
        if (!chip) return;

        event.preventDefault();

        const rect = chip.getBoundingClientRect();
        const offsetX = event.clientX - rect.left;
        const offsetY = event.clientY - rect.top;

        const placeholder = document.createElement("li");
        placeholder.className = "active-filter-chip filter-drop-placeholder";
        placeholder.style.width = `${rect.width}px`;
        placeholder.style.height = `${rect.height}px`;
        chip.after(placeholder);

        chip.classList.add("dragging");
        chip.style.width = `${rect.width}px`;
        chip.style.left = `${rect.left}px`;
        chip.style.top = `${rect.top}px`;

        chip.setPointerCapture(event.pointerId);

        const onMove = (moveEvent) => {
            chip.style.left = `${moveEvent.clientX - offsetX}px`;
            chip.style.top = `${moveEvent.clientY - offsetY}px`;

            const target = document
                .elementsFromPoint(moveEvent.clientX, moveEvent.clientY)
                .find((el) => el.classList.contains("active-filter-chip") && el !== chip && el !== placeholder);

            if (target) {
                const targetRect = target.getBoundingClientRect();
                const before = moveEvent.clientX < targetRect.left + targetRect.width / 2;
                target.parentNode.insertBefore(placeholder, before ? target : target.nextSibling);
            }
        };

        const onEnd = () => {
            chip.releasePointerCapture(event.pointerId);
            chip.classList.remove("dragging");
            chip.style.width = "";
            chip.style.left = "";
            chip.style.top = "";
            placeholder.replaceWith(chip);
            chip.removeEventListener("pointermove", onMove);
            chip.removeEventListener("pointerup", onEnd);
            chip.removeEventListener("pointercancel", onEnd);
        };

        chip.addEventListener("pointermove", onMove);
        chip.addEventListener("pointerup", onEnd);
        chip.addEventListener("pointercancel", onEnd);
    });
}

document.addEventListener("DOMContentLoaded", initFilterDragDrop);

function listFieldActiveKeys() {
    return new Set(
        Array.from(document.querySelectorAll("#list-field-active-list .list-field-active-chip"))
            .map((chip) => chip.dataset.key)
    );
}

function addActiveListField(field) {
    const list = document.getElementById("list-field-active-list");

    const li = document.createElement("li");
    li.className = "list-field-active-chip";
    li.dataset.key = field.key;

    const handle = document.createElement("span");
    handle.className = "list-field-drag-handle";
    handle.setAttribute("aria-hidden", "true");
    handle.textContent = "⠿";

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.name = "enabled_list_fields";
    checkbox.value = field.key;
    checkbox.checked = true;
    checkbox.hidden = true;

    const label = document.createElement("span");
    label.textContent = field.label;

    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "remove-list-field-btn";
    removeBtn.setAttribute("aria-label", `Remove ${field.label}`);
    removeBtn.textContent = "×";
    removeBtn.onclick = () => removeActiveListField(removeBtn);

    li.append(handle, checkbox, label, removeBtn);
    list.appendChild(li);

    const search = document.getElementById("list-field-search");
    search.value = "";
    renderListFieldSuggestions("");
}

function removeActiveListField(button) {
    button.closest(".list-field-active-chip").remove();
    renderListFieldSuggestions(document.getElementById("list-field-search").value);
}

function renderListFieldSuggestions(query) {
    const dataEl = document.getElementById("all-list-fields-data");
    const suggestionsList = document.getElementById("list-field-suggestions");
    if (!dataEl || !suggestionsList) return;

    const allFields = JSON.parse(dataEl.textContent);
    const active = listFieldActiveKeys();
    const normalizedQuery = query.trim().toLowerCase();

    const matches = allFields.filter(
        (field) => !active.has(field.key) && field.label.toLowerCase().includes(normalizedQuery)
    );

    suggestionsList.innerHTML = "";
    for (const field of matches) {
        const li = document.createElement("li");
        li.className = "list-field-suggestion";
        li.textContent = field.label;
        li.onclick = () => addActiveListField(field);
        suggestionsList.appendChild(li);
    }

    suggestionsList.style.display = matches.length > 0 ? "block" : "none";
}

document.addEventListener("DOMContentLoaded", () => {
    const searchInput = document.getElementById("list-field-search");
    if (!searchInput) return;

    document.addEventListener("click", (event) => {
        const control = document.querySelector(".add-list-field-control");
        if (control && !control.contains(event.target)) {
            document.getElementById("list-field-suggestions").style.display = "none";
        }
    });
});

// Same Pointer Events drag-to-reorder approach as initFilterDragDrop, but
// for a vertical single-column list (list-view metadata fields) instead of
// horizontally wrapping chips, so position comparisons use Y instead of X.
function initListFieldDragDrop() {
    const list = document.getElementById("list-field-active-list");
    if (!list) return;

    list.addEventListener("pointerdown", (event) => {
        const handle = event.target.closest(".list-field-drag-handle");
        if (!handle) return;
        const item = handle.closest(".list-field-active-chip");
        if (!item) return;

        event.preventDefault();

        const rect = item.getBoundingClientRect();
        const offsetX = event.clientX - rect.left;
        const offsetY = event.clientY - rect.top;

        const placeholder = document.createElement("li");
        placeholder.className = "list-field-active-chip list-field-drop-placeholder";
        placeholder.style.width = `${rect.width}px`;
        placeholder.style.height = `${rect.height}px`;
        item.after(placeholder);

        item.classList.add("dragging");
        item.style.width = `${rect.width}px`;
        item.style.left = `${rect.left}px`;
        item.style.top = `${rect.top}px`;

        item.setPointerCapture(event.pointerId);

        const onMove = (moveEvent) => {
            item.style.left = `${moveEvent.clientX - offsetX}px`;
            item.style.top = `${moveEvent.clientY - offsetY}px`;

            const target = document
                .elementsFromPoint(moveEvent.clientX, moveEvent.clientY)
                .find((el) => el.classList.contains("list-field-active-chip") && el !== item && el !== placeholder);

            if (target) {
                const targetRect = target.getBoundingClientRect();
                const before = moveEvent.clientY < targetRect.top + targetRect.height / 2;
                target.parentNode.insertBefore(placeholder, before ? target : target.nextSibling);
            }
        };

        const onEnd = () => {
            item.releasePointerCapture(event.pointerId);
            item.classList.remove("dragging");
            item.style.width = "";
            item.style.left = "";
            item.style.top = "";
            placeholder.replaceWith(item);
            item.removeEventListener("pointermove", onMove);
            item.removeEventListener("pointerup", onEnd);
            item.removeEventListener("pointercancel", onEnd);
        };

        item.addEventListener("pointermove", onMove);
        item.addEventListener("pointerup", onEnd);
        item.addEventListener("pointercancel", onEnd);
    });
}

document.addEventListener("DOMContentLoaded", initListFieldDragDrop);