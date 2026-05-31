/* =========================================
   PRIVACY GUARDIAN — CORE JS
   Particle Canvas + Scroll Reveals + Theme Toggle + Sidebar
   ========================================= */

document.addEventListener("DOMContentLoaded", () => {

    // =============================================
    // 1. THEME TOGGLE (persisted via localStorage)
    // =============================================
    const savedTheme = localStorage.getItem("pg-theme");
    if (savedTheme === "light") {
        document.body.classList.add("light");
    }

    window.toggleDarkMode = function() {
        document.body.classList.toggle("light");
        const isLight = document.body.classList.contains("light");
        localStorage.setItem("pg-theme", isLight ? "light" : "dark");

        // Update particle colors live
        if (window._particles) {
            const col = isLight ? { r: 30, g: 30, b: 50 } : { r: 255, g: 255, b: 255 };
            window._particles.forEach(p => {
                p.color = col;
            });
        }
    };

    // =============================================
    // 1.5. ACCOUNT MENU TOGGLE
    // =============================================
    window.toggleAccountMenu = function() {
        const menu = document.getElementById("accountMenu");
        if (menu) menu.classList.toggle("show-menu");
    };

    document.addEventListener("click", function(event) {
        const accountBtn = document.querySelector(".account-btn");
        const accountMenu = document.getElementById("accountMenu");
        
        if (accountBtn && accountMenu && !accountBtn.contains(event.target) && !accountMenu.contains(event.target)) {
            accountMenu.classList.remove("show-menu");
        }
    });

    // =============================================
    // 2. SIDEBAR TOGGLE
    // =============================================
    const menuBtn = document.getElementById("menuToggle");
    const sidebar = document.getElementById("sidebar");
    const main = document.querySelector(".main");

    if (menuBtn && sidebar && main) {
        sidebar.classList.add("collapsed");
        main.classList.add("expanded");

        menuBtn.addEventListener("click", () => {
            sidebar.classList.toggle("collapsed");
            main.classList.toggle("expanded");
        });
    }

    // =============================================
    // 3. SIDEBAR LINK HIGHLIGHTING
    // =============================================
    const sidebarLinks = document.querySelectorAll('.sidebar a');

    function setActiveLink() {
        const path = window.location.pathname;
        let hash = window.location.hash;

        sidebarLinks.forEach(l => l.classList.remove('active'));

        let matched = false;

        if (path === "/employee-dashboard" || path === "/admin-dashboard" || path === "/") {
            if (!hash) {
                hash = path === "/admin-dashboard" ? "#admin-overview" : "#profile";
            }
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

    // Scroll-based active state for dashboard
    const currentPath = window.location.pathname;
    if (currentPath === "/employee-dashboard" || currentPath === "/admin-dashboard" || currentPath === "/") {
        window.addEventListener('scroll', () => {
            let current = '';
            const sections = document.querySelectorAll('section[id]');

            sections.forEach(section => {
                const sectionTop = section.offsetTop;
                if (window.pageYOffset >= sectionTop - 140) {
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

    // =============================================
    // 4. INTERSECTION OBSERVER (Scroll Reveals)
    // =============================================
    const reveals = document.querySelectorAll('.reveal');

    if (reveals.length > 0) {
        const observer = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    entry.target.classList.add('visible');
                    observer.unobserve(entry.target); // Only animate once
                }
            });
        }, {
            threshold: 0.08,
            rootMargin: '0px 0px -40px 0px'
        });

        reveals.forEach(el => observer.observe(el));
    }

    // =============================================
    // 4.5. DYNAMIC SCROLL SCALING
    // =============================================
    const scaleSections = document.querySelectorAll('.full-page-section');
    if (scaleSections.length > 0) {
        const scaleObserver = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    entry.target.classList.add('focused');
                } else {
                    entry.target.classList.remove('focused');
                }
            });
        }, {
            threshold: 0,
            rootMargin: '-30% 0px -30% 0px'
        });

        scaleSections.forEach(el => {
            scaleObserver.observe(el);
            // Default to not focused if not initially in center
            el.classList.remove('focused');
        });
    }

    // =============================================
    // 5. TOPBAR HIDE ON SCROLL DOWN
    // =============================================
    let lastScrollY = window.scrollY;
    const topbar = document.querySelector(".topbar");

    if (topbar) {
        window.addEventListener("scroll", () => {
            const currentScrollY = window.scrollY;
            if (currentScrollY > lastScrollY && currentScrollY > 60) {
                // Scrolling down
                topbar.classList.add("topbar--hidden");
            } else {
                // Scrolling up
                topbar.classList.remove("topbar--hidden");
            }
            lastScrollY = currentScrollY;
        }, { passive: true });
    }

    // =============================================
    // 6. PARTICLE CANVAS
    // =============================================
    initParticles();

});

