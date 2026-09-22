// Remembers where you were scrolled in the library, per "view signature"
// (URL + the settings that change entry heights). A signature mismatch —
// different filters, page, view, or font size — simply has no stored entry,
// so a changed list starts at the top instead of at a meaningless offset.

const LIBRARY_SCROLL_STORAGE_KEY = "shelf-mg:library-scroll";
const LIBRARY_SCROLL_MAX_ENTRIES = 10;
const LIBRARY_SCROLL_DEBOUNCE_MS = 200;

// We're the only thing allowed to move this page — otherwise the browser's own
// restore races ours after the bfcache reload in library.html. Set before the
// document finishes parsing, since the browser restores scroll on its own.
if ("scrollRestoration" in history) {
    history.scrollRestoration = "manual";
}

function readLibraryScrollMap() {
    // localStorage can throw outright (private browsing, blocked site data),
    // and the stored value can be corrupt — either way, fall back to "nothing
    // remembered" rather than breaking the page.
    try {
        return JSON.parse(localStorage.getItem(LIBRARY_SCROLL_STORAGE_KEY)) || {};
    } catch (e) {
        return {};
    }
}

function writeLibraryScrollMap(map) {
    try {
        localStorage.setItem(LIBRARY_SCROLL_STORAGE_KEY, JSON.stringify(map));
    } catch (e) {
        // out of quota or storage disabled — nothing to recover from
    }
}

// Keeps the map bounded without expiring entries: oldest writes drop first.
function pruneLibraryScrollMap(map) {
    const keys = Object.keys(map);
    if (keys.length <= LIBRARY_SCROLL_MAX_ENTRIES) return map;

    keys.sort((a, b) => (map[b].at || 0) - (map[a].at || 0));
    const pruned = {};
    for (const key of keys.slice(0, LIBRARY_SCROLL_MAX_ENTRIES)) {
        pruned[key] = map[key];
    }
    return pruned;
}

document.addEventListener("DOMContentLoaded", () => {
    const scrollKey = document.body.dataset.libraryScrollKey;
    if (!scrollKey) return;

    function saveScroll() {
        const map = readLibraryScrollMap();
        if (window.scrollY === 0 && !map[scrollKey]) return;
        map[scrollKey] = { y: window.scrollY, at: Date.now() };
        writeLibraryScrollMap(pruneLibraryScrollMap(map));
    }

    const saved = readLibraryScrollMap()[scrollKey];
    if (saved && saved.y > 0) {
        window.scrollTo(0, saved.y);
    }

    let saveDebounceTimer = null;
    window.addEventListener("scroll", () => {
        clearTimeout(saveDebounceTimer);
        saveDebounceTimer = setTimeout(saveScroll, LIBRARY_SCROLL_DEBOUNCE_MS);
    });

    // pagehide, not beforeunload — beforeunload is unreliable on mobile, and a
    // navigation can easily beat the debounce above.
    window.addEventListener("pagehide", () => {
        clearTimeout(saveDebounceTimer);
        saveScroll();
    });
});
