// frontend/static/js/app.js
(() => {
  const log = (...a) => console.log('[Residuos360]', ...a);
  const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function setupReveal() {
    const els = document.querySelectorAll('.fade-up');
    if (!els.length) return;

    if (prefersReducedMotion || !('IntersectionObserver' in window)) {
      els.forEach(el => { el.style.opacity = 1; el.style.transform = 'none'; });
      return;
    }

    els.forEach(el => { el.style.animationPlayState = 'paused'; el.style.opacity = 0; });

    const io = new IntersectionObserver((entries, obs) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          const el = entry.target;
          el.style.animationPlayState = 'running';
          el.style.opacity = '';
          obs.unobserve(el);
        }
      });
    }, { threshold: 0.15 });

    els.forEach(el => io.observe(el));
  }

  function setupTilt(selector = '.feature, .plan-card') {
    if (prefersReducedMotion) return;
    const cards = document.querySelectorAll(selector);
    cards.forEach(card => {
      let rafId = null;
      let rx = 0, ry = 0;

      const onMove = (e) => {
        const r = card.getBoundingClientRect();
        const x = (e.clientX ?? e.touches?.[0]?.clientX) - r.left;
        const y = (e.clientY ?? e.touches?.[0]?.clientY) - r.top;
        if (x == null || y == null) return;

        const px = (x / r.width) - 0.5;
        const py = (y / r.height) - 0.5;
        rx = (-py * 6);
        ry = (px * 6);

        if (rafId) cancelAnimationFrame(rafId);
        rafId = requestAnimationFrame(() => {
          card.style.transform = `perspective(600px) rotateX(${rx}deg) rotateY(${ry}deg) translateZ(0)`;
        });
      };

      const onLeave = () => {
        if (rafId) cancelAnimationFrame(rafId);
        rafId = requestAnimationFrame(() => {
          card.style.transform = 'perspective(600px) rotateX(0deg) rotateY(0deg) translateZ(0)';
        });
      };

      card.addEventListener('mousemove', onMove);
      card.addEventListener('mouseleave', onLeave);
      card.addEventListener('touchmove', onMove, { passive: true });
      card.addEventListener('touchend', onLeave);
    });
  }

  function setupProfileMenu() {
    const menus = document.querySelectorAll('.profile-menu, .header-utility-menu');
    if (!menus.length) return;

    const closeAll = () => {
      menus.forEach((menu) => {
        const btn = menu.querySelector('.profile-icon');
        const dd = menu.querySelector('.dropdown');
        if (!btn || !dd) return;
        dd.classList.remove('open');
        btn.setAttribute('aria-expanded', 'false');
      });
    };

    menus.forEach((menu) => {
      const btn = menu.querySelector('.profile-icon');
      const dd = menu.querySelector('.dropdown');
      if (!btn || !dd) return;

      btn.setAttribute('aria-haspopup', 'true');
      btn.setAttribute('aria-expanded', 'false');

      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const willOpen = !dd.classList.contains('open');
        closeAll();
        if (willOpen) {
          dd.classList.add('open');
          btn.setAttribute('aria-expanded', 'true');
        }
      });
    });

    document.addEventListener('click', (e) => {
      const insideSomeMenu = Array.from(menus).some((menu) => menu.contains(e.target));
      if (!insideSomeMenu) closeAll();
    });

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') closeAll();
    });
  }

  function setupSmoothAnchors() {
    const links = document.querySelectorAll('a[href^="#"]:not([href="#"])');
    links.forEach(link => {
      link.addEventListener('click', (e) => {
        const id = link.getAttribute('href').slice(1);
        const target = document.getElementById(id);
        if (target) {
          e.preventDefault();
          target.scrollIntoView({ behavior: 'smooth', block: 'start' });
          history.pushState(null, '', `#${id}`);
        }
      });
    });
  }

  async function renderPlans() {
    const cont = document.getElementById('planes-container');
    if (!cont || cont.children.length) return;

    try {
      const res = await fetch('/api/plans', { headers: { 'Accept': 'application/json' } });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();

      const html = (data || []).map(p => {
        const iconClass =
          p.name === 'Starter' ? 'fa-seedling' :
          p.name === 'Pro' ? 'fa-recycle' :
          'fa-handshake';

        const price = (p.price_uyu > 0) ? `${p.price_uyu} UYU / mes` : 'Sin mensualidad';
        const limit = p.limit_requests ? `Hasta ${p.limit_requests} solicitudes/mes` : 'Solicitudes ilimitadas';

        const extras = [];
        if (p.name === 'Starter') {
          extras.push('Dashboard básico', 'Alertas por email', 'Soporte email 48 h');
        } else if (p.name === 'Pro') {
          extras.push('PDF de manifiestos', 'Exportes Excel', 'Acceso API');
        } else {
          extras.push('Comisión 2 % o 0,50 USD por solicitud');
        }

        return `
          <div class="plan-card glass fade-up">
            <i class="fas ${iconClass} icon" aria-hidden="true"></i>
            <h3>${p.name}</h3>
            <p class="plan-price">${price}</p>
            <p class="plan-limit">${limit}</p>
            <ul class="plan-features">
              ${extras.map(e => `<li>${e}</li>`).join('')}
            </ul>
            <button class="btn" onclick="window.location='/register'">Elegir plan</button>
          </div>
        `;
      }).join('');

      cont.innerHTML = html || '<p class="text-center">No se pudieron cargar los planes.</p>';
    } catch (err) {
      console.error(err);
      cont.innerHTML = '<p class="text-center">No se pudieron cargar los planes.</p>';
    }
  }

  function init() {
    log('frontend iniciado');
    setupReveal();
    setupTilt();
    setupProfileMenu();
    setupSmoothAnchors();
    renderPlans();
  }

  document.addEventListener('DOMContentLoaded', init);
})();

(() => {
  const header = document.querySelector('header');
  const links = document.querySelectorAll('.nav-list a');

  function onScroll() {
    if (!header) return;
    header.classList.toggle('scrolled', window.scrollY > 10);
  }

  function markActive() {
    const path = location.pathname.replace(/\/+$/, '') || '/';
    links.forEach(a => {
      const href = (a.getAttribute('href') || '').replace(/\/+$/, '') || '/';
      if (href === path) a.classList.add('active');
    });
  }

  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();
  markActive();
})();
