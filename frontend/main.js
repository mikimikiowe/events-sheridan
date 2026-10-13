// Where the backend runs. When the backend serves this page (http://localhost:3000)
// the API is on the same address, so no prefix is needed. When the page is opened
// as a file or through VS Code Live Server (port 5500), point at the backend.
const API_BASE = location.protocol === 'file:' || location.port === '5500' ? 'http://localhost:3000' : '';

// Which endpoint each view button uses
const VIEW_ENDPOINTS = {
    all: () => '/api/events',
    today: () => '/api/events/today',
    week: () => '/api/events/week',
    upcoming: () => '/api/events/upcoming',
    month: () => {
        const now = new Date();
        return `/api/events/month/${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
    },
};

const eventsEl = document.getElementById('events');
const statusEl = document.getElementById('status');
const viewsEl = document.getElementById('views');
const sourceEl = document.getElementById('source');
const locationEl = document.getElementById('location');
const timeEl = document.getElementById('time');
const searchEl = document.getElementById('search');

let currentView = 'all';


// ---------- talking to the API ----------

async function fetchJson(path) {
    const response = await fetch(API_BASE + path);
    if (!response.ok) {
        throw new Error(`${response.status} ${response.statusText}`);
    }
    return response.json();
}

// Build the URL from the selected view + dropdowns, e.g.
// /api/events/week?source=clubs&location=hmc&time=evening
function buildUrl() {
    const params = new URLSearchParams();
    if (sourceEl.value) params.set('source', sourceEl.value);
    if (locationEl.value) params.set('location', locationEl.value);
    if (timeEl.value) params.set('time', timeEl.value);
    if (searchEl.value.trim()) params.set('search', searchEl.value.trim());

    const query = params.toString();
    return VIEW_ENDPOINTS[currentView]() + (query ? `?${query}` : '');
}

async function loadSources() {
    try {
        const sources = await fetchJson('/api/sources');
        for (const source of sources) {
            const option = document.createElement('option');
            option.value = source.key;
            option.textContent = `${source.name} (${source.count})`;
            sourceEl.append(option);
        }
    } catch (error) {
        console.error('Could not load sources', error);
    }
}

async function loadEvents() {
    statusEl.textContent = 'Loading events…';
    try {
        const data = await fetchJson(buildUrl());
        renderEvents(data.events);
        statusEl.textContent = data.count === 1 ? '1 event' : `${data.count} events`;
    } catch (error) {
        eventsEl.innerHTML = '';
        statusEl.textContent = `Could not load events (${error.message}). Is the backend running? Start it with "node server.js" in the backend folder.`;
    }
}


// ---------- showing the events ----------

// Scraped text goes through this so it can't inject HTML into the page
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text ?? '';
    return div.innerHTML;
}

function dateLabel(event) {
    if (event.end_date && event.end_date !== event.start_date) {
        return `${formatDate(event.start_date)} – ${formatDate(event.end_date)}`;
    }
    return formatDate(event.start_date);
}

function formatDate(dateString) {
    if (!dateString) return '';
    const date = new Date(`${dateString}T12:00:00`);
    return date.toLocaleDateString('en-CA', { weekday: 'short', month: 'short', day: 'numeric' });
}

function renderEvents(events) {
    if (!events.length) {
        eventsEl.innerHTML = '<p>No events match these filters.</p>';
        return;
    }

    eventsEl.innerHTML = events.map(event => {
        const chips = [...(event.categories || []), ...(event.tags || [])]
            .filter((chip, index, all) => all.indexOf(chip) === index)
            .slice(0, 5)
            .map(chip => `<span class="chip">${escapeHtml(chip)}</span>`)
            .join('');

        return `
            <article class="card">
                ${event.image_url ? `<img src="${escapeHtml(event.image_url)}" alt="" loading="lazy">` : ''}
                <div class="card-body">
                    <div class="card-top">
                        <div class="date-box">
                            <div class="month">${escapeHtml(event.month)}</div>
                            <div class="day">${escapeHtml(event.day)}</div>
                        </div>
                        <h2>${escapeHtml(event.title)}</h2>
                    </div>
                    <p class="meta">📅 ${escapeHtml(dateLabel(event))}${event.time ? ` · ${escapeHtml(event.time)}` : ''}</p>
                    <p class="meta">📍 ${escapeHtml(event.venue || 'TBA')}</p>
                    <div class="chips">
                        <span class="chip source">${escapeHtml(event.source_name)}</span>
                        ${chips}
                    </div>
                    <a class="more" href="${escapeHtml(event.url)}" target="_blank" rel="noopener">View event →</a>
                </div>
            </article>`;
    }).join('');
}


// ---------- filters ----------

viewsEl.addEventListener('click', event => {
    const button = event.target.closest('button[data-view]');
    if (!button) return;
    viewsEl.querySelectorAll('button').forEach(b => b.classList.toggle('active', b === button));
    currentView = button.dataset.view;
    loadEvents();
});

for (const select of [sourceEl, locationEl, timeEl]) {
    select.addEventListener('change', loadEvents);
}

// Wait until the user stops typing before searching
let searchTimer;
searchEl.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(loadEvents, 300);
});


loadSources();
loadEvents();
