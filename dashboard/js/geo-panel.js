// ═══════════════════════════════════════════════════════════
// Гео-скоринг на карте: шестерёнка → окно настроек.
//  • режим «Оценка локации»: иконки банкоматов → круг с оценкой и ★, цвет — качество места;
//  • тепловая карта гео-спроса по Ташкенту (итог или отдельный фактор);
//  • фильтр по оценке и градациям, слои мест скопления людей, зоны влияния;
//  • «белые пятна» — где открыть банкомат; слабые точки сети; оценка любой точки кликом.
// Данные: /api/geo/* (api/analytics/geo_map.py). Подключается после map-app.js.
// ═══════════════════════════════════════════════════════════
(function () {
  const GP = {
    scoreMode: false,
    data: null,               // /api/geo/atms
    byId: {},
    filter: { min: 0, max: 10, grades: new Set() },
    heat: { on: false, layer: 'score', step: 200, opacity: 0.65 },
    poi: { metro: false, market: false, mall: false, supermarket: false },
    zones: false,
    opps: { on: false, gap: 700 },
    weak: false,
    probe: false,
    competitors: true,
    layers: {},
    grids: {},
    pois: null,
  };
  window.GeoPanel = GP;

  // ── Цвет: непрерывная шкала, у каждой сотой свой оттенок ──
  // 0 — красный, 4 — оранжевый, 5.5 — янтарный, 7+ — зелёный.
  const STOPS = [[0, 0, 72, 46], [2.5, 10, 78, 48], [4.0, 27, 90, 48], [5.5, 42, 92, 46], [7.0, 100, 55, 40], [8.5, 145, 60, 34], [10, 160, 70, 28]];
  function scoreColor(v) {
    v = Math.max(0, Math.min(10, Number(v) || 0));
    for (let i = 1; i < STOPS.length; i++) {
      const [x1, h1, s1, l1] = STOPS[i - 1], [x2, h2, s2, l2] = STOPS[i];
      if (v <= x2) {
        const t = (v - x1) / (x2 - x1);
        return `hsl(${(h1 + (h2 - h1) * t).toFixed(1)}, ${(s1 + (s2 - s1) * t).toFixed(1)}%, ${(l1 + (l2 - l1) * t).toFixed(1)}%)`;
      }
    }
    return `hsl(160, 70%, 28%)`;
  }
  GP.scoreColor = scoreColor;

  // Тепловая карта: классическая палитра «тёмно-синий → красный» (мало → много спроса)
  const HEAT_STOPS = [
    [0.0, [30, 27, 120]],   // тёмно-синий
    [1.5, [37, 64, 200]],   // синий
    [3.0, [14, 165, 233]],  // голубой
    [4.5, [34, 197, 94]],   // зелёный
    [5.8, [250, 204, 21]],  // жёлтый
    [6.8, [249, 115, 22]],  // оранжевый
    [7.6, [220, 38, 38]],   // красный
    [10, [153, 27, 27]],    // тёмно-красный
  ];
  function heatColor(v) {
    v = Math.max(0, Math.min(10, Number(v) || 0));
    for (let i = 1; i < HEAT_STOPS.length; i++) {
      const [x1, c1] = HEAT_STOPS[i - 1], [x2, c2] = HEAT_STOPS[i];
      if (v <= x2) {
        const t = (v - x1) / (x2 - x1);
        const c = c1.map((a, k) => Math.round(a + (c2[k] - a) * t));
        return `rgb(${c[0]}, ${c[1]}, ${c[2]})`;
      }
    }
    return 'rgb(153, 27, 27)';
  }
  GP.heatColor = heatColor;
  const GRADE_ORDER = ['Отличная', 'Хорошая', 'Средняя', 'Слабая', 'Плохая'];
  const GRADE_SAMPLE = { 'Отличная': 7.6, 'Хорошая': 6.2, 'Средняя': 4.7, 'Слабая': 3.2, 'Плохая': 1.2 };
  const CAT_COLORS = { metro: '#16a34a', market: '#e11d48', mall: '#7c3aed', supermarket: '#0891b2', competitors: '#8b5cf6' };
  const CAT_LABELS = { score: 'Итоговый гео-скоринг', metro: 'Метро', market: 'Рынки и базары', mall: 'ТЦ и моллы', supermarket: 'Супермаркеты', competitors: 'Банкоматы других банков (снижают оценку)' };
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const mln = v => v == null ? '—' : (v / 1e6).toLocaleString('ru-RU', { maximumFractionDigits: 1 }) + ' млн';
  const pane = (name, z) => { if (!map.getPane(name)) { map.createPane(name); map.getPane(name).style.zIndex = z; } return name; };

  // ── Иконка «оценка локации» ──
  function scoreIcon(a, highlight) {
    const c = scoreColor(a.geo_score);
    return L.divIcon({
      className: 'atm-marker-icon',
      html: `<div class="gp-pin${highlight ? ' gp-pin--hl' : ''}" style="--c:${c}">
        <span class="gp-pin__val">${a.geo_score.toFixed(2)}</span><span class="gp-pin__star">★</span></div>`,
      iconSize: [38, 38], iconAnchor: [19, 19], popupAnchor: [0, -18],
    });
  }

  function passesFilter(id) {
    const a = GP.byId[id];
    if (!a) return true;
    if (a.geo_score < GP.filter.min || a.geo_score > GP.filter.max) return false;
    if (GP.filter.grades.size && !GP.filter.grades.has(a.grade)) return false;
    if (GP.weak && a.geo_score >= 2.5) return false;
    return true;
  }

  function filterActive() {
    return GP.filter.min > 0 || GP.filter.max < 10 || GP.filter.grades.size > 0 || GP.weak;
  }

  // Применить режим и фильтр ко всем маркерам банкоматов
  function applyMarkers() {
    Object.entries(markers).forEach(([id, m]) => {
      m._gpId = id;
      const a = GP.byId[id];
      if (GP.scoreMode && a) {
        const key = `score|${a.geo_score}|${GP.weak && a.geo_score < 2.5 ? 1 : 0}`;
        if (m._atmIconKey !== key) { m._atmIconKey = key; m.setIcon(scoreIcon(a, GP.weak && a.geo_score < 2.5)); }
      } else if (String(m._atmIconKey || '').startsWith('score|')) {
        m._atmIconKey = null;
        const st = (atmState[id] || {}).status || 'ok';
        _setIcon(m, st, false);
      }
      const show = atmsVisible && passesFilter(id);
      if (show && !map.hasLayer(m)) m.addTo(map);
      if (!show && map.hasLayer(m)) map.removeLayer(m);
    });
    renderLegend();
    updateCounter();
    if (GP.zones) drawZones();
  }
  GP.apply = applyMarkers;

  // ── Перехват функций карты ──
  const _setIcon = window.setAtmMarkerIcon;
  window.setAtmMarkerIcon = function (marker, status, isHighlighted = false) {
    if (GP.scoreMode && marker && GP.byId[marker._gpId]) return;   // в режиме оценки иконки не трогаем
    return _setIcon(marker, status, isHighlighted);
  };
  const _initMarkers = window.initMarkers;
  window.initMarkers = function (...args) { const r = _initMarkers.apply(this, args); applyMarkers(); return r; };
  const _toggleAtms = window.toggleAtmsLayer;
  window.toggleAtmsLayer = function (...args) { const r = _toggleAtms.apply(this, args); applyMarkers(); return r; };
  const _selectAtm = window.selectAtm;
  window.selectAtm = function (id) {
    if (GP.scoreMode && GP.byId[id]) return openAtmPopup(id);
    return _selectAtm(id);
  };

  // ── Попапы с объяснением ──
  // Разбор: места спроса дают «+баллы», банкоматы других банков — «−баллы»
  function breakdownHtml(items) {
    if (!items || !items.length) return '<div class="gp-muted">Рядом нет мест скопления людей из OSM — поэтому оценка низкая.</div>';
    const plus = items.filter(b => b.points > 0);
    const minus = items.filter(b => b.points < 0);
    const maxPts = Math.max(...items.map(b => Math.abs(b.points)), 0.01);
    const row = b => {
      const neg = b.points < 0;
      const label = neg ? `${b.label}${b.count ? ` (${b.count})` : ''}` : b.label;
      return `<div class="gp-bar${neg ? ' gp-bar--neg' : ''}"><span>${esc(label)}</span>
        <i><b style="width:${Math.abs(b.points) / maxPts * 100}%;background:${neg ? '#dc2626' : (CAT_COLORS[b.factor] || '#2563eb')}"></b></i>
        <em>${neg ? '−' : '+'}${Math.abs(b.points).toFixed(2)}</em></div>`;
    };
    const pot = plus.reduce((t, b) => t + b.points, 0);
    return (plus.length ? plus.map(row).join('') : '<div class="gp-muted">Мест скопления людей рядом нет.</div>')
      + (minus.length ? `<div class="gp-bar gp-bar--sum"><span>Потенциал места</span><i></i><em>${pot.toFixed(2)}</em></div>` + minus.map(row).join('') : '');
  }
  function whyText(score, items) {
    const plus = (items || []).filter(b => b.points > 0);
    const minus = (items || []).find(b => b.points < 0);
    const med = GP.data ? GP.data.stats.median : 5;
    const rel = score >= med ? `выше медианы сети (${med.toFixed(2)})` : `ниже медианы сети (${med.toFixed(2)})`;
    if (!plus.length) return `Оценка ${score.toFixed(2)} — ${rel}: в радиусе 1,2 км нет размеченных мест скопления людей.`;
    const top = plus[0];
    const cut = minus ? ` Минус: ${minus.count} банкоматов других банков делят этот спрос (−${Math.abs(minus.points).toFixed(2)}).` : ' Конкурентов в 500 м нет.';
    return `Оценка ${score.toFixed(2)} — ${rel}. Главный плюс: ${top.label.toLowerCase()} (+${top.points.toFixed(2)}).${cut}`;
  }
  function nearestHtml(list) {
    if (!list || !list.length) return '';
    return `<div class="gp-sub">Ближайшие места</div>` + list.map(p =>
      `<div class="gp-near"><span class="gp-dot" style="background:${CAT_COLORS[p.cat]}"></span>${esc(p.name)} <em>${esc(p.label.toLowerCase())}, ${p.dist_m} м</em></div>`).join('');
  }
  function scoreHead(score, grade, extra) {
    return `<div class="gp-head"><div class="gp-badge" style="--c:${scoreColor(score)}">${score.toFixed(2)}<small>★</small></div>
      <div><div class="gp-grade" style="color:${scoreColor(score)}">${esc(grade)} локация</div><div class="gp-muted">${extra}</div></div></div>`;
  }

  function openAtmPopup(id) {
    const a = GP.byId[id];
    const m = markers[id];
    if (!a || !m) return;
    const html = `<div class="gp-pop">
      ${scoreHead(a.geo_score, a.grade, `Банкомат ${esc(id)} · ${a.rank} из ${GP.data.stats.n} · лучше ${a.percentile}% сети`)}
      <div class="gp-addr">${esc(a.address)}</div>
      <div class="gp-why">${esc(whyText(a.geo_score, a.breakdown))}</div>
      <div class="gp-sub">Из чего сложилась оценка</div>${breakdownHtml(a.breakdown)}
      ${nearestHtml(a.nearest_poi)}
      <div class="gp-kv"><span>Тип локации</span><b>${esc(a.category_label)}</b></div>
      <div class="gp-kv"><span>Банкоматов других банков в 500 м</span><b>${a.competitors_500m}</b></div>
      <div class="gp-kv"><span>Средний спрос за 30 дней</span><b>${mln(a.daily_demand_30d)}/день</b></div>
      ${a.devices_at_site > 1 ? `<div class="gp-kv"><span>Устройств на точке</span><b>${a.devices_at_site}</b></div>` : ''}
      <a class="gp-link" href="/dashboard/atm-analytics.html?atm=${encodeURIComponent(id)}">Прогноз и кассеты →</a>
    </div>`;
    m.unbindPopup();
    m.bindPopup(html, { maxWidth: 340, className: 'gp-leaflet-pop' }).openPopup();
  }
  GP.openAtm = openAtmPopup;

  function probeHtml(p, title) {
    const own = p.nearest_own_atm;
    const cmp = p.comparable || {};
    return `<div class="gp-pop">
      ${scoreHead(p.geo_score, p.grade, `${esc(title)} · лучше ${p.percentile}% банкоматов сети`)}
      <div class="gp-addr">${esc(p.district || 'За пределами районов Ташкента')} · ${p.lat.toFixed(5)}, ${p.lon.toFixed(5)}</div>
      <div class="gp-why">${esc(whyText(p.geo_score, p.breakdown))}</div>
      <div class="gp-sub">Из чего сложилась оценка</div>${breakdownHtml(p.breakdown)}
      ${nearestHtml(p.nearest_poi)}
      <div class="gp-kv"><span>Ближайший наш банкомат</span><b>${own ? `${own.dist_m} м (★ ${own.geo_score.toFixed(2)})` : '—'}</b></div>
      ${p.gap_to_own_m != null ? `<div class="gp-kv"><span>Разрыв покрытия</span><b>${p.gap_to_own_m} м</b></div>` : ''}
      <div class="gp-kv"><span>Сопоставимые банкоматы сети</span><b>${cmp.median_daily_demand ? mln(cmp.median_daily_demand) + '/день' : '—'}</b></div>
      <div class="gp-muted" style="margin-top:6px">Спрос сопоставимых — медиана ${cmp.n || 0} банкоматов сети с близкой оценкой (синтетические данные).</div>
    </div>`;
  }

  // ── Тепловая карта ──
  const heatRenderer = L.canvas({ padding: 0.3, pane: pane('gp-heat', 380) });
  async function drawHeat() {
    if (GP.layers.heat) { map.removeLayer(GP.layers.heat); GP.layers.heat = null; }
    if (!GP.heat.on) return renderLegend();
    const key = GP.heat.step;
    if (!GP.grids[key]) GP.grids[key] = await fetch(`${API_BASE}/api/geo/grid?step_m=${key}`).then(r => r.json());
    const g = GP.grids[key];
    const vals = g.layers[GP.heat.layer];
    const grp = L.layerGroup();
    const isScore = GP.heat.layer === 'score';
    for (let i = 0; i < g.n; i++) {
      const v = vals[i];
      // у слоёв-факторов значения 0–10 относительно максимума слоя → растягиваем на ту же палитру
      const hv = isScore ? v : v * 0.76;
      const color = heatColor(hv);
      // «пустые» клетки чуть прозрачнее, чтобы под тёмно-синим читалась карта
      const op = GP.heat.opacity * (0.55 + 0.45 * Math.min(hv, 6) / 6);
      L.rectangle([[g.lat[i] - g.dlat / 2, g.lon[i] - g.dlon / 2], [g.lat[i] + g.dlat / 2, g.lon[i] + g.dlon / 2]], {
        renderer: heatRenderer, stroke: false, fillColor: color, fillOpacity: op, interactive: false,
      }).addTo(grp);
    }
    GP.layers.heat = grp.addTo(map);
    renderLegend();
  }

  // ── Слои POI ──
  async function drawPois() {
    if (GP.layers.poi) { map.removeLayer(GP.layers.poi); GP.layers.poi = null; }
    if (!Object.values(GP.poi).some(Boolean)) return;
    if (!GP.pois) GP.pois = (await fetch(`${API_BASE}/api/geo/pois`).then(r => r.json())).items;
    const grp = L.layerGroup();
    GP.pois.filter(p => GP.poi[p.cat]).forEach(p => {
      L.circleMarker([p.lat, p.lon], {
        pane: pane('gp-poi', 610), radius: p.cat === 'supermarket' ? 4 : 6, color: '#fff', weight: 1.5,
        fillColor: CAT_COLORS[p.cat], fillOpacity: 0.95,
      }).bindTooltip(`${esc(p.name || CAT_LABELS[p.cat])} · ${CAT_LABELS[p.cat]}`).addTo(grp);
    });
    GP.layers.poi = grp.addTo(map);
  }

  // ── Зоны влияния 300 м ──
  function drawZones() {
    if (GP.layers.zones) { map.removeLayer(GP.layers.zones); GP.layers.zones = null; }
    if (!GP.zones || !GP.data) return;
    const grp = L.layerGroup();
    GP.data.items.filter(a => passesFilter(a.atm_id)).forEach(a => {
      const c = scoreColor(a.geo_score);
      L.circle([a.lat, a.lon], { pane: pane('gp-zones', 390), radius: 300, color: c, weight: 1, fillColor: c, fillOpacity: 0.12, interactive: false }).addTo(grp);
    });
    GP.layers.zones = grp.addTo(map);
  }

  // ── Белые пятна ──
  async function drawOpps() {
    if (GP.layers.opps) { map.removeLayer(GP.layers.opps); GP.layers.opps = null; }
    const list = document.getElementById('gp-opps-list');
    if (!GP.opps.on) { if (list) list.innerHTML = ''; return; }
    const d = await fetch(`${API_BASE}/api/geo/opportunities?min_gap_m=${GP.opps.gap}&top=12`).then(r => r.json());
    const grp = L.layerGroup();
    d.items.forEach((p, i) => {
      const icon = L.divIcon({
        className: 'atm-marker-icon',
        html: `<div class="gp-opp" style="--c:${scoreColor(p.geo_score)}"><span>+</span><b>${p.geo_score.toFixed(2)}</b></div>`,
        iconSize: [46, 46], iconAnchor: [23, 23], popupAnchor: [0, -20],
      });
      L.marker([p.lat, p.lon], { icon, pane: pane('gp-opps', 640) })
        .bindPopup(probeHtml(p, `Белое пятно №${i + 1}`), { maxWidth: 340, className: 'gp-leaflet-pop' }).addTo(grp);
    });
    GP.layers.opps = grp.addTo(map);
    if (list) {
      list.innerHTML = d.items.map((p, i) => `<button class="gp-row" data-lat="${p.lat}" data-lon="${p.lon}">
        <span class="gp-chipnum" style="--c:${scoreColor(p.geo_score)}">${p.geo_score.toFixed(2)}</span>
        <span>№${i + 1} · ${esc(p.district || '—')}<em>до нашего банкомата ${p.gap_to_own_m} м</em></span></button>`).join('')
        || '<div class="gp-muted">С такими условиями белых пятен нет.</div>';
    }
  }

  // ── Оценка точки кликом ──
  let probeMarker = null;
  async function onMapClick(e) {
    if (!GP.probe) return;
    const { lat, lng } = e.latlng;
    const p = await fetch(`${API_BASE}/api/geo/probe?lat=${lat.toFixed(6)}&lon=${lng.toFixed(6)}`).then(r => r.json());
    if (probeMarker) map.removeLayer(probeMarker);
    probeMarker = L.marker([lat, lng], {
      pane: pane('gp-opps', 640),
      icon: L.divIcon({ className: 'atm-marker-icon', html: `<div class="gp-probe" style="--c:${scoreColor(p.geo_score)}">${p.geo_score.toFixed(2)}</div>`, iconSize: [40, 40], iconAnchor: [20, 20], popupAnchor: [0, -18] }),
    }).addTo(map).bindPopup(probeHtml(p, 'Выбранная точка'), { maxWidth: 340, className: 'gp-leaflet-pop' }).openPopup();
  }
  map.on('click', onMapClick);

  // ── Легенда на карте ──
  function renderLegend() {
    let el = document.getElementById('gp-legend');
    const need = GP.scoreMode || GP.heat.on;
    if (!need) { if (el) el.remove(); return; }
    if (!el) {
      el = document.createElement('div');
      el.id = 'gp-legend';
      document.querySelector('.map-area').appendChild(el);
    }
    const blocks = [];
    if (GP.heat.on) {
      const isScore = GP.heat.layer === 'score';
      const grad = Array.from({ length: 21 }, (_, i) => heatColor(i * 0.4)).join(',');
      blocks.push(`<div class="gp-legend__t">Тепловая карта · ${CAT_LABELS[GP.heat.layer]}</div>
        <div class="gp-legend__bar" style="background:linear-gradient(90deg,${grad})"></div>
        <div class="gp-legend__s">${isScore ? '<span>0</span><span>2</span><span>4</span><span>6</span><span>8</span>' : ''}</div>
        <div class="gp-legend__s gp-legend__g"><span>мало спроса</span><span>много спроса</span></div>`);
    }
    if (GP.scoreMode) {
      const grad = Array.from({ length: 11 }, (_, i) => scoreColor(i)).join(',');
      blocks.push(`<div class="gp-legend__t">Оценка банкомата ★</div><div class="gp-legend__bar" style="background:linear-gradient(90deg,${grad})"></div>
         <div class="gp-legend__s"><span>0</span><span>2.5</span><span>4</span><span>5.5</span><span>7</span><span>10</span></div>
         <div class="gp-legend__s gp-legend__g"><span>плохо</span><span>средне</span><span>хорошо</span></div>`);
    }
    el.innerHTML = blocks.join('<div class="gp-legend__sep"></div>');
  }

  function updateCounter() {
    const el = document.getElementById('gp-count');
    if (!el || !GP.data) return;
    const shown = GP.data.items.filter(a => passesFilter(a.atm_id)).length;
    el.textContent = `Показано ${shown} из ${GP.data.items.length} банкоматов`;
  }

  // ── Окно настроек ──
  function histogramHtml() {
    const s = GP.data.stats;
    const max = Math.max(...Object.values(s.by_grade), 1);
    return GRADE_ORDER.map(g => `<button class="gp-hist${GP.filter.grades.has(g) ? ' on' : ''}" data-grade="${g}">
      <span class="gp-hist__l">${g}</span><i><b style="width:${s.by_grade[g] / max * 100}%;background:${scoreColor(GRADE_SAMPLE[g])}"></b></i><em>${s.by_grade[g]}</em></button>`).join('');
  }
  function topHtml(list) {
    return list.map(a => `<button class="gp-row" data-lat="${a.lat}" data-lon="${a.lon}" data-atm="${a.atm_id}">
      <span class="gp-chipnum" style="--c:${scoreColor(a.geo_score)}">${a.geo_score.toFixed(2)}</span>
      <span>${esc(a.address)}<em>${esc((() => { const t = a.breakdown.find(b => b.points > 0); const c = a.breakdown.find(b => b.points < 0); return (t ? 'плюс: ' + t.label.toLowerCase() : 'рядом нет мест скопления') + (c ? ` · конкурентов ${c.count}` : ''); })())}</em></span></button>`).join('');
  }

  // ── Элементы окна (только разметка; поведение — в bindModal) ──
  const ico = (name, color) => `<span class="gp-ico" style="--ic:${color}"><i data-lucide="${name}"></i></span>`;
  function secHead(icon, color, title, hint) {
    return `<div class="gp-sec__h">${ico(icon, color)}<div><h4>${title}</h4><p>${hint}</p></div></div>`;
  }
  function toggleCard(id, icon, color, title, hint, checked) {
    return `<label class="gp-toggle" for="${id}">${ico(icon, color)}
      <span class="gp-toggle__txt"><b>${title}</b><em>${hint}</em></span>
      <input type="checkbox" id="${id}" ${checked ? 'checked' : ''}><span class="gp-switch" aria-hidden="true"></span></label>`;
  }
  const POI_ICONS = { metro: 'train-front', market: 'store', mall: 'shopping-bag', supermarket: 'shopping-cart' };

  function buildModal() {
    const s = GP.data.stats;
    const sorted = [...GP.data.items].sort((a, b) => b.geo_score - a.geo_score);
    const f = GP.data.formula;
    const heatGrad = Array.from({ length: 21 }, (_, i) => heatColor(i * 0.4)).join(',');
    const statusPreview = `<span class="gp-prev gp-prev--status"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.6" stroke-linecap="round"><rect width="20" height="14" x="2" y="5" rx="2"/><line x1="2" x2="22" y1="10" y2="10"/></svg></span>`;
    const scorePreview = `<span class="gp-prev"><span class="gp-pin" style="--c:${scoreColor(6.12)}"><span class="gp-pin__val">6.12</span><span class="gp-pin__star">★</span></span></span>`;
    const m = document.createElement('div');
    m.id = 'gp-modal';
    m.className = 'gp-overlay';
    m.innerHTML = `<div class="gp-modal" role="dialog" aria-label="Гео-скоринг локаций">
      <div class="gp-modal__head">
        <div class="gp-head__l">${ico('map-pinned', '#2563eb')}
          <div><h3>Гео-скоринг локаций</h3>
          <div class="gp-muted">Насколько место банкомата «денежное»: метро, рынки, ТЦ и супермаркеты (OSM) повышают оценку 0–10, банкоматы других банков рядом (ЦБ РУз) — слегка снижают.</div>
          <div class="gp-stats">
            <span><i data-lucide="credit-card"></i>${s.n} банкоматов</span>
            <span><i data-lucide="gauge"></i>медиана ${s.median.toFixed(2)}</span>
            <span><i data-lucide="trophy"></i>лучший ${s.max.toFixed(2)}</span>
          </div></div></div>
        <button class="gp-x" id="gp-close" aria-label="Закрыть"><i data-lucide="x"></i></button></div>
      <div class="gp-modal__body">

        <section>${secHead('eye', '#2563eb', 'Что показывают иконки банкоматов', 'Переключите, чтобы вместо статуса инкассации увидеть оценку места')}
          <div class="gp-seg gp-seg--cards" id="gp-mode">
            <button data-v="status" class="${GP.scoreMode ? '' : 'on'}">${statusPreview}<span><b>Статус ML</b><em>когда нужен выезд инкассации</em></span></button>
            <button data-v="score" class="${GP.scoreMode ? 'on' : ''}">${scorePreview}<span><b>★ Оценка локации</b><em>число 0–10, цвет — качество места</em></span></button>
          </div>
          ${toggleCard('gp-zones', 'circle-dashed', '#0ea5e9', 'Зоны влияния 300 м', 'Круг вокруг банкомата — где места дают основной вклад в оценку', GP.zones)}
        </section>

        <section>${secHead('flame', '#ef4444', 'Тепловая карта гео-спроса', 'Где в Ташкенте люди и деньги — даже там, где нашего банкомата нет')}
          ${toggleCard('gp-heat', 'layers', '#ef4444', 'Показать тепловую карту', 'Город раскрасится клетками: синий — мало спроса, красный — много', GP.heat.on)}
          <div class="gp-heatbar"><div style="background:linear-gradient(90deg,${heatGrad})"></div><span>мало спроса</span><span>много спроса</span></div>
          <div class="gp-grid2">
            <label class="gp-field"><span><i data-lucide="filter"></i>Что раскрасить</span><select id="gp-heat-layer">${Object.entries(CAT_LABELS).map(([k, v]) => `<option value="${k}" ${k === GP.heat.layer ? 'selected' : ''}>${v}</option>`).join('')}</select></label>
            <label class="gp-field"><span><i data-lucide="grid-3x3"></i>Размер клетки</span><select id="gp-heat-step">${[200, 250, 400, 600].map(v => `<option value="${v}" ${v === GP.heat.step ? 'selected' : ''}>${v} м</option>`).join('')}</select></label>
          </div>
          <label class="gp-field gp-range"><span><i data-lucide="contrast"></i>Насыщенность цвета</span><input type="range" id="gp-heat-op" min="0.15" max="0.85" step="0.05" value="${GP.heat.opacity}"></label>
        </section>

        <section>${secHead('sliders-horizontal', '#f59e0b', 'Фильтр по оценке', 'Оставьте на карте только банкоматы с нужной оценкой')}
          <div class="gp-grid2">
            <label class="gp-field gp-range"><span><i data-lucide="arrow-down-to-line"></i>От <b class="gp-val" id="gp-min-v">${GP.filter.min.toFixed(1)}</b></span><input type="range" id="gp-min" min="0" max="10" step="0.1" value="${GP.filter.min}"></label>
            <label class="gp-field gp-range"><span><i data-lucide="arrow-up-to-line"></i>До <b class="gp-val" id="gp-max-v">${GP.filter.max.toFixed(1)}</b></span><input type="range" id="gp-max" min="0" max="10" step="0.1" value="${GP.filter.max}"></label>
          </div>
          <div class="gp-sub"><i data-lucide="mouse-pointer-click"></i>Нажмите на градацию — останутся только эти банкоматы</div>
          <div id="gp-hist">${histogramHtml()}</div>
          ${toggleCard('gp-weak', 'triangle-alert', '#dc2626', 'Только слабые точки (оценка < 2.5)', 'Кандидаты на перенос — подсветятся пульсирующей обводкой', GP.weak)}
          <div class="gp-foot"><span><i data-lucide="eye"></i><span id="gp-count"></span></span><button class="gp-btn-ghost" id="gp-reset"><i data-lucide="rotate-ccw"></i>Сбросить фильтр</button></div>
        </section>

        <section>${secHead('layers-3', '#7c3aed', 'Слои на карте', 'Что ещё нарисовать поверх банкоматов')}
          ${toggleCard('gp-comp', 'landmark', '#8b5cf6', 'Банкоматы других банков', 'Фиолетовые иконки в 50 м от наших — они делят спрос и снижают оценку', GP.competitors)}
          <div class="gp-sub"><i data-lucide="users"></i>Места скопления людей (OSM) — из них складывается оценка</div>
          <div class="gp-chips">${['metro', 'market', 'mall', 'supermarket'].map(k => `<label class="gp-chip" style="--cc:${CAT_COLORS[k]}"><input type="checkbox" data-poi="${k}" ${GP.poi[k] ? 'checked' : ''}><i data-lucide="${POI_ICONS[k]}"></i>${CAT_LABELS[k]}</label>`).join('')}</div>
        </section>

        <section>${secHead('lightbulb', '#16a34a', 'Где открыть банкомат', 'Подсказки для развития сети')}
          ${toggleCard('gp-opps', 'map-pin-plus', '#16a34a', '«Белые пятна»', 'Сильные места без нашего банкомата рядом — ромбы «+» с оценкой', GP.opps.on)}
          <label class="gp-field"><span><i data-lucide="ruler"></i>Минимальное расстояние до нашего банкомата</span><select id="gp-gap">${[500, 700, 1000, 1500].map(v => `<option value="${v}" ${v === GP.opps.gap ? 'selected' : ''}>${v} м</option>`).join('')}</select></label>
          <div id="gp-opps-list" class="gp-list"></div>
          ${toggleCard('gp-probe', 'crosshair', '#2563eb', 'Оценить любую точку', 'Включите и кликните по карте — получите оценку места и почему', GP.probe)}
        </section>

        <section>${secHead('award', '#0891b2', 'Лучшие и худшие локации сети', 'Нажмите — карта перелетит к банкомату и покажет разбор')}
          <div class="gp-grid2 gp-top">
            <div><div class="gp-sub gp-sub--good"><i data-lucide="trophy"></i>Топ-5</div>${topHtml(sorted.slice(0, 5))}</div>
            <div><div class="gp-sub gp-sub--bad"><i data-lucide="trending-down"></i>Худшие 5</div>${topHtml(sorted.slice(-5).reverse())}</div>
          </div>
        </section>

        <section>${secHead('sigma', '#64748b', 'Как считается оценка', 'Прозрачная формула — её можно проверить')}
          <div class="gp-formula">потенциал = 10 · (1 − e<sup>−raw/${f.scale}</sup>), raw = Σ w · e<sup>−d/${f.decay_m} м</sup> по местам в ${f.max_radius_m} м<br>
          оценка = потенциал − ${f.competitor_penalty} · ln(1 + банкоматов других банков в ${f.competitor_radius_m} м), не ниже 0</div>
          <div class="gp-weights">${Object.entries(f.weights).map(([k, v]) => `<span style="--cc:${CAT_COLORS[k]}"><i data-lucide="${POI_ICONS[k]}"></i>${CAT_LABELS[k]} <b>+${v}</b></span>`).join('')}
            <span style="--cc:#dc2626"><i data-lucide="landmark"></i>Банкоматы других банков <b>−${f.competitor_penalty}·ln(1+n)</b></span></div>
          <div class="gp-muted">Места спроса повышают оценку, банкоматы других банков слегка снижают: они делят тот же спрос (5 рядом ≈ −0.54, 20 ≈ −0.91, 40 ≈ −1.11). Веса — допущение команды; на пилоте калибруются по реальным снятиям.</div>
        </section>
      </div></div>`;
    document.body.appendChild(m);
    bindModal(m);
    updateCounter();
    if (window.lucide) lucide.createIcons();
    if (GP.opps.on) drawOpps();
  }

  function bindModal(m) {
    const $ = id => m.querySelector('#' + id);
    const close = () => { m.style.display = 'none'; };
    $('gp-close').onclick = close;
    m.addEventListener('click', e => { if (e.target === m) close(); });
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && m.style.display !== 'none') close(); });
    $('gp-mode').onclick = e => {
      const b = e.target.closest('button'); if (!b) return;
      GP.scoreMode = b.dataset.v === 'score';
      $('gp-mode').querySelectorAll('button').forEach(x => x.classList.toggle('on', x === b));
      map.closePopup();
      applyMarkers();
      syncGearBtn();
    };
    $('gp-zones').onchange = e => { GP.zones = e.target.checked; drawZones(); };
    $('gp-heat').onchange = e => { GP.heat.on = e.target.checked; drawHeat(); syncGearBtn(); };
    $('gp-heat-layer').onchange = e => { GP.heat.layer = e.target.value; if (!GP.heat.on) { GP.heat.on = true; $('gp-heat').checked = true; } drawHeat(); syncGearBtn(); };
    $('gp-heat-step').onchange = e => { GP.heat.step = +e.target.value; if (GP.heat.on) drawHeat(); };
    $('gp-heat-op').oninput = e => { GP.heat.opacity = +e.target.value; if (GP.heat.on) drawHeat(); };
    const syncRange = () => {
      let lo = +$('gp-min').value, hi = +$('gp-max').value;
      if (lo > hi) [lo, hi] = [hi, lo];
      GP.filter.min = lo; GP.filter.max = hi;
      $('gp-min-v').textContent = lo.toFixed(1); $('gp-max-v').textContent = hi.toFixed(1);
      applyMarkers(); syncGearBtn();
    };
    $('gp-min').oninput = syncRange;
    $('gp-max').oninput = syncRange;
    $('gp-hist').onclick = e => {
      const b = e.target.closest('[data-grade]'); if (!b) return;
      const g = b.dataset.grade;
      GP.filter.grades.has(g) ? GP.filter.grades.delete(g) : GP.filter.grades.add(g);
      b.classList.toggle('on');
      applyMarkers(); syncGearBtn();
    };
    $('gp-weak').onchange = e => { GP.weak = e.target.checked; if (GP.weak && !GP.scoreMode) { GP.scoreMode = true; m.querySelectorAll('#gp-mode button').forEach(x => x.classList.toggle('on', x.dataset.v === 'score')); } applyMarkers(); syncGearBtn(); };
    $('gp-reset').onclick = () => {
      GP.filter = { min: 0, max: 10, grades: new Set() }; GP.weak = false;
      $('gp-min').value = 0; $('gp-max').value = 10; $('gp-weak').checked = false;
      $('gp-min-v').textContent = '0.0'; $('gp-max-v').textContent = '10.0';
      m.querySelectorAll('.gp-hist').forEach(x => x.classList.remove('on'));
      applyMarkers(); syncGearBtn();
    };
    $('gp-comp').onchange = e => { GP.competitors = e.target.checked; syncCompetitors(); };
    m.querySelectorAll('[data-poi]').forEach(cb => cb.onchange = () => { GP.poi[cb.dataset.poi] = cb.checked; drawPois(); });
    $('gp-opps').onchange = e => { GP.opps.on = e.target.checked; drawOpps(); };
    $('gp-gap').onchange = e => { GP.opps.gap = +e.target.value; if (GP.opps.on) drawOpps(); };
    $('gp-probe').onchange = e => { GP.probe = e.target.checked; map.getContainer().classList.toggle('gp-probing', GP.probe); if (!GP.probe && probeMarker) { map.removeLayer(probeMarker); probeMarker = null; } };
    m.addEventListener('click', e => {
      const r = e.target.closest('.gp-row'); if (!r) return;
      close();
      map.flyTo([+r.dataset.lat, +r.dataset.lon], 16, { duration: 0.8 });
      if (r.dataset.atm) setTimeout(() => { if (!GP.scoreMode) { GP.scoreMode = true; applyMarkers(); syncGearBtn(); } openAtmPopup(r.dataset.atm); }, 900);
    });
  }

  function syncCompetitors() {
    if (typeof competitorLayer === 'undefined' || !competitorLayer) return;
    if (GP.competitors && !map.hasLayer(competitorLayer)) competitorLayer.addTo(map);
    if (!GP.competitors && map.hasLayer(competitorLayer)) map.removeLayer(competitorLayer);
  }

  function syncGearBtn() {
    const b = document.getElementById('gp-gear');
    if (!b) return;
    const active = GP.scoreMode || GP.heat.on || filterActive();
    b.classList.toggle('gp-gear--on', active);
  }

  async function openModal() {
    if (!GP.data) await loadData();
    let m = document.getElementById('gp-modal');
    if (!m) { buildModal(); m = document.getElementById('gp-modal'); }
    m.style.display = 'flex';
    updateCounter();
  }

  async function loadData() {
    GP.data = await fetch(`${API_BASE}/api/geo/atms`).then(r => r.json());
    GP.byId = Object.fromEntries(GP.data.items.map(a => [a.atm_id, a]));
  }

  // ── Кнопка-шестерёнка ──
  function addGear() {
    const area = document.querySelector('.map-area');
    if (!area || document.getElementById('gp-gear')) return;
    const b = document.createElement('button');
    b.id = 'gp-gear';
    b.className = 'map-floating-btn';
    b.title = 'Гео-скоринг: тепловая карта, оценки точек, фильтры';
    b.style.top = '206px';
    b.innerHTML = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/></svg>`;
    b.onclick = openModal;
    area.appendChild(b);
  }

  addGear();
  loadData().then(applyMarkers).catch(e => console.warn('Гео-скоринг не загружен', e));
  // Открыть сразу: /dashboard/map.html?geo=1 (режим оценки), &heat=1 (тепловая карта)
  const qp = new URLSearchParams(location.search);
  // Демо-ссылки: ?geo=1 (оценки) &heat=1 (тепловая) &opps=1 (белые пятна) &panel=1 (окно)
  //              &atm=<ID> (разбор банкомата) &probe=<lat>,<lon> (оценка точки)
  if (['geo', 'heat', 'opps', 'probe', 'panel', 'atm'].some(k => qp.has(k))) {
    loadData().then(async () => {
      GP.scoreMode = qp.get('geo') === '1' || qp.has('atm');
      GP.heat.on = qp.get('heat') === '1';
      GP.opps.on = qp.get('opps') === '1';
      applyMarkers(); syncGearBtn();
      await drawHeat();
      if (GP.opps.on) await drawOpps();
      if (qp.has('probe')) {
        const [la, lo] = qp.get('probe').split(',').map(Number);
        GP.probe = true;
        map.setView([la, lo], 15);
        await onMapClick({ latlng: { lat: la, lng: lo } });
      }
      if (qp.has('atm') && markers[qp.get('atm')]) {
        const a = GP.byId[qp.get('atm')];
        map.setView([a.lat, a.lon], 15);
        setTimeout(() => openAtmPopup(qp.get('atm')), 600);
      }
      if (qp.get('panel') === '1') openModal();
    });
  }
})();
