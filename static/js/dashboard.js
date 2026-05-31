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

        if (path === "/employee-dashboard" || path === "/") {
            if (!hash) hash = "#profile";
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
    if (currentPath === "/employee-dashboard" || currentPath === "/") {
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
    // 5. PARTICLE CANVAS
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
