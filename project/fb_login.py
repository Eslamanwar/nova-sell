"""
One-time Facebook login setup script using Nova Act.

Run this once inside the worker pod to authenticate and save cookies:

    kubectl exec -it <worker-pod> -- python -m project.fb_login

Uses CapSolver API to automatically solve CAPTCHAs.
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


class CapSolverHITLCallbacks(HumanInputCallbacksBase):
    """Solves CAPTCHAs automatically using CapSolver API."""

    def __init__(self, nova_ref):
        super().__init__()
        self._nova_ref = nova_ref

    def approve(self, message: str) -> ApprovalResponse:
        print(f"[HITL] Approval requested: {message} -> auto-approving")
        return ApprovalResponse.YES

    def _detect_captcha_type(self, page):
        """Detect whether page has Arkose FunCaptcha, reCAPTCHA, or other CAPTCHA."""
        return page.evaluate("""() => {
            // Check for Arkose Labs / FunCaptcha (Facebook's primary CAPTCHA)
            const arkoseIframe = document.querySelector('iframe[src*="arkoselabs"], iframe[src*="funcaptcha"]');
            if (arkoseIframe) {
                const match = arkoseIframe.src.match(/[?&]pkey=([^&]+)/);
                return { type: 'funcaptcha', publicKey: match ? match[1] : null, src: arkoseIframe.src };
            }

            // Check for Arkose enforcement script or data attributes
            const arkoseScript = document.querySelector('script[src*="arkoselabs"], script[src*="funcaptcha"]');
            if (arkoseScript) {
                const match = arkoseScript.src.match(/[?&]pkey=([^&]+)/);
                return { type: 'funcaptcha', publicKey: match ? match[1] : null, src: arkoseScript.src };
            }

            // Check for data-callback or hidden inputs with Arkose tokens
            const arkoseInput = document.querySelector('input[name*="arkose"], input[name*="captcha_token"], #captcha-recaptcha');
            if (arkoseInput) {
                return { type: 'funcaptcha', publicKey: null, src: 'input-detected' };
            }

            // Check for reCAPTCHA
            const recaptchaIframe = document.querySelector('iframe[src*="recaptcha"]');
            if (recaptchaIframe) {
                const match = recaptchaIframe.src.match(/[?&]k=([^&]+)/);
                return { type: 'recaptcha', siteKey: match ? match[1] : null };
            }
            const recaptchaDiv = document.querySelector('.g-recaptcha, [data-sitekey]');
            if (recaptchaDiv) {
                return { type: 'recaptcha', siteKey: recaptchaDiv.getAttribute('data-sitekey') };
            }

            // Check page content for clues
            const bodyText = document.body ? document.body.innerText : '';
            if (bodyText.includes('security check') || bodyText.includes('confirm your identity')) {
                return { type: 'unknown_security_check', publicKey: null };
            }

            return { type: 'unknown', publicKey: null };
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

        # Detect CAPTCHA type
        captcha_info = self._detect_captcha_type(page)
        captcha_type = captcha_info.get("type", "unknown")
        print(f"Detected CAPTCHA type: {captcha_type}, info: {captcha_info}")

        try:
            if captcha_type == "funcaptcha":
                # Facebook uses Arkose Labs FunCaptcha
                public_key = captcha_info.get("publicKey")
                if not public_key:
                    # Well-known Facebook Arkose public keys
                    public_key = "B5B07C35-4F00-AD71-1F40-BBE498B7E85A"
                    print(f"Using Facebook default Arkose public key: {public_key}")
                else:
                    print(f"Found Arkose public key: {public_key}")

                # Try the Arkose subdomain from iframe src
                subdomain = None
                iframe_src = captcha_info.get("src", "")
                if "arkoselabs.com" in iframe_src or "funcaptcha.com" in iframe_src:
                    import re as _re
                    m = _re.search(r'(https?://[^/]+)', iframe_src)
                    if m:
                        subdomain = m.group(1)
                        print(f"Arkose subdomain: {subdomain}")

                task_payload = {
                    "type": "FunCaptchaTaskProxyLess",
                    "websiteURL": page_url,
                    "websitePublicKey": public_key,
                }
                if subdomain:
                    task_payload["funcaptchaApiJSSubdomain"] = subdomain

                create_resp = requests.post(
                    "https://api.capsolver.com/createTask",
                    json={"clientKey": capsolver_key, "task": task_payload},
                    timeout=30,
                )
                create_data = create_resp.json()
                task_id = create_data.get("taskId")
                if not task_id:
                    print(f"CapSolver create FunCaptcha task failed: {create_data}")
                    return UiTakeoverResponse.CANCEL

                print(f"CapSolver FunCaptcha task created: {task_id}")

                # Poll for result
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
                            print(f"FunCaptcha solved! Token length: {len(token)}")

                            # Inject token into the page
                            page.evaluate("""(token) => {
                                // Set hidden input fields that Facebook uses for the Arkose token
                                const inputs = document.querySelectorAll(
                                    'input[name*="captcha"], input[name*="arkose"], input[name*="fc-token"]'
                                );
                                inputs.forEach(input => { input.value = token; });

                                // Try to find and call the Arkose callback
                                if (window.ArkoseEnforcement && window.ArkoseEnforcement.setConfig) {
                                    // Trigger the callback that Facebook registered
                                    const evt = new CustomEvent('arkose-complete', { detail: { token: token } });
                                    document.dispatchEvent(evt);
                                }

                                // Try common Arkose callback patterns
                                if (window.arkoseCallback) window.arkoseCallback(token);
                                if (window.onArkoseSuccess) window.onArkoseSuccess(token);

                                // Facebook-specific: try to submit the form with the token
                                const forms = document.querySelectorAll('form');
                                for (const form of forms) {
                                    let tokenInput = form.querySelector('input[name*="captcha"], input[name*="arkose"]');
                                    if (!tokenInput) {
                                        tokenInput = document.createElement('input');
                                        tokenInput.type = 'hidden';
                                        tokenInput.name = 'captcha_token';
                                        form.appendChild(tokenInput);
                                    }
                                    tokenInput.value = token;
                                }
                            }""", token)

                            time.sleep(3)
                            return UiTakeoverResponse.COMPLETE
                        else:
                            print(f"CapSolver ready but no token: {result_data}")
                            return UiTakeoverResponse.CANCEL

                    if status == "failed":
                        print(f"CapSolver FunCaptcha failed: {result_data}")
                        return UiTakeoverResponse.CANCEL

                    print(f"  Polling... attempt {attempt + 1}/30, status={status}")

                print("CapSolver FunCaptcha timed out after 150s")
                return UiTakeoverResponse.CANCEL

            elif captcha_type == "recaptcha":
                # Standard reCAPTCHA v2
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
                    print(f"CapSolver create reCAPTCHA task failed: {create_data}")
                    return UiTakeoverResponse.CANCEL

                print(f"CapSolver reCAPTCHA task created: {task_id}")

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
                                if (textarea) {
                                    textarea.style.display = 'block';
                                    textarea.value = token;
                                }
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

                        print(f"CapSolver ready but no token: {result_data}")
                        return UiTakeoverResponse.CANCEL

                    if status == "failed":
                        print(f"CapSolver reCAPTCHA failed: {result_data}")
                        return UiTakeoverResponse.CANCEL

                    print(f"  Polling... attempt {attempt + 1}/24, status={status}")

                print("CapSolver reCAPTCHA timed out after 120s")
                return UiTakeoverResponse.CANCEL

            else:
                print(f"Unknown CAPTCHA type: {captcha_type}. Taking page screenshot for debugging.")
                # Try screenshot for debug
                try:
                    screenshot = page.screenshot()
                    if screenshot:
                        import base64 as _b64
                        print(f"Screenshot size: {len(screenshot)} bytes")
                except Exception:
                    pass
                return UiTakeoverResponse.CANCEL

        except Exception as e:
            print(f"CapSolver error: {e}")
            import traceback
            traceback.print_exc()
            return UiTakeoverResponse.CANCEL


def main():
    user_data_dir = os.environ.get("NOVA_ACT_USER_DATA_DIR", "/data/novasell/nova-act-profile")
    os.makedirs(user_data_dir, exist_ok=True)

    # Clean up stale lock files
    singleton_lock = os.path.join(user_data_dir, "SingletonLock")
    if os.path.exists(singleton_lock):
        os.remove(singleton_lock)
        print(f"Removed stale {singleton_lock}")

    fb_email = os.environ.get("FACEBOOK_EMAIL", "")
    fb_pass = os.environ.get("FACEBOOK_PASS", "")
    fb_2fa_secret = os.environ.get("FACEBOOK_2FA_SECRET", "")
    capsolver_key = os.environ.get("CAPSOLVER_API_KEY", "")

    if not fb_email or not fb_pass:
        print("ERROR: FACEBOOK_EMAIL and FACEBOOK_PASS env vars must be set")
        return

    print(f"User data dir:  {user_data_dir}")
    print(f"Facebook email: {fb_email}")
    print(f"2FA secret:     {'set' if fb_2fa_secret else 'NOT SET'}")
    print(f"CapSolver key:  {'set' if capsolver_key else 'NOT SET (CAPTCHA will fail!)'}")
    print()

    nova_ref = [None]
    hitl_callbacks = CapSolverHITLCallbacks(nova_ref)

    @nova_workflow(
        workflow_definition_name=NOVA_ACT_WORKFLOW_DEFINITION,
        model_id=NOVA_ACT_MODEL_ID,
    )
    def do_facebook_login():
        with NovaAct(
            starting_page="https://www.facebook.com/login",
            tty=False,
            user_data_dir=user_data_dir,
            clone_user_data_dir=False,
            human_input_callbacks=hitl_callbacks,
        ) as nova:
            nova_ref[0] = nova
            time.sleep(2)
            current_url = nova.page.url or ""
            print(f"Current URL: {current_url}")

            # Check if already logged in
            if "login" not in current_url and "checkpoint" not in current_url:
                print("Already logged in! Cookies are valid.")
                nova.page.goto("https://www.facebook.com/marketplace/create/item")
                time.sleep(3)
                print(f"Marketplace URL: {nova.page.url}")
                return {"status": "already_logged_in", "url": nova.page.url}

            # Use nova.act() for login — triggers HITL automatically on CAPTCHA
            nova.act("Click on the email or phone number input field")
            nova.page.keyboard.type(fb_email)

            nova.act("Click on the password input field")
            nova.page.keyboard.type(fb_pass)

            nova.act('Click the "Log In" button')
            time.sleep(5)

            post_login_url = nova.page.url or ""
            print(f"After login URL: {post_login_url}")

            # Check if still on login page (CAPTCHA may have been solved, retry login)
            max_retries = 3
            retry = 0
            while "login" in (nova.page.url or "") and retry < max_retries:
                retry += 1
                print(f"Still on login page (attempt {retry}/{max_retries})...")

                # nova.act() will trigger HITL -> CapSolver if CAPTCHA present
                nova.act(
                    'If there is a CAPTCHA or security check, complete it. '
                    'If there is an error, try clicking "Log In" again.'
                )

                # Re-enter credentials if needed
                current_url = nova.page.url or ""
                if "login" in current_url:
                    try:
                        email_field = nova.page.locator('#email, input[name="email"]').first
                        if email_field.is_visible():
                            email_field.click()
                            email_field.fill("")
                            nova.page.keyboard.type(fb_email)
                            pass_field = nova.page.locator('#pass, input[name="pass"]').first
                            pass_field.click()
                            pass_field.fill("")
                            nova.page.keyboard.type(fb_pass)
                            nova.page.keyboard.press('Enter')
                            time.sleep(5)
                    except Exception as e:
                        print(f"Re-entry failed: {e}")

                print(f"URL after retry: {nova.page.url}")

            post_login_url = nova.page.url or ""

            # Handle 2FA
            if any(kw in post_login_url for kw in ("two_step_verification", "checkpoint", "authentication")):
                print("2FA page detected.")
                if fb_2fa_secret:
                    import pyotp
                    totp = pyotp.TOTP(fb_2fa_secret)
                    code = totp.now()
                    print("Entering 2FA code...")

                    nova.act("Click on the verification code input field")
                    nova.page.keyboard.type(code)
                    nova.page.keyboard.press('Enter')
                    time.sleep(5)
                    print(f"After 2FA URL: {nova.page.url}")

                    nova.act(
                        'If you see a "Remember browser" or "Continue" prompt, '
                        'click to continue. Otherwise skip this step.'
                    )
                    time.sleep(2)
                else:
                    print("ERROR: FACEBOOK_2FA_SECRET not set but 2FA is required!")
                    return {"status": "2fa_required", "url": nova.page.url}

            # Dismiss prompts
            nova.act(
                'If you see any "Not now", cookie consent, or notification prompts, '
                'dismiss them. Otherwise skip this step.'
            )

            # Final login check
            if "login" in (nova.page.url or ""):
                print(f"ERROR: Still on login page: {nova.page.url}")
                return {"status": "login_failed", "url": nova.page.url}

            # Verify marketplace access
            nova.page.goto("https://www.facebook.com/marketplace/create/item")
            time.sleep(3)
            final_url = nova.page.url or ""
            print(f"Marketplace URL: {final_url}")

            if "login" in final_url:
                return {"status": "marketplace_blocked", "url": final_url}

            return {"status": "logged_in", "url": final_url}

    print("Starting Facebook login via Nova Act on AWS...")
    print()
    result = do_facebook_login()
    print()
    print(f"Result: {result}")

    if result.get("status") == "logged_in":
        print(f"Cookies saved to {user_data_dir}")
        print("Future NovaSell runs will reuse these cookies.")
    else:
        print(f"Login was NOT successful: {result.get('status')}")


if __name__ == "__main__":
    main()
