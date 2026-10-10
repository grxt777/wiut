(function () {
  const ITEMS = [
    ['/dashboard/index.html', 'home', 'Главная', 'Главная'],
    ['/dashboard/map.html', 'map', 'Карта', 'Карта банкоматов и маршруты'],
    ['/dashboard/atm-analytics.html', 'radar', 'Мониторинг', 'Мониторинг и прогноз'],
    ['/dashboard/analytics.html', 'bar-chart-3', 'Аналитика', 'Аналитика ATM'],
    ['/dashboard/incassation.html', 'calendar-days', 'Инкассация', 'Календарь инкассации'],
    ['/dashboard/import.html', 'file-spreadsheet', 'Импорт', 'Импорт реестров'],
  ];
  const ALIASES = {
    '/dashboard/import-atms.html': '/dashboard/import.html',
    '/dashboard/import-branches.html': '/dashboard/import.html',
  };
  const path = location.pathname;
  const active = ALIASES[path] || path;

  document.querySelectorAll('nav[data-app-nav]').forEach(nav => {
    nav.classList.add('app-nav');
    nav.setAttribute('aria-label', 'Разделы');
    nav.innerHTML = ITEMS.map(([href, icon, label, title]) => {
      const on = href === active ? ' class="on" aria-current="page"' : '';
      return `<a href="${href}" title="${title}"${on}><i data-lucide="${icon}"></i><span>${label}</span></a>`;
    }).join('');
  });
})();
