# LinkedIn Account Creation

Create a new LinkedIn account via the browser using the `cm_browser` tool.

## Prerequisites

- A browser profile is open via `cm_browser`.
- You have a valid email address and a real name (first + last).
- You know the intended password (minimum 6 characters).
- You are ready for email or phone verification.

## Step-by-step flow

1. **Open the signup page**
   - Navigate to `https://www.linkedin.com/signup`.
   - Use `cm_browser(action="open_url", profile="…", url="https://www.linkedin.com/signup")`
   - Wait for the page to fully load.

2. **Enter personal info (Page 1 — email + password)**
   - Use `extract_text` to verify the page loaded. Look for text like "Join now" or "LinkedIn sign up".
   - LinkedIn shows **two fields** on this page:
     - **Email** (or phone number): `input[id="email-or-phone"]` or `input[name="session_key"]` — but on the signup form it's typically an `input[type="text"]` labeled "Email or phone number".
     - **Password**: `input[id="password"]` or `input[type="password"]` — LinkedIn shows a **Show/Hide toggle** (a `<button>` next to the password field).
   - **Recommended approach:** Use `fill_form` with explicit selectors:
     ```json
     {
       "action": "fill_form",
       "profile": "…",
       "fields": [
         {"selector": "input[id='email-or-phone']", "value": "user@example.com"},
         {"selector": "input[id='password']", "value": "YourPassword123!"}
       ],
       "submit_selector": "button:has-text('Agree & Join'), button:has-text('Join now')"
     }
     ```
   - If the exact field IDs differ (LinkedIn may vary them), use `extract_text` to find the field labels, then use text-based selectors like `text=Email` or `text=Password` to locate nearby inputs.
   - The primary CTA button says **"Agree & Join"** or **"Join now"** — it creates the account and accepts LinkedIn's User Agreement, Privacy Policy, and Cookie Policy in one click.
   - Click **Agree & Join**: `click(selector="text=Agree & Join")`.
   - **Wait for transition.** LinkedIn may proceed to Page 2 or show an error.

3. **Enter name (Page 2 — first name + last name, if not previously collected)**
   - LinkedIn may ask for **First name** and **Last name** after the initial signup.
   - Use `extract_text` to check if name fields are present.
   - If present, fill them using:
     ```json
     {
       "action": "fill_form",
       "profile": "…",
       "fields": [
         {"selector": "input:first-of-type", "value": "John"},
         {"selector": "input:last-of-type", "value": "Doe"}
       ],
       "submit_selector": "text=Continue"
     }
     ```
   - Or use two `type` calls: `type(selector="input:nth-of-type(1)", text="John")` and `type(selector="input:nth-of-type(2)", text="Doe")`.
   - Click **Continue**: `click(selector="text=Continue")`.

4. **Location and most recent job title (Page 3 — profile setup)**
   - LinkedIn may ask you to fill in **Country/Region**, **Zip/Postal code**, and optionally **Most recent job title** or **Company**.
   - **Country/Region** is typically a native `<select>` element. Use `select_option`:
     - `select_option(selector="select", label="United States")` or `select_option(selector="select", value="us")`
   - **Zip/Postal code** is a standard `<input>`:
     - `type(selector='input[type="text"]', text="10001")`
   - **Job title** and **Company** are standard `<input>` fields.
   - If the user is a student, look for a "I'm a student" link and click it.
   - Click **Continue**: `click(selector="text=Continue")`.
   - **If these fields don't appear**, the account may have been created on Page 1 already. Use `extract_text` to check.

5. **Verification (if required)**
   - LinkedIn may send a verification code to your email or phone.
   - Use `await_operator` to ask the human for the code:
     ```json
     {
       "action": "await_operator",
       "profile": "…",
       "prompt": "LinkedIn sent a verification code to [email/phone]. Please paste it here so I can complete the signup."
     }
     ```
   - Enter the code in the verification input field: `type(selector='input[type="text"]', text="123456")`
   - Click **Verify**: `click(selector="text=Verify")`.

