let catalogue = [];

const form = document.querySelector('#ticketForm');
const category = document.querySelector('#category');
const approval = document.querySelector('#approval');
const route = document.querySelector('#route');
const message = document.querySelector('#message');
const submitButton = form.querySelector('button[type="submit"]');

fetch('/api/catalogue')
  .then(response => {
    if (!response.ok) throw new Error('Catalogue unavailable');
    return response.json();
  })
  .then(data => {
    catalogue = data;
    category.innerHTML = '<option value="">Choose a category</option>' + data
      .map(item => `<option value="${item.name}">${item.area} — ${item.type}</option>`)
      .join('');
  })
  .catch(() => {
    category.innerHTML = '<option value="">Service catalogue unavailable</option>';
    message.textContent = 'The ticket service is temporarily unavailable. Please try again shortly.';
    message.className = 'error';
  });

category.addEventListener('change', () => {
  const selected = catalogue.find(item => item.name === category.value);
  approval.classList.toggle('hidden', !selected?.requires_approval);
  approval.querySelectorAll('input').forEach(input => {
    input.required = Boolean(selected?.requires_approval);
  });
  route.textContent = selected ? `Auto-assigned to: ${selected.group}` : '';
  form.priority.value = selected?.type === 'Incident' ? 'P3' : selected?.type === 'Request' ? 'P4' : 'P3';
});

form.addEventListener('submit', async event => {
  event.preventDefault();
  message.textContent = 'Submitting…';
  message.className = '';
  submitButton.disabled = true;

  try {
    const response = await fetch('/api/tickets', { method: 'POST', body: new FormData(form) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || 'Unable to submit ticket.');

    message.textContent = `Ticket ${data.number} created and assigned to ${data.assignment_group}.${data.email_sent ? ' A confirmation email was sent.' : ''}`;
    message.className = 'success';
    form.reset();
    approval.classList.add('hidden');
    approval.querySelectorAll('input').forEach(input => { input.required = false; });
    route.textContent = '';
  } catch (error) {
    message.textContent = error.message || 'Unable to submit ticket.';
    message.className = 'error';
  } finally {
    submitButton.disabled = false;
  }
});
