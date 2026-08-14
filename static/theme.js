function toggleTheme() {
    const themes = ["light", "dark"];
    const current = document.documentElement.getAttribute("data-theme");
    const next = themes[(themes.indexOf(current) + 1) % themes.length];

    document.documentElement.setAttribute("data-theme", next);

    const frame = document.getElementById("chapter-frame");
    if (frame && frame.contentDocument) {
        frame.contentDocument.documentElement.setAttribute("data-theme", next);
    }

    fetch("/settings/theme", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ theme: next }),
    }).catch((err) => console.error("Failed to save theme:", err));
}