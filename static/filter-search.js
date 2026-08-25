function filterOptions(input) {
    const container = input.closest(".filter-field").querySelector(".filter-options");
    const query = input.value.trim().toLowerCase();
    for (const choice of container.querySelectorAll(".filter-choice")) {
        choice.style.display = choice.textContent.trim().toLowerCase().includes(query) ? "" : "none";
    }
}

function clearFieldInputs(field) {
    field.querySelectorAll('input[type="checkbox"]').forEach((cb) => (cb.checked = false));
    field.querySelectorAll(".filter-range input").forEach((input) => (input.value = ""));
    const matchMode = field.querySelector(".filter-match-mode");
    if (matchMode) {
        matchMode.classList.add("filter-match-mode-hidden");
        const anyRadio = matchMode.querySelector('input[value="any"]');
        if (anyRadio) {
            anyRadio.checked = true;
        }
    }
    const clearBtn = field.querySelector(".filter-clear-btn");
    if (clearBtn) {
        clearBtn.classList.add("filter-clear-btn-hidden");
    }
}

function fieldHasActiveInput(field) {
    if (field.querySelector('input[type="checkbox"]:checked')) return true;
    return [...field.querySelectorAll(".filter-range input")].some((input) => input.value.trim() !== "");
}

function updateClearBtnVisibility(field) {
    const clearBtn = field.querySelector(".filter-clear-btn");
    if (!clearBtn) return;
    clearBtn.classList.toggle("filter-clear-btn-hidden", !fieldHasActiveInput(field));
}

// Reveal each field's any/all match-mode toggle only once 2+ checkboxes are
// checked within that field — with 0 or 1 selected there's no AND/OR
// ambiguity to resolve, so the toggle stays hidden. Also reveal the field's
// Clear button as soon as anything is selected, rather than waiting for the
// filters to actually be applied.
document.addEventListener("change", (event) => {
    if (event.target.type !== "checkbox") return;
    const field = event.target.closest(".filter-field");
    if (!field) return;
    const matchMode = field.querySelector(".filter-match-mode");
    if (matchMode) {
        const checkedCount = field.querySelectorAll('.filter-options input[type="checkbox"]:checked').length;
        matchMode.classList.toggle("filter-match-mode-hidden", checkedCount < 2);
    }
    updateClearBtnVisibility(field);
});

document.addEventListener("input", (event) => {
    if (!event.target.closest(".filter-range")) return;
    const field = event.target.closest(".filter-field");
    if (!field) return;
    updateClearBtnVisibility(field);
});

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
