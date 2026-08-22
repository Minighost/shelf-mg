function toggleHeaderNavDrawer() {
    const drawer = document.getElementById("header-nav-drawer");
    const backdrop = document.querySelector(".header-nav-drawer-backdrop");
    const toggle = document.querySelector(".header-nav-drawer-toggle");
    const isOpen = drawer.classList.toggle("open");
    backdrop.classList.toggle("open", isOpen);
    toggle.setAttribute("aria-expanded", isOpen);
}

function closeHeaderNavDrawer() {
    document.getElementById("header-nav-drawer").classList.remove("open");
    document.querySelector(".header-nav-drawer-backdrop").classList.remove("open");
    document.querySelector(".header-nav-drawer-toggle").setAttribute("aria-expanded", "false");
}

document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    const drawer = document.getElementById("header-nav-drawer");
    if (drawer && drawer.classList.contains("open")) closeHeaderNavDrawer();
});
