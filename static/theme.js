(function () {
    const saved = localStorage.getItem("shelf-mg-theme");
    if (saved) {
        document.documentElement.setAttribute("data-theme", saved);
    }
})();

function toggleTheme() {
    const current = document.documentElement.getAttribute("data-theme");
    const next = current === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("shelf-mg-theme", next);

    const frame = document.getElementById("chapter-frame");
    if (frame && frame.contentDocument) {
        frame.contentDocument.documentElement.setAttribute("data-theme", next);
    }
}