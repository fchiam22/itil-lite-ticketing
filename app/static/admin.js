const keyInput = document.querySelector('#key');
const actorInput = document.querySelector('#actor');
const consoleEl = document.querySelector('#console');
const loginMsg = document.querySelector('#loginMessage');
const ticketRows = document.querySelector('#tickets');
const summaryEl = document.querySelector('#summary');
const statusFilter = document.querySelector('#status');
const groupFilter = document.querySelector('#group');
const ticketDialog = document.querySelector('#ticketDialog');
const detailMessage = document.querySelector('#detailMessage');

let adminKey = sessionStorage.getItem('itil-admin-key') || '';
let staffName = sessionStorage.getItem('itil-staff-name') || 'IT Support';
let catalogue = [];
let routingConfig = { fallback_recipient: null, routes: [] };
let selectedTicket = null;
keyInput.value = adminKey;
actorInput.value = staffName;

const headers = () => ({ 'X-Admin-Key': adminKey });
const jsonHeaders = () => ({ ...headers(), 'Content-Type': 'application/json' });

function escapeHtml(value) {
  const element = document.createElement('div');
  element.textContent = value ?? '';
  return element.innerHTML;
}

function formatDate(value) {
  return value ? new Date(value).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : '—';
}

function getStaffName() {
  staffName = actorInput.value.trim() || 'IT Support';
  actorInput.value = staffName;
  sessionStorage.setItem('itil-staff-name', staffName);
  return staffName;
}

async function api(url, options = {}) {
  const response = await fetch(url, { ...options, headers: options.headers || headers() });
  const data = await response.json().catch(() => ({}));
  if (response.status === 401) throw new Error('The staff key was not accepted.');
  if (!response.ok) throw new Error(data.detail || 'The service desk request failed.');
  return data;
}

async function loadCatalogue() {
  if (catalogue.length) return;
  catalogue = await fetch('/api/catalogue').then(response => response.json());
  const groups = [...new Set(catalogue.map(item => item.group).concat('Service Desk'))].sort();
  const options = groups.map(group => `<option value="${escapeHtml(group)}">${escapeHtml(group)}</option>`).join('');
  groupFilter.insertAdjacentHTML('beforeend', options);
  document.querySelector('#detailGroup').innerHTML = options;
  document.querySelector('#detailCategory').innerHTML = catalogue.map(item => `<option value="${escapeHtml(item.name)}">${escapeHtml(item.area)} — ${escapeHtml(item.type)}</option>`).join('');
}

function routeForCategory(category) {
  return routingConfig.routes.find(route => route.category === category);
}

function updateCategorySelection() {
  const category = document.querySelector('#detailCategory').value;
  const item = catalogue.find(entry => entry.name === category);
  if (item) document.querySelector('#detailGroup').value = item.group;
  const route = routeForCategory(category);
  document.querySelector('#categoryRouteHint').textContent = route?.effective_recipient
    ? `Reclassification will notify ${route.effective_recipient}${route.uses_fallback ? ' (fallback)' : ''}.`
    : 'No routing recipient is configured.';
}

function renderSummary(report) {
  summaryEl.innerHTML = [
    ['Open backlog', report.open, 'All active tickets'],
    ['High priority', report.open_high_priority, 'Open P1 and P2'],
    ['Opened in 7 days', report.opened_last_7_days, 'Recent demand'],
    ['Completed in 7 days', report.completed_last_7_days, 'Resolved or closed']
  ].map(([label, value, note]) => `<article class="metric"><span>${label}</span><b>${value}</b><small>${note}</small></article>`).join('');
}

function renderEmailStatus(status) {
  document.querySelector('#smtpStatus').textContent = status.enabled
    ? `SMTP enabled via ${status.host}:${status.port} · From ${status.from}`
    : 'SMTP is disabled';
  document.querySelector('#sendSmtpTest').disabled = !status.enabled;
}

function renderTickets(data) {
  document.querySelector('#emptyTickets').classList.toggle('hidden', data.length > 0);
  ticketRows.innerHTML = data.map(ticket => `<tr>
    <td><b>${escapeHtml(ticket.number)}</b></td>
    <td>${formatDate(ticket.created_at)}</td>
    <td>${escapeHtml(ticket.requester_name)}<br><small>${escapeHtml(ticket.requester_email)}</small></td>
    <td>${escapeHtml(ticket.category)}<br><small>${escapeHtml(ticket.assignment_group)}</small></td>
    <td><span class="priority priority-${escapeHtml(ticket.priority)}">${escapeHtml(ticket.priority)}</span></td>
    <td><span class="status-pill status-${ticket.status.toLowerCase().replaceAll(' ', '-')}">${escapeHtml(ticket.status)}</span></td>
    <td>${escapeHtml(ticket.subject)}</td>
    <td><button class="open-ticket secondary" data-number="${escapeHtml(ticket.number)}">Work ticket</button></td>
  </tr>`).join('');
  document.querySelectorAll('.open-ticket').forEach(button => {
    button.onclick = () => openTicket(button.dataset.number);
  });
}

