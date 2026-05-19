# X (Twitter) Account Creation

Create a new X account via the browser using the `cm_browser` tool.

## Prerequisites

- A browser profile is open via `cm_browser`.
- You have a valid phone number or email address for verification.
- You know the desired display name and password.
- **Note:** X's signup page (`x.com/i/flow/signup`) may initially show an error page saying "Something went wrong, but don't fret — let's give it another shot. Try again." This is normal for automated browsers — see Step 1 for handling instructions.

## Step-by-step flow

1. **Open the signup page**
   - Navigate to `https://x.com/i/flow/signup`.
   - Use `cm_browser(action="open_url", profile="…", url="https://x.com/i/flow/signup")`
   - Wait for the page to fully load.
   - Use `extract_text` to check the page content:
     - **If you see** "Create your account" or "Join X" or name fields → the signup form loaded successfully. Proceed to Step 2.
     - **If you see** "Something went wrong" or "Try again" → This is X's anti-bot protection. **Reload the page** and try again:
       - `click(selector="text=Try again")` or
       - `navigate_back()` then `open_url` again, or
       - Simply call `open_url` to the same URL again.
     - **Retry up to 3 times.** If it still fails, use `await_operator`:
       ```json
       {
         "action": "await_operator",
         "profile": "…",
         "prompt": "X.com signup page is showing 'Something went wrong'. Privacy extensions may cause this. Please navigate to https://x.com/i/flow/signup in the browser window and let me know when the signup form loads."
       }
       ```

2. **Enter name (Step 1)**
   - Input the display name (this can be changed later).
   - Use `type(selector='input[type="text"]', text="Display Name")` or look for `input[name="name"]`.
   - Click **Next**: `click(selector="text=Next")`.

3. **Use phone or email (Step 2)**
   - X asks you to choose either phone or email for verification.
   - Look for a toggle or tabs labeled "Phone" / "Email". Use `extract_text` to find the exact labels.
   - Click the desired option: `click(selector="text=Email")` or `click(selector="text=Phone")`.
   - Enter the chosen contact method: `type(selector='input[type="text"]', text="user@example.com")` or `type(selector='input[type="tel"]', text="+1234567890")`.
   - Click **Next**: `click(selector="text=Next")`.

4. **Set birth date (Step 3)**
   - X asks for **Month**, **Day**, and **Year**.
   - **Important:** X uses native `<select>` dropdowns or text inputs depending on the UI version. Use `extract_text` first to see what's on the page.
   - **If native `<select>` elements** (most common on X):
     - `select_option(selector='select[id="month"]', label="January")` or `select_option(selector='select', label="January")`
     - `select_option(selector='select[id="day"]', label="15")` or `select_option(selector='select:nth-of-type(2)', label="15")`
     - `select_option(selector='select[id="year"]', label="1992")` or `select_option(selector='select:nth-of-type(3)', label="1992")`
   - **If text inputs** (less common):
     - `type(selector='input[autocomplete="bday-month"]', text="1")`
     - `type(selector='input[autocomplete="bday-day"]', text="15")`
     - `type(selector='input[autocomplete="bday-year"]', text="1992")`
   - You must indicate an age of at least 13.
   - Click **Next**: `click(selector="text=Next")`.

5. **Customize experience (Step 4 — optional)**
   - X may ask you to allow tracking for personalization (e.g., "Let people find you by email" or "Personalization").
   - These are typically checkboxes or toggles. Use `extract_text` to see the options and `click` to toggle.
   - Click **Next**: `click(selector="text=Next")`.

6. **Sign up / Review (Step 5)**
   - X shows a summary of the information you've entered.
   - Click **Sign up**: `click(selector="text=Sign up")`.

7. **Verification (Step 6)**
   - X sends a code to your phone or email (depending on what you chose in Step 3).
   - Use `await_operator` to ask the human for the code:
     ```json
     {
       "action": "await_operator",
       "profile": "…",
       "prompt": "X sent a verification code to [email/phone]. Please paste it here so I can complete the signup."
     }
     ```
   - Enter the code in the verification input field: `type(selector='input[type="text"]', text="123456")`
   - Click **Next**: `click(selector="text=Next")`.

8. **Set password (Step 7)**
   - Create a strong password (minimum 8 characters).
   - Use `type(selector='input[type="password"]', text="YourStrongPassword123!")`
   - Click **Next**: `click(selector="text=Next")`.

