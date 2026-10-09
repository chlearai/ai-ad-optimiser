/* Owner operations. Calling remains deferred to the future Exotel integration. */
async function loadOperationsOverview() {
    const box = $('operationsOverview');
    if (!box) return;
    try {
        const data = await api('GET', '/admin/operations');
        const s = data.summary;
        const statusLabel = {not_connected:'Not connected',unverified:'Not yet verified',stale:'Needs checking',recent_sync:'Recent successful sync',error:'Connection error'};
        box.innerHTML = '<div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:12px;">' +
            [['Need attention',s.needs_attention],['Activation pending',s.pending_activation],['Open tickets',s.open_tickets],['Overdue tickets',s.overdue_tickets],['Email failures / retrying',s.email_failures]].map(x =>
                '<div style="padding:10px;border:1px solid var(--color-border);border-radius:8px;"><b>' + x[1] + '</b><br>' + x[0] + '</div>').join('') + '</div>' +
            '<details><summary style="cursor:pointer;font-weight:600;">Subscriber health and integration status</summary><div class="table-wrap"><table><thead><tr><th>Subscriber</th><th>Audit period (UTC)</th><th>Integrations</th><th>Needs attention</th><th>Recovery</th></tr></thead><tbody>' +
            data.workspaces.map(w => '<tr><td><b>' + escapeHtml(w.name) + '</b><br>#' + w.id + ' · ' + escapeHtml(w.plan) + '</td><td>' +
                '<span style="white-space:nowrap;">' + escapeHtml(w.period_start.slice(0,10)) + '</span> to <span style="white-space:nowrap;">' + escapeHtml(w.period_end.slice(0,10)) + '</span>' + '<br>' + w.audits_used + ' used · ' + (w.audits_remaining === null ? 'Unlimited' : w.audits_remaining + ' remaining') + '</td><td>' +
                ['google','meta'].map(p => escapeHtml((p === 'google' ? 'Google' : 'Meta') + ': ' + statusLabel[w.integrations[p].status]) + '<br>Last sync: ' + (w.integrations[p].last_success_at ? escapeHtml(w.integrations[p].last_success_at.slice(0,16).replace('T',' ')) + ' UTC' : 'Not confirmed')).join('<br>') +
                '<br>CRM: ' + escapeHtml(w.crm.provider) + ' · ' + w.crm.failed + ' failed<br>' + escapeHtml(w.crm.last_error || '') + '</td><td>' +
                (w.issues.length ? w.issues.map(escapeHtml).join('<br>') : 'No flagged issues') + '</td><td>' +
                '<button class="btn btn-secondary btn-sm" onclick="renewSubscriber(' + w.id + ')">Renew</button> ' +
                '<button class="btn btn-secondary btn-sm" onclick="configureInstallation(' + w.id + ')">Installation</button><br>' +
                ['google','meta'].filter(p => w.integrations[p].status !== 'not_connected').map(p => '<button class="btn btn-secondary btn-sm" onclick="checkIntegration(' + w.id + ',\'' + p + '\')">Check ' + p + '</button>').join(' ') +
                (w.crm.last_failed_lead_id ? '<br><button class="btn btn-secondary btn-sm" onclick="retryCrmDelivery(' + w.crm.last_failed_lead_id + ')">Retry last CRM failure</button>' : '') + '</td></tr>').join('') +
            '</tbody></table></div></details>';
    } catch (e) {
        box.textContent = 'Unable to load subscriber health: ' + e.message;
    }
}

async function renewSubscriber(id) {
    const input = prompt('Extend this subscriber’s plan by how many months? Current audit usage is preserved.', '1');
    if (input === null) return;
    const months = Number(input);
    if (!Number.isInteger(months) || months < 1 || months > 12) return toast('Enter 1–12 months', 'error');
    try {
        await api('POST', '/admin/subscribers/' + id + '/renew', {months});
        toast('Plan renewed'); loadAdminSubscribers();
    } catch (e) { toast(e.message, 'error'); }
}

async function configureInstallation(id) {
    try {
        const install = await api('GET', '/workspaces/' + id + '/installation');
        const origins = prompt('Allowed website origins, separated by commas (e.g. https://example.com). Leave empty to allow any origin with this workspace’s public installation token.', install.allowed_origins.join(', '));
        if (origins === null) return;
        await api('PUT', '/workspaces/' + id + '/installation', {allowed_origins: origins.split(',').map(x=>x.trim()).filter(Boolean)});
        $('embedSubSelect').value = String(id);
        await updateAdminEmbedSnippet();
        toast('Installation settings saved. Copy the workspace script tag above.');
        let panel = $('installationDetails');
        if (!panel) {
            panel = document.createElement('div'); panel.id = 'installationDetails';
            $('adminSubscribersCard').prepend(panel);
        }
        panel.innerHTML = '<details open style="padding:12px;border:1px solid var(--color-border);border-radius:8px;margin-bottom:12px;"><summary><b>Workspace #' + id + ' integration setup</b></summary><p>Google lead-form webhook URL:</p><input id="googleWebhookUrl" class="filter-input" readonly style="width:100%;"><p>Google webhook key / private CRM callback secret (server configuration only):</p><input id="workspaceWebhookSecret" class="filter-input" type="password" readonly style="width:100%;"><button class="btn btn-secondary btn-sm" onclick="copyWorkspaceWebhookSecret()">Copy private key</button><p>CRM callback: POST /api/v1/webhooks/crm with workspace_id ' + id + ' and the X-AdGuard-Webhook-Secret header. Keep the private key out of website tags.</p></details>';
        $('googleWebhookUrl').value = window.location.origin + '/api/adguard/webhook?workspace_id=' + id;
        $('workspaceWebhookSecret').value = install.webhook_secret;
    } catch (e) { toast(e.message, 'error'); }
}

