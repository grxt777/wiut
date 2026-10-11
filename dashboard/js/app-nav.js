(function () {
  const ITEMS = [
    ['/dashboard/index.html', 'home', 'Главная', 'Главная'],
    ['/dashboard/map.html', 'map', 'Карта', 'Карта банкоматов и маршруты'],
    ['/dashboard/atm-analytics.html', 'radar', 'Мониторинг', 'Мониторинг и прогноз'],
    ['/dashboard/analytics.html', 'bar-chart-3', 'Аналитика', 'Аналитика ATM'],
    ['/dashboard/incassation.html', 'calendar-days', 'Инкассация', 'Календарь инкассации'],
  ];
  const active = location.pathname;

  document.querySelectorAll('nav[data-app-nav]').forEach(nav => {
    nav.classList.add('app-nav');
    nav.setAttribute('aria-label', 'Разделы');
    nav.innerHTML = ITEMS.map(([href, icon, label, title]) => {
      const on = href === active ? ' class="on" aria-current="page"' : '';
      return `<a href="${href}" title="${title}"${on}><i data-lucide="${icon}"></i><span>${label}</span></a>`;
    }).join('');
  });
})();
