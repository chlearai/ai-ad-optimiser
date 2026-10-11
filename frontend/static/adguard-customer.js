/* Pearl & Teal. Reuses the existing connection, support, account and billing workflows. */
(() => {
    'use strict';
    const el = id => document.getElementById(id);
    const esc = value => escapeHtml(String(value ?? ''));
    const number = value => Number(value || 0).toLocaleString('en-IN');
    const money = value => new Intl.NumberFormat('en-IN', {style:'currency',currency:'INR',maximumFractionDigits:0}).format(value || 0);
    const date = value => value ? new Date(value.endsWith('Z') ? value : value+'Z').toLocaleDateString('en-IN',{day:'numeric',month:'short'}) : 'Not set';
    let generation = 0, portfolio = false, snapshot = null, page = 1, brandPage = 0;
    const brandColors=['#109d75','#087f8c','#6564bd','#d38a29','#ce647c','#5a86bf','#81a338','#925aa4','#bd704b','#3f9a9a'];
    const pageSize = 20;
    async function downloadReport(params) {
        try {
            const response=await fetch(API+'/reports/export?'+params,{headers:{Authorization:'Bearer '+token}});
            if(!response.ok)throw new Error('Could not export your report. Please try again.');
            const url=URL.createObjectURL(await response.blob());
            const link=document.createElement('a');link.href=url;link.download='adguard-report.csv';link.click();
            setTimeout(()=>URL.revokeObjectURL(url),1000);
        } catch(error){toast(error.message,'error');}
    }
    exportReportCsv=function(){
        const params=new URLSearchParams({workspace_id:selectedWorkspaceObj.id});
        const range=el('exportRange')?.value || '30';
        if(range==='custom') {if(el('exportStartDate').value)params.set('start_date',el('exportStartDate').value);if(el('exportEndDate').value)params.set('end_date',el('exportEndDate').value);}
        else params.set('days',range);
        downloadReport(params);
    };
    const paths = ['M3 10l9-7 9 7v10H5V10m4 10v-7h6v7','M8 3h8v4H8zM5 5H3v16h18V5h-2M7 12h10M7 16h7','M4 20V10h4v10m4 0V4h4v16m4 0V8','M9 15l6-6M8 16H6a4 4 0 010-8h4m4 0h4a4 4 0 010 8h-4','M4 4h16v13H8l-4 4zM8 8h8M8 12h5','M12 8a4 4 0 100 8 4 4 0 000-8M12 2v3m0 14v3M2 12h3m14 0h3M5 5l2 2m10 10l2 2M5 19l2-2M17 7l2-2'];
    const tabs = [['dashboard','Overview'],['leads','Leads'],['reports','Reports'],['connections','Connections'],['support','Support'],['settings','Account']];
    function init() {
        const nav = document.createElement('nav'); nav.className='customer-nav'; nav.setAttribute('aria-label','Workspace navigation');
        nav.innerHTML=tabs.map(([id,label],i)=>`<button type="button" data-customer-tab="${id}" ${i===0?'class="active"':''}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="${paths[i]}"/></svg>${label}</button>`).join('');
        nav.addEventListener('click', e=>{const button=e.target.closest('[data-customer-tab]');if(button) switchTab(button.dataset.customerTab);});
        document.querySelector('.sidebar>.nav').prepend(nav);
        const selector=document.createElement('div');selector.className='customer-brand-selector';selector.innerHTML='<label for="customerBrandSelect">CURRENT WORKSPACE</label><select id="customerBrandSelect" aria-label="Select brand workspace"></select><button type="button" id="customerAddBrand" class="btn btn-secondary btn-sm" style="margin-top:8px;width:100%" onclick="createAgencyWorkspace()">+ Add brand</button>';
        nav.after(selector);
        el('customerBrandSelect').addEventListener('change',e=>{portfolio=false;switchWorkspace(e.target.value);});
        const planDetail=document.createElement('div');planDetail.className='customer-plan-detail';planDetail.id='customerPlanDetail';el('sidebarQuotaWidget').append(planDetail);
        const dashboard=el('tab-dashboard');
        [...dashboard.children].forEach(child=>child.classList.add('customer-legacy'));
        const leads=document.createElement('section');leads.id='tab-leads';leads.className='hidden';
        const feed=el('feedTable').closest('.card');
        if(feed){feed.classList.remove('customer-legacy');leads.append(feed);}
        const oldControls=document.querySelector('.ryze-controls');
        if(oldControls){oldControls.style.marginBottom='12px';leads.prepend(oldControls);}
        const pagination=document.createElement('div');pagination.className='customer-lead-pagination';pagination.id='customerLeadPagination';leads.append(pagination);
        dashboard.after(leads);
        const view=document.createElement('section');view.id='customerOverview';view.className='customer-overview';view.setAttribute('aria-live','polite');view.innerHTML='<div class="customer-state" role="status">Loading your workspace…</div>';dashboard.append(view);
        const account=document.createElement('div');account.id='customerAccountSummary';account.className='customer-account-summary';el('tab-settings').prepend(account);
        const controls=document.createElement('div');controls.className='customer-panel customer-controls';controls.innerHTML='<h2>Campaign quality controls</h2><p>Review recommendations here. Campaign pausing requires action in your ad platform.</p><details><summary>Review thresholds and activity</summary></details>';
        controls.querySelector('details').append(el('shieldSection'));el('tab-settings').append(controls);
        const connections=document.createElement('div');connections.className='customer-panel';connections.style.marginBottom='12px';connections.innerHTML='<div class="customer-panel-head"><h2>Set up your workspace</h2></div><div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn btn-primary btn-sm" onclick="startConnect()">Connect Google Ads</button><button class="btn btn-secondary btn-sm" onclick="startMetaConnect()">Connect Meta Ads</button><button class="btn btn-secondary btn-sm" onclick="openCrmPicker()">Connect CRM</button><button class="btn btn-secondary btn-sm" onclick="openScriptTagPanel()">Install website tag</button></div><p style="margin-top:9px">Login limits apply per workspace, not per ad account. Salesforce delivery is not available yet.</p>';
        el('tab-connections').prepend(connections);
        document.querySelectorAll('.ryze-card-label,.ryze-chart-title>span').forEach(node=>{node.textContent=node.textContent.replace(/Recovered.*Spend/,'Estimated junk exposure').replace('Blocked Garbage','Flagged leads').replace('Audited vs Blocked Leads','Audited vs flagged leads');});
        // Replace misleading legacy report text while keeping the export contract intact.
        document.querySelectorAll('#tab-reports th').forEach(node=>{if(/recovered|saved/i.test(node.textContent))node.textContent='Estimated exposure (₹350/flagged lead)';});
    }
    const originalSwitch = switchTab;
    switchTab = function(name) {
        originalSwitch(name);
        document.querySelectorAll('[data-customer-tab]').forEach(node=>node.classList.toggle('active',node.dataset.customerTab===name));
        if(name==='leads') renderFeed(getFilteredLeads());
        if(name==='dashboard') refreshOverview();
        if(name==='settings' && snapshot) renderAccount(snapshot);
    };
    const originalFeed=renderFeed;
    renderFeed=function(rows) {
        rows=rows || getFilteredLeads();
        const pages=Math.max(1,Math.ceil(rows.length/pageSize));page=Math.min(page,pages);
        originalFeed(rows.slice((page-1)*pageSize,page*pageSize));
        const box=el('customerLeadPagination');if(!box)return;
        box.innerHTML=`<span>${number(rows.length)} loaded leads · Page ${page} of ${pages}${allLeads.length>=500?' · Latest 500 only; use Reports to export more':''}</span><span><button class="btn btn-secondary btn-sm" data-page="-1" ${page<=1?'disabled':''}>Previous</button><button class="btn btn-secondary btn-sm" data-page="1" ${page>=pages?'disabled':''}>Next</button></span>`;
        box.querySelectorAll('[data-page]').forEach(button=>button.onclick=()=>{page+=Number(button.dataset.page);renderFeed(rows);});
    };
    renderRyzeDashboard=function(){renderFeed(getFilteredLeads());refreshOverview();};
    renderCallWidget=function(){el('sidebarCallWidget').style.display='none';};
    statusBadge=function(lead){
        if(selectedWorkspaceObj?.plan==='trial' || lead.stage==='observed')return '<span class="badge badge-blue">Observed</span>';
        if(['flagged','red'].includes(lead.verdict))return '<span class="badge badge-orange">Flagged</span>';
        if(lead.lsq_status==='pushed')return '<span class="badge badge-green">Sent to CRM</span>';
        if(lead.lsq_status==='failed')return '<span class="badge badge-red" title="'+esc(lead.lsq_error||'')+'">Delivery failed</span>';
        return '<span class="badge badge-orange">'+(lead.verdict==='grey'?'OTP review':'Not sent')+'</span>';
    };
    const originalShield=renderShield;
    renderShield=function(ws){originalShield(ws);if(el('servicesLiveContent'))el('servicesLiveContent').textContent=ws?.plan==='trial'?'Observation only · No forms blocked':'Campaign quality monitoring · Review recommendations in Account';};
    async function refreshOverview() {
        if(!selectedWorkspaceObj || !el('customerOverview'))return;
        const request=++generation;
        const days=el('customerRange')?.value || '30';
        try {
            const data=await api('GET',`/workspace/overview?workspace_id=${encodeURIComponent(selectedWorkspaceObj.id)}&days=${days}&portfolio=${portfolio}`);
            if(request!==generation)return;
            snapshot=data;renderOverview(data,days);renderAccount(data);
        } catch(error) {
            if(request!==generation)return;
            el('customerOverview').innerHTML=`<div class="customer-panel customer-state"><h2>We couldn’t load your overview</h2><p>${esc(error.message)}</p><button class="btn btn-primary btn-sm" id="customerRetry">Try again</button></div>`;
            el('customerRetry').onclick=refreshOverview;
        }
    }
    function stat(label,value,note){return `<div class="customer-stat"><span class="customer-stat-label">${label}</span><strong>${value}</strong><small>${note}</small></div>`;}
    function chart(trends,series=[]) {
        const width=760,height=230,left=34,right=12,top=15,bottom=29;
        const max=Math.max(4,...(series.length?series.flatMap(brand=>brand.points):trends.map(day=>day.audited)));const ceiling=Math.ceil(max/4)*4;
        const x=i=>left+i*(width-left-right)/Math.max(1,trends.length-1);
        const y=n=>height-bottom-n/ceiling*(height-top-bottom);
        const line=key=>trends.map((day,i)=>`${i?'L':'M'}${x(i).toFixed(1)},${y(day[key]).toFixed(1)}`).join(' ');
        const area=key=>`${line(key)} L${x(trends.length-1)},${height-bottom} L${left},${height-bottom} Z`;
        const grid=Array.from({length:5},(_,i)=>{const n=i*ceiling/4;return `<line class="grid" x1="${left}" x2="${width-right}" y1="${y(n)}" y2="${y(n)}"/><text x="${left-8}" y="${y(n)+3}" text-anchor="end">${n}</text>`;}).join('');
        const ticks=[...new Set([0,Math.floor((trends.length-1)/3),Math.floor((trends.length-1)*2/3),trends.length-1])].map(i=>`<text x="${x(i)}" y="${height-5}" text-anchor="${i===0?'start':i===trends.length-1?'end':'middle'}">${esc(date(trends[i].date+'T00:00:00'))}</text>`).join('');
        if(series.length)return `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="Daily audits by brand">${grid}${series.map((brand,index)=>`<path d="${brand.points.map((n,i)=>`${i?'L':'M'}${x(i).toFixed(1)},${y(n).toFixed(1)}`).join(' ')}" fill="none" stroke="${brandColors[index%10]}" stroke-width="2.3"><title>${esc(brand.name)}</title></path>`).join('')}${ticks}</svg>`;
        return `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="Daily audited and flagged lead counts. ${trends.reduce((n,d)=>n+d.audited,0)} audited leads in this period."><defs><linearGradient id="customerArea" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#25ae82" stop-opacity=".28"/><stop offset="1" stop-color="#25ae82" stop-opacity=".02"/></linearGradient></defs>${grid}<path d="${area('audited')}" fill="url(#customerArea)"/><path d="${line('audited')}" fill="none" stroke="#109d75" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/><path d="${line('flagged')}" fill="none" stroke="#e4a127" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>${trends.map((day,i)=>`<circle cx="${x(i)}" cy="${y(day.audited)}" r="2.5" fill="#109d75"><title>${esc(day.date)}: ${day.audited} audited, ${day.flagged} flagged</title></circle>`).join('')}${ticks}</svg>`;
    }
    function recentTable(data) {
        if(portfolio) return `<table class="customer-portfolio"><thead><tr><th>Brand</th><th>Audited</th><th>Quality</th><th>Flagged</th></tr></thead><tbody>${data.brands.slice(brandPage*5,brandPage*5+5).map(b=>`<tr data-brand="${Number(b.id)}" tabindex="0" role="button" aria-label="Open ${esc(b.name)}"><td>${esc(b.name)}</td><td>${number(b.audited)}</td><td>${b.quality_rate===null?'—':b.quality_rate+'%'}</td><td>${number(b.flagged)}</td></tr>`).join('')}</tbody></table>${data.brands.length>5?`<button class="btn btn-secondary btn-xs" id="customerMoreBrands" style="margin-top:5px">${brandPage?'Previous':'More'} brands · ${brandPage+1}/${Math.ceil(data.brands.length/5)}</button>`:''}`;
        return `<table><thead><tr><th style="width:32%">Lead</th><th>Score</th><th>Assessment</th><th>CRM</th></tr></thead><tbody>${data.recent.map(lead=>{
            const flagged=['flagged','red'].includes(lead.verdict),clean=['verified','green'].includes(lead.verdict);
            const status=data.entitlements.observation_only?'Observed':(['pushed','success'].includes(lead.lsq_status)?'Delivered':lead.lsq_status==='failed'?'Failed':lead.lsq_status==='skipped_flagged'?'Withheld':'Not sent');
            return `<tr><td title="${esc(lead.full_name)}">${esc(lead.full_name||'Unnamed lead')}<small class="customer-muted" style="display:block;font-size:9px;font-weight:400">${esc(date(lead.received_at))}</small></td><td>${lead.integrity_score ?? '—'}</td><td><span class="customer-tag ${flagged?'warn':''}">${flagged?'Flagged':clean?'Verified':'Review'}</span></td><td>${status}</td></tr>`;
        }).join('') || '<tr><td colspan="4" style="white-space:normal;padding:22px;text-align:center;font-weight:400">No leads in this period. Connect a source or install your website tag to start.</td></tr>'}</tbody></table>`;
    }
    function renderOverview(data,days) {
        const trial=data.entitlements.observation_only,agency=data.entitlements.plan==='agency',pro=data.entitlements.plan==='pro';
        const t=data.totals,quota=data.allowance,unlimited=quota.audit_limit<0;
        const daysLeft=data.expires_at?Math.max(0,Math.ceil((new Date(data.expires_at+'Z')-Date.now())/86400000)):null;
        const title=trial?'See your lead quality, clearly.':agency?'Every brand. One clear view.':pro?'Your growth, with greater clarity.':'Your lead quality, in focus.';
        const legend=portfolio && data.brand_trends?.length?data.brand_trends.map((brand,index)=>`<span><i style="background:${brandColors[index%10]}"></i>${esc(brand.name)}</span>`).join(''):'<span><i></i>Audited</span><span><i class="amber"></i>Flagged</span>';
        const sub=trial?'Explore real quality signals. Your trial observes leads without blocking submissions.':agency?'Monitor brand performance, quality and connections from one place.':'We audit your leads for quality so you can focus on what matters.';
        const attention=[];
        if(data.access_issue)attention.push(['Account needs attention',data.access_issue,'settings']);
        if(!data.connections.some(c=>c.connected && c.name!=='CRM'))attention.push(['Connect your first lead source','Connect Google Ads, Meta Ads or install your website tag.','connections']);
        data.connections.filter(c=>c.error).forEach(c=>attention.push([c.name+' needs attention',c.error,'connections']));
        if(t.delivery_failed)attention.push([number(t.delivery_failed)+' CRM deliveries failed','Review your CRM credentials and delivery status.','connections']);
        if(!unlimited && quota.quota_pct>=80)attention.push(['Audit allowance is running low',number(quota.audits_remaining)+' audits remain in this period.','settings']);
        if(trial && daysLeft!==null && daysLeft<=3)attention.push(['Your trial ends soon',`${daysLeft} days remaining. Review your plan when you are ready.`,'settings']);
        el('customerOverview').innerHTML=`
            <header class="customer-heading"><div><h1>${title}</h1><p>${sub}</p></div><div class="customer-heading-actions"><span class="customer-plan-pill">${esc(data.entitlements.label)}${trial?' · Observation only':''}</span>${agency?`<button id="customerPortfolioToggle" class="btn btn-secondary btn-sm">${portfolio?'Selected brand':'All brands'}</button>`:''}<select id="customerRange" class="filter-select" aria-label="Overview reporting period"><option value="7">Last 7 days</option><option value="30">Last 30 days</option><option value="90">Last 90 days</option></select><button id="customerRefresh" class="btn btn-secondary btn-sm" aria-label="Refresh overview">↻</button></div></header>
            ${trial?`<div class="customer-setup"><span><b>Your 14-day trial</b> · ${daysLeft===null?'Expiry not set':daysLeft+' days left'} · ${number(quota.audits_used)} / ${number(quota.audit_limit)} audits used. No OTP, form blocking or CRM delivery.</span><button class="btn btn-primary btn-sm" data-go="connections">Continue setup</button></div>`:''}
            <div class="customer-body"><div class="customer-center"><div class="customer-kpis">
                ${stat(trial?'Leads observed':portfolio?'Audits across brands':'Audits used',number(portfolio?t.audited:quota.audits_used),portfolio?'Selected reporting period':unlimited?'Unlimited audits this period':`of ${number(quota.audit_limit)} this period`)}
                ${stat('Verified leads',number(t.verified),'High quality assessments')}${stat('Flagged leads',number(t.flagged),trial?'Potential quality concerns':'Potentially low quality')}
                ${stat('Quality rate',t.quality_rate===null?'—':t.quality_rate+'%',t.audited?'Verified out of audited':'Awaiting your first audit')}
            </div><section class="customer-panel customer-plot"><div class="customer-panel-head"><div><h2>${portfolio?'Portfolio audit activity':trial?'Observed lead quality':'Audited vs flagged'}</h2><p>Daily quality trend · ${number(t.audited)} audits in the selected period</p></div></div><div class="customer-legend"><span><i></i>Audited</span><span><i class="amber"></i>Flagged</span></div><div class="customer-chart">${chart(data.trends,portfolio?(data.brand_trends||[]):[])}</div>${!t.audited?'<p style="text-align:center">Your chart will grow as real leads arrive.</p>':''}</section>
            <div class="customer-bottom"><section class="customer-panel customer-recent"><div class="customer-panel-head"><h2>${portfolio?'Brand portfolio':'Recent leads'}</h2><button data-go="leads">View all leads →</button></div>${recentTable(data)}</section><section class="customer-panel customer-sources"><h2>${pro?'Source quality insights':'Quality by source'}</h2>${data.sources.map(source=>`<div class="customer-source"><div><b>${esc(source.source.replaceAll('_',' '))}</b><span>${source.quality_rate===null?'—':source.quality_rate+'%'}</span></div><div class="track"><span style="width:${Math.max(0,Math.min(100,source.quality_rate||0))}%"></span></div><small>${number(source.audited)} audited · ${number(source.flagged)} flagged</small></div>`).join('') || '<div class="customer-state" style="min-height:110px;padding:16px">Source comparisons appear after your first audit.</div>'}</section></div></div>
            <aside class="customer-rail"><section class="customer-panel"><div class="customer-panel-head"><h2>Connection health</h2><button data-go="connections">View all</button></div>${data.connections.map(connection=>`<div class="customer-health-row"><div><b>${esc(connection.name)}</b><small>${connection.name==='CRM'?(trial?'Observation only':esc(connection.provider)):connection.last_sync?'Last sync '+esc(date(connection.last_sync)):'No sync recorded yet'}</small></div><span class="${connection.connected && !connection.error?'connected':'pending'}">${connection.error?'Attention':connection.connected?'Connected':'Set up'}</span></div>`).join('')}</section>
            <section class="customer-panel"><div class="customer-panel-head"><h2>Needs attention</h2>${attention.length?`<span class="customer-tag warn">${attention.length}</span>`:''}</div>${attention.slice(0,3).map(([title,body,tab])=>`<button class="customer-action" data-go="${tab}"><span>!</span><span><b>${esc(title)}</b><small>${esc(body)}</small></span></button>`).join('') || '<p>Your account has no outstanding issues detected in this view.</p>'}</section>
            <section class="customer-panel"><h2>Quick actions</h2><button class="customer-action" id="customerInstall"><span>‹›</span><span><b>Install website tag</b><small>Add AdGuard to your website</small></span></button><button class="customer-action" id="customerExport"><span>↓</span><span><b>Export report</b><small>Download the selected brand’s data</small></span></button><button class="customer-action" data-go="support"><span>?</span><span><b>Get help</b><small>Support response target: ${data.support_hours}h</small></span></button></section>
            <section class="customer-panel customer-exposure"><p>Estimated junk exposure</p><strong>${money(data.estimated_exposure_inr)}</strong><p>Assumes ₹${data.exposure_benchmark_inr} per flagged lead.<br>Illustrative exposure, not confirmed savings.</p></section></aside></div>
            <footer class="customer-footer"><span>${portfolio?data.brands.length+' brands':esc(data.name)} · ${trial?'Observation only':'Quality monitoring'} · Updated ${new Date().toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit'})}</span><span>Clean leads. Confident growth.</span></footer>`;
        el('customerRange').value=days;el('customerRange').onchange=refreshOverview;el('customerRefresh').onclick=refreshOverview;
        el('customerOverview').querySelector('.customer-legend').innerHTML=legend;
        if(portfolio)el('customerOverview').querySelector('.customer-rail h2').textContent='Selected brand connections';
        if(el('customerPortfolioToggle'))el('customerPortfolioToggle').onclick=()=>{portfolio=!portfolio;brandPage=0;refreshOverview();};
        if(el('customerMoreBrands'))el('customerMoreBrands').onclick=()=>{brandPage=(brandPage+1)%Math.ceil(data.brands.length/5);renderOverview(data,days);};
        el('customerInstall').onclick=openScriptTagPanel;
        el('customerExport').onclick=()=>{if(portfolio){toast('Choose a brand before exporting its report.');return;}downloadReport(new URLSearchParams({workspace_id:data.workspace_id,days}));};
        el('customerOverview').querySelectorAll('[data-go]').forEach(button=>button.onclick=()=>switchTab(button.dataset.go));
        el('customerOverview').querySelectorAll('[data-brand]').forEach(row=>{const activate=()=>{portfolio=false;switchWorkspace(row.dataset.brand);};row.onclick=activate;row.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();activate();}};});
        const select=el('customerBrandSelect');select.innerHTML=workspaces.map(ws=>`<option value="${Number(ws.id)}">${esc(ws.display_name||'Workspace '+ws.id)}</option>`).join('');select.value=String(data.workspace_id);
        el('customerAddBrand').style.display=agency?'block':'none';
        const owned=workspaces.filter(ws=>ws.owner_email===selectedWorkspaceObj.owner_email).length;
        el('customerAddBrand').disabled=owned>=data.entitlements.workspace_limit;
        el('customerPlanDetail').innerHTML=`${trial?'Trial ends':'Plan expires'}: ${esc(date(data.expires_at))}<br>Audit reset: ${esc(date(quota.period_end))}${agency?`<br>${owned} / ${data.entitlements.workspace_limit} brand workspaces`:''}`;
        el('sidebarUpgradeBtn').textContent='Manage plan';
        if(el('customerConversionReport')){
            el('customerConversionReport').disabled=!data.entitlements.conversion_delivery;
            el('customerConversionReport').textContent=data.entitlements.conversion_delivery?'View signal dispatch counts':'Included with Pro & Agency';
        }
        const mode=el('setMode');if(mode){[...mode.options].forEach(option=>{if(option.value==='protect')option.disabled=trial;});if(trial)mode.value='monitor';}
        document.querySelectorAll('[onclick="syncExclusions()"],[onclick="runShieldScan()"],#shieldThreshold,#shieldMinLeads').forEach(control=>{control.disabled=trial || (!data.entitlements.audience_exclusions && control.getAttribute('onclick')==='syncExclusions()');});
    }
    function renderAccount(data){const a=data.allowance;el('customerAccountSummary').innerHTML=`<div class="customer-panel"><div class="customer-panel-head"><div><h2>Your plan &amp; account</h2><p>${esc(data.name)} · ${esc(data.entitlements.label)}</p></div><button class="btn btn-primary btn-sm" onclick="openUpgradeModal()">Manage plan</button></div><div class="customer-kpis">${stat('Audits this period',number(a.audits_used),a.audit_limit<0?'Unlimited allowance':number(a.audits_remaining)+' remaining')}${stat('Brand workspaces',number(data.entitlements.workspace_limit),'Plan workspace limit')}${stat('Ad platform logins',number(data.entitlements.connector_logins.google)+' + '+number(data.entitlements.connector_logins.meta),'Google + Meta, per workspace')}${stat('Support target',data.support_hours+'h','First response target')}</div><p style="margin-top:12px">${data.entitlements.observation_only?'Free Trial observes lead quality without taking protective actions.':'Conversion delivery and audience exclusions '+(data.entitlements.conversion_delivery?'are included; successful use depends on configured platform access.':'are available on Pro and Agency.')} ${data.entitlements.ai_scoring ? (data.ai_configured ? ' AI intent scoring is configured.' : ' AI intent scoring is included; provider configuration is pending.') : ''} Salesforce delivery and verification calls are not available.</p></div>`;}
    if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();
})();
