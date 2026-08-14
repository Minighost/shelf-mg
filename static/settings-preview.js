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
    document.getElementById("settings-preview").style.setProperty("--reader-font-size", `${size}rem`);
    document.getElementById("font-size-label").textContent = size;
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
    document.getElementById("reader-font-preview").style.setProperty("--reader-font-size", `${size}rem`);
    document.getElementById("reader-font-size-label").textContent = size;
}