async function load() {
  loginMsg.textContent = 'Loading…';
  loginMsg.className = '';
  try {
    await loadCatalogue();
    const params = new URLSearchParams();
    if (statusFilter.value) params.set('status', statusFilter.value);
    if (groupFilter.value) params.set('group', groupFilter.value);
    const suffix = params.size ? `?${params}` : '';
    const [tickets, report, emailStatus, routing] = await Promise.all([
      api('/api/tickets' + suffix),
      api('/api/reports/summary'),
      api('/api/email/status'),
      api('/api/routing')
    ]);
    routingConfig = routing;
    consoleEl.classList.remove('hidden');
    loginMsg.textContent = '';
    renderTickets(tickets);
    renderSummary(report);
    renderReport(report);
    renderEmailStatus(emailStatus);
    renderRouting(routing);
  } catch (error) {
    consoleEl.classList.add('hidden');
    loginMsg.textContent = error.message;
    loginMsg.className = 'error';
  }
}

function eventText(event) {
  const labels = { status: 'Status', priority: 'Priority', assignment_group: 'Assignment group', description: 'Description', category: 'Category', ticket_type: 'Ticket type', notification_recipient: 'Routing recipient', created: 'Ticket' };
  if (event.field === 'created') return 'created the ticket';
  if (event.field === 'description') return 'updated the description';
  return `changed ${labels[event.field] || event.field} from “${event.old_value || '—'}” to “${event.new_value || '—'}”`;
}

function renderTimeline(ticket) {
  const entries = [
    ...ticket.events.map(event => ({ ...event, kind: 'event' })),
    ...ticket.replies.map(reply => ({ ...reply, kind: 'reply' }))
  ].sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
  document.querySelector('#timeline').innerHTML = entries.length ? entries.map(entry => entry.kind === 'reply'
    ? `<article class="timeline-item reply"><div><b>${escapeHtml(entry.author)}</b><time>${formatDate(entry.created_at)}</time></div><p>${escapeHtml(entry.body).replaceAll('\n', '<br>')}</p></article>`
    : `<article class="timeline-item event"><div><b>${escapeHtml(entry.actor)}</b><time>${formatDate(entry.created_at)}</time></div><p>${escapeHtml(eventText(entry))}</p></article>`
  ).join('') : '<p class="empty-state">No activity recorded yet.</p>';
}

function populateTicket(ticket) {
  selectedTicket = ticket;
  document.querySelector('#detailNumber').textContent = ticket.number;
  document.querySelector('#detailSubject').textContent = ticket.subject;
  document.querySelector('#detailStatus').value = ticket.status;
  document.querySelector('#detailPriority').value = ticket.priority;
  document.querySelector('#detailCategory').value = ticket.category;
  document.querySelector('#detailGroup').value = ticket.assignment_group;
  document.querySelector('#detailDescription').value = ticket.description;
  document.querySelector('#detailRequester').textContent = `${ticket.requester_name} · ${ticket.requester_email}`;
  document.querySelector('#detailContext').textContent = [ticket.entity, ticket.department, ticket.category, ticket.notification_recipient ? `Routed to ${ticket.notification_recipient}` : 'No routing recipient'].filter(Boolean).join(' · ');
  document.querySelector('#replyBody').value = '';
  detailMessage.textContent = '';
  renderTimeline(ticket);
  const route = routeForCategory(ticket.category);
  document.querySelector('#categoryRouteHint').textContent = route?.effective_recipient
    ? `Current effective recipient: ${route.effective_recipient}${route.uses_fallback ? ' (fallback)' : ''}.`
    : 'No routing recipient is configured.';
}

async function openTicket(number) {
  try {
    populateTicket(await api(`/api/tickets/${encodeURIComponent(number)}`));
    ticketDialog.showModal();
  } catch (error) {
    loginMsg.textContent = error.message;
    loginMsg.className = 'error';
  }
}

