function filterOptions(input) {
    const container = input.closest(".filter-field").querySelector(".filter-options");
    const query = input.value.trim().toLowerCase();
    for (const choice of container.querySelectorAll(".filter-choice")) {
        choice.style.display = choice.textContent.trim().toLowerCase().includes(query) ? "" : "none";
    }
}
