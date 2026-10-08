// Landing page behavior. API requests stay on the same origin.
function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
}
function safeVerifyLink(value) {
    const url = new URL(value, window.location.origin);
    return ['https:', 'http:'].includes(url.protocol) ? escapeHtml(url.href) : '#';
}
let dialogOpener = null;
function showDialog(id) {
    dialogOpener = document.activeElement;
    document.getElementById(id).style.display = 'flex';
    document.body.classList.add('modal-open');
    document.getElementById(id).querySelector('input, button, select, a').focus();
}
function hideDialog(id) {
    document.getElementById(id).style.display = 'none';
    document.body.classList.remove('modal-open');
    dialogOpener?.focus();
}
document.querySelectorAll('.modal-card').forEach((card, index) => {
    card.setAttribute('role', 'dialog');
    card.setAttribute('aria-modal', 'true');
    const heading = card.querySelector('h2, h3');
    if (heading) { heading.id ||= 'dialog-heading-' + index; card.setAttribute('aria-labelledby', heading.id); }
    card.querySelectorAll('label').forEach(label => {
        const control = label.parentElement.querySelector('input, select, textarea');
        if (control?.id && !label.querySelector('input')) label.htmlFor = control.id;
    });
    card.querySelector('.modal-close')?.setAttribute('aria-label', 'Close dialog');
});
document.querySelectorAll('[id$="Message"]').forEach(message => message.setAttribute('role', 'status'));
document.addEventListener('keydown', event => {
    const overlay = [...document.querySelectorAll('.modal-overlay')].find(el => el.style.display === 'flex');
    if (!overlay) return;
    if (event.key === 'Escape') { hideDialog(overlay.id); return; }
    if (event.key !== 'Tab') return;
    const controls = [...overlay.querySelectorAll('a[href], button, input, select, textarea, [tabindex="0"]')].filter(el => !el.disabled && el.getClientRects().length);
    const first = controls[0], last = controls.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
});
document.querySelectorAll('.faq-question').forEach((question, index) => {
    question.setAttribute('role', 'button');
    question.tabIndex = 0;
    question.setAttribute('aria-expanded', 'false');
    question.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); question.click(); }
    });
});

        // CONFIGURABLE BRAND & PRICING IDENTIFIERS
        const BRAND_NAME = 'AdGuard';
        const EXTRA_AI_CALL_PRICE = 4; // deprecated: extras now sold as prepaid packs (kept for calculator compat)
        const SHOW_CASE_STUDY = false; // Flag to toggle pilot case study component
        const RAZORPAY_MODE = 'none'; // 'live' for real payment checkout, 'none' or 'test' redirects to signup flow

        // Call verification extras now sold as prepaid packs (see pricing footnote) — no per-call billing

        // Toggle case study card based on flag
        const caseStudyCard = document.getElementById('pilotCaseStudyCard');
        if (caseStudyCard) {
            caseStudyCard.style.display = SHOW_CASE_STUDY ? 'block' : 'none';
        }

        // 1. DYNAMIC CALCULATOR (WASTED SPEND ESTIMATOR)
        function formatINR(val) {
            return '₹' + Math.round(val).toLocaleString('en-IN');
        }

        function calculateSavings() {
            const spend = parseFloat(document.getElementById('spendRange').value);
            const cpl = parseFloat(document.getElementById('cplRange').value);
            const junkPct = parseFloat(document.getElementById('junkRange').value) / 100;

            document.getElementById('spendValLabel').innerText = formatINR(spend);
            document.getElementById('cplValLabel').innerText = formatINR(cpl);
            document.getElementById('junkValLabel').innerText = Math.round(junkPct * 100) + '%';

            // Formulas from specifications:
            // 1. Spent on junk this month: spend * junk%
            const spentOnJunk = spend * junkPct;
            // 2. Junk leads chased: (spend / cpl) * junk%
            const totalLeads = spend / (cpl || 1);
            const junkLeads = totalLeads * junkPct;
            // 3. Real cost per genuine lead: CPL / (1 - junk%)
            const realCPL = cpl / Math.max(0.05, (1 - junkPct));
            // 4. Real cost per lead if junk drops 40%: CPL / (1 - junk% * 0.6)
            const reducedCPL = cpl / Math.max(0.05, (1 - (junkPct * 0.6)));

            document.getElementById('resSpentJunk').innerText = formatINR(spentOnJunk);
            document.getElementById('resJunkLeads').innerText = Math.round(junkLeads).toLocaleString('en-IN');
            document.getElementById('resRealCPL').innerText = formatINR(realCPL);
            document.getElementById('resReducedCPL').innerText = formatINR(reducedCPL);
        }

        // Initialize calculator on page load
        calculateSavings();

        // 2. FAQ ACCORDION TOGGLE
        function toggleFaq(el) {
            const item = el.parentElement;
            const isOpen = item.classList.contains('active');

            document.querySelectorAll('.faq-item').forEach(i => { i.classList.remove('active'); i.querySelector('.faq-question')?.setAttribute('aria-expanded', 'false'); });
            if (!isOpen) {
                item.classList.add('active');
                el.setAttribute('aria-expanded', 'true');
            }
        }

        // 3. MODAL CONTROLS & PLAN HANDLER
        // Country + dial code data (major digital-marketing markets, grouped by region)
        const COUNTRIES = [
            { name: 'India', dial: '+91', flag: '🇮🇳' },
            { name: 'United States', dial: '+1', flag: '🇺🇸' },
            { name: 'United Kingdom', dial: '+44', flag: '🇬🇧' },
            { name: 'United Arab Emirates', dial: '+971', flag: '🇦🇪' },
            { name: 'Saudi Arabia', dial: '+966', flag: '🇸🇦' },
            { name: 'Singapore', dial: '+65', flag: '🇸🇬' },
            { name: 'Australia', dial: '+61', flag: '🇦🇺' },
            { name: 'Canada', dial: '+1', flag: '🇨🇦' },
            { name: 'Germany', dial: '+49', flag: '🇩🇪' },
            { name: 'Netherlands', dial: '+31', flag: '🇳🇱' },
            { name: 'Ireland', dial: '+353', flag: '🇮🇪' },
            { name: 'France', dial: '+33', flag: '🇫🇷' },
            { name: 'Spain', dial: '+34', flag: '🇪🇸' },
            { name: 'Italy', dial: '+39', flag: '🇮🇹' },
            { name: 'Switzerland', dial: '+41', flag: '🇨🇭' },
            { name: 'Sweden', dial: '+46', flag: '🇸🇪' },
            { name: 'Norway', dial: '+47', flag: '🇳🇴' },
            { name: 'Denmark', dial: '+45', flag: '🇩🇰' },
            { name: 'Finland', dial: '+358', flag: '🇫🇮' },
            { name: 'Belgium', dial: '+32', flag: '🇧🇪' },
            { name: 'Austria', dial: '+43', flag: '🇦🇹' },
            { name: 'Poland', dial: '+48', flag: '🇵🇱' },
            { name: 'Portugal', dial: '+351', flag: '🇵🇹' },
            { name: 'Israel', dial: '+972', flag: '🇮🇱' },
            { name: 'Qatar', dial: '+974', flag: '🇶🇦' },
            { name: 'Kuwait', dial: '+965', flag: '🇰🇼' },
            { name: 'Bahrain', dial: '+973', flag: '🇧🇭' },
            { name: 'Oman', dial: '+968', flag: '🇴🇲' },
            { name: 'South Africa', dial: '+27', flag: '🇿🇦' },
            { name: 'Nigeria', dial: '+234', flag: '🇳🇬' },
            { name: 'Kenya', dial: '+254', flag: '🇰🇪' },
            { name: 'Egypt', dial: '+20', flag: '🇪🇬' },
            { name: 'Ghana', dial: '+233', flag: '🇬🇭' },
            { name: 'Indonesia', dial: '+62', flag: '🇮🇩' },
            { name: 'Malaysia', dial: '+60', flag: '🇲🇾' },
            { name: 'Philippines', dial: '+63', flag: '🇵🇭' },
            { name: 'Thailand', dial: '+66', flag: '🇹🇭' },
            { name: 'Vietnam', dial: '+84', flag: '🇻🇳' },
            { name: 'Japan', dial: '+81', flag: '🇯🇵' },
            { name: 'South Korea', dial: '+82', flag: '🇰🇷' },
            { name: 'Hong Kong', dial: '+852', flag: '🇭🇰' },
            { name: 'New Zealand', dial: '+64', flag: '🇳🇿' },
            { name: 'Brazil', dial: '+55', flag: '🇧🇷' },
            { name: 'Mexico', dial: '+52', flag: '🇲🇽' },
            { name: 'Argentina', dial: '+54', flag: '🇦🇷' },
            { name: 'Chile', dial: '+56', flag: '🇨🇱' },
            { name: 'Colombia', dial: '+57', flag: '🇨🇴' },
            { name: 'Bangladesh', dial: '+880', flag: '🇧🇩' },
            { name: 'Pakistan', dial: '+92', flag: '🇵🇰' },
            { name: 'Sri Lanka', dial: '+94', flag: '🇱🇰' },
            { name: 'Nepal', dial: '+977', flag: '🇳🇵' },
            { name: 'Turkey', dial: '+90', flag: '🇹🇷' },
            { name: 'Greece', dial: '+30', flag: '🇬🇷' },
            { name: 'Romania', dial: '+40', flag: '🇷🇴' },
            { name: 'Czechia', dial: '+420', flag: '🇨🇿' },
            { name: 'Ukraine', dial: '+380', flag: '🇺🇦' },
            { name: 'Other / Worldwide', dial: '', flag: '🌍' }
        ];

        function populateCountrySelectors(defDial) {
            const prefixes = ['su', 'sm', 'dm'];
            prefixes.forEach(p => {
                const dialSel = document.getElementById(p + 'DialCode');
                const countrySel = document.getElementById(p + 'Country');
                if (!dialSel || dialSel.options.length) return;
                COUNTRIES.forEach((c, i) => {
                    const o1 = document.createElement('option');
                    o1.value = i;
                    o1.textContent = (c.dial ? c.dial : 'N/A') + ' ' + c.flag;
                    dialSel.appendChild(o1);
                    const o2 = document.createElement('option');
                    o2.value = i;
                    o2.textContent = c.name;
                    countrySel.appendChild(o2);
                });
            });
            // Default: browser language hint, fallback India
            let defIdx = 0;
            const lang = (navigator.language || '').toLowerCase();
            const localeMatch = COUNTRIES.findIndex(c => lang.includes(c.name.toLowerCase().split(' ')[0]));
            const dialMatch = COUNTRIES.findIndex(c => c.dial === defDial);
            if (localeMatch >= 0) defIdx = localeMatch;
            else if (dialMatch >= 0) defIdx = dialMatch;
            prefixes.forEach(p => {
                const ds = document.getElementById(p + 'DialCode');
                const cs = document.getElementById(p + 'Country');
                if (ds) ds.value = String(defIdx);
                if (cs) { cs.value = String(defIdx); updateSpendCurrency(p); }
            });
        }

        function syncCountryFromDial(prefix) {
            const i = document.getElementById(prefix + 'DialCode').value;
            const cs = document.getElementById(prefix + 'Country');
            cs.value = i;
            updateSpendCurrency(prefix);
        }

        function syncDialFromCountry(prefix) {
            const i = document.getElementById(prefix + 'Country').value;
            document.getElementById(prefix + 'DialCode').value = i;
            updateSpendCurrency(prefix);
        }

        // Currency per country (digital-marketing majors; Others -> $ as world default)
        const CURRENCY_BY_COUNTRY = {
            'India': '₹', 'United States': '$', 'United Kingdom': '£', 'United Arab Emirates': 'AED ',
            'Saudi Arabia': 'SAR ', 'Singapore': 'S$', 'Australia': 'A$', 'Canada': 'C$',
            'Germany': '€', 'Netherlands': '€', 'Ireland': '€', 'France': '€', 'Spain': '€',
            'Italy': '€', 'Switzerland': 'CHF ', 'Sweden': 'kr ', 'Norway': 'kr ', 'Denmark': 'kr ',
            'Finland': '€', 'Belgium': '€', 'Austria': '€', 'Poland': 'zł ', 'Portugal': '€',
            'Israel': '₪ ', 'Qatar': 'QAR ', 'Kuwait': 'KWD ', 'Bahrain': 'BHD ', 'Oman': 'OMR ',
            'South Africa': 'R ', 'Nigeria': '₦ ', 'Kenya': 'KSh ', 'Egypt': 'E£ ', 'Ghana': '₵ ',
            'Indonesia': 'Rp ', 'Malaysia': 'RM ', 'Philippines': '₱ ', 'Thailand': '฿ ',
            'Vietnam': '₫ ', 'Japan': '¥', 'South Korea': '₩ ', 'Hong Kong': 'HK$ ',
            'New Zealand': 'NZ$ ', 'Brazil': 'R$ ', 'Mexico': 'Mex$ ', 'Argentina': 'AR$ ',
            'Chile': 'CLP$ ', 'Colombia': 'COL$ ', 'Bangladesh': '৳ ', 'Pakistan': 'Rs ',
            'Sri Lanka': 'Rs ', 'Nepal': 'Rs ', 'Turkey': '₺ ', 'Greece': '€', 'Romania': 'lei ',
            'Czechia': 'Kč ', 'Ukraine': '₴ ', 'Other / Worldwide': '$'
        };
        // Spend bands in local currency, scaled by rough FX vs INR (INR band: 1-5L, 5-15L, 15-50L, 50L+)
        const FX_SCALE = {
            'India': 1, 'United States': 0.012, 'United Kingdom': 0.0095, 'United Arab Emirates': 0.044,
            'Saudi Arabia': 0.045, 'Singapore': 0.016, 'Australia': 0.018, 'Canada': 0.016,
            'Other / Worldwide': 0.012
        };
        function spendBands(country) {
            const sym = CURRENCY_BY_COUNTRY[country] || '$';
            const s = FX_SCALE[country];
            const fmt = n => Math.round(n).toLocaleString('en-IN').replace(/,/g, ',');
            if (!s) {
                // No FX mapping: generic relative bands with symbol
                return [
                    { v: '1', label: sym + fmt(25000) + ' – ' + sym + fmt(125000) },
                    { v: '2', label: sym + fmt(125000) + ' – ' + sym + fmt(375000) },
                    { v: '3', label: sym + fmt(375000) + ' – ' + sym + fmt(1250000) },
                    { v: '4', label: sym + fmt(1250000) + '+' }
                ];
            }
            return [
                { v: '1', label: sym + fmt(100000 * s) + ' – ' + sym + fmt(500000 * s) },
                { v: '2', label: sym + fmt(500000 * s) + ' – ' + sym + fmt(1500000 * s) },
                { v: '3', label: sym + fmt(1500000 * s) + ' – ' + sym + fmt(5000000 * s) },
                { v: '4', label: sym + fmt(5000000 * s) + '+' }
            ];
        }

        function updateSpendCurrency(prefix) {
            const cs = document.getElementById(prefix + 'Country');
            const spendSel = document.getElementById(prefix + 'Spend');
            const curSpan = document.querySelector('[data-currency-for="' + prefix + '"]');
            if (!cs || !spendSel) return;
            const idx = parseInt(cs.value, 10);
            const countryObj = COUNTRIES[idx] || COUNTRIES[0];
            if (curSpan) curSpan.textContent = '(' + (CURRENCY_BY_COUNTRY[countryObj.name] || '$') + ')';
            const selected = spendSel.value;
            spendSel.innerHTML = '<option value="" disabled>Select</option>' +
                spendBands(countryObj.name).map(b => '<option value="' + b.v + '">' + b.label + '</option>').join('');
            spendSel.value = selected || '';
        }

        // Timezone -> dial-code guess for default selection
        (function guessCountry() {
            const tzMap = [
                { tz: 'Kolkata', dial: '+91' }, { tz: 'New_York', dial: '+1' }, { tz: 'Chicago', dial: '+1' },
                { tz: 'Los_Angeles', dial: '+1' }, { tz: 'Toronto', dial: '+1' }, { tz: 'London', dial: '+44' },
                { tz: 'Dubai', dial: '+971' }, { tz: 'Riyadh', dial: '+966' }, { tz: 'Singapore', dial: '+65' },
                { tz: 'Sydney', dial: '+61' }, { tz: 'Berlin', dial: '+49' }, { tz: 'Amsterdam', dial: '+31' },
                { tz: 'Tokyo', dial: '+81' }, { tz: 'Seoul', dial: '+82' }, { tz: 'Sao_Paulo', dial: '+55' }
            ];
            let defDial = '+91';
            try {
                const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || '';
                const hit = tzMap.find(m => tz.includes(m.tz));
                if (hit) defDial = hit.dial;
            } catch (e) {}
            populateCountrySelectors(defDial);
        })();

        function openSignupModal() {
            if (!window.__fromPlanSelect) pendingPaidPlan = '';
            applyPendingPlanBranding();
            showDialog('signupModal');
        }

        function closeSignupModal() {
            hideDialog('signupModal');
        }

        const PLAN_BRANDING = {
            starter: { chip: 'STARTER PLAN — ₹4,999/mo', heading: 'Get started with Starter', note: 'Starter plan (1,000 leads/mo) will be activated on your workspace — payment is bypassed during BETA testing.' },
            pro: { chip: 'PRO PLAN — ₹14,999/mo', heading: 'Get started with Pro', note: 'Pro plan (5,000 leads/mo) will be activated on your workspace — payment is bypassed during BETA testing.' },
            agency: { chip: 'AGENCY PLAN — ₹39,999/mo', heading: 'Get started with Agency', note: 'Agency plan (10 brand workspaces) will be activated on your workspace — payment is bypassed during BETA testing.' }
        };

        function applyPendingPlanBranding() {
            const b = PLAN_BRANDING[pendingPaidPlan];
            const chip = document.getElementById('suPlanChip');
            const heading = document.getElementById('suPlanHeading');
            const note = document.getElementById('suPlanNote');
            const btn = document.getElementById('suSubmitBtn');
            if (b) {
                chip.textContent = b.chip;
                heading.textContent = b.heading;
                note.textContent = b.note;
                note.style.display = 'block';
                btn.textContent = 'CONTINUE →';
            } else {
                chip.textContent = 'Start My Free Trial';
                heading.textContent = 'Create your AdGuard account';
                note.style.display = 'none';
                btn.textContent = 'START MY FREE TRIAL';
            }
        }

        // Paid plan chosen from the pricing table flows into signup (payment bypassed in beta)
        let pendingPaidPlan = '';

        // Shared: collect identical fields from any modal (su/sm/dm prefixes)
        function collectForm(prefix) {
            const name = document.getElementById(prefix + 'Name').value.trim();
            const company = document.getElementById(prefix + 'Company').value.trim();
            const email = document.getElementById(prefix + 'Email').value.trim().toLowerCase();
            const phoneDigits = document.getElementById(prefix + 'Phone').value.replace(/\D/g, '');
            const idx = parseInt(document.getElementById(prefix + 'Country').value, 10);
            const countryObj = COUNTRIES[idx] || COUNTRIES[0];
            const fullPhone = (countryObj.dial ? countryObj.dial : '') + phoneDigits;
            const industry = document.getElementById(prefix + 'Industry').value;
            const channels = document.getElementById(prefix + 'Channels') ? document.getElementById(prefix + 'Channels').value : '';
            const spendBand = document.getElementById(prefix + 'Spend') ? document.getElementById(prefix + 'Spend').value : '';
            return {
                full_name: name, company_name: company, email: email,
                phone: fullPhone, country: countryObj.name, industry: industry,
                channels: channels, monthly_ad_spend_band: spendBand,
                currency: CURRENCY_BY_COUNTRY[countryObj.name] || '$',
                selected_plan: prefix === 'su' ? (pendingPaidPlan || 'trial') : 'trial'
            };
        }

        function commonError(prefix, err, btn, originalText) {
            const msg = document.getElementById(prefix + 'Message');
            msg.style.color = '#fca5a5';
            msg.innerText = err.message + '  ·  Need help? 80509 97977';
            btn.innerText = originalText;
            btn.disabled = false;
        }

        async function handleSignupSubmit(e) {
            e.preventDefault();
            const btn = document.getElementById('suSubmitBtn');
            const msg = document.getElementById('suMessage');
            const original = btn.innerText;
            btn.innerText = 'Creating Account...';
            btn.disabled = true;
            msg.innerText = '';
            const d = collectForm('su');
            if (!document.getElementById('suTos').checked) { commonError('su', { message: 'Please accept the Terms of Service.' }, btn, original); return; }
            d.tos_accepted = true;

            try {
                const res = await fetch('/api/auth/signup-subscriber', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(d)
                });
                const data = await res.json();
                if (!res.ok) {
                    if (res.status === 400 && /already exists/i.test(data.detail || '')) {
                        msg.style.color = '#fbbf24';
                        msg.innerHTML = '<b>You already have an AdGuard account with this email.</b><br>' +
                            '<a href="/adguard/login?email=' + encodeURIComponent(d.email) + '" style="color:#34d399;font-weight:700;">Sign in instead →</a><br>' +
                            '<span style="font-size:11.5px;">Forgot your password? Call us at 80509 97977 to reset.</span>';
                        btn.innerText = original;
                        btn.disabled = false;
                        return;
                    }
                    throw new Error(data.detail || 'Signup failed');
                }

                btn.innerText = '✓ Account Created';
                msg.style.color = '#34d399';
                const planLabel = { starter: 'Starter', pro: 'Pro', agency: 'Agency' }[pendingPaidPlan];
                if (data.email_sent) {
                    msg.innerHTML = '<b>📧 Verify & Set Password link emailed to ' + escapeHtml(d.email) + '</b><br>' +
                        (planLabel ? '<b>' + planLabel + ' plan will be active when you verify.</b> Payment bypassed in BETA.<br>' : '') +
                        'It expires in 60 minutes. Check spam if you don\'t see it.<br>' +
                        'Customer Care: 80509 97977 · shekhar.chlear@gmail.com';
                } else if (data.verify_link) {
                    msg.innerHTML = '<b>Email failed - use this verify link directly:</b><br>' +
                        '<a href="' + safeVerifyLink(data.verify_link) + '" style="color:#34d399;word-break:break-all;">Set your password now →</a>';
                } else {
                    msg.innerHTML = 'Account created. ' + escapeHtml(data.email_error || '');
                }
                setTimeout(() => {
                    closeSignupModal();
                    btn.innerText = original;
                    btn.disabled = false;
                    msg.innerText = '';
                }, 9000);
            } catch (err) {
                commonError('su', err, btn, original);
            }
        }

        function openShadowModeModal() {
            showDialog('shadowModal');
        }

        function closeShadowModeModal() {
            hideDialog('shadowModal');
        }

        function openDemoModal() {
            showDialog('demoModal');
        }

        function closeDemoModal() {
            hideDialog('demoModal');
        }

        function handlePlanSelect(planKey) {
            if (planKey === 'shadow_mode') { openShadowModeModal(); return; }
            if (RAZORPAY_MODE === 'live') {
                // Live Razorpay integration when live mode is configured
                window.location.href = '/adguard/subscribe?plan=' + encodeURIComponent(planKey);
                return;
            }
            // Beta/test mode: payment step is bypassed. Paid plans go through the
            // signup form with the plan remembered (applied on workspace activation).
            pendingPaidPlan = planKey;
            window.__fromPlanSelect = true;
            openSignupModal();
            window.__fromPlanSelect = false;
        }

        // 4. SIGNUP / DEMO HANDLERS — all three forms share identical fields
        // Shadow Mode Modal = same signup API (trial), fields identical
        async function handleShadowSignup(e) {
            e.preventDefault();
            const btn = document.getElementById('smSubmitBtn');
            const msg = document.getElementById('smMessage');
            const original = btn.innerText;
            btn.innerText = 'Creating Shadow Account...';
            btn.disabled = true;
            msg.innerText = '';
            const d = collectForm('sm');
            if (!document.getElementById('smTos').checked) { commonError('sm', { message: 'Please accept the Terms of Service.' }, btn, original); return; }
            d.tos_accepted = true;

            try {
                const res = await fetch('/api/auth/signup-subscriber', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(d)
                });
                const data = await res.json();
                if (!res.ok) {
                    if (res.status === 400 && /already exists/i.test(data.detail || '')) {
                        msg.style.color = '#fbbf24';
                        msg.innerHTML = '<b>You already have an AdGuard account with this email.</b><br>' +
                            '<a href="/adguard/login?email=' + encodeURIComponent(d.email) + '" style="color:#34d399;font-weight:700;">Sign in instead →</a><br>' +
                            '<span style="font-size:11.5px;">Forgot your password? Call us at 80509 97977 to reset.</span>';
                        btn.innerText = original;
                        btn.disabled = false;
                        return;
                    }
                    throw new Error(data.detail || 'Signup failed');
                }

                btn.innerText = '✓ Account Created';
                msg.style.color = '#34d399';
                if (data.email_sent) {
                    msg.innerHTML = '<b>📧 Verify & Set Password link emailed to ' + escapeHtml(d.email) + '</b><br>' +
                        'It expires in 60 minutes. Shadow Mode (14 days, 300 leads) starts once verified.';
                } else if (data.verify_link) {
                    msg.innerHTML = '<b>Email failed - use this verify link directly:</b><br>' +
                        '<a href="' + safeVerifyLink(data.verify_link) + '" style="color:#34d399;word-break:break-all;">Set your password now →</a>';
                } else {
                    msg.innerHTML = 'Account created. ' + escapeHtml(data.email_error || '');
                }
                setTimeout(() => {
                    closeShadowModeModal();
                    btn.innerText = original;
                    btn.disabled = false;
                    msg.innerText = '';
                }, 9000);
            } catch (err) {
                commonError('sm', err, btn, original);
            }
        }

        // Demo booking = captured through the same signup API; team follows up to schedule
        async function handleDemoBooking(e) {
            e.preventDefault();
            const btn = document.getElementById('dmSubmitBtn');
            const msg = document.getElementById('dmMessage');
            const original = btn.innerText;
            btn.innerText = 'Booking Demo...';
            btn.disabled = true;
            msg.innerText = '';
            const d = collectForm('dm');
            d.demo_request = true;
            if (!document.getElementById('dmTos').checked) { commonError('dm', { message: 'Please accept the Terms of Service.' }, btn, original); return; }
            d.tos_accepted = true;

            try {
                const res = await fetch('/api/auth/signup-subscriber', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(d)
                });
                const data = await res.json();
                if (!res.ok) {
                    if (res.status === 400 && /already exists/i.test(data.detail || '')) {
                        msg.style.color = '#fbbf24';
                        msg.innerHTML = '<b>You already have an AdGuard account with this email.</b><br>' +
                            '<a href="/adguard/login?email=' + encodeURIComponent(d.email) + '" style="color:#34d399;font-weight:700;">Sign in instead →</a> — or call 80509 97977 to arrange a demo.';
                        btn.innerText = original;
                        btn.disabled = false;
                        return;
                    }
                    throw new Error(data.detail || 'Demo request failed');
                }

                btn.innerText = '✓ Demo Requested';
                msg.style.color = '#34d399';
                msg.innerHTML = '<b>Thank you! Our growth engineer will connect within 15 minutes</b> on ' + escapeHtml(d.phone) + '.';
                setTimeout(() => {
                    closeDemoModal();
                    btn.innerText = original;
                    btn.disabled = false;
                    msg.innerText = '';
                }, 9000);
            } catch (err) {
                commonError('dm', err, btn, original);
            }
        }

        // 5. LIVE STREAM DEMO ANIMATION (Looping every 8s)
        let currentActiveCard = 0;
        const cards = [
            document.getElementById('streamCard1'),
            document.getElementById('streamCard2'),
            document.getElementById('streamCard3')
        ];

        function cycleStreamCards() {
            if (!cards[0]) return;
            // Highlight active card subtly
            cards.forEach((card, idx) => {
                if (idx === currentActiveCard) {
                    card.style.transform = 'scale(1.01)';
                    card.style.boxShadow = '0 6px 20px rgba(0,0,0,0.5)';
                } else {
                    card.style.transform = 'scale(1)';
                    card.style.boxShadow = 'none';
                }
            });
            currentActiveCard = (currentActiveCard + 1) % cards.length;
        }

        if (!window.matchMedia('(prefers-reduced-motion: reduce)').matches) setInterval(cycleStreamCards, 8000);

        // Give visitors control over the illustrative comparison animation.
        const demoAnimation = document.querySelector('.dashboard-proof-wrap');
        const demoAnimationToggle = document.getElementById('demoAnimationToggle');

        if (demoAnimation && demoAnimationToggle) {
            const demoToggleLabel = demoAnimationToggle.querySelector('.demo-toggle-label');
            const demoPauseIcon = demoAnimationToggle.querySelector('.demo-pause-icon');
            const demoPlayIcon = demoAnimationToggle.querySelector('.demo-play-icon');

            demoAnimationToggle.addEventListener('click', () => {
                const isPaused = demoAnimation.classList.toggle('is-paused');
                demoAnimationToggle.setAttribute('aria-pressed', String(isPaused));
                demoAnimationToggle.setAttribute('aria-label', isPaused ? 'Play animation' : 'Pause animation');
                demoToggleLabel.textContent = isPaused ? 'Play animation' : 'Pause animation';
                demoPauseIcon.hidden = isPaused;
                demoPlayIcon.hidden = !isPaused;
            });
        }

        // Keep the fixed mobile CTA out of the hero until its primary CTA scrolls away.
        const mobileStickyBar = document.querySelector('.mobile-sticky-bar');
        const heroCtas = document.querySelector('.hero-ctas');
        const mobileStickyButton = mobileStickyBar?.querySelector('button');

        if (mobileStickyBar && heroCtas && mobileStickyButton && 'IntersectionObserver' in window) {
            const heroCtaObserver = new IntersectionObserver(([entry]) => {
                const shouldShow = entry.boundingClientRect.bottom < 0 && window.innerWidth <= 768;
                mobileStickyBar.classList.toggle('is-visible', shouldShow);
                mobileStickyBar.setAttribute('aria-hidden', String(!shouldShow));
                mobileStickyButton.tabIndex = shouldShow ? 0 : -1;
            }, { threshold: 0 });

            heroCtaObserver.observe(heroCtas);
            window.addEventListener('resize', () => {
                if (window.innerWidth > 768) {
                    mobileStickyBar.classList.remove('is-visible');
                    mobileStickyBar.setAttribute('aria-hidden', 'true');
                    mobileStickyButton.tabIndex = -1;
                }
            });
        }
