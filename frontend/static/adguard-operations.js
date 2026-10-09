/* Owner operations. Calling remains deferred to the future Exotel integration. */
async function loadOperationsOverview() {
    const box = $('operationsOverview');
    if (!box) return;
    try {
        const data = await api('GET', '/admin/operations');
        renderOwnerOverview(data);
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
        $('overviewLoadStatus').textContent = 'Unable to load overview: ' + e.message + '. Use Refresh overview to retry.';
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
        $('adminInstallationPanel').open = true;
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


function overviewIcon(name) {
    const paths = {
        subscribers:'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M16 3a4 4 0 0 1 0 8M22 21v-2a4 4 0 0 0-3-3.87M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0',
        audits:'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8ZM14 2v6h6M8 13h8M8 17h5',
        warning:'m12 3 10 18H2ZM12 9v4M12 17h.01',
        support:'M21 11.5a8.5 8.5 0 0 1-8.5 8.5H4l-3 3V11.5a8.5 8.5 0 0 1 17-1M17 3h5v5',
        connection:'M10 13a5 5 0 0 0 7 .5l3-3a5 5 0 0 0-7-7l-2 2M14 11a5 5 0 0 0-7-.5l-3 3a5 5 0 0 0 7 7l2-2'
    };
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="'+paths[name]+'"/></svg>';
}

function renderOwnerOverview(data) {
    const summary = data.summary || {}, rows = data.workspaces || [];
    $('overviewLoadStatus').textContent = '';
    const metrics = [
        ['Active Subscribers',summary.active_subscribers ?? rows.filter(w=>w.activation_status==='active' && w.account_status==='active').length,'subscribers','Activated & active'],
        ['Audits This Month',summary.audits_this_month ?? 0,'audits','Calendar month · UTC'],
        ['Near Quota',summary.near_quota ?? rows.filter(w=>w.quota_pct>=80).length,'warning','80% or more of allowance'],
        ['Open Escalations',summary.overdue_tickets ?? 0,'support','Overdue support tickets']
    ];
    $('overviewMetrics').innerHTML = metrics.map(([label,value,icon,sub])=>'<div class="overview-metric"><span class="overview-icon '+(icon==='warning'?'is-warning':'')+'">'+overviewIcon(icon)+'</span><div><span class="overview-metric-label">'+label+'</span><strong>'+Number(value).toLocaleString()+'</strong><small>'+sub+'</small></div></div>').join('');
    $('overviewSubscribers').innerHTML = rows.length ? '<table class="overview-subscriber-table"><thead><tr><th>Company</th><th>Plan</th><th>Audit Usage</th><th>Status</th><th>Expiry</th></tr></thead><tbody>'+rows.slice(0,4).map(w=>{
        const pct = w.audit_limit < 0 ? 0 : Math.min(100,Math.max(0,Number(w.quota_pct)||0));
        const expired = w.plan_expires_at && new Date(w.plan_expires_at+'Z') < new Date();
        const status = expired ? 'Expired' : w.account_status !== 'active' ? w.account_status || 'Unknown' : w.activation_status !== 'active' ? 'Pending' : pct>=80 ? 'Near limit' : w.plan==='trial' ? 'Trial' : 'Active';
        const cls = status==='Active' ? 'healthy' : status==='Trial' ? 'neutral' : 'warning';
        return '<tr><td><div class="overview-company"><span class="company-avatar">'+escapeHtml((w.name||'?').slice(0,1).toUpperCase())+'</span><div><b>'+escapeHtml(w.name)+'</b><small>'+escapeHtml(w.owner_email || '')+'</small></div></div></td><td class="overview-plan">'+escapeHtml(w.plan)+'</td><td><span class="usage-number">'+Number(w.audits_used||0).toLocaleString()+' / '+(w.audit_limit<0?'Unlimited':Number(w.audit_limit||0).toLocaleString())+'</span><div class="usage-track"><span style="width:'+pct+'%;" class="'+(pct>=80?'warning':'')+'"></span></div></td><td><span class="overview-status '+cls+'">'+escapeHtml(status)+'</span></td><td>'+escapeHtml(w.plan_expires_at ? w.plan_expires_at.slice(0,10) : 'Not set')+'</td></tr>';
    }).join('')+'</tbody></table>' : '<div class="overview-empty">No subscribers yet.<button class="overview-link" onclick="openCreateSubscriber()">Create your first subscriber →</button></div>';
    const attention = [];
    if(summary.overdue_tickets) attention.push({title:'Support response overdue',body:summary.overdue_tickets+' tickets need a response.',icon:'support',view:'support'});
    if(summary.email_failures) attention.push({title:'Email delivery needs attention',body:summary.email_failures+' notifications failed or are retrying.',icon:'warning',view:'support'});
    rows.forEach(w=>(w.issues||[]).forEach(issue=>attention.push({title:issue,body:w.name,icon:issue==='Activation pending'?'subscribers':'warning',view:'subscribers'})));
    $('overviewAttention').innerHTML = attention.length ? attention.slice(0,3).map(a=>'<button title="'+escapeHtml(a.title+' — '+a.body).replace(/"/g, '&quot;')+'" class="attention-item" onclick="showMasterView(\''+a.view+'\')"><span class="overview-icon is-warning">'+overviewIcon(a.icon)+'</span><span><b>'+escapeHtml(a.title)+'</b><small>'+escapeHtml(a.body)+'</small></span><span class="attention-chevron">›</span></button>').join('')+(attention.length>3?'<div class="attention-more">+'+(attention.length-3)+' more items · View all to manage</div>':'') : '<div class="overview-empty">All clear. No issues need attention.</div>';
    const health = ['google','meta'].map(p=>{
        const connected = rows.filter(w=>w.integrations[p].status!=='not_connected');
        const concerns = connected.filter(w=>w.integrations[p].status!=='recent_sync');
        return {name:p==='google'?'Google Ads':'Meta Ads',detail:connected.length+' connected workspaces',label:!connected.length?'Not connected':concerns.length?concerns.length+' need checking':'Recent sync',cls:!connected.length?'neutral':concerns.length?'warning':'healthy'};
    });
    const crmConnected = rows.filter(w=>w.crm.provider && w.crm.provider!=='none');
    const crmFailed = rows.reduce((n,w)=>n+(w.crm.failed||0),0);
    health.push({name:'CRM Delivery',detail:crmConnected.length+' configured workspaces',label:crmFailed?crmFailed+' failed':crmConnected.some(w=>w.crm.last_success_at)?'Delivery confirmed':'Not confirmed',cls:crmFailed?'warning':crmConnected.some(w=>w.crm.last_success_at)?'healthy':'neutral'});
    health.push({name:'Support Email',detail:'Notification delivery',label:summary.email_failures?summary.email_failures+' failures / retrying':'No failures recorded',cls:summary.email_failures?'warning':'neutral'});
    $('overviewIntegrations').innerHTML = health.map(h=>'<div class="integration-health-row"><span class="integration-symbol">'+overviewIcon('connection')+'</span><div><b>'+h.name+'</b><small>'+h.detail+'</small></div><span class="overview-status '+h.cls+'">'+h.label+'</span></div>').join('');
    renderAuditActivity(data.audit_activity || []);
}

function renderAuditActivity(series) {
    const box = $('overviewActivity');
    if(!series.length) { box.innerHTML='<div class="overview-empty">Audit history is not available yet.</div>'; return; }
    const values=series.map(d=>Math.max(0,Number(d.audits)||0)), total=values.reduce((a,b)=>a+b,0);
    const chartWidth = box.clientWidth ? Math.max(400, box.clientWidth - 28) : 728;
    const chartHeight = window.innerWidth >= 1000 && window.innerHeight <= 800 ? 156 : 190;
    const max=Math.max(4,Math.ceil(Math.max(...values)/4)*4), left=42, right=chartWidth-24, top=12, bottom=chartHeight-34;
    const points=values.map((v,i)=>[left+i*(right-left)/Math.max(1,values.length-1),bottom-v/max*(bottom-top)]);
    const line=points.map((p,i)=>(i?'L':'M')+p[0].toFixed(1)+','+p[1].toFixed(1)).join(' ');
    const grid=Array.from({length:5},(_,i)=>{const y=bottom-i*(bottom-top)/4;return '<line x1="'+left+'" x2="'+right+'" y1="'+y+'" y2="'+y+'" stroke="var(--color-border)"/><text x="32" y="'+(y+4)+'" text-anchor="end">'+Math.round(max*i/4).toLocaleString()+'</text>';}).join('');
    const labels=[0,7,14,21,series.length-1].filter((v,i,a)=>v<series.length && a.indexOf(v)===i).map(i=>'<text x="'+points[i][0]+'" y="'+(chartHeight-11)+'" text-anchor="'+(i===0?'start':i===series.length-1?'end':'middle')+'">'+escapeHtml(series[i].date.slice(5))+'</text>').join('');
    box.innerHTML='<svg class="audit-chart" viewBox="0 0 '+chartWidth+' '+chartHeight+'" role="img" aria-label="Audit activity for the last 30 days: '+total+' recorded audits"><title>Daily audit activity (UTC)</title><desc>'+escapeHtml(series.map(d=>d.date+': '+d.audits).join('; '))+'</desc><defs><linearGradient id="auditAreaFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#10b981" stop-opacity=".22"/><stop offset="100%" stop-color="#10b981" stop-opacity=".02"/></linearGradient></defs>'+grid+'<path d="'+line+' L'+right+','+bottom+' L'+left+','+bottom+' Z" fill="url(#auditAreaFill)"/><path d="'+line+'" fill="none" stroke="#059669" stroke-width="2.5" stroke-linejoin="round"/>'+points.map((p,i)=>'<circle cx="'+p[0]+'" cy="'+p[1]+'" r="3" fill="var(--color-bg-card)" stroke="#059669" stroke-width="1.8"><title>'+escapeHtml(series[i].date)+': '+values[i]+' audits</title></circle>').join('')+labels+(total?'':'<text x="'+((left+right)/2)+'" y="'+((top+bottom)/2)+'" text-anchor="middle" class="chart-zero-message">No audits recorded in this period</text>')+'</svg><div class="chart-caption">'+total.toLocaleString()+' audits in the last 30 days <span>Includes historical leads and recorded audit reservations</span></div>';
}