document.querySelector('#saveTicket').onclick = async () => {
  if (!selectedTicket) return;
  const button = document.querySelector('#saveTicket');
  button.disabled = true;
  detailMessage.textContent = 'Saving…';
  try {
    const updated = await api(`/api/tickets/${encodeURIComponent(selectedTicket.number)}`, {
      method: 'PATCH', headers: jsonHeaders(), body: JSON.stringify({
        actor: getStaffName(),
        status: document.querySelector('#detailStatus').value,
        priority: document.querySelector('#detailPriority').value,
        category: document.querySelector('#detailCategory').value,
        assignment_group: document.querySelector('#detailGroup').value,
        description: document.querySelector('#detailDescription').value,
        notify_route: true
      })
    });
    populateTicket(updated);
    detailMessage.textContent = updated.routing_email_sent
      ? 'Ticket changes saved and the new routing mailbox was notified.'
      : 'Ticket changes saved.';
    detailMessage.className = 'success';
    await load();
  } catch (error) {
    detailMessage.textContent = error.message;
    detailMessage.className = 'error';
  } finally {
    button.disabled = false;
  }
};

document.querySelector('#addReply').onclick = async () => {
  if (!selectedTicket) return;
  const body = document.querySelector('#replyBody').value.trim();
  if (!body) {
    detailMessage.textContent = 'Write a reply before posting.';
    detailMessage.className = 'error';
    return;
  }
  const button = document.querySelector('#addReply');
  button.disabled = true;
  try {
    const updated = await api(`/api/tickets/${encodeURIComponent(selectedTicket.number)}/replies`, {
      method: 'POST', headers: jsonHeaders(), body: JSON.stringify({ author: getStaffName(), body })
    });
    populateTicket(updated);
    detailMessage.textContent = updated.email_sent
      ? 'Reply added and an email update was sent.'
      : 'Reply added to the history, but no email was sent.';
    detailMessage.className = 'success';
    await load();
  } catch (error) {
    detailMessage.textContent = error.message;
    detailMessage.className = 'error';
  } finally {
    button.disabled = false;
  }
};

function renderBars(elementId, values) {
  const element = document.querySelector(elementId);
  const entries = Object.entries(values || {});
  const max = Math.max(...entries.map(([, value]) => value), 1);
  element.innerHTML = entries.length ? entries.map(([label, value]) => `<div class="bar-row"><span>${escapeHtml(label)}</span><div class="bar-track"><i style="width:${Math.max(value / max * 100, value ? 4 : 0)}%"></i></div><b>${value}</b></div>`).join('') : '<p class="empty-state">No data yet.</p>';
}

function renderReport(report) {
  document.querySelector('#reportGenerated').textContent = `Generated ${formatDate(report.generated_at)}`;
  const average = report.average_resolution_hours == null ? '—' : report.average_resolution_hours < 24 ? `${report.average_resolution_hours}h` : `${(report.average_resolution_hours / 24).toFixed(1)}d`;
  document.querySelector('#reportMetrics').innerHTML = [
    ['Open backlog', report.open, 'Active demand'],
    ['Completion rate', `${report.completion_rate}%`, 'All recorded tickets'],
    ['High priority open', report.open_high_priority, 'P1 and P2 exposure'],
    ['Average resolution', average, 'Resolved and closed tickets'],
    ['Opened / 7 days', report.opened_last_7_days, 'Demand trend'],
    ['Completed / 7 days', report.completed_last_7_days, 'Delivery trend']
  ].map(([label, value, note]) => `<article class="report-metric"><span>${label}</span><b>${value}</b><small>${note}</small></article>`).join('');
  renderBars('#statusChart', report.by_status);
  renderBars('#ageChart', report.age_buckets);
  renderBars('#groupChart', report.by_group);
  renderBars('#typeChart', report.by_type);
  renderBars('#categoryChart', report.by_category);
  const trendMax = Math.max(...report.daily_opened.map(day => day.count), 1);
  document.querySelector('#trendChart').innerHTML = report.daily_opened.map(day => `<div class="trend-day"><b>${day.count}</b><i style="height:${Math.max(day.count / trendMax * 110, day.count ? 8 : 2)}px"></i><span>${new Date(day.date + 'T00:00:00').toLocaleDateString([], { weekday: 'short' })}</span></div>`).join('');
  document.querySelector('#oldestOpen').innerHTML = report.oldest_open.length ? `<table class="mini-table"><thead><tr><th>Ticket</th><th>Priority</th><th>Status</th><th>Subject</th><th>Opened</th></tr></thead><tbody>${report.oldest_open.map(ticket => `<tr><td>${escapeHtml(ticket.number)}</td><td>${escapeHtml(ticket.priority)}</td><td>${escapeHtml(ticket.status)}</td><td>${escapeHtml(ticket.subject)}</td><td>${formatDate(ticket.created_at)}</td></tr>`).join('')}</tbody></table>` : '<p class="empty-state">No open tickets.</p>';
}