9. **Upload profile picture (Step 8 — optional)**
   - X may ask you to upload a profile photo or customize your profile.
   - Look for "Skip for now" or similar: `click(selector="text=Skip")`

10. **Confirm success**
    - The X home timeline or onboarding wizard should appear. Look for text like "What's happening" or the X logo in the sidebar.
    - Use `extract_text` to verify the page loaded.
    - Capture a screenshot as proof of completion: `screenshot(label="x-account-done")`.

11. **Register the profile (optional)**
    - If the operator wants to reuse this session, register the profile:
      ```json
      {
        "action": "register_profile",
        "profile": "…",
        "summary": "Logged into X account",
        "services": ["x"]
      }
      ```

## cm_browser action reference (for this skill)

| Action | Parameters | Purpose |
|--------|-----------|---------|
| `open_url` | `url`, `profile` | Navigate to signup page |
| `extract_text` | `profile`, `selector?` | Read page content to check for error pages |
| `screenshot` | `profile`, `label?` | Visual verification |
| `type` | `profile`, `selector`, `text`, `submit?` | Fill text fields |
| `click` | `profile`, `selector` | Click buttons / toggles |
| `select_option` | `profile`, `selector`, `value`/`label` | Select from native `<select>` dropdowns |
| `wait_for` | `profile`, `selector`/`url`, `timeout_s?` | Wait for element to appear |
| `navigate_back` | `profile` | Go back and retry if error page appears |
| `await_operator` | `profile`, `prompt`, `timeout_s?` | Ask human for verification code / help with error page |
| `register_profile` | `profile`, `summary`, `services[]` | Save session for reuse |

## Handling X's anti-bot protection

X.com's signup flow is behind aggressive anti-bot protection. Here's how to handle it:

1. **"Something went wrong" error page** — This is the most common issue. The page URL stays on `x.com/i/flow/signup` but shows an error instead of the form.
   - **Mitigation 1:** Click "Try again" on the page: `click(selector="text=Try again")`
   - **Mitigation 2:** Reload by calling `open_url` again to the same URL.
   - **Mitigation 3:** Use `navigate_back` → `open_url` again.
   - **Mitigation 4:** If all else fails, use `await_operator` to ask the human.

2. **Rate limiting** — X may temporarily block signups from the same IP after a few attempts.
   - **Mitigation:** Wait a few minutes between attempts.

3. **CAPTCHA** — X may present a CAPTCHA during the signup flow.
   - **Mitigation:** Use `await_operator` to ask the human to solve it.

## Known selectors (approximate — may vary)

X's UI changes frequently. Always use `extract_text` before interacting to confirm the actual page state:

- **Name field:** `input[type="text"]` (first text input on the form)
- **Email toggle:** text "Email" — `selector: "text=Email"`
- **Phone toggle:** text "Phone" — `selector: "text=Phone"`
- **Contact input:** `input[type="text"]` or `input[type="tel"]` or `input[name="phone_number"]`
- **Birth date dropdowns:** `select` elements — use `select_option`
- **Password field:** `input[type="password"]`
- **Next button:** text "Next" — `selector: "text=Next"`
- **Sign up button:** text "Sign up" — `selector: "text=Sign up"`
- **Skip link:** text "Skip" or "Skip for now" — `selector: "text=Skip"`

**Pro tip:** Before each interaction, call `extract_text(profile="…")` to get all visible text. This tells you exactly what X is showing at each step of the flow. The text will contain button labels, field labels, and error messages.

## Common failure modes

| Issue | Mitigation |
|-------|-----------|
| "Something went wrong" on signup page | Click "Try again", reload, or navigate_back + retry. Up to 3 attempts. |
| Phone/email already associated | Use a different phone number or email. |
| Suspicious signup blocked | X may temporarily lock the account; verify via email/phone. |
| CAPTCHA | Use `await_operator` to ask human to solve it. |
| Rate limited | Wait a few minutes and retry. |
| Birth date too young | Must be at least 13. |
| Verification code not arriving | Use `await_operator` to ask human to check spam or resend. |
| Step doesn't advance after "Next" | Use `extract_text` + `screenshot` to check for hidden validation errors. |

## Safety / compliance notes

- Only create accounts for legitimate purposes.
- Respect X's Terms of Service.
- If the operator has not explicitly requested an X account, ask for confirmation before proceeding.
