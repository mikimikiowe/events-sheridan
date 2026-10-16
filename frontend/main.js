// Where the backend runs. It's hosted separately on Vercel, so every request
// goes there. To use a local backend instead, change this to 'http://localhost:3000'.
const API_BASE = 'https://events-sheridan-backend.vercel.app';

// Short descriptions shown in the Organizers list
const ORGANIZER_BLURBS = {
    ssu: 'Socials, trips and free food',
    clubs: 'Meetups, workshops and parties',
    edge: 'Startup programs and mentors',
    sheridan: 'Open houses and info sessions',
};

// Which endpoint each view button uses
const VIEW_ENDPOINTS = {
    all: () => '/api/events',
    today: () => '/api/events/today',
    week: () => '/api/events/week',
    month: () => `/api/events/month/${localDateString(new Date()).slice(0, 7)}`,
};

const VIEW_NAMES = { all: 'All events', today: 'Today', week: 'This week', month: 'This month' };

const $ = id => document.getElementById(id);

const state = {
    view: 'all',
    results: [],
    weekEvents: [],
    eventsById: new Map(),
    lastFocus: null,
};


// ---------- talking to the API ----------

async function fetchJson(path) {
    const response = await fetch(API_BASE + path);
    if (!response.ok) {
        // The API explains bad filters in { error: "..." }
        const body = await response.json().catch(() => ({}));
        throw new Error(body.error || `${response.status} ${response.statusText}`);
    }
    return response.json();
}

// e.g. /api/events/week?source=clubs&location=hmc&time=evening
function buildEventsUrl() {
    const params = new URLSearchParams();
    if ($('organizer').value) params.set('source', $('organizer').value);
    if ($('campus').value) params.set('location', $('campus').value);
    if ($('time').value) params.set('time', $('time').value);

    const query = params.toString();
    return VIEW_ENDPOINTS[state.view]() + (query ? `?${query}` : '');
}


// ---------- dates ----------

// Event dates are Sheridan (Toronto) dates, so "today" is worked out in Toronto
// time too, whatever time zone the visitor's computer is in. Matches the backend.
const TIME_ZONE = 'America/Toronto';
const dateParts = new Intl.DateTimeFormat('en-US', {
    timeZone: TIME_ZONE, year: 'numeric', month: '2-digit', day: '2-digit',
});

function localDateString(date) {
    const { year, month, day } = Object.fromEntries(
        dateParts.formatToParts(date).map(part => [part.type, part.value])
    );
    return `${year}-${month}-${day}`;
}

// "2026-10-13" -> Date (noon, so time zones can't shift the day)
function toDate(dateString) {
    return new Date(`${dateString}T12:00:00`);
}

function format(dateString, options) {
    return dateString ? toDate(dateString).toLocaleDateString('en-CA', options) : '';
}

// "Tue, Oct 13"
function mediumDate(dateString) {
    return format(dateString, { weekday: 'short', month: 'short', day: 'numeric' });
}

