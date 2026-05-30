// --- Global Sidebar Logic ---

document.addEventListener("DOMContentLoaded", () => {
    // 1. Sidebar Toggle
    const menuBtn = document.getElementById("menuToggle");
    const sidebar = document.getElementById("sidebar");
    const main = document.querySelector(".main");

    if (menuBtn && sidebar && main) {
        menuBtn.addEventListener("click", () => {
            sidebar.classList.toggle("collapsed");
            main.classList.toggle("expanded");
        });
    }

    // 2. Highlighting Logic
    const currentPath = window.location.pathname;
    const sidebarLinks = document.querySelectorAll('.sidebar a');

    function setActiveLink() {
        const path = window.location.pathname;
        let hash = window.location.hash;
        
        sidebarLinks.forEach(l => l.classList.remove('active'));

        let matched = false;

        if (path === "/employee-dashboard" || path === "/") {
            if (!hash) hash = "#profile"; // default section
            sidebarLinks.forEach(link => {
                if (link.getAttribute("href").includes(hash)) {
                    link.classList.add("active");
                    matched = true;
                }
            });
        } else {
            sidebarLinks.forEach(link => {
                const href = link.getAttribute("href");
                if (href && href.startsWith(path)) {
                    link.classList.add("active");
                    matched = true;
                }
            });
        }
        
        if (!matched && sidebarLinks.length > 0) {
            if (path.startsWith("/user-details")) {
                const explorerLink = document.getElementById("nav-employee-directory");
                if (explorerLink) explorerLink.classList.add("active");
            }
        }
    }

    setActiveLink();
    window.addEventListener("hashchange", setActiveLink);

    // 3. Update active state based on scrolling (for dashboard)
    if (currentPath === "/employee-dashboard" || currentPath === "/") {
        window.addEventListener('scroll', () => {
            let current = '';
            const sections = document.querySelectorAll('section');
            
            sections.forEach(section => {
                const sectionTop = section.offsetTop;
                if (window.pageYOffset >= sectionTop - 120) {
                    current = section.getAttribute('id');
                }
            });

            if (current) {
                sidebarLinks.forEach(link => {
                    link.classList.remove('active');
                    if (link.getAttribute('href').includes("#" + current)) {
                        link.classList.add('active');
                    }
                });
            }
        });
    }
});