6. **Import contacts (optional — Page 4)**
   - LinkedIn may ask to import contacts from your email to grow your network.
   - Offer suggestions: "Skip this step" or "Import contacts" — use `extract_text` to see the options.
   - Click **Skip**: `click(selector="text=Skip")` or find a "Thanks, I'll do this later" link.

7. **Profile photo (optional — Page 5)**
   - LinkedIn may ask you to upload a profile photo to complete your profile.
   - Click **Skip**: `click(selector="text=Skip")` or look for "Skip for now" / "I'll do this later".

8. **Confirm success**
   - The LinkedIn feed or "Welcome to LinkedIn" onboarding wizard should appear.
   - Look for text like "Welcome" or "Home" in the page content.
   - Use `extract_text` to verify the page loaded correctly.
   - Capture a screenshot as proof of completion: `screenshot(label="linkedin-account-done")`.

9. **Register the profile (optional)**
   - If the operator wants to reuse this session, register the profile:
     ```json
     {
       "action": "register_profile",
       "profile": "…",
       "summary": "Logged into LinkedIn account",
       "services": ["linkedin"]
     }
     ```

## cm_browser action reference (for this skill)

| Action | Parameters | Purpose |
|--------|-----------|---------|
| `open_url` | `url`, `profile` | Navigate to signup page |
| `extract_text` | `profile`, `selector?` | Read page content to verify state |
| `screenshot` | `profile`, `label?` | Visual verification |
| `type` | `profile`, `selector`, `text`, `submit?` | Fill text fields |
| `fill_form` | `profile`, `fields[]`, `submit_selector?` | Fill multiple fields at once |
| `click` | `profile`, `selector` | Click buttons / links |
| `select_option` | `profile`, `selector`, `value`/`label` | Select from native `<select>` dropdowns |
| `wait_for` | `profile`, `selector`/`url`, `timeout_s?` | Wait for element to appear |
| `await_operator` | `profile`, `prompt`, `timeout_s?` | Ask human for verification code |
| `register_profile` | `profile`, `summary`, `services[]` | Save session for reuse |

## Known selectors (verified via live page scrape)

LinkedIn's signup form uses standard HTML elements. The following were observed on the signup page:

- **Email field:** `input[id="email-or-phone"]` — labeled "Email or phone number"
- **Password field:** `input[id="password"]` — labeled "Password (6+ characters)", has a Show/Hide toggle button next to it
- **First name:** Text input (may appear on a later page)
- **Last name:** Text input (may appear on a later page)
- **Agree & Join button:** text "Agree & Join"
- **Links:** "User Agreement", "Privacy Policy", "Cookie Policy" — linked from the signup form
- **Continue button:** text "Continue"

LinkedIn uses **native HTML elements** throughout (not custom div-based dropdowns), so `type`, `fill_form`, and `select_option` work reliably.

## Common failure modes

| Issue | Mitigation |
|-------|-----------|
| Email already registered | Use a different email address or recover the existing account. LinkedIn shows "This email is already registered". |
| CAPTCHA or reCAPTCHA | Use `await_operator` to ask the human to solve it in the browser window. |
| Phone verification required | Some regions require phone verification; use a valid number. |
| Rate limited or flagged | Wait and retry; use a different IP if necessary. |
| Name rejected | LinkedIn requires real names; avoid obviously fake names. |
| Password too short | Minimum 6 characters. LinkedIn shows a strength indicator. |
| "Agree & Join" does nothing | Check for CAPTCHA overlay or validation errors. Use `extract_text` + `screenshot` to debug. |

## Safety / compliance notes

- Only create accounts for legitimate professional purposes.
- Respect LinkedIn's User Agreement and Professional Community Policies.
- Do not use fake identities.
- If the operator has not explicitly requested a LinkedIn account, ask for confirmation before proceeding.