// "Tuesday, October 13, 2026"
function longDate(dateString) {
    return format(dateString, { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' });
}

// "Tue, Oct 13" or "Tue, Oct 13 – Thu, Dec 10"
function dateRange(event) {
    if (event.end_date && event.end_date !== event.start_date) {
        return `${mediumDate(event.start_date)} – ${mediumDate(event.end_date)}`;
    }
    return mediumDate(event.start_date);
}

function isHappeningNow(event) {
    const today = localDateString(new Date());
    return event.start_date < today && event.end_date >= today;
}

// "4:00 PM", or the end time too when the calendar link has it: "4:00 PM – 8:00 PM"
function timeRange(event) {
    if (!event.time) return 'Time not listed';
    const match = (event.google_calendar_url || '').match(/dates=(\d{8}T\d{6}Z)\/(\d{8}T\d{6}Z)/);
    if (!match) return event.time;
    const parse = s => new Date(`${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}T${s.slice(9, 11)}:${s.slice(11, 13)}:00Z`);
    const start = parse(match[1]);
    const end = parse(match[2]);
    // Only show an end time for same-day events
    if (localDateString(start) !== localDateString(end)) return event.time;
    const endText = end.toLocaleTimeString('en-CA', { hour: 'numeric', minute: '2-digit', timeZone: TIME_ZONE })
        .replace(/\s?a\.m\./i, ' AM').replace(/\s?p\.m\./i, ' PM');
    return `${event.time} – ${endText}`;
}


// ---------- small building blocks ----------

// Scraped text goes through this so it can't inject HTML into the page
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text ?? '';
    return div.innerHTML;
}

function colorClass(index) {
    return `c${(index % 6) + 1}`;
}

const NEW_TAB = '<span class="visually-hidden"> (opens in a new tab)</span>';

// Event image, or a coloured block with the organizer name if there isn't one.
// The title is always next to the image, so the alt text just says what the image is.
function eventImage(event, index) {
    if (!event.image_url) {
        return `<div class="no-image ${colorClass(index)}" aria-hidden="true">${escapeHtml(event.source_name)}</div>`;
    }
    return `<img src="${escapeHtml(event.image_url)}" alt="Poster for ${escapeHtml(event.title)}" loading="lazy" data-fallback="${escapeHtml(event.source_name)}" data-color="${colorClass(index)}">`;
}

function whereText(event) {
    const lines = event.address?.lines || [];
    const detail = lines.find(line => line && line !== event.venue);
    return [event.venue || 'Location not listed', detail].filter(Boolean).join(', ');
}

function priceText(event) {
    if (event.price) return event.price;
    if ((event.categories || []).some(c => c.toLowerCase() === 'free')) return 'Free';
    return null;
}

function labels(event) {
    const all = [...(event.categories || []), ...(event.tags || [])]
        .map(label => label.trim())
        .filter(label => label && label.toLowerCase() !== 'free');
    return [...new Set(all)];
}

// Small tilted poster (hero, footer band)
function poster(event, index, { small = false, tilt = 0 } = {}) {
    return `
        <a class="poster ${small ? 'small' : ''} ${colorClass(index)}" href="${escapeHtml(event.url)}" target="_blank" rel="noopener" style="transform: rotate(${tilt}deg)">
            ${eventImage(event, index)}
            <span class="text">
                <span class="source">${escapeHtml(event.source_name)}</span>
                <span class="title">${escapeHtml(event.title)}</span>
                <span class="when">${escapeHtml(mediumDate(event.start_date))}${event.time ? ` · ${escapeHtml(event.time)}` : ''}</span>
            </span>
            ${NEW_TAB}
        </a>`;
}

// Bigger poster (this week and organizers panels)
function feature(event, index) {
    return `
        <a class="feature ${colorClass(index)}" href="${escapeHtml(event.url)}" target="_blank" rel="noopener">
            ${eventImage(event, index)}
            <span class="text">
                <span class="source">${escapeHtml(event.source_name)}</span>
                <span class="title">${escapeHtml(event.title)}</span>
                <span class="when">${escapeHtml(dateRange(event))}${event.time ? ` · ${escapeHtml(event.time)}` : ''}</span>
            </span>
            ${NEW_TAB}
        </a>`;
}


// ---------- top sections (built once) ----------

function renderHero(upcoming) {
    const tilts = [-4, 3, -3, 4];
    $('hero-left').innerHTML = upcoming.slice(0, 2)
        .map((e, i) => poster(e, i, { small: i === 1, tilt: tilts[i] })).join('');
    $('hero-right').innerHTML = upcoming.slice(2, 4)
        .map((e, i) => poster(e, i + 2, { small: i === 0, tilt: tilts[i + 2] })).join('');
}

function renderWeek(weekEvents, upcoming) {
    const today = localDateString(new Date());
    // Events that haven't started yet first; ones already running go last
    const sorted = [
        ...weekEvents.filter(e => e.start_date >= today),
        ...weekEvents.filter(e => e.start_date < today),
    ];
    const plural = n => `${n} event${n === 1 ? '' : 's'}`;

    $('topbar-count').textContent = `${plural(weekEvents.length)} happening this week across every campus`;
    $('week-eyebrow').textContent = `This week · ${plural(weekEvents.length)}`;

    $('week-list').innerHTML = sorted.slice(0, 5).map(event => `
        <li>
            <span class="date">${escapeHtml(isHappeningNow(event) ? 'On now' : mediumDate(event.start_date))}</span>
            <div>
                <a class="title-link" href="${escapeHtml(event.url)}" target="_blank" rel="noopener">${escapeHtml(event.title)}${NEW_TAB}</a>
                <div class="where">${escapeHtml(event.time || 'Time not listed')} · ${escapeHtml(whereText(event))}</div>
            </div>
        </li>`).join('') || '<li><span></span><div class="where">Nothing on this week yet.</div></li>';

    const picks = sorted.length > 5 ? sorted.slice(5, 7) : upcoming.slice(0, 2);
    $('week-features').innerHTML = picks.map((e, i) => feature(e, i + 3)).join('');
}

function renderOrganizers(sources, upcoming) {
    $('organizer-list').innerHTML = sources.map(source => `
        <li>
            <button data-organizer="${escapeHtml(source.key)}">
                <span class="name">${escapeHtml(source.name)}</span>
                <span class="about">${escapeHtml(ORGANIZER_BLURBS[source.key] || '')} · ${source.count} events</span>
            </button>
        </li>`).join('');

    $('footer-organizers').innerHTML = sources.map(source =>
        `<li><button data-organizer="${escapeHtml(source.key)}">${escapeHtml(source.name)}</button></li>`
    ).join('');

    $('organizer').innerHTML = '<option value="">All organizers</option>' + sources.map(source =>
        `<option value="${escapeHtml(source.key)}">${escapeHtml(source.name)} (${source.count})</option>`
    ).join('');

    const pick = upcoming.find(e => e.source === 'edge') || upcoming[0];
    $('organizer-feature').innerHTML = pick ? feature(pick, 5) : '';
}

function renderCta(upcoming) {
    const picks = upcoming.slice(4, 7);
    $('cta-left').innerHTML = picks[0] ? poster(picks[0], 3, { tilt: -3 }) : '';
    $('cta-right').innerHTML = picks.slice(1)
        .map((e, i) => poster(e, i + 4, { small: i === 0, tilt: [3, -2][i] })).join('');
}


// ---------- the events grid ----------

function eventCard(event, index) {
    const price = priceText(event);
    return `
        <article class="card" aria-labelledby="title-${escapeHtml(event.id)}">
            <div class="card-media">
                ${eventImage(event, index)}
                <p class="date-badge" aria-hidden="true">
                    <span class="month">${escapeHtml(format(event.start_date, { month: 'short' }))}</span>
                    <span class="day">${escapeHtml(String(toDate(event.start_date).getDate()))}</span>
                </p>
            </div>
            <div class="card-body">
                <p class="source">${escapeHtml(event.source_name)}${event.organizations?.length ? ` · ${escapeHtml(event.organizations.join(', '))}` : ''}</p>
                <h3 id="title-${escapeHtml(event.id)}">${escapeHtml(event.title)}</h3>
                <dl class="facts">
                    <div><dt>Date</dt><dd>${escapeHtml(dateRange(event))}${isHappeningNow(event) ? ' <span class="now">On now</span>' : ''}</dd></div>
                    <div><dt>Time</dt><dd>${escapeHtml(timeRange(event))}</dd></div>
                    <div><dt>Where</dt><dd>${escapeHtml(whereText(event))}</dd></div>
                    ${price ? `<div><dt>Price</dt><dd>${escapeHtml(price)}</dd></div>` : ''}
                </dl>
                ${event.excerpt ? `<p class="excerpt">${escapeHtml(event.excerpt)}</p>` : ''}
                <div class="card-actions">
                    <button class="btn btn-dark on-dark" data-details="${escapeHtml(event.id)}">Details<span class="visually-hidden"> for ${escapeHtml(event.title)}</span></button>
                    <a class="view-link" href="${escapeHtml(event.url)}" target="_blank" rel="noopener">View on ${escapeHtml(event.source_name)}<span class="visually-hidden">: ${escapeHtml(event.title)}</span>${NEW_TAB}</a>
                </div>
            </div>
        </article>`;
}

async function loadEvents() {
    $('count').textContent = 'Loading events…';
    $('events-grid').setAttribute('aria-busy', 'true');
    try {
        const data = await fetchJson(buildEventsUrl());
        state.results = data.events;
        for (const event of data.events) state.eventsById.set(event.id, event);

        const filtersUsed = [VIEW_NAMES[state.view]];
        for (const id of ['organizer', 'campus', 'time']) {
            if ($(id).value) filtersUsed.push($(id).selectedOptions[0].textContent.replace(/\s*\(.*\)$/, ''));
        }
        $('count').textContent = `Showing ${data.count} event${data.count === 1 ? '' : 's'} · ${filtersUsed.join(', ')}`;

        $('events-grid').innerHTML = data.events.length
            ? data.events.map(eventCard).join('')
            : '<p class="empty">No events match these filters. Try another campus, organizer or time.</p>';
    } catch (error) {
        $('count').textContent = '';
        $('events-grid').innerHTML = `<p class="empty">Could not load events (${escapeHtml(error.message)}). Please try again in a moment.</p>`;
    } finally {
        $('events-grid').removeAttribute('aria-busy');
    }
}

function setView(view) {
    state.view = view;
    for (const button of $('views').querySelectorAll('button')) {
        button.setAttribute('aria-pressed', String(button.dataset.view === view));
    }
    loadEvents();
}

function goToEvents() {
    $('events').scrollIntoView();
    $('events-title').setAttribute('tabindex', '-1');
    $('events-title').focus({ preventScroll: true });
}


// ---------- event details dialog ----------

function paragraphs(text) {
    return String(text || '')
        .split(/\n+/)
        .map(line => line.trim())
        .filter(Boolean)
        .map(line => `<p>${escapeHtml(line)}</p>`)
        .join('');
}

function openDetails(id, trigger) {
    const event = state.eventsById.get(id);
    if (!event) return;
    state.lastFocus = trigger;

    const price = priceText(event);
    const tags = labels(event);
    const occurrences = (event.occurrences || []).map(o =>
        `<li>${escapeHtml(longDate(o.date))}${o.time_12h ? ` at ${escapeHtml(o.time_12h)}` : ''}</li>`
    ).join('');
    const addressLines = (event.address?.lines || []).filter(Boolean);

    $('dialog-content').innerHTML = `
        ${event.image_url ? `<img class="dialog-image" src="${escapeHtml(event.image_url)}" alt="Poster for ${escapeHtml(event.title)}">` : ''}
        <div class="dialog-body">
            <div>
                <p class="eyebrow">${escapeHtml(event.source_name)}${event.organizations?.length ? ` · ${escapeHtml(event.organizations.join(', '))}` : ''}</p>
                <h2 id="dialog-title">${escapeHtml(event.title)}</h2>
            </div>
            <dl class="facts">
                <div><dt>Date</dt><dd>${escapeHtml(dateRange(event))}${isHappeningNow(event) ? ' <span class="now">On now</span>' : ''}</dd></div>
                <div><dt>Time</dt><dd>${escapeHtml(timeRange(event))}</dd></div>
                <div><dt>Where</dt><dd>${escapeHtml(event.venue || 'Location not listed')}</dd></div>
                ${price ? `<div><dt>Price</dt><dd>${escapeHtml(price)}</dd></div>` : ''}
                ${event.sales_status ? `<div><dt>Tickets</dt><dd>${escapeHtml(event.sales_status)}</dd></div>` : ''}
            </dl>
            ${occurrences ? `<section><h3>Dates</h3><ul>${occurrences}</ul></section>` : ''}
            ${addressLines.length ? `
                <section>
                    <h3>Location</h3>
                    <p class="address">${addressLines.map(escapeHtml).join('<br>')}</p>
                    ${event.address?.map_url ? `<a href="${escapeHtml(event.address.map_url)}" target="_blank" rel="noopener">Open in Google Maps${NEW_TAB}</a>` : ''}
                </section>` : ''}
            ${tags.length ? `<section><h3>Tags</h3><ul class="chips">${tags.map(t => `<li>${escapeHtml(t)}</li>`).join('')}</ul></section>` : ''}
            ${event.description ? `<section class="description"><h3>About this event</h3>${paragraphs(event.description)}</section>` : ''}
            <div class="dialog-links">
                <a class="btn btn-dark on-dark" href="${escapeHtml(event.url)}" target="_blank" rel="noopener">View on ${escapeHtml(event.source_name)}${NEW_TAB}</a>
                ${event.google_calendar_url ? `<a class="btn btn-light" href="${escapeHtml(event.google_calendar_url)}" target="_blank" rel="noopener">Add to Google Calendar${NEW_TAB}</a>` : ''}
                ${event.ics_url ? `<a class="btn btn-light" href="${escapeHtml(event.ics_url)}">Download calendar file</a>` : ''}
            </div>
        </div>`;

    $('event-dialog').showModal();
    $('dialog-close').focus();
}

function closeDetails() {
    $('event-dialog').close();
}

$('event-dialog').addEventListener('close', () => {
    state.lastFocus?.focus();
});
$('dialog-close').addEventListener('click', closeDetails);
// Clicking the dark backdrop closes it too
$('event-dialog').addEventListener('click', e => {
    if (e.target === $('event-dialog')) closeDetails();
});


// ---------- "Add this week to my calendar" (.ics download) ----------

function icsText(text) {
    return String(text ?? '').replace(/\\/g, '\\\\').replace(/\n/g, '\\n').replace(/[,;]/g, m => `\\${m}`);
}

function downloadWeekCalendar() {
    const lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Whats New at Sheridan//EN'];
    for (const event of state.weekEvents) {
        // Every event's Google Calendar link has its start/end in UTC: dates=START/END
        const match = (event.google_calendar_url || '').match(/dates=(\d{8}T\d{6}Z)\/(\d{8}T\d{6}Z)/);
        if (!match) continue;
        lines.push(
            'BEGIN:VEVENT',
            `UID:${event.id}@whats-new-at-sheridan`,
            `DTSTAMP:${new Date().toISOString().replace(/[-:]/g, '').slice(0, 15)}Z`,
            `DTSTART:${match[1]}`,
            `DTEND:${match[2]}`,
            `SUMMARY:${icsText(event.title)}`,
            `LOCATION:${icsText((event.address?.lines || [event.venue]).join(', '))}`,
            `URL:${event.url}`,
            `DESCRIPTION:${icsText(`${event.excerpt || ''}\n\n${event.url}`)}`,
            'END:VEVENT'
        );
    }
    lines.push('END:VCALENDAR');

    const blob = new Blob([lines.join('\r\n')], { type: 'text/calendar' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = 'sheridan-this-week.ics';
    link.click();
    URL.revokeObjectURL(link.href);
}


// ---------- wiring up ----------

// Swap a broken image for the coloured block with the organizer name
document.addEventListener('error', e => {
    const img = e.target;
    if (!(img instanceof HTMLImageElement) || !img.dataset.fallback) return;
    const block = document.createElement('div');
    block.className = `no-image ${img.dataset.color}`;
    block.setAttribute('aria-hidden', 'true');
    block.textContent = img.dataset.fallback;
    img.replaceWith(block);
}, true);

$('views').addEventListener('click', e => {
    const button = e.target.closest('button[data-view]');
    if (button) setView(button.dataset.view);
});

for (const id of ['organizer', 'campus', 'time']) {
    $(id).addEventListener('change', loadEvents);
}

$('add-to-calendar').addEventListener('click', downloadWeekCalendar);

document.addEventListener('click', e => {
    const details = e.target.closest('[data-details]');
    const viewButton = e.target.closest('[data-show-view]');
    const organizerButton = e.target.closest('[data-organizer]');
    const campusButton = e.target.closest('[data-campus]');
    const scrollButton = e.target.closest('[data-scroll]');

    if (details) {
        openDetails(details.dataset.details, details);
    } else if (viewButton) {
        setView(viewButton.dataset.showView);
        goToEvents();
    } else if (organizerButton) {
        $('organizer').value = organizerButton.dataset.organizer;
        loadEvents();
        goToEvents();
    } else if (campusButton) {
        $('campus').value = campusButton.dataset.campus;
        loadEvents();
        goToEvents();
    } else if (scrollButton) {
        goToEvents();
    }
});

// Mobile menu
$('menu-toggle').addEventListener('click', () => {
    const open = $('nav-links').classList.toggle('open');
    $('menu-toggle').setAttribute('aria-expanded', String(open));
});
$('nav-links').addEventListener('click', e => {
    const link = e.target.closest('a');
    if (!link) return;
    $('nav-links').classList.remove('open');
    $('menu-toggle').setAttribute('aria-expanded', 'false');
    for (const other of $('nav-links').querySelectorAll('a')) {
        if (other === link) other.setAttribute('aria-current', 'true');
        else other.removeAttribute('aria-current');
    }
});
document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && $('nav-links').classList.contains('open')) {
        $('nav-links').classList.remove('open');
        $('menu-toggle').setAttribute('aria-expanded', 'false');
        $('menu-toggle').focus();
    }
});


// ---------- start ----------

async function init() {
    $('year').textContent = new Date().getFullYear();
    loadEvents();

    try {
        const [sources, all, week] = await Promise.all([
            fetchJson('/api/sources'),
            fetchJson('/api/events'),
            fetchJson('/api/events/week'),
        ]);
        for (const event of all.events) state.eventsById.set(event.id, event);

        const today = localDateString(new Date());
        // Every event runs from start_date to end_date, so anything that hasn't ended counts.
        // Ones still to start come first; ones already running go after them.
        const upcoming = [
            ...all.events.filter(e => e.start_date >= today),
            ...all.events.filter(e => e.start_date < today && e.end_date >= today),
        ];

        state.weekEvents = week.events;
        renderHero(upcoming);
        renderWeek(week.events, upcoming);
        renderOrganizers(sources, upcoming);
        renderCta(upcoming);
    } catch (error) {
        $('topbar-count').textContent = 'Could not reach the events API';
        console.error(error);
    }
}

init();
