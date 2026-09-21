/**
 * AdGuard Pre-Submit Interceptor Tag v1.0
 * Lightweight (<15KB), async telemetry collection and pre-submit gatekeeper.
 */
(function() {
    'use strict';

    var SCRIPT_TAG = document.currentScript || (function() {
        var scripts = document.getElementsByTagName('script');
        return scripts[scripts.length - 1];
    })();

    var ACCOUNT_ID = SCRIPT_TAG ? SCRIPT_TAG.getAttribute('data-account-id') || SCRIPT_TAG.getAttribute('data-account') : null;
    var BASE_URL = SCRIPT_TAG ? SCRIPT_TAG.src.replace(/\/static\/adguard\.js.*$/, '').replace(/\/api\/v1\/tag\/adguard\.js.*$/, '') : '';
    if (!BASE_URL || BASE_URL.indexOf('http') !== 0) {
        BASE_URL = window.location.origin;
    }

    var SESSION_KEY = '_adguard_sid';
    var sessionUuid = sessionStorage.getItem(SESSION_KEY);
    if (!sessionUuid) {
        sessionUuid = 'ag_' + Math.random().toString(36).substring(2, 15) + Math.random().toString(36).substring(2, 15) + '_' + Date.now();
        sessionStorage.setItem(SESSION_KEY, sessionUuid);
    }

    var startTime = Date.now();
    var firstInteractionTime = 0;
    var mouseMovesCount = 0;
    var keystrokesCount = 0;
    var pasteEventsCount = 0;

    // Track interactions
    window.addEventListener('mousemove', function onFirstMove() {
        mouseMovesCount++;
        if (!firstInteractionTime) firstInteractionTime = Date.now();
    }, { passive: true });

    window.addEventListener('keydown', function() {
        keystrokesCount++;
        if (!firstInteractionTime) firstInteractionTime = Date.now();
    }, { passive: true });

    window.addEventListener('paste', function() {
        pasteEventsCount++;
    }, { passive: true });

    // Fingerprint collection
    function getCanvasHash() {
        try {
            var canvas = document.createElement('canvas');
            var ctx = canvas.getContext('2d');
            canvas.width = 200;
            canvas.height = 50;
            ctx.textBaseline = 'top';
            ctx.font = "14px 'Arial'";
            ctx.fillStyle = '#f60';
            ctx.fillRect(125, 1, 62, 20);
            ctx.fillStyle = '#069';
            ctx.fillText('AdGuard-Device-FP-1.0', 2, 15);
            ctx.fillStyle = 'rgba(102, 204, 0, 0.7)';
            ctx.fillText('AdGuard-Device-FP-1.0', 4, 17);
            var str = canvas.toDataURL();
            var hash = 0;
            for (var i = 0; i < str.length; i++) {
                hash = ((hash << 5) - hash) + str.charCodeAt(i);
                hash |= 0;
            }
            return hash.toString(16);
        } catch (e) {
            return 'canvas_err';
        }
    }

    function getAutomationSignals() {
        var isWebdriver = !!navigator.webdriver;
        var hasCdp = !!(window.cdc_adoQzda3378572001_Array || window.document.__selenium_unwrapped || window._phantom || window.__nightmare);
        var isHeadless = /HeadlessChrome|PhantomJS|Electron/i.test(navigator.userAgent) || (!navigator.plugins || navigator.plugins.length === 0);
        return {
            webdriver: isWebdriver,
            cdp: hasCdp,
            headless: isHeadless,
            languages: navigator.languages ? navigator.languages.join(',') : navigator.language,
            screen: window.screen.width + 'x' + window.screen.height + 'x' + window.screen.colorDepth,
            timezone: Intl && Intl.DateTimeFormat ? Intl.DateTimeFormat().resolvedOptions().timeZone : ''
        };
    }

    function getUrlParams() {
        var params = {};
        try {
            var search = window.location.search.substring(1);
            if (search) {
                var pairs = search.split('&');
                for (var i = 0; i < pairs.length; i++) {
                    var pair = pairs[i].split('=');
                    params[decodeURIComponent(pair[0])] = decodeURIComponent(pair[1] || '');
                }
            }
        } catch (e) {}
        return params;
    }

    // Register session payload
    function initSession() {
        var urlParams = getUrlParams();
        var autoSignals = getAutomationSignals();
        var canvasHash = getCanvasHash();

        var sessionPayload = {
            session_uuid: sessionUuid,
            adguard_account_id: ACCOUNT_ID,
            fingerprint_hash: canvasHash + '_' + autoSignals.screen + '_' + autoSignals.timezone,
            gclid: urlParams.gclid || null,
            fbclid: urlParams.fbclid || null,
            utm_source: urlParams.utm_source || null,
            utm_medium: urlParams.utm_medium || null,
            utm_campaign: urlParams.utm_campaign || null,
            utm_content: urlParams.utm_content || null,
            utm_term: urlParams.utm_term || null,
            page_url: window.location.href,
            referrer: document.referrer || '',
            device_info: autoSignals,
            behaviour: {
                time_on_page_ms: 0,
                mouse_moves_count: 0
            }
        };

        try {
            var xhr = new XMLHttpRequest();
            xhr.open('POST', BASE_URL + '/api/v1/tag/session', true);
            xhr.setRequestHeader('Content-Type', 'application/json');
            xhr.send(JSON.stringify(sessionPayload));
        } catch (e) {}
    }

    if (document.readyState === 'complete' || document.readyState === 'interactive') {
        initSession();
    } else {
        document.addEventListener('DOMContentLoaded', initSession);
    }

    // OTP Modal UI
    function showOtpModal(phone, onVerified, onCancel) {
        var modalId = '_adguard_otp_modal';
        var existing = document.getElementById(modalId);
        if (existing) existing.remove();

        var overlay = document.createElement('div');
        overlay.id = modalId;
        overlay.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.75);z-index:999999;display:flex;align-items:center;justify-content:center;font-family:system-ui,-apple-system,sans-serif;';

        overlay.innerHTML = '<div style="background:#fff;border-radius:16px;padding:28px;max-width:380px;width:90%;box-shadow:0 20px 40px rgba(0,0,0,0.3);text-align:center;color:#1e293b;">' +
            '<div style="font-size:32px;margin-bottom:8px;">🔒</div>' +
            '<h3 style="margin:0 0 8px;font-size:18px;font-weight:700;color:#0f172a;">Quick Verification</h3>' +
            '<p style="font-size:13px;color:#64748b;margin:0 0 20px;">We sent a 6-digit verification code to <b>' + (phone || 'your phone') + '</b> to confirm your enquiry.</p>' +
            '<input type="text" id="_ag_otp_input" placeholder="Enter 6-digit OTP" maxlength="6" style="width:100%;box-sizing:border-box;padding:12px;font-size:20px;letter-spacing:6px;text-align:center;border:2px solid #cbd5e1;border-radius:8px;outline:none;margin-bottom:16px;font-weight:700;">' +
            '<div id="_ag_otp_err" style="color:#ef4444;font-size:12px;margin-bottom:12px;display:none;">Invalid code. Please try again.</div>' +
            '<button id="_ag_otp_verify_btn" style="width:100%;background:#0284c7;color:#fff;border:none;padding:12px;font-size:15px;font-weight:600;border-radius:8px;cursor:pointer;margin-bottom:8px;">Verify & Submit</button>' +
            '<button id="_ag_otp_cancel_btn" style="background:none;border:none;color:#94a3b8;font-size:12px;cursor:pointer;">Cancel</button>' +
            '</div>';

        document.body.appendChild(overlay);

        var input = document.getElementById('_ag_otp_input');
        var verifyBtn = document.getElementById('_ag_otp_verify_btn');
        var cancelBtn = document.getElementById('_ag_otp_cancel_btn');
        var errBox = document.getElementById('_ag_otp_err');

        input.focus();

        verifyBtn.onclick = function() {
            var code = input.value.trim();
            if (code.length < 4) {
                errBox.style.display = 'block';
                return;
            }
            verifyBtn.disabled = true;
            verifyBtn.innerText = 'Verifying...';

            var xhr = new XMLHttpRequest();
            xhr.open('POST', BASE_URL + '/api/v1/tag/otp', true);
            xhr.setRequestHeader('Content-Type', 'application/json');
            xhr.onreadystatechange = function() {
                if (xhr.readyState === 4) {
                    if (xhr.status === 200) {
                        try {
                            var resp = JSON.parse(xhr.responseText);
                            if (resp.verified) {
                                overlay.remove();
                                onVerified();
                                return;
                            }
                        } catch (e) {}
                    }
                    verifyBtn.disabled = false;
                    verifyBtn.innerText = 'Verify & Submit';
                    errBox.style.display = 'block';
                }
            };
            xhr.send(JSON.stringify({
                session_uuid: sessionUuid,
                otp_code: code
            }));
        };

        cancelBtn.onclick = function() {
            overlay.remove();
            if (onCancel) onCancel();
        };
    }

    // Form Submissions Hook
    function attachFormInterceptors() {
        var forms = document.querySelectorAll('form');
        forms.forEach(function(form) {
            if (form.getAttribute('data-adguard-attached')) return;
            form.setAttribute('data-adguard-attached', 'true');

            form.addEventListener('submit', function(evt) {
                if (form.getAttribute('data-adguard-bypassed') === 'true') {
                    return; // native submission allowed
                }

                evt.preventDefault();
                var timeOnPage = Date.now() - startTime;
                var timeToFill = firstInteractionTime ? (Date.now() - firstInteractionTime) : timeOnPage;

                // Extract fields
                var formDataObj = {};
                var elements = form.elements;
                for (var i = 0; i < elements.length; i++) {
                    var el = elements[i];
                    if (el.name) {
                        formDataObj[el.name] = el.value;
                    }
                }

                var auto = getAutomationSignals();
                var verdictPayload = {
                    session_uuid: sessionUuid,
                    adguard_account_id: ACCOUNT_ID,
                    full_name: formDataObj.name || formDataObj.full_name || formDataObj.user_name || formDataObj.fname || '',
                    email: formDataObj.email || formDataObj.user_email || formDataObj.email_address || '',
                    phone: formDataObj.phone || formDataObj.mobile || formDataObj.phone_number || formDataObj.contact || '',
                    city: formDataObj.city || formDataObj.town || '',
                    state: formDataObj.state || '',
                    message: formDataObj.message || formDataObj.comments || formDataObj.remarks || '',
                    webdriver: auto.webdriver,
                    headless: auto.headless,
                    time_on_page_ms: timeOnPage,
                    time_to_fill_ms: timeToFill,
                    mouse_moves_count: mouseMovesCount,
                    keystrokes_count: keystrokesCount
                };

                var hasTimedOut = false;
                var failOpenTimer = setTimeout(function() {
                    hasTimedOut = true;
                    // Fail open after 2.0s
                    form.setAttribute('data-adguard-bypassed', 'true');
                    form.submit();
                }, 2000);

                var xhr = new XMLHttpRequest();
                xhr.open('POST', BASE_URL + '/api/v1/tag/verdict', true);
                xhr.setRequestHeader('Content-Type', 'application/json');
                xhr.onreadystatechange = function() {
                    if (hasTimedOut || xhr.readyState !== 4) return;
                    clearTimeout(failOpenTimer);

                    var outcome = { verdict: 'green' };
                    if (xhr.status === 200) {
                        try {
                            outcome = JSON.parse(xhr.responseText);
                        } catch (e) {}
                    }

                    if (outcome.verdict === 'grey') {
                        // Request OTP step-up
                        showOtpModal(verdictPayload.phone, function onVerified() {
                            form.setAttribute('data-adguard-bypassed', 'true');
                            form.submit();
                        }, function onCancel() {
                            // User cancelled OTP
                        });
                    } else if (outcome.verdict === 'red') {
                        // Silent quarantine: trigger custom reject pixel event if Meta pixel is present
                        try {
                            if (window.fbq) {
                                window.fbq('trackCustom', 'adguard_reject');
                            }
                        } catch (e) {}
                        // Submit silently to simulate regular success so bots do not adapt
                        form.setAttribute('data-adguard-bypassed', 'true');
                        form.submit();
                    } else {
                        // Green: proceed normally
                        form.setAttribute('data-adguard-bypassed', 'true');
                        form.submit();
                    }
                };
                xhr.send(JSON.stringify(verdictPayload));
            });
        });
    }

    if (document.readyState === 'complete' || document.readyState === 'interactive') {
        attachFormInterceptors();
    } else {
        document.addEventListener('DOMContentLoaded', attachFormInterceptors);
    }
})();
