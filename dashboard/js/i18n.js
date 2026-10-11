// ═══════════════════════════════════════════════════════════
// Переключатель языка RU / EN (как на iPhone) и перевод интерфейса.
// Переводит текст страниц, подсказки и всё, что JS дорисовывает позже
// (попапы карты, окна, таблицы) — по словарю фраз, длинные фразы первыми.
// Оригинальный русский текст сохраняется у узла и возвращается при RU.
// Подключается последним скриптом на каждой странице dashboard/*.html.
// ═══════════════════════════════════════════════════════════
(function () {
  const KEY = 'cf_lang';
  const PAIRS = [
    // ── Навигация и шапки ──
    ['Главная', 'Home'], ['Карта', 'Map'], ['Мониторинг', 'Monitoring'], ['Аналитика', 'Analytics'],
    ['Инкассация', 'Cash collection'], ['Импорт', 'Import'], ['Разделы', 'Sections'],
    ['Карта банкоматов и маршруты', 'ATM map and routes'], ['Мониторинг и прогноз', 'Monitoring & forecast'],
    ['Аналитика ATM', 'ATM analytics'], ['Календарь инкассации', 'Cash collection calendar'],
    ['Карта банкоматов — Cash Flow AI', 'ATM map — Cash Flow AI'],
    ['Мониторинг и прогноз — Cash Flow AI', 'Monitoring & forecast — Cash Flow AI'],
    ['Аналитика ATM — Cash Flow AI', 'ATM analytics — Cash Flow AI'],
    ['План инкассации — Cash Flow AI', 'Cash collection plan — Cash Flow AI'],
    ['Ipak Yo\'li Bank · Ташкент', 'Ipak Yo\'li Bank · Tashkent'],
    ['Обновлено', 'Updated'], ['Данные на', 'Data as of'], ['синтетика', 'synthetic'],

    // ── Главная ──
    ['Прогноз спроса на наличные, мониторинг сети банкоматов и планирование инкассации', 'Cash demand forecasting, ATM network monitoring and cash collection planning'],
    ['модель подсказывает, когда выехать, сколько загрузить и по какому маршруту.', 'the model tells you when to go, how much to load and which route to take.'],
    ['Открыть карту', 'Open map'], ['Открыть мониторинг', 'Open monitoring'], ['Открыть календарь', 'Open calendar'],
    ['Открыть аналитику', 'Open analytics'], ['Сеть банкоматов и инкассация', 'ATM network and cash collection'],
    ['Карта банкоматов', 'ATM map'], ['План инкассации', 'Cash collection plan'],
    ['Сеть на карте с остатками, статусами cash-out и построением маршрутов инкассации.', 'The network on a map with balances, cash-out statuses and cash collection routing.'],
    ['Кассеты, остаток и транзакции каждого банкомата; прогноз XGBoost — когда закончатся деньги и сколько загрузить.', 'Cassettes, balance and transactions of every ATM; XGBoost forecast — when cash runs out and how much to load.'],
    ['Календарь выездов и региональные маршруты от филиалов с признаком «Инкассация = 1».', 'Trip calendar and regional routes from branches flagged «Cash collection = 1».'],
    ['Спрос, денежный поток, история транзакций и инсайты по выбранному периоду.', 'Demand, cash flow, transaction history and insights for the selected period.'],

    // ── Карта: KPI и панели ──
    ['Всего ATM', 'Total ATMs'], ['Критично', 'Critical'], ['Предупреждение', 'Warning'], ['В норме', 'OK'],
    ['Выезды сегодня', 'Trips today'], ['ML · Высокий риск', 'ML · High risk'], ['ML · Средний риск', 'ML · Medium risk'],
    ['Зарплатный день', 'Payday'], ['Кассеты нужны', 'Cassettes needed'], ['Сегодня', 'Today'], ['Завтра', 'Tomorrow'],
    ['Банкоматы', 'ATMs'], ['Поиск по региону...', 'Search by region...'], ['Загрузка банкоматов...', 'Loading ATMs...'],
    ['Загрузка данных банкоматов...', 'Loading ATM data...'], ['Маршрут инкассации', 'Cash collection route'],
    ['Макс. ATM / рейс', 'Max ATMs / trip'], ['Скорость', 'Speed'], ['Только критичные', 'Critical only'],
    ['Критичные + предупреждения', 'Critical + warnings'], ['Календарь маршрутов', 'Route calendar'],
    ['Очистить маршрут', 'Clear route'], ['точек', 'stops'], ['км/ч', 'km/h'],
    ['Выбор региона', 'Select region'], ['Выберите регион', 'Select a region'], ['Все регионы', 'All regions'],
    ['Показать/скрыть банкоматы', 'Show/hide ATMs'], ['Показать/скрыть филиалы', 'Show/hide branches'],
    ['Гео-скоринг: тепловая карта, оценки точек, фильтры', 'Geo-scoring: heatmap, location scores, filters'],
    ['Сейчас', 'Now'], ['Прогноз площадки', 'Platform forecast'], ['до cash-out', 'to cash-out'],
    ['заполнен', 'full'], ['Посл. инк.', 'Last coll.'], ['Посл. инкассация', 'Last collection'],
    ['Кассеты (реальные, atm_monitor)', 'Cassettes (live, atm_monitor)'], ['Кассеты (оценка от остатка)', 'Cassettes (estimated from balance)'],
    ['Нужно довезти', 'Need to deliver'], ['Довозить не нужно', 'No delivery needed'],
    ['Деньги закончатся', 'Cash runs out'], ['выезд возможен', 'trip possible'], ['Кассета', 'Cassette'],
    ['опустеет', 'empties'], ['Остаток ниже 20%', 'Balance below 20%'], ['Остаток ниже 40% (прогноза нет)', 'Balance below 40% (no forecast)'],
    ['По ожидаемому спросу хватит, но с запасом на пиковый спрос', 'Enough for expected demand, but with a buffer for peak demand'],
    ['нужен выезд сегодня', 'trip needed today'], ['в ближайшие сутки', 'within 24 hours'],
    ['Найден по координатам (id в реестре не совпал с системой мониторинга)', 'Matched by coordinates (registry id differs from monitoring system)'],
    ['Пункт инкассации', 'Collection point'], ['Депо (Amir Temur 107)', 'Depot (Amir Temur 107)'],
    ['Машина A', 'Vehicle A'], ['Машина B', 'Vehicle B'], ['Машина C', 'Vehicle C'], ['Машина D', 'Vehicle D'],
    ['Строим маршруты по дорогам только для ATM ниже нормы…', 'Building road routes for below-threshold ATMs only…'],
    ['Строим маршруты по текущим остаткам…', 'Building routes from current balances…'],
    ['Банкоматы других банков в маршрут не входят и на время маршрута скрыты.', 'Other banks’ ATMs are not part of the route and are hidden while it is shown.'],
    ['Ошибка построения регионального маршрута', 'Failed to build regional route'],
    ['Время в пути', 'Travel time'], ['Расстояние', 'Distance'], ['Остановок', 'Stops'], ['остановок', 'stops'],
    ['Возврат в депо', 'Return to depot'], ['Маршруты инкассации', 'Cash collection routes'],
    ['Сохранено рейсов', 'Trips saved'], ['сохранено рейсов', 'trips saved'], ['Открыть в календаре →', 'Open in calendar →'],
    ['Список банкоматов в регионе', 'ATMs in region'], ['Выбрать все', 'Select all'], ['Снять все', 'Clear all'], ['Найти', 'Find'],
    ['Выбрано банкоматов', 'ATMs selected'], ['Отмена', 'Cancel'], ['Закрыть', 'Close'],

    // ── Гео-скоринг (окно шестерёнки и попапы) ──
    ['Гео-скоринг локаций', 'Location geo-scoring'],
    ['Насколько место банкомата «денежное»: метро, рынки, ТЦ и супермаркеты (OSM) повышают оценку 0–10, банкоматы других банков рядом (ЦБ РУз) — слегка снижают.', 'How “cash-rich” an ATM location is: metro, markets, malls and supermarkets (OSM) raise the 0–10 score; other banks’ ATMs nearby (CBU) slightly lower it.'],
    ['банкоматов', 'ATMs'], ['медиана', 'median'], ['лучший', 'best'],
    ['Что показывают иконки банкоматов', 'What ATM icons show'],
    ['Переключите, чтобы вместо статуса инкассации увидеть оценку места', 'Switch to see the location score instead of the collection status'],
    ['Статус ML', 'ML status'], ['когда нужен выезд инкассации', 'when a collection trip is needed'],
    ['★ Оценка локации', '★ Location score'], ['число 0–10, цвет — качество места', 'a 0–10 number, color = location quality'],
    ['Зоны влияния 300 м', '300 m influence zones'],
    ['Круг вокруг банкомата — где места дают основной вклад в оценку', 'Circle around the ATM where places contribute most to the score'],
    ['Тепловая карта гео-спроса', 'Geo-demand heatmap'],
    ['Где в Ташкенте люди и деньги — даже там, где нашего банкомата нет', 'Where people and cash are in Tashkent — even where we have no ATM'],
    ['Показать тепловую карту', 'Show heatmap'],
    ['Город раскрасится клетками: синий — мало спроса, красный — много', 'The city is colored in cells: blue — low demand, red — high'],
    ['мало спроса', 'low demand'], ['много спроса', 'high demand'], ['Что раскрасить', 'Layer'], ['Размер клетки', 'Cell size'],
    ['Насыщенность цвета', 'Color intensity'], ['Итоговый гео-скоринг', 'Total geo-score'],
    ['Банкоматы других банков (снижают оценку)', 'Other banks’ ATMs (lower the score)'],
    ['Рынки и базары', 'Markets & bazaars'], ['ТЦ и моллы', 'Malls'], ['Супермаркеты', 'Supermarkets'],
    ['Фильтр по оценке', 'Filter by score'], ['Оставьте на карте только банкоматы с нужной оценкой', 'Keep only ATMs with the score you need'],
    ['Нажмите на градацию — останутся только эти банкоматы', 'Click a grade to keep only those ATMs'],
    ['Только слабые точки (оценка < 2.5)', 'Weak locations only (score < 2.5)'],
    ['Кандидаты на перенос — подсветятся пульсирующей обводкой', 'Relocation candidates — highlighted with a pulsing ring'],
    ['Сбросить фильтр', 'Reset filter'], ['Показано', 'Showing'],
    ['Слои на карте', 'Map layers'], ['Что ещё нарисовать поверх банкоматов', 'What else to draw on top of ATMs'],
    ['Фиолетовые иконки в 50 м от наших — они делят спрос и снижают оценку', 'Purple icons within 50 m of ours — they share demand and lower the score'],
    ['Места скопления людей (OSM) — из них складывается оценка', 'Crowded places (OSM) — the score is built from them'],
    ['Где открыть банкомат', 'Where to open an ATM'], ['Подсказки для развития сети', 'Network expansion hints'],
    ['«Белые пятна»', '“White spots”'], ['Сильные места без нашего банкомата рядом — ромбы «+» с оценкой', 'Strong places with no ATM of ours nearby — “+” diamonds with a score'],
    ['Минимальное расстояние до нашего банкомата', 'Minimum distance to our ATM'],
    ['Оценить любую точку', 'Score any location'], ['Включите и кликните по карте — получите оценку места и почему', 'Turn on and click the map to get the location score and why'],
    ['Лучшие и худшие локации сети', 'Best and worst locations in the network'],
    ['Нажмите — карта перелетит к банкомату и покажет разбор', 'Click — the map flies to the ATM and shows the breakdown'],
    ['Топ-5', 'Top 5'], ['Худшие 5', 'Worst 5'], ['Как считается оценка', 'How the score is calculated'],
    ['Прозрачная формула — её можно проверить', 'A transparent formula you can check'],
    ['Места спроса повышают оценку, банкоматы других банков слегка снижают: они делят тот же спрос (5 рядом ≈ −0.54, 20 ≈ −0.91, 40 ≈ −1.11). Веса — допущение команды; на пилоте калибруются по реальным снятиям.', 'Demand places raise the score, other banks’ ATMs slightly lower it: they share the same demand (5 nearby ≈ −0.54, 20 ≈ −0.91, 40 ≈ −1.11). Weights are a team assumption; calibrated on real withdrawals during the pilot.'],
    ['потенциал', 'potential'], ['оценка', 'score'], ['по местам в', 'over places within'], ['банкоматов других банков в', 'other banks’ ATMs within'], ['не ниже 0', 'not below 0'],
    ['Оценка локации ★', 'Location score ★'], ['Оценка банкомата ★', 'ATM score ★'], ['Тепловая карта', 'Heatmap'],
    ['плохо', 'poor'], ['средне', 'average'], ['хорошо', 'good'],
    ['Отличная локация', 'Excellent location'], ['Хорошая локация', 'Good location'], ['Средняя локация', 'Average location'],
    ['Слабая локация', 'Weak location'], ['Плохая локация', 'Poor location'],
    ['Отличная', 'Excellent'], ['Хорошая', 'Good'], ['Средняя', 'Average'], ['Слабая', 'Weak'], ['Плохая', 'Poor'],
    ['Из чего сложилась оценка', 'What the score is made of'], ['Потенциал места', 'Location potential'],
    ['Ближайшие места', 'Nearest places'], ['Тип локации', 'Location type'],
    ['Банкоматов других банков в 500 м', 'Other banks’ ATMs within 500 m'], ['Средний спрос за 30 дней', 'Average demand, last 30 days'],
    ['Устройств на точке', 'Devices at site'], ['Прогноз и кассеты →', 'Forecast & cassettes →'],
    ['Ближайший наш банкомат', 'Our nearest ATM'], ['Разрыв покрытия', 'Coverage gap'],
    ['Сопоставимые банкоматы сети', 'Comparable network ATMs'], ['Спрос сопоставимых — медиана', 'Comparable demand — median of'],
    ['банкоматов сети с близкой оценкой (синтетические данные).', 'network ATMs with a similar score (synthetic data).'],
    ['Выбранная точка', 'Selected point'], ['Белое пятно №', 'White spot #'], ['За пределами районов Ташкента', 'Outside Tashkent districts'],
    ['выше медианы сети (', 'above the network median ('], ['ниже медианы сети (', 'below the network median ('],
    ['Главный плюс', 'Main plus'], ['Минус', 'Minus'], ['делят этот спрос', 'share this demand'],
    ['Конкурентов в 500 м нет.', 'No competitors within 500 m.'],
    ['в радиусе 1,2 км нет размеченных мест скопления людей.', 'no mapped crowded places within 1.2 km.'],
    ['Рядом нет мест скопления людей из OSM — поэтому оценка низкая.', 'No OSM crowded places nearby — hence the low score.'],
    ['Мест скопления людей рядом нет.', 'No crowded places nearby.'], ['рядом нет мест скопления', 'no crowded places nearby'],
    ['до нашего банкомата', 'to our ATM'], ['С такими условиями белых пятен нет.', 'No white spots under these conditions.'],
    ['лучше', 'better than'], ['сети', 'of the network'], ['банкоматов сети', 'network ATMs'], ['конкурентов', 'competitors'],
    ['плюс', 'plus'], ['Банкомат', 'ATM'], ['банкомат', 'ATM'],

    // ── Категории, градации, районы ──
    ['Банкоматы других банков', 'Other banks’ ATMs'], ['Метро', 'Metro'], ['метро', 'metro'], ['Рынок', 'Market'], ['рынок', 'market'],
    ['ТЦ', 'Mall'], ['Супермаркет', 'Supermarket'], ['супермаркет', 'supermarket'], ['Офис банка', 'Bank office'],
    ['Жилой массив', 'Residential area'], ['Бизнес-центр', 'Business center'], ['Отделение банка', 'Bank branch'],
    ['рядом:', 'near:'], ['Ташкент', 'Tashkent'],
    ['Алмазарский район', 'Almazar district'], ['Бектемирский район', 'Bektemir district'], ['Мирабадский район', 'Mirabad district'],
    ['Мирзо-Улугбекский район', 'Mirzo-Ulugbek district'], ['Сергилийский район', 'Sergeli district'], ['Учтепинский район', 'Uchtepa district'],
    ['Чиланзарский район', 'Chilanzar district'], ['Шайхантахурский район', 'Shaykhantahur district'], ['Юнусабадский район', 'Yunusabad district'],
    ['Яккасарайский район', 'Yakkasaray district'], ['Яшнабадский район', 'Yashnabad district'], ['Янгихаётский район', 'Yangihayot district'],
    ['Яшнободский район', 'Yashnabad district'], ['район', 'district'], ['г. Ташкент', 'Tashkent'],
    ['Тошкент шаҳри', 'Tashkent city'],

    // ── Мониторинг и прогноз ──
    ['На связи', 'Online'], ['Нет связи', 'Offline'], ['Нет связи с', 'Offline since'], ['Наличных в сети', 'Cash in network'],
    ['сум, все кассеты', 'UZS, all cassettes'], ['Опустеют за 24 ч', 'Empty within 24 h'], ['по прогнозу модели', 'per model forecast'],
    ['срочные и до конца дня', 'urgent and by end of day'], ['Отказы «нет наличных»', '“No cash” declines'],
    ['транзакций за 7 дней', 'of transactions in 7 days'], ['Можно высвободить', 'Can be freed'],
    ['против стандартной загрузки', 'vs standard load'], ['В работе', 'Active'], ['Пилот', 'Pilot'], ['Обслуживание', 'Maintenance'],
    ['Все', 'All'], ['серийный номер, адрес, тип локации', 'serial number, address, location type'],
    ['Скоро опустеют', 'Emptying soon'], ['Остаток ↑', 'Balance ↑'], ['Остаток ↓', 'Balance ↓'], ['Гео-скоринг ↓', 'Geo-score ↓'],
    ['Сумма загрузки ↓', 'Load amount ↓'], ['Локация', 'Location'], ['Кассеты', 'Cassettes'], ['Остаток', 'Balance'],
    ['Прогноз', 'Forecast'], ['Рекомендация', 'Recommendation'], ['Оборудование', 'Equipment'], ['Загрузка…', 'Loading…'],
    ['Ничего не найдено', 'Nothing found'], ['Сервер недоступен', 'Server unavailable'],
    ['На обслуживании', 'In maintenance'], ['Нет прогноза', 'No forecast'], ['запаса хватит на неделю', 'enough for a week'],
    ['Срочно', 'Urgent'], ['Плановый', 'Planned'], ['Не нужно', 'Not needed'], ['выехать сейчас', 'go now'],
    ['в ближайшие 7 дней не нужно', 'not needed in the next 7 days'], ['загрузить', 'load'],
    ['Обзор', 'Overview'], ['Транзакции', 'Transactions'], ['Модель', 'Model'], ['Лобби', 'Lobby'], ['Уличный', 'Outdoor'], ['Встроенный', 'Through-the-wall'],
    ['Считаем прогноз…', 'Computing forecast…'], ['Кассеты · остаток', 'Cassettes · balance'], ['выдачи', 'of dispense'],
    ['Остаток: 7 дней факта и 7 дней прогноза', 'Balance: 7 days actual and 7 days forecast'],
    ['Полоса — интервал P10–P90 (конформная калибровка на отдельном окне, не участвовавшем в обучении). Прогноз без учёта будущей инкассации.', 'Band — P10–P90 interval (conformal calibration on a separate window not used in training). Forecast ignores future collections.'],
    ['Рекомендация по инкассации', 'Collection recommendation'], ['Срочный выезд', 'Urgent trip'], ['Выезд сегодня', 'Trip today'],
    ['Плановый выезд', 'Planned trip'], ['Пополнение не требуется', 'No refill needed'], ['запаса хватает на 7 дней', 'enough for 7 days'],
    ['запаса не хватит до следующего окна инкассации (Пн–Сб 09:00–18:00)', 'not enough until the next collection window (Mon–Sat 09:00–18:00)'],
    ['Деньги закончатся ~', 'Cash runs out ~'], ['Окно P10–P90', 'P10–P90 window'], ['Первой опустеет кассета', 'First cassette to empty'],
    ['уже пуста', 'already empty'], ['Спрос за 24 ч', 'Demand, 24 h'], ['Следующий визит через', 'Next visit in'],
    ['ставка, % год.', 'rate, % p.a.'], ['Номинал', 'Denomination'], ['Стандарт банка, куп.', 'Bank standard, notes'],
    ['Рекомендация, куп.', 'Recommended, notes'], ['Сумма', 'Amount'], ['Итого', 'Total'],
    ['Спрос за цикл', 'Demand per cycle'], ['ожидаемо', 'expected'], ['Загрузка покрывает P', 'Load covers P'],
    ['резерв', 'reserve'], ['Высвобождается', 'Freed'], ['стоимости денег за цикл при ставке', 'cost of money per cycle at'],
    ['допущение', 'assumption'], ['Почему такой прогноз', 'Why this forecast'],
    ['Вклад признаков в спрос ближайших 24 ч (SHAP, XGBoost).', 'Feature contributions to next-24 h demand (SHAP, XGBoost).'],
    ['Последние инкассации', 'Recent collections'], ['Время', 'Time'], ['Тип', 'Type'], ['Загружено', 'Loaded'], ['Возврат', 'Returned'],
    ['аварийная', 'emergency'], ['по графику', 'scheduled'], ['Локация и гео-скоринг', 'Location & geo-scoring'],
    ['Место установки', 'Installed at'], ['Район (по координатам)', 'District (by coordinates)'], ['Координаты', 'Coordinates'],
    ['Филиал по реестру ЦБ', 'Branch per CBU registry'], ['Платёжные системы', 'Payment systems'],
    ['Устройств на этой точке', 'Devices at this site'], ['Гео-скоринг', 'Geo-score'],
    ['Скоринг считается по открытым данным: места скопления людей OpenStreetMap (метро, рынки, ТЦ, супермаркеты) повышают оценку с затуханием по расстоянию, банкоматы других банков из реестра ЦБ РУз слегка снижают — они делят тот же спрос. Веса — допущение, на пилоте калибруются по реальным снятиям.', 'The score uses open data: OpenStreetMap crowded places (metro, markets, malls, supermarkets) raise it with distance decay; other banks’ ATMs from the CBU registry slightly lower it — they share the same demand. Weights are an assumption, calibrated on real withdrawals during the pilot.'],
    ['Выдано', 'Dispensed'], ['Нет наличных', 'No cash'], ['Отказ эмитента', 'Issuer decline'],
    ['Последние 4 цифры карты или токен', 'Last 4 card digits or token'], ['Сумма, сум', 'Amount, UZS'],
    ['Статус', 'Status'], ['Купюры', 'Notes'], ['Банкомат · локация', 'ATM · location'], ['Нет транзакций', 'No transactions'],
    ['Назад', 'Back'], ['Вперёд', 'Next'], ['Стр.', 'Page'], ['операций', 'operations'],
    ['Номера карт синтетические и хранятся только в маске (BIN + последние 4 цифры) с необратимым токеном. Полный номер не генерируется и не хранится. Адрес банкомата', 'Card numbers are synthetic and stored only masked (BIN + last 4 digits) with an irreversible token. The full number is never generated or stored. ATM address'],
    ['Ошибка прогноза накопленного спроса (WAPE, тест)', 'Cumulative demand forecast error (WAPE, test)'],
    ['Метод', 'Method'], ['дней', 'days'], ['XGBoost (наша модель)', 'XGBoost (our model)'], ['наша модель)', 'our model)'],
    ['Метод банка: среднее за неделю', 'Bank method: weekly average'], ['Как неделю назад', 'Same as last week'],
    ['Покрытие интервала P10–P90', 'P10–P90 interval coverage'], ['цель 80%). Прогнозов с разных моментов на тесте', 'target 80%). Forecasts from different origins on test'],
    ['Холодный старт (банкоматы с историей < 28 дней), 24 ч: модель', 'Cold start (ATMs with < 28 days of history), 24 h: model'],
    ['против', 'vs'], ['у наивного. Уровень прогноза подстраивается по факту последних 7 дней.', 'for naive. Forecast level adapts to the last 7 days of actuals.'],
    ['Разбиение по времени', 'Time split'], ['Обучение', 'Training'], ['Валидация (ранняя остановка)', 'Validation (early stopping)'],
    ['Калибровка интервалов', 'Interval calibration'], ['Тест (не виден модели)', 'Test (unseen by the model)'],
    ['Строк обучения', 'Training rows'], ['Итераций бустинга', 'Boosting iterations'], ['Важность признаков (gain)', 'Feature importance (gain)'],
    ['Данные синтетические: генератор', 'Data is synthetic: generator'],
    ['метод описан в README. Метрики показывают качество на этих данных и сравнение с бейзлайнами на одном и том же тесте. На реальных данных банка модель переобучается тем же пайплайном.', 'method documented in the repository. Metrics show quality on this data and comparison with baselines on the same test. On real bank data the model is retrained with the same pipeline.'],
    ['Проверка политики загрузки на тесте (цикл', 'Load policy backtest (cycle'], ['Политика', 'Policy'], ['Средняя загрузка', 'Average load'],
    ['Не хватило до конца цикла', 'Short before cycle end'], ['Деньги кончились до визита', 'Ran out before visit'],
    ['Стандартная загрузка банка', 'Bank standard load'], ['используется)', 'in use)'], ['граница P90', 'P90 bound'],
    ['уровень сервиса P', 'service level P'], ['Час суток', 'Hour of day'], ['День недели', 'Day of week'], ['День месяца', 'Day of month'],
    ['Месяц', 'Month'], ['Выходной', 'Weekend'], ['Праздник УЗ', 'UZ holiday'], ['Канун праздника', 'Holiday eve'],
    ['День после праздника', 'Day after holiday'], ['Зарплатный день (10/25)', 'Payday (10/25)'], ['День после зарплаты', 'Day after payday'],
    ['Дней до зарплаты', 'Days to payday'], ['Гео-скоринг точки', 'Location geo-score'], ['Горизонт прогноза', 'Forecast horizon'],
    ['Спрос неделю назад в тот же час', 'Demand a week ago, same hour'], ['Спрос 2 недели назад в тот же час', 'Demand 2 weeks ago, same hour'],
    ['Среднее за 4 недели в тот же час', '4-week mean, same hour'], ['Спрос за последние 24 ч', 'Demand, last 24 h'],
    ['Спрос за последние 7 дней', 'Demand, last 7 days'], ['Длина истории банкомата', 'ATM history length'],
    ['В банкомате после визита, куп.', 'In ATM after visit, notes'], ['К визиту останется', 'Left at visit'], ['После визита', 'After visit'],
    ['Довезти', 'Deliver'], ['довезти', 'deliver'], ['в банкомате станет', 'ATM will hold'], ['из 400 млн', 'of 400M'],
    ['Спрос за цикл больше 400 млн — в банкомат больше не помещается', 'Cycle demand exceeds 400M — no more fits in the ATM'],
    ['Полной загрузки хватит примерно на', 'A full load lasts about'], ['макс. 400 млн', 'max 400M'],

    // ── Аналитика ATM ──
    ['ATM DEMAND INTELLIGENCE', 'ATM DEMAND INTELLIGENCE'], ['Операционная аналитика сети', 'Network operational analytics'],
    ['Спрос, cash-out риск, инкассации и денежный поток по выбранному периоду. Эта страница отделена от карты, чтобы основной дашборд оставался большим и удобным для маршрутов.', 'Demand, cash-out risk, collections and cash flow for the selected period. This page is separate from the map so the main dashboard stays large and convenient for routing.'],
    ['12ч', '12h'], ['24ч', '24h'], ['7 дней', '7 days'], ['1 месяц', '1 month'],
    ['Операционная аналитика', 'Operational analytics'], ['История транзакций', 'Transaction history'], ['AI-инсайты', 'AI insights'],
    ['Снято наличных', 'Cash withdrawn'], ['Средний остаток', 'Average balance'], ['Предотвращено cash-out', 'Cash-outs prevented'],
    ['Инкассации', 'Collections'], ['за 24 часа', 'in 24 hours'], ['за 12 часов', 'in 12 hours'], ['за 7 дней', 'in 7 days'], ['за 1 месяц', 'in 1 month'],
    ['по всем ATM', 'across all ATMs'], ['инкассации после low-cash риска', 'collections after low-cash risk'], ['фактические визиты', 'actual visits'],
    ['Топ спроса', 'Top demand'], ['Топ риска', 'Top risk'], ['Средний остаток', 'Average balance'], ['инкассаций', 'collections'],
    ['Нет данных за период', 'No data for the period'], ['Журнал транзакций ATM', 'ATM transaction log'],
    ['История транзакций ATM — временной журнал операций: снятия, инкассации, проверки баланса, low-cash события и технические ошибки.', 'ATM transaction history — a time log of operations: withdrawals, collections, balance checks, low-cash events and technical errors.'],
    ['Тип операции', 'Operation type'], ['Снятие наличных', 'Cash withdrawal'], ['Успешно', 'Success'],
    ['Из сырых операций в управленческие решения', 'From raw operations to management decisions'],
    ['Используя историю транзакций банкоматов, решение превращает операционные данные в инсайты: прогнозируемое пополнение наличности, снижение cash-out и оптимизацию маршрутов инкассации.', 'Using ATM transaction history, the solution turns operational data into insights: predictive cash replenishment, fewer cash-outs and optimized collection routes.'],
    ['Предиктивный сервис', 'Predictive service'], ['Динамическая инкассация', 'Dynamic collection'], ['Аномалии', 'Anomalies'], ['и аномалии', 'and anomalies'],
    ['Исторические операции подаются в ML-модель, чтобы находить паттерны снятий: зарплатные дни, праздники, часы пик и сезонность.', 'Historical operations feed the ML model to find withdrawal patterns: paydays, holidays, rush hours and seasonality.'],
    ['Вместо фиксированного графика маршрут строится по приоритету: остаток, ML-риск, кассеты и ожидаемое время до cash-out.', 'Instead of a fixed schedule, the route is built by priority: balance, ML risk, cassettes and expected time to cash-out.'],
    ['Необычные ночные снятия, резкий спад баланса или повторяющиеся ошибки можно подсветить как подозрительное поведение.', 'Unusual night withdrawals, sharp balance drops or repeated errors can be flagged as suspicious behavior.'],
    ['Если ATM стабилен и заполнен, его можно пропустить. Если спрос растёт, система повышает приоритет для инкассации.', 'If an ATM is stable and full, it can be skipped. If demand grows, the system raises its collection priority.'],
    ['Серия крупных снятий, low-cash события или ошибки помогают заранее обнаружить fraud, сбой или износ устройства.', 'A series of large withdrawals, low-cash events or errors help detect fraud, failures or device wear early.'],
    ['Каждая операция меняет остаток в кассетах. Эти данные становятся основой для прогноза спроса и риска cash-out.', 'Every operation changes the cassette balance. This data is the basis for demand and cash-out risk forecasts.'],
    ['Если в логах растёт число ошибок, банк может отправить техника заранее и снизить простой ATM.', 'If errors grow in the logs, the bank can send a technician early and reduce ATM downtime.'],
    ['Для выезда инкассаторов', 'For collection crews'],

    // ── Инкассация ──
    ['Инкассации', 'Collections'], ['Обновить план', 'Update plan'], ['Маршруты', 'Routes'],
    ['Срочная (остаток < 20%)', 'Urgent (balance < 20%)'], ['В течение дня (< 40%)', 'Within the day (< 40%)'],
    ['Как считается', 'How it works'], ['Выберите день в календаре', 'Select a day in the calendar'],
    ['Нажмите на дату — рейсы сгруппируются по вилоятам.', 'Click a date — trips are grouped by region.'],
    ['В этот день рейсов нет', 'No trips on this day'], ['Выберите другую дату или нажмите «Обновить план».', 'Choose another date or click “Update plan”.'],
    ['Календарь пуст — нажмите «Обновить план».', 'The calendar is empty — click “Update plan”.'], ['Загрузка календаря…', 'Loading calendar…'],
    ['Не удалось загрузить календарь. Обновите страницу или нажмите «Обновить план».', 'Failed to load the calendar. Refresh the page or click “Update plan”.'],
    ['Карточка рейса', 'Trip card'], ['Дата выезда', 'Trip date'], ['Горизонт плана', 'Plan horizon'], ['Рейсы дня', 'Trips of the day'],
    ['Регионы дня', 'Regions of the day'], ['Рейсов', 'Trips'], ['Рейс', 'Trip'], ['рейс', 'trip'], ['вилоятов', 'regions'], ['вилоят', 'region'],
    ['Вилоят', 'Region'], ['Остановки (', 'Stops ('], ['Дистанция', 'Distance'], ['Приоритет', 'Priority'], ['приоритет', 'priority'],
    ['Открыть на карте', 'Open on map'], ['Предыдущий месяц', 'Previous month'], ['Следующий месяц', 'Next month'],
    ['Нуждаются (critical/warning)', 'In need (critical/warning)'], ['В норме (пропущены)', 'OK (skipped)'], ['маршрутов', 'routes'],
    ['Критично — деньги закончатся до ближайшего выезда или остаток < 20%', 'Critical — cash runs out before the next trip or balance < 20%'],
    ['Предупреждение — ML: выезд нужен в ближайший рабочий день', 'Warning — ML: a trip is needed on the next working day'],
    ['Даты — рабочие дни по 8 часов, начиная с сегодня (UTC+5).', 'Dates are 8-hour working days starting today (UTC+5).'],
    ['Срок визита и сумма загрузки — из ML-прогноза спроса (XGBoost) по истории транзакций: выезд, пока с вероятностью 99%', 'Visit deadline and load amount come from the ML demand forecast (XGBoost) on transaction history: go while with 99% probability'],
    ['хватает остатка, загрузка — на цикл с тем же запасом. Банкоматы с остатком < 20% едут в любом случае', 'the balance is enough; load covers the cycle with the same buffer. ATMs with balance < 20% are visited in any case'],
    ['для банкоматов без прогноза — пороги остатка (< 20% / < 40%). Рейс выходит из филиала «Инкассация = 1» своего вилоята.', 'for ATMs without a forecast — balance thresholds (< 20% / < 40%). A trip starts from a «Cash collection = 1» branch of its region.'],
    ['Инкассация сейчас', 'Collection now'], ['Пополнение', 'Refill'], ['Внимание', 'Attention'], ['Норма', 'OK'],

    // ── Общие слова и единицы ──
    ['млн сум', 'M UZS'], ['млрд', 'B'], ['млн', 'M'], ['сум', 'UZS'], ['сўм', 'UZS'], ['купюр', 'notes'], ['шт.', 'pcs'],
    ['км', 'km'], ['мин', 'min'], ['ч', 'h'], ['д', 'd'], ['м', 'm'],
    ['Пн', 'Mon'], ['Вт', 'Tue'], ['Ср', 'Wed'], ['Чт', 'Thu'], ['Пт', 'Fri'], ['Сб', 'Sat'], ['Вс', 'Sun'],
    ['пн', 'Mon'], ['вт', 'Tue'], ['ср', 'Wed'], ['чт', 'Thu'], ['пт', 'Fri'], ['сб', 'Sat'], ['вс', 'Sun'],
    ['янв.', 'Jan'], ['февр.', 'Feb'], ['мар.', 'Mar'], ['апр.', 'Apr'], ['мая', 'May'], ['июн.', 'Jun'], ['июл.', 'Jul'],
    ['авг.', 'Aug'], ['сент.', 'Sep'], ['окт.', 'Oct'], ['нояб.', 'Nov'], ['дек.', 'Dec'],
    ['Октябрь', 'October'], ['Сентябрь', 'September'], ['Ноябрь', 'November'], ['г.,', ','], ['г.', ''],
    ['Загрузка...', 'Loading...'], ['Ошибка загрузки', 'Loading error'], ['Ошибка', 'Error'], ['Нет данных', 'No data'], ['нет данных', 'no data'],
    ['Регион', 'Region'], ['Филиал', 'Branch'], ['Адрес', 'Address'], ['Дата', 'Date'], ['План', 'Plan'], ['Факт', 'Actual'],
    ['Сортировка', 'Sorting'], ['От', 'From'], ['До', 'To'], ['до', 'by'], ['из', 'of'], ['за', 'for'], ['ещё', 'more'],
    ['сегодня', 'today'], ['срочно', 'urgent'], ['стабильно', 'stable'], ['день', 'day'], ['дня', 'days'], ['дн.', 'd'],
    ['Сбросить', 'Reset'], ['Модель', 'Model'], ['Оценка', 'Score'], ['Банк', 'Bank'], ['Код', 'Code'], ['Нет', 'No'], ['Старт', 'Start'],
    ['банкоматов других банков', 'other banks’ ATMs'], ['других банков', 'of other banks'], ['станет', 'becomes'],
    ['шт', 'pcs'], ['тц', 'mall'], ['P90 спроса', 'P90 demand'], ['Интервал P10–P90', 'P10–P90 interval'],
    ['Синтетические данные', 'Synthetic data'], ['синтетический поток (демо)', 'synthetic stream (demo)'], ['Последний опрос', 'Last poll'],
  ];

  // ── Перевод строки: одна регулярка, длинные фразы первыми, границы по буквам ──
  const DICT = new Map(PAIRS);
  const keys = [...DICT.keys()].sort((a, b) => b.length - a.length);
  const esc = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const RE = new RegExp(`(?<![А-Яа-яЁёA-Za-z])(?:${keys.map(esc).join('|')})(?![А-Яа-яЁё])`, 'g');
  const CYR = /[А-Яа-яЁё]/;
  function t(s) {
    if (!s || !CYR.test(s)) return s;
    return s.replace(RE, m => DICT.get(m) ?? m);
  }

  let lang = 'ru';
  try { lang = localStorage.getItem(KEY) === 'en' ? 'en' : 'ru'; } catch (_) {}
  const qpLang = new URLSearchParams(location.search).get('lang');
  if (qpLang === 'en' || qpLang === 'ru') { lang = qpLang; try { localStorage.setItem(KEY, lang); } catch (_) {} }
  let busy = false;
  const ATTRS = ['title', 'placeholder', 'aria-label'];
  const SKIP = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEXTAREA', 'CODE']);

  function translateNode(node) {
    if (node.nodeType === 3) {
      const p = node.parentNode;
      if (!p || SKIP.has(p.nodeName) || p.closest?.('.cf-lang')) return;
      const cur = node.nodeValue;
      if (lang === 'en') {
        if (node.__en !== undefined && cur === node.__en) return;   // уже переведено нами
        if (!CYR.test(cur)) return;
        node.__ru = cur;
        node.__en = t(cur);
        if (node.__en !== cur) node.nodeValue = node.__en;
      } else if (node.__ru !== undefined && cur === node.__en) {
        node.nodeValue = node.__ru;
        node.__en = undefined;
      }
      return;
    }
    if (node.nodeType !== 1 || SKIP.has(node.nodeName) || node.classList?.contains('cf-lang')) return;
    for (const a of ATTRS) {
      if (!node.hasAttribute(a)) continue;
      const k = `__ru_${a}`, ke = `__en_${a}`;
      const cur = node.getAttribute(a);
      if (lang === 'en') {
        if (node[ke] !== undefined && cur === node[ke]) continue;
        if (!CYR.test(cur)) continue;
        node[k] = cur; node[ke] = t(cur); node.setAttribute(a, node[ke]);
      } else if (node[k] !== undefined && cur === node[ke]) {
        node.setAttribute(a, node[k]); node[ke] = undefined;
      }
    }
    for (const c of node.childNodes) translateNode(c);
  }

  let ruTitle = document.title;
  function applyAll() {
    busy = true;
    translateNode(document.body);
    if (lang === 'en') { if (CYR.test(document.title)) ruTitle = document.title; document.title = t(ruTitle); }
    else document.title = ruTitle;
    document.documentElement.lang = lang;
    busy = false;
  }

  const obs = new MutationObserver(muts => {
    if (busy) return;
    busy = true;
    for (const m of muts) {
      if (m.type === 'characterData') translateNode(m.target);
      else if (m.type === 'attributes') translateNode(m.target);
      else m.addedNodes.forEach(n => translateNode(n));
    }
    busy = false;
  });

  function setLang(next) {
    lang = next;
    try { localStorage.setItem(KEY, lang); } catch (_) {}
    document.querySelectorAll('.cf-lang').forEach(el => el.dataset.lang = lang);
    applyAll();
    window.dispatchEvent(new CustomEvent('cf:langchange', { detail: { lang } }));
  }

  // ── Переключатель в стиле iOS ──
  function mountToggle() {
    if (document.querySelector('.cf-lang')) return;
    const el = document.createElement('div');
    el.className = 'cf-lang';
    el.dataset.lang = lang;
    el.setAttribute('role', 'radiogroup');
    el.setAttribute('aria-label', 'Language / Язык');
    el.innerHTML = `<span class="cf-lang__thumb" aria-hidden="true"></span>
      <button type="button" role="radio" data-v="ru" aria-label="Русский">RU</button>
      <button type="button" role="radio" data-v="en" aria-label="English">EN</button>`;
    el.addEventListener('click', e => {
      const b = e.target.closest('button');
      setLang(b ? b.dataset.v : (lang === 'ru' ? 'en' : 'ru'));
    });
    const nav = document.querySelector('nav[data-app-nav]');
    if (nav) nav.insertAdjacentElement('afterend', el);
    else { el.classList.add('cf-lang--float'); document.body.appendChild(el); }
  }

  const css = document.createElement('style');
  css.textContent = `
  .cf-lang { position: relative; display: inline-grid; grid-template-columns: 1fr 1fr; align-items: center;
    width: 92px; height: 34px; padding: 3px; border-radius: 999px; flex-shrink: 0;
    background: rgba(120, 120, 128, .16); box-shadow: inset 0 0 0 1px rgba(15, 23, 42, .06);
    font: 700 12px/1 Inter, -apple-system, system-ui, sans-serif; user-select: none; -webkit-tap-highlight-color: transparent; }
  .cf-lang button { position: relative; z-index: 1; height: 100%; border: 0; background: none; cursor: pointer;
    color: #64748b; font: inherit; letter-spacing: .04em; transition: color .3s ease; padding: 0; }
  .cf-lang[data-lang="ru"] button[data-v="ru"], .cf-lang[data-lang="en"] button[data-v="en"] { color: #0f172a; }
  .cf-lang__thumb { position: absolute; top: 3px; left: 3px; width: calc(50% - 3px); height: calc(100% - 6px);
    border-radius: 999px; background: #fff;
    box-shadow: 0 3px 8px rgba(0, 0, 0, .12), 0 3px 1px rgba(0, 0, 0, .04), 0 0 0 .5px rgba(0, 0, 0, .04);
    transition: transform .38s cubic-bezier(.32, .72, 0, 1); }
  .cf-lang[data-lang="en"] .cf-lang__thumb { transform: translateX(100%); }
  .cf-lang button:active { transform: scale(.96); }
  .cf-lang--float { position: fixed; top: 12px; right: 12px; z-index: 6000; }
  @media (prefers-reduced-motion: reduce) { .cf-lang__thumb, .cf-lang button { transition: none; } }`;
  document.head.appendChild(css);

  window.I18N = { t: s => (lang === 'en' ? t(s) : s), get lang() { return lang; }, set: setLang };

  function start() {
    mountToggle();
    if (lang === 'en') applyAll();
    obs.observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRS });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
