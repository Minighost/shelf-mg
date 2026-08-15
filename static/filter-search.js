function filterOptions(input) {
    const container = input.closest(".filter-field").querySelector(".filter-options");
    const query = input.value.trim().toLowerCase();
    for (const choice of container.querySelectorAll(".filter-choice")) {
        choice.style.display = choice.textContent.trim().toLowerCase().includes(query) ? "" : "none";
    }
}

// On mobile, the on-screen keyboard can cover the search input since it
// isn't at the very top of the page. Scroll it into view after focusing,
// once the keyboard has finished opening (an immediate scroll gets
// overridden by the keyboard's own viewport resize).
document.addEventListener("DOMContentLoaded", () => {
    const searchInput = document.querySelector(".search-form input[name='q']");
    const searchWrapper = document.querySelector(".search-form-wrapper");
    if (!searchInput || !searchWrapper) return;

    searchInput.addEventListener("focus", () => {
        if (!window.matchMedia("(max-width: 599px)").matches) return;
        setTimeout(() => {
            searchWrapper.scrollIntoView({ block: "start", behavior: "smooth" });
        }, 300);
    });
});