/* =========================================
   PARTICLE CANVAS SYSTEM
   Lightweight, connects nearby particles with faint lines
   ========================================= */
function initParticles() {
    const canvas = document.getElementById('particles');
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    let width, height;
    const isLight = document.body.classList.contains('light');

    function resize() {
        width = canvas.width = window.innerWidth;
        height = canvas.height = window.innerHeight;
    }
    resize();
    window.addEventListener('resize', resize);

    // Particle configuration
    const PARTICLE_COUNT = Math.min(80, Math.floor(window.innerWidth / 18));
    const CONNECTION_DIST = 150;
    const SPEED = 0.3;

    const baseColor = isLight
        ? { r: 30, g: 30, b: 50 }
        : { r: 255, g: 255, b: 255 };

    const particles = [];
    window._particles = particles;

    for (let i = 0; i < PARTICLE_COUNT; i++) {
        particles.push({
            x: Math.random() * width,
            y: Math.random() * height,
            vx: (Math.random() - 0.5) * SPEED,
            vy: (Math.random() - 0.5) * SPEED,
            radius: Math.random() * 1.5 + 0.5,
            opacity: Math.random() * 0.3 + 0.1,
            color: { ...baseColor }
        });
    }

    // Mouse interaction
    let mouse = { x: -1000, y: -1000 };
    document.addEventListener('mousemove', (e) => {
        mouse.x = e.clientX;
        mouse.y = e.clientY;
    });

    function animate() {
        ctx.clearRect(0, 0, width, height);

        // Update & draw particles
        for (let i = 0; i < particles.length; i++) {
            const p = particles[i];

            // Move
            p.x += p.vx;
            p.y += p.vy;

            // Bounce off edges
            if (p.x < 0 || p.x > width) p.vx *= -1;
            if (p.y < 0 || p.y > height) p.vy *= -1;

            // Keep in bounds
            p.x = Math.max(0, Math.min(width, p.x));
            p.y = Math.max(0, Math.min(height, p.y));

            // Draw particle
            ctx.beginPath();
            ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2);
            ctx.fillStyle = `rgba(${p.color.r},${p.color.g},${p.color.b},${p.opacity})`;
            ctx.fill();

            // Connect to nearby particles
            for (let j = i + 1; j < particles.length; j++) {
                const p2 = particles[j];
                const dx = p.x - p2.x;
                const dy = p.y - p2.y;
                const dist = Math.sqrt(dx * dx + dy * dy);

                if (dist < CONNECTION_DIST) {
                    const alpha = (1 - dist / CONNECTION_DIST) * 0.08;
                    ctx.beginPath();
                    ctx.moveTo(p.x, p.y);
                    ctx.lineTo(p2.x, p2.y);
                    ctx.strokeStyle = `rgba(${p.color.r},${p.color.g},${p.color.b},${alpha})`;
                    ctx.lineWidth = 0.5;
                    ctx.stroke();
                }
            }

            // Mouse repulsion (subtle)
            const mdx = p.x - mouse.x;
            const mdy = p.y - mouse.y;
            const mDist = Math.sqrt(mdx * mdx + mdy * mdy);
            if (mDist < 120) {
                const force = (120 - mDist) / 120 * 0.015;
                p.vx += (mdx / mDist) * force;
                p.vy += (mdy / mDist) * force;
            }

            // Speed limit
            const speed = Math.sqrt(p.vx * p.vx + p.vy * p.vy);
            if (speed > SPEED * 2) {
                p.vx = (p.vx / speed) * SPEED * 2;
                p.vy = (p.vy / speed) * SPEED * 2;
            }
        }

        requestAnimationFrame(animate);
    }

    animate();
}

