"""
One-time Dubizzle login setup script using Nova Act.

Option A — run locally on Mac (recommended for first-time / Imperva-blocked IPs):

    LOCAL_LOGIN=1 DUBIZZLE_EMAIL=you@example.com DUBIZZLE_PASS=secret \\
        python -m project.dubizzle_login

    A real visible browser opens. Log in normally, then copy the profile to the pod:

        kubectl cp ~/dubizzle-profile <worker-pod>:/data/novasell/nova-act-profile

Option B — run directly inside the worker pod (requires IP not blocked by Imperva):

    kubectl exec -it <worker-pod> -- python -m project.dubizzle_login

Cookies are saved to user_data_dir and reused by subsequent automation runs.
"""

import os
import time

from nova_act import NovaAct, workflow as nova_workflow
from nova_act.tools.human.interface.human_input_callback import (
    ApprovalResponse,
    HumanInputCallbacksBase,
    UiTakeoverResponse,
)
from project.constants import NOVA_ACT_WORKFLOW_DEFINITION, NOVA_ACT_MODEL_ID


class DubizzleHITLCallbacks(HumanInputCallbacksBase):
    """Handles any CAPTCHAs or prompts during Dubizzle login."""

    def __init__(self, nova_ref):
        super().__init__()
        self._nova_ref = nova_ref

    def approve(self, message: str) -> ApprovalResponse:
        print(f"[HITL] Approval requested: {message} -> auto-approving")
        return ApprovalResponse.YES

    def _detect_captcha_type(self, page):
        """Detect CAPTCHA type on the page (Turnstile, hCaptcha, reCAPTCHA, Arkose)."""
        return page.evaluate("""() => {
            // Check for Cloudflare Turnstile
            const turnstileDiv = document.querySelector('.cf-turnstile, [data-sitekey][class*="turnstile"]');
            if (turnstileDiv) {
                return { type: 'turnstile', siteKey: turnstileDiv.getAttribute('data-sitekey') };
            }
            const turnstileIframe = document.querySelector('iframe[src*="challenges.cloudflare.com"]');
            if (turnstileIframe) {
                const match = turnstileIframe.src.match(/[?&]sitekey=([^&]+)/);
                return { type: 'turnstile', siteKey: match ? match[1] : null };
            }
            // Turnstile also injects a script tag
            const turnstileScript = document.querySelector('script[src*="turnstile"]');
            if (turnstileScript) {
                // Try to find the sitekey from any cf-turnstile container
                const container = document.querySelector('[data-sitekey]');
                return { type: 'turnstile', siteKey: container ? container.getAttribute('data-sitekey') : null };
            }

            // Check for hCaptcha
            const hcaptchaIframe = document.querySelector('iframe[src*="hcaptcha"]');
            if (hcaptchaIframe) {
                const match = hcaptchaIframe.src.match(/[?&]sitekey=([^&]+)/);
                return { type: 'hcaptcha', siteKey: match ? match[1] : null };
            }
            const hcaptchaDiv = document.querySelector('.h-captcha, [data-hcaptcha-widget-id]');
            if (hcaptchaDiv) {
                return { type: 'hcaptcha', siteKey: hcaptchaDiv.getAttribute('data-sitekey') };
            }
            const hcaptchaScript = document.querySelector('script[src*="hcaptcha"]');
            if (hcaptchaScript) {
                return { type: 'hcaptcha', siteKey: null };
            }
            const hcaptchaTextarea = document.querySelector('textarea[name="h-captcha-response"], [name="g-recaptcha-response"][data-hcaptcha]');
            if (hcaptchaTextarea) {
                return { type: 'hcaptcha', siteKey: null };
            }

            // Check for Arkose Labs / FunCaptcha
            const arkoseIframe = document.querySelector('iframe[src*="arkoselabs"], iframe[src*="funcaptcha"]');
            if (arkoseIframe) {
                const match = arkoseIframe.src.match(/[?&]pkey=([^&]+)/);
                return { type: 'funcaptcha', publicKey: match ? match[1] : null, src: arkoseIframe.src };
            }

            // Check for reCAPTCHA
            const recaptchaIframe = document.querySelector('iframe[src*="recaptcha"]');
            if (recaptchaIframe) {
                const match = recaptchaIframe.src.match(/[?&]k=([^&]+)/);
                return { type: 'recaptcha', siteKey: match ? match[1] : null };
            }
            const recaptchaDiv = document.querySelector('.g-recaptcha');
            if (recaptchaDiv) {
                return { type: 'recaptcha', siteKey: recaptchaDiv.getAttribute('data-sitekey') };
            }

            // Check for Imperva / Incapsula
            const incapsulaIframe = document.querySelector('iframe[src*="_Incapsula_Resource"], iframe[src*="incapsula"]');
            if (incapsulaIframe) {
                return { type: 'incapsula', src: incapsulaIframe.src };
            }
            // Incapsula also injects a script
            const incapsulaScript = document.querySelector('script[src*="_Incapsula_Resource"]');
            if (incapsulaScript) {
                return { type: 'incapsula', src: incapsulaScript.src };
            }

            // Last resort: dump all iframes and [data-sitekey] elements for debugging
            const iframes = Array.from(document.querySelectorAll('iframe')).map(f => f.src);
            const sitekeyEls = Array.from(document.querySelectorAll('[data-sitekey]')).map(el => ({
                tag: el.tagName, cls: el.className, sitekey: el.getAttribute('data-sitekey')
            }));
            return { type: 'unknown', iframes, sitekeyEls };
        }""")

    def ui_takeover(self, message: str) -> UiTakeoverResponse:
        import requests

        print(f"\n[HITL] UI Takeover: {message}")
        print("Attempting to solve CAPTCHA via CapSolver...")

        capsolver_key = os.environ.get("CAPSOLVER_API_KEY", "")
        if not capsolver_key:
            print("ERROR: CAPSOLVER_API_KEY not set!")
            return UiTakeoverResponse.CANCEL

        page = self._nova_ref[0].page
        page_url = page.url

        captcha_info = self._detect_captcha_type(page)
        captcha_type = captcha_info.get("type", "unknown")
        print(f"Detected CAPTCHA type: {captcha_type}, info: {captcha_info}")

        try:
            if captcha_type == "turnstile":
                site_key = captcha_info.get("siteKey")
                if not site_key:
                    # Try extracting from page source
                    site_key = page.evaluate("""() => {
                        const el = document.querySelector('.cf-turnstile, [data-sitekey]');
                        if (el) return el.getAttribute('data-sitekey');
                        const match = document.documentElement.innerHTML.match(/sitekey['":\\s]+['"]([0-9a-zA-Z_-]{10,})['"]/);
                        return match ? match[1] : null;
                    }""")

                if not site_key:
                    print("Turnstile detected but no site key found")
                    return UiTakeoverResponse.CANCEL

                print(f"Solving Cloudflare Turnstile with site key: {site_key}")

                create_resp = requests.post(
                    "https://api.capsolver.com/createTask",
                    json={
                        "clientKey": capsolver_key,
                        "task": {
                            "type": "AntiTurnstileTaskProxyLess",
                            "websiteURL": page_url,
                            "websiteKey": site_key,
                        },
                    },
                    timeout=30,
                )
                create_data = create_resp.json()
                task_id = create_data.get("taskId")
                if not task_id:
                    print(f"CapSolver Turnstile create failed: {create_data}")
                    return UiTakeoverResponse.CANCEL

                print(f"CapSolver Turnstile task created: {task_id}")

                for attempt in range(30):
                    time.sleep(5)
                    result_resp = requests.post(
                        "https://api.capsolver.com/getTaskResult",
                        json={"clientKey": capsolver_key, "taskId": task_id},
                        timeout=30,
                    )
                    result_data = result_resp.json()
                    status = result_data.get("status")

                    if status == "ready":
                        token = result_data.get("solution", {}).get("token", "")
                        if token:
                            print(f"Turnstile solved! Token length: {len(token)}")
                            page.evaluate("""(token) => {
                                // Inject into Turnstile response input
                                const inputs = document.querySelectorAll(
                                    'input[name="cf-turnstile-response"], [name="cf-turnstile-response"]'
                                );
                                inputs.forEach(el => { el.value = token; });

                                // Try the Turnstile JS callback
                                if (window.turnstile) {
                                    try { window.turnstile.reset(); } catch(e) {}
                                }

                                // Dispatch change event so frameworks pick it up
                                inputs.forEach(el => el.dispatchEvent(new Event('change', { bubbles: true })));

                                // Submit the form if one is present
                                setTimeout(() => {
                                    const form = document.querySelector('form');
                                    if (form) form.submit();
                                }, 500);
                            }""", token)
                            time.sleep(3)
                            return UiTakeoverResponse.COMPLETE
                        else:
                            print(f"CapSolver Turnstile ready but no token: {result_data}")
                            return UiTakeoverResponse.CANCEL

                    if status == "failed":
                        print(f"CapSolver Turnstile failed: {result_data}")
                        return UiTakeoverResponse.CANCEL

                    print(f"  Polling... attempt {attempt + 1}/30, status={status}")

                print("CapSolver Turnstile timed out after 150s")
                return UiTakeoverResponse.CANCEL

            elif captcha_type == "incapsula":
                print("Imperva/Incapsula bot challenge detected.")
                print("Injecting stealth patches and reloading...")

                # Imperva detects headless Chromium via navigator.webdriver, missing plugins, etc.
                # add_init_script runs BEFORE any page scripts on every subsequent navigation.
                page.add_init_script("""
                    (() => {
                        // Hide webdriver flag
                        Object.defineProperty(navigator, 'webdriver', {
                            get: () => undefined,
                            configurable: true,
                        });

                        // Fake plugin list
                        Object.defineProperty(navigator, 'plugins', {
                            get: () => {
                                const arr = [
                                    { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
                                    { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
                                    { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' },
                                ];
                                arr.__proto__ = PluginArray.prototype;
                                return arr;
                            },
                            configurable: true,
                        });

                        // Languages
                        Object.defineProperty(navigator, 'languages', {
                            get: () => ['en-US', 'en'],
                            configurable: true,
                        });

                        // Expose window.chrome
                        if (!window.chrome) {
                            window.chrome = {
                                app: { isInstalled: false },
                                runtime: { PlatformOs: { MAC: 'mac', WIN: 'win', ANDROID: 'android', CROS: 'cros', LINUX: 'linux', OPENBSD: 'openbsd' } },
                            };
                        }

                        // Permissions API
                        const originalQuery = window.navigator.permissions && window.navigator.permissions.query.bind(window.navigator.permissions);
                        if (originalQuery) {
                            window.navigator.permissions.query = (params) =>
                                params.name === 'notifications'
                                    ? Promise.resolve({ state: Notification.permission })
                                    : originalQuery(params);
                        }
                    })();
                """)

                # Reload with stealth patches active
                page.reload()
                time.sleep(5)

                for attempt in range(12):
                    time.sleep(5)
                    still_blocked = page.evaluate("""() => {
                        return !!(
                            document.querySelector('iframe[src*="_Incapsula_Resource"]') ||
                            document.querySelector('iframe[src*="incapsula"]') ||
                            document.querySelector('script[src*="_Incapsula_Resource"]')
                        );
                    }""")
                    print(f"  Incapsula check {attempt + 1}/12: {'still blocked' if still_blocked else 'cleared'}")
                    if not still_blocked:
                        print("Incapsula challenge resolved!")
                        return UiTakeoverResponse.COMPLETE

                print("Incapsula challenge could not be resolved even with stealth patches.")
                print("The worker IP may be flagged by Imperva. "
                      "Consider using a residential proxy or seeding cookies from a non-headless browser.")
                return UiTakeoverResponse.CANCEL

            elif captcha_type == "hcaptcha":
                site_key = captcha_info.get("siteKey")
                if not site_key:
                    # Try to extract from page more aggressively
                    site_key = page.evaluate("""() => {
                        // Look for sitekey in any data attribute
                        const el = document.querySelector('[data-sitekey]');
                        if (el) return el.getAttribute('data-sitekey');
                        // Look in iframe src
                        const iframes = document.querySelectorAll('iframe');
                        for (const iframe of iframes) {
                            const match = iframe.src.match(/sitekey=([^&]+)/);
                            if (match) return match[1];
                        }
                        // Look in page source
                        const match = document.documentElement.innerHTML.match(/sitekey['":\\s]+['"]([a-f0-9-]{36})['"]/i);
                        if (match) return match[1];
                        return null;
                    }""")

                if not site_key:
                    print("hCaptcha detected but no site key found")
                    return UiTakeoverResponse.CANCEL

                print(f"Solving hCaptcha with site key: {site_key}")

                create_resp = requests.post(
                    "https://api.capsolver.com/createTask",
                    json={
                        "clientKey": capsolver_key,
                        "task": {
                            "type": "HCaptchaTaskProxyLess",
                            "websiteURL": page_url,
                            "websiteKey": site_key,
                        },
                    },
                    timeout=30,
                )
                create_data = create_resp.json()
                task_id = create_data.get("taskId")
                if not task_id:
                    print(f"CapSolver hCaptcha create failed: {create_data}")
                    return UiTakeoverResponse.CANCEL

                print(f"CapSolver hCaptcha task created: {task_id}")

                for attempt in range(30):
                    time.sleep(5)
                    result_resp = requests.post(
                        "https://api.capsolver.com/getTaskResult",
                        json={"clientKey": capsolver_key, "taskId": task_id},
                        timeout=30,
                    )
                    result_data = result_resp.json()
                    status = result_data.get("status")

                    if status == "ready":
                        token = result_data.get("solution", {}).get("gRecaptchaResponse", "")
                        if token:
                            print(f"hCaptcha solved! Token length: {len(token)}")

                            # Inject hCaptcha token into the page
                            page.evaluate("""(token) => {
                                // Set hCaptcha response textareas
                                document.querySelectorAll(
                                    'textarea[name="h-captcha-response"], textarea[name="g-recaptcha-response"]'
                                ).forEach(el => {
                                    el.style.display = 'block';
                                    el.value = token;
                                });

                                // Call hCaptcha callback if available
                                if (window.hcaptcha && window.hcaptcha.execute) {
                                    // Try to trigger via the hcaptcha API
                                    try {
                                        const widgetIds = document.querySelectorAll('[data-hcaptcha-widget-id]');
                                        widgetIds.forEach(el => {
                                            const widgetId = el.getAttribute('data-hcaptcha-widget-id');
                                            if (widgetId && window.hcaptcha.getResponse) {
                                                // Trigger the callback
                                            }
                                        });
                                    } catch(e) {}
                                }

                                // Find and call any registered callbacks
                                const hcaptchaDiv = document.querySelector('.h-captcha, [data-hcaptcha-widget-id]');
                                if (hcaptchaDiv) {
                                    const callback = hcaptchaDiv.getAttribute('data-callback');
                                    if (callback && window[callback]) {
                                        window[callback](token);
                                    }
                                }

                                // Dispatch event for frameworks listening
                                document.dispatchEvent(new CustomEvent('hcaptcha-success', { detail: { token } }));

                                // Try submitting the form after a short delay
                                setTimeout(() => {
                                    const submitBtn = document.querySelector(
                                        'button[type="submit"], input[type="submit"], button:has-text("Log"), button:has-text("Sign")'
                                    );
                                    if (submitBtn) submitBtn.click();
                                }, 500);
                            }""", token)

                            time.sleep(3)
                            return UiTakeoverResponse.COMPLETE
                        else:
                            print(f"CapSolver hCaptcha ready but no token: {result_data}")
                            return UiTakeoverResponse.CANCEL

                    if status == "failed":
                        print(f"CapSolver hCaptcha failed: {result_data}")
                        return UiTakeoverResponse.CANCEL

                    print(f"  Polling... attempt {attempt + 1}/30, status={status}")

                print("CapSolver hCaptcha timed out after 150s")
                return UiTakeoverResponse.CANCEL

            elif captcha_type == "recaptcha":
                site_key = captcha_info.get("siteKey")
                if not site_key:
                    print("reCAPTCHA detected but no site key found")
                    return UiTakeoverResponse.CANCEL

                print(f"Solving reCAPTCHA with site key: {site_key}")

                create_resp = requests.post(
                    "https://api.capsolver.com/createTask",
                    json={
                        "clientKey": capsolver_key,
                        "task": {
                            "type": "ReCaptchaV2TaskProxyLess",
                            "websiteURL": page_url,
                            "websiteKey": site_key,
                        },
                    },
                    timeout=30,
                )
                create_data = create_resp.json()
                task_id = create_data.get("taskId")
                if not task_id:
                    print(f"CapSolver reCAPTCHA create failed: {create_data}")
                    return UiTakeoverResponse.CANCEL

                for attempt in range(24):
                    time.sleep(5)
                    result_resp = requests.post(
                        "https://api.capsolver.com/getTaskResult",
                        json={"clientKey": capsolver_key, "taskId": task_id},
                        timeout=30,
                    )
                    result_data = result_resp.json()
                    status = result_data.get("status")

                    if status == "ready":
                        token = result_data.get("solution", {}).get("gRecaptchaResponse", "")
                        if token:
                            print(f"reCAPTCHA solved! Token length: {len(token)}")
                            page.evaluate("""(token) => {
                                const textarea = document.querySelector('#g-recaptcha-response, textarea[name="g-recaptcha-response"]');
                                if (textarea) { textarea.style.display = 'block'; textarea.value = token; }
                                if (typeof ___grecaptcha_cfg !== 'undefined') {
                                    const clients = ___grecaptcha_cfg.clients;
                                    for (const key in clients) {
                                        const client = clients[key];
                                        for (const prop in client) {
                                            if (client[prop] && client[prop].callback) {
                                                client[prop].callback(token);
                                                return;
                                            }
                                        }
                                    }
                                }
                            }""", token)
                            time.sleep(2)
                            return UiTakeoverResponse.COMPLETE

                    if status == "failed":
                        print(f"CapSolver reCAPTCHA failed: {result_data}")
                        return UiTakeoverResponse.CANCEL

                    print(f"  Polling... attempt {attempt + 1}/24, status={status}")

                return UiTakeoverResponse.CANCEL

            else:
                print(f"Unknown CAPTCHA type: {captcha_type}")
                return UiTakeoverResponse.CANCEL

        except Exception as e:
            print(f"CapSolver error: {e}")
            import traceback
            traceback.print_exc()
            return UiTakeoverResponse.CANCEL