async function checkIntegration(id, platform) {
    try {
        const result = await api('POST', '/admin/workspaces/' + id + '/check-connection', {platform});
        toast(result.warning || 'Discovery completed', result.warning ? 'error' : 'success');
        loadOperationsOverview();
    } catch (e) { toast(e.message, 'error'); }
}

async function retryCrmDelivery(id) {
    try {
        const result = await api('POST', '/admin/leads/' + id + '/retry-crm');
        toast(result.status === 'pushed' ? 'CRM delivery succeeded' : result.error || result.status, result.status === 'pushed' ? 'success' : 'error');
        loadOperationsOverview();
    } catch (e) { toast(e.message, 'error'); }
}

function renderTicketOperations(ticket) {
    let panel = $('adminTicketOperations');
    if (!panel) {
        panel = document.createElement('div'); panel.id = 'adminTicketOperations';
        $('adminTicketThread').appendChild(panel);
    }
    panel.innerHTML = '<hr style="margin:16px 0;"><div><b>Owner controls</b> · ' + (ticket.overdue ? 'Response overdue' : 'Due: ' + escapeHtml(ticket.response_due_at || 'No response pending')) + ' UTC</div>' +
        '<div style="display:flex;gap:8px;flex-wrap:wrap;margin:8px 0;"><input id="ticketAssignee" class="filter-input" placeholder="Admin email (blank = unassigned)"><select id="ticketPriority" class="filter-select"><option>low</option><option>normal</option><option>high</option></select><button class="btn btn-secondary btn-sm" onclick="saveTicketOperations()">Save assignment / priority</button></div>' +
        '<textarea id="ticketInternalNote" class="filter-input" placeholder="Internal note — visible only to admins" style="width:100%;"></textarea><button class="btn btn-secondary btn-sm" onclick="saveTicketOperations(true)">Add internal note</button>' +
        '<div>' + (ticket.internal_notes || []).map(n=>'<p><b>' + escapeHtml(n.author) + '</b> · ' + escapeHtml(n.created_at) + ' UTC<br>' + escapeHtml(n.body) + '</p>').join('') + '</div>' +
        '<div><b>Email delivery</b>' + ((ticket.notifications || []).map(n=>'<p>#' + n.id + ': ' + escapeHtml(n.status) + ' · attempts ' + n.attempts + (n.last_error ? '<br>' + escapeHtml(n.last_error) : '') +
            (['failed','retry'].includes(n.status) ? ' <button class="btn btn-secondary btn-sm" onclick="retrySupportEmail(' + n.id + ')">Retry</button>' : '') + '</p>').join('') || '<p>No notifications recorded yet.</p>') + '</div>';
    $('ticketAssignee').value = ticket.assigned_to || '';
    $('ticketPriority').value = ticket.priority || 'normal';
}

async function saveTicketOperations(noteOnly=false) {
    const body = noteOnly ? {internal_note:$('ticketInternalNote').value} : {assigned_to:$('ticketAssignee').value, priority:$('ticketPriority').value};
    if (noteOnly && !body.internal_note.trim()) return;
    try {
        const ticket = await api('PUT', '/support/admin/tickets/' + adminTicketId + '/manage', body);
        renderTicketOperations(ticket); loadSupportInbox(); toast('Ticket updated');
    } catch (e) { toast(e.message, 'error'); }
}

async function retrySupportEmail(id) {
    try {
        await api('POST', '/support/admin/notifications/' + id + '/retry');
        await openAdminTicket(adminTicketId); toast('Email queued for retry');
    } catch (e) { toast(e.message, 'error'); }
}

async function configureSupportTargets() {
    try {
        const settings = await api('GET', '/support/admin/sla');
        const plans = ['trial','starter','pro','agency','custom'];
        const input = prompt('Response targets in hours: Trial, Starter, Pro, Enterprise, Custom. Applies to new tickets and reopened response clocks.', plans.map(p=>settings.hours_by_plan[p]).join(', '));
        if (input === null) return;
        const values = input.split(',').map(v=>Number(v.trim()));
        if (values.length !== plans.length || values.some(v=>!Number.isInteger(v) || v<1 || v>168)) return toast('Enter five whole numbers from 1 to 168', 'error');
        await api('PUT', '/support/admin/sla', {hours_by_plan:Object.fromEntries(plans.map((p,i)=>[p,values[i]]))});
        toast('Response targets saved');
    } catch (e) { toast(e.message, 'error'); }
}

async function copyWorkspaceWebhookSecret() {
    try { await navigator.clipboard.writeText($('workspaceWebhookSecret').value); toast('Private workspace key copied'); }
    catch (e) { toast('Clipboard unavailable; copy the key from the field', 'error'); }
}
