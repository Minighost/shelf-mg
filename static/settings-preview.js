function previewTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
}

function previewFont(fontKey) {
    const select = document.getElementById("font_family");
    const option = select.querySelector(`option[value="${fontKey}"]`);
    if (option) {
        document.documentElement.style.setProperty("--reader-font-family", option.dataset.css);
    }
}

function previewFontSize(size) {
    document.documentElement.style.setProperty("--reader-font-size", `${size}rem`);
    document.getElementById("font-size-label").textContent = size;
}