/* =========================================
   CUSTOM NATIVE DIALOG CONFIRM
   Replaces browser default confirm() using <dialog>
   ========================================= */
window.customConfirm = function(message) {
    return new Promise((resolve) => {
        const dialog = document.createElement('dialog');
        dialog.setAttribute('closedby', 'any'); // enable light dismiss
        dialog.style.border = '1px solid var(--border-subtle, rgba(255,255,255,0.1))';
        dialog.style.borderRadius = '16px';
        dialog.style.padding = '24px';
        
        // Adaptive styling based on theme
        const isLight = document.body.classList.contains('light');
        dialog.style.background = isLight ? '#ffffff' : '#1e1e2e';
        dialog.style.color = isLight ? '#111827' : '#f3f4f6';
        dialog.style.boxShadow = '0 25px 50px -12px rgba(0, 0, 0, 0.25)';
        dialog.style.maxWidth = '400px';
        dialog.style.width = '90%';
        dialog.style.margin = 'auto';
        dialog.style.backdropFilter = 'blur(10px)';
        
        dialog.classList.add('custom-confirm-dialog');
        
        // CSS for ::backdrop injected locally to dialog
        const style = document.createElement('style');
        style.innerHTML = `
            dialog.custom-confirm-dialog::backdrop {
                background: rgba(0, 0, 0, 0.6);
                backdrop-filter: blur(4px);
            }
        `;
        document.head.appendChild(style);

        dialog.innerHTML = `
            <form method="dialog">
                <h3 style="margin-top: 0; margin-bottom: 12px; font-size: 18px; display: flex; align-items: center; gap: 8px; font-family: 'Space Grotesk', sans-serif;">
                    ⚠️ Confirm Action
                </h3>
                <p style="margin-bottom: 24px; font-size: 14px; color: ${isLight ? '#4b5563' : '#9ca3af'}; line-height: 1.5; font-family: 'Inter', sans-serif;">
                    ${message}
                </p>
                <div style="display: flex; justify-content: flex-end; gap: 12px;">
                    <button value="cancel" type="button" id="confirm-cancel-btn" style="padding: 8px 16px; border-radius: 8px; border: 1px solid ${isLight ? '#d1d5db' : '#4b5563'}; background: transparent; color: inherit; cursor: pointer; font-family: 'Inter', sans-serif; font-weight: 500; transition: background 0.2s;">Cancel</button>
                    <button value="confirm" style="padding: 8px 16px; border-radius: 8px; border: none; background: linear-gradient(90deg, #ea0016, #b00010); color: white; cursor: pointer; font-family: 'Inter', sans-serif; font-weight: 600; box-shadow: 0 4px 12px rgba(234,0,22,0.2); transition: transform 0.2s;">Confirm</button>
                </div>
            </form>
        `;
        
        document.body.appendChild(dialog);
        
        const cancelBtn = dialog.querySelector('#confirm-cancel-btn');
        cancelBtn.addEventListener('click', () => {
            dialog.close('cancel');
        });

        dialog.addEventListener('close', () => {
            document.body.removeChild(dialog);
            // Cleanup the injected style element to prevent clutter
            document.head.removeChild(style);
            resolve(dialog.returnValue === 'confirm');
        });
        
        dialog.showModal();
    });
};