def main():
    local_mode = os.environ.get("LOCAL_LOGIN", "").lower() in ("1", "true", "yes")

    default_dir = (
        os.path.expanduser("~/dubizzle-profile") if local_mode
        else "/data/novasell/nova-act-profile"
    )
    user_data_dir = os.environ.get("NOVA_ACT_USER_DATA_DIR", default_dir)
    os.makedirs(user_data_dir, exist_ok=True)

    # Clean up stale lock files
    singleton_lock = os.path.join(user_data_dir, "SingletonLock")
    if os.path.exists(singleton_lock):
        os.remove(singleton_lock)
        print(f"Removed stale {singleton_lock}")

    dubizzle_email = os.environ.get("DUBIZZLE_EMAIL", "")
    dubizzle_pass = os.environ.get("DUBIZZLE_PASS", "")

    if not dubizzle_email or not dubizzle_pass:
        print("ERROR: DUBIZZLE_EMAIL and DUBIZZLE_PASS env vars must be set")
        return

    if local_mode:
        print("=== LOCAL LOGIN MODE (visible browser) ===")
        print(f"Profile will be saved to: {user_data_dir}")
        print()
        print("After successful login, copy the profile to the pod with:")
        print()
        pod = os.environ.get("WORKER_POD", "<worker-pod>")
        pod_dir = "/data/novasell/nova-act-profile"
        print(f"  kubectl cp {user_data_dir} {pod}:{pod_dir}")
        print()

    print(f"User data dir:    {user_data_dir}")
    print(f"Dubizzle email:   {dubizzle_email}")
    print()

    nova_ref = [None]
    hitl_callbacks = DubizzleHITLCallbacks(nova_ref)

    @nova_workflow(
        workflow_definition_name=NOVA_ACT_WORKFLOW_DEFINITION,
        model_id=NOVA_ACT_MODEL_ID,
    )
    def do_dubizzle_login():
        with NovaAct(
            starting_page="https://dubai.dubizzle.com/",
            tty=local_mode,
            user_data_dir=user_data_dir,
            clone_user_data_dir=False,
            human_input_callbacks=hitl_callbacks,
        ) as nova:
            nova_ref[0] = nova
            time.sleep(3)
            print(f"Current URL: {nova.page.url}")

            # Check if already logged in by looking for a user/account menu
            already_logged_in = nova.page.evaluate("""() => {
                // Dubizzle shows user avatar or account nav when logged in
                const indicators = [
                    '[data-testid="user-menu"]',
                    '[aria-label*="account" i]',
                    '[aria-label*="profile" i]',
                    'a[href*="/my-account"]',
                    'a[href*="/profile"]',
                    '[class*="userAvatar"]',
                    '[class*="UserAvatar"]',
                    '[class*="user-avatar"]',
                ];
                return indicators.some(sel => document.querySelector(sel) !== null);
            }""")

            if already_logged_in:
                print("Already logged in! Cookies are valid.")
                nova.page.goto("https://dubai.dubizzle.com/place-your-ad/")
                time.sleep(3)
                print(f"Place-ad URL: {nova.page.url}")
                return {"status": "already_logged_in", "url": nova.page.url}

            # Dismiss cookie consent if present
            try:
                cookie_btn = nova.page.locator('button:has-text("Accept"), button:has-text("Got it"), button:has-text("OK")')
                if cookie_btn.count() > 0:
                    cookie_btn.first.click(timeout=5000)
                    time.sleep(1)
                    print("Dismissed cookie consent dialog")
            except Exception:
                pass

            # Click the Login button on the homepage via Nova Act (handles any language/label)
            print("Clicking login button on homepage...")
            nova.act('Click the Login or Sign in button in the navigation header')
            time.sleep(2)

            # Dubizzle shows a popup with social/email options — choose email
            print("Selecting 'Continue with email'...")
            nova.act('Click the "Continue with email" option in the popup')
            time.sleep(2)

            # Fill credentials via Nova Act (keyboard.type keeps password out of prompts)
            print("Filling in credentials...")
            nova.act('Click on the email or username input field')
            nova.page.keyboard.type(dubizzle_email)

            nova.act('Click on the password input field')
            nova.page.keyboard.type(dubizzle_pass)

            nova.act('Click the submit / Log in button to sign in')

            try:
                nova.page.wait_for_load_state('networkidle', timeout=15000)
            except Exception:
                pass
            time.sleep(3)

            print(f"After login URL: {nova.page.url}")

            # Check if login form is still visible (login failed / CAPTCHA)
            def login_form_visible():
                return nova.page.evaluate("""() => {
                    const pw = document.querySelector('input[type="password"]');
                    return pw !== null && pw.offsetParent !== null;
                }""")

            max_retries = 3
            retry = 0
            while login_form_visible() and retry < max_retries:
                retry += 1
                print(f"Login form still visible (attempt {retry}/{max_retries})...")

                # Nova Act handles CAPTCHA or any visible blocker
                nova.act(
                    'If there is a CAPTCHA or security check, complete it. '
                    'If there is an error message, clear the fields and re-enter the credentials, '
                    f'then submit: email={dubizzle_email}'
                )

                if login_form_visible():
                    try:
                        email_field = nova.page.locator('input[name="email"], input[type="email"]').first
                        if email_field.is_visible():
                            email_field.fill(dubizzle_email)
                            pass_field = nova.page.locator('input[name="password"], input[type="password"]').first
                            pass_field.fill(dubizzle_pass)
                            nova.page.keyboard.press('Enter')
                            time.sleep(5)
                    except Exception as e:
                        print(f"Re-entry failed: {e}")

                print(f"URL after retry: {nova.page.url}")

            if login_form_visible():
                print(f"ERROR: Login form still visible after retries: {nova.page.url}")
                return {"status": "login_failed", "url": nova.page.url}

            # Dismiss any post-login prompts
            nova.act(
                'If you see any popups, notification prompts, or overlays, '
                'dismiss them. Otherwise skip this step.'
            )

            # Verify place-ad access
            nova.page.goto("https://dubai.dubizzle.com/place-your-ad/")
            time.sleep(3)
            final_url = nova.page.url or ""
            print(f"Place-ad URL: {final_url}")

            if "login" in final_url or "signin" in final_url:
                return {"status": "place_ad_blocked", "url": final_url}

            return {"status": "logged_in", "url": final_url}

    print("Starting Dubizzle login via Nova Act on AWS...")
    print()
    result = do_dubizzle_login()
    print()
    print(f"Result: {result}")

    if result.get("status") in ("logged_in", "already_logged_in"):
        print(f"Cookies saved to {user_data_dir}")
        print("Future NovaSell runs will reuse these cookies.")
    else:
        print(f"Login was NOT successful: {result.get('status')}")


if __name__ == "__main__":
    main()
