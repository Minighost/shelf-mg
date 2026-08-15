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

function previewRecentListLimit(count) {
    document.getElementById("recent-list-limit-label").textContent = count;
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

    li.append(checkbox, label, removeBtn);
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