function renderRouting(config) {
  routingConfig = config;
  document.querySelector('#fallbackRecipient').value = config.fallback_recipient || '';
  document.querySelector('#routingRows').innerHTML = config.routes.map(route => `<tr>
    <td><b>${escapeHtml(route.area)}</b></td>
    <td>${escapeHtml(route.type)}<br><small>${escapeHtml(route.category)}</small></td>
    <td>${escapeHtml(route.group)}</td>
    <td><input class="route-recipient" data-category="${escapeHtml(route.category)}" type="email" value="${escapeHtml(route.recipient || '')}" placeholder="Use fallback"></td>
    <td><span class="${route.uses_fallback ? 'fallback-badge' : 'route-badge'}">${escapeHtml(route.effective_recipient || 'Not configured')}</span></td>
    <td><button class="save-route secondary" data-category="${escapeHtml(route.category)}">Save</button></td>
  </tr>`).join('');
  document.querySelectorAll('.save-route').forEach(button => {
    button.onclick = async () => {
      const input = [...document.querySelectorAll('.route-recipient')].find(item => item.dataset.category === button.dataset.category);
      const recipient = input.value.trim();
      button.disabled = true;
      try {
        const updated = await api('/api/routing/category', {
          method: 'PATCH', headers: jsonHeaders(), body: JSON.stringify({ category: button.dataset.category, recipient, actor: getStaffName() })
        });
        renderRouting(updated);
        const message = document.querySelector('#routingMessage');
        message.textContent = recipient ? 'Category recipient saved.' : 'Category now uses the Service Desk fallback.';
        message.className = 'success';
      } catch (error) {
        const message = document.querySelector('#routingMessage');
        message.textContent = error.message;
        message.className = 'error';
        button.disabled = false;
      }
    };
  });
}

document.querySelector('#login').onclick = () => {
  adminKey = keyInput.value;
  sessionStorage.setItem('itil-admin-key', adminKey);
  getStaffName();
  load();
};
document.querySelector('#refresh').onclick = load;
statusFilter.onchange = load;
groupFilter.onchange = load;
document.querySelector('#closeDialog').onclick = () => ticketDialog.close();
ticketDialog.addEventListener('click', event => { if (event.target === ticketDialog) ticketDialog.close(); });
document.querySelector('#printReport').onclick = () => window.print();
document.querySelector('#detailCategory').onchange = updateCategorySelection;
document.querySelector('#saveFallback').onclick = async () => {
  const message = document.querySelector('#routingMessage');
  const button = document.querySelector('#saveFallback');
  button.disabled = true;
  try {
    const updated = await api('/api/routing/fallback', {
      method: 'PATCH', headers: jsonHeaders(), body: JSON.stringify({ recipient: document.querySelector('#fallbackRecipient').value.trim(), actor: getStaffName() })
    });
    renderRouting(updated);
    message.textContent = 'Service Desk fallback saved.';
    message.className = 'success';
  } catch (error) {
    message.textContent = error.message;
    message.className = 'error';
  } finally {
    button.disabled = false;
  }
};
document.querySelector('#sendSmtpTest').onclick = async () => {
  const recipient = document.querySelector('#smtpRecipient').value.trim();
  const message = document.querySelector('#smtpMessage');
  const button = document.querySelector('#sendSmtpTest');
  message.textContent = 'Sending…';
  message.className = '';
  button.disabled = true;
  try {
    await api('/api/email/test', {
      method: 'POST', headers: jsonHeaders(), body: JSON.stringify({ recipient })
    });
    message.textContent = 'Test email captured. Open the Mailpit inbox to inspect it.';
    message.className = 'success';
  } catch (error) {
    message.textContent = error.message;
    message.className = 'error';
  } finally {
    button.disabled = false;
  }
};
document.querySelectorAll('.view-tab').forEach(tab => {
  tab.onclick = () => {
    document.querySelectorAll('.view-tab').forEach(item => item.classList.toggle('active', item === tab));
    document.querySelectorAll('.console-view').forEach(view => view.classList.toggle('hidden', view.id !== tab.dataset.view));
  };
});
document.querySelector('#csv').onclick = async () => {
  try {
    const response = await fetch('/api/reports/tickets.csv', { headers: headers() });
    if (!response.ok) throw new Error('CSV download failed.');
    const blob = await response.blob();
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = 'itil-tickets.csv';
    link.click();
    URL.revokeObjectURL(link.href);
  } catch (error) {
    loginMsg.textContent = error.message;
    loginMsg.className = 'error';
  }
};
if (adminKey) load();
