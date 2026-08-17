function filterOptions(input) {
    const container = input.closest(".filter-field").querySelector(".filter-options");
    const query = input.value.trim().toLowerCase();
    for (const choice of container.querySelectorAll(".filter-choice")) {
        choice.style.display = choice.textContent.trim().toLowerCase().includes(query) ? "" : "none";
    }
}

function clearFieldInputs(field) {
    const anyRadio = field.querySelector('input[type="radio"][value=""]');
    if (anyRadio) {
        anyRadio.checked = true;
    }
    field.querySelectorAll('input[type="checkbox"]').forEach((cb) => (cb.checked = false));
    field.querySelectorAll(".filter-range input").forEach((input) => (input.value = ""));
    const clearBtn = field.querySelector(".filter-clear-btn");
    if (clearBtn) {
        clearBtn.style.display = "none";
    }
}

function clearFilterField(event, button) {
    event.preventDefault();
    event.stopPropagation(); // don't toggle the <details> open/closed
    clearFieldInputs(button.closest(".filter-field"));
}

function clearAllFilters(event) {
    event.preventDefault();
    const form = event.target.closest("form");
    form.querySelectorAll(".filter-field").forEach(clearFieldInputs);
    form.requestSubmit();
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
