# GitHub Account Creation

Create a new GitHub account via the browser using the `cm_browser` tool.

## Prerequisites

- A browser profile is open via `cm_browser`.
- You have a valid email address and desired username.
- You know the intended password (must meet GitHub's requirements: **at least 15 characters OR at least 8 characters including a number and a lowercase letter**).
- You are ready for an email verification step (GitHub sends a launch code).

## Step-by-step flow

1. **Open the signup page**
   - Navigate to `https://github.com/signup`.
   - Use `cm_browser(action="open_url", profile="…", url="https://github.com/signup")`
   - Wait for the page to fully load.

2. **Enter email (Step 1)**
   - Use `extract_text` to verify the page loaded. Look for a heading like "Create your account" or "Welcome to GitHub".
   - GitHub renders a native `<input>` element for email. Use `type` with an appropriate selector:
     - `type(selector='input[id="email"]', text="user@example.com")` OR
     - `type(selector='input[type="email"]', text="user@example.com")`
   - Click **Continue**: `click(selector="text=Continue")`.
   - Wait briefly for the transition to Step 2.

3. **Create password (Step 2)**
   - Password requirements displayed on page: **"Password must be at least 15 characters OR at least 8 characters including a number and a lowercase letter."**
   - Use `type(selector='input[type="password"]', text="YourStrongPassword123!")` for the password field.
   - GitHub shows a strength indicator below the input as you type. The **Continue** button only enables when the password meets requirements.
   - Click **Continue**: `click(selector="text=Continue")`.
   - Wait for transition to Step 3.

4. **Choose username (Step 3)**
   - Use `type(selector='input[type="text"]', text="desired-username")` — GitHub's username field is a native `<input>`.
   - Username requirements: **May only contain alphanumeric characters or single hyphens, and cannot begin or end with a hyphen.**
   - GitHub checks availability in real time (shows a checkmark or "Username is already taken" message below the field).
   - If taken, try variations with numbers, hyphens, or dots.
   - Click **Continue**: `click(selector="text=Continue")`.

5. **Set email preferences (Step 4)**
   - GitHub asks: **"Would you like to receive product updates and announcements via email?"**
   - This is a checkbox followed by a **Continue** button.
   - Decide whether to check/uncheck the preference checkbox. Use:
     - To uncheck: `click(selector='input[type="checkbox"]')` (GitHub may pre-check it)
     - To keep checked: skip
   - Click **Continue**: `click(selector="text=Continue")`.

6. **Solve the puzzle (Step 5 — CAPTCHA / verification)**
   - GitHub presents a puzzle challenge to verify you're human. The type varies:
     - **Visual puzzle:** "Select all images matching [description]" — click the correct images by locating them with `selector='img[alt*="…"]'` or by clicking within the puzzle area.
     - **Audio puzzle:** Rare; would need operator assistance.
     - **Simple checkbox:** "I'm not a robot" reCAPTCHA.
   - Common visual puzzle approach:
     - Use `screenshot` + `extract_text` to read the puzzle instructions.
     - Click the correct images one at a time using `click(selector="img[alt*='…']")`.
     - Some puzzles require clicking a "Verify" button after selecting.
   - **If the puzzle is too complex for automation**, use `await_operator` to ask the human:
     ```json
     {
       "action": "await_operator",
       "profile": "…",
       "prompt": "GitHub presented a CAPTCHA/puzzle that I cannot solve automatically. Please solve it in the browser window."
     }
     ```
   - After the puzzle, GitHub may show a **"Create account"** button. Click it: `click(selector="text=Create account")`.

7. **Email verification (Step 6)**
   - GitHub sends a **launch code** (8 characters, mix of letters and numbers) to the email address you provided.
   - Use `await_operator` to ask the human for the code:
     ```json
     {
       "action": "await_operator",
       "profile": "…",
       "prompt": "GitHub sent an 8-character launch code to [email]. Please paste the code here so I can complete the signup."
     }
     ```
   - Enter the code in the verification input field: `type(selector='input[type="text"]', text="ABC123DE")` OR `type(selector='input[data-verify="true"]', text="ABC123DE")`
   - GitHub may auto-submit after 8 characters, or require clicking **Verify**: `click(selector="text=Verify")`.

8. **Confirm success**
   - The GitHub dashboard or "Welcome to GitHub" page should appear. Look for text like "Let's get started" or a dashboard with repository tabs.
   - Use `extract_text` to verify the page loaded (look for "Dashboard" or "Welcome").
   - Capture a screenshot as proof of completion: `screenshot(label="github-account-done")`.

9. **Register the profile (optional)**
   - If the operator wants to reuse this session, register the profile:
     ```json
     {
       "action": "register_profile",
       "profile": "…",
       "summary": "Logged into GitHub account",
       "services": ["github"]
     }
     ```

## cm_browser action reference (for this skill)

| Action | Parameters | Purpose |
|--------|-----------|---------|
| `open_url` | `url`, `profile` | Navigate to signup page |
| `extract_text` | `profile`, `selector?` | Read page content to verify state |
| `screenshot` | `profile`, `label?` | Visual verification |
| `type` | `profile`, `selector`, `text`, `submit?` | Fill text fields |
| `click` | `profile`, `selector` | Click buttons / checkboxes / puzzle images |
| `wait_for` | `profile`, `selector`/`url`, `timeout_s?` | Wait for element to appear |
| `await_operator` | `profile`, `prompt`, `timeout_s?` | Ask human for launch code / puzzle help |
| `register_profile` | `profile`, `summary`, `services[]` | Save session for reuse |

## Known selectors (verified via live page scrape)

GitHub's signup form uses standard HTML elements. The following worked when scraped:

- **Email field:** `input[id="email"]` or `input[type="email"]`
- **Password field:** `input[type="password"]`
- **Username field:** `input[type="text"]` (appears after email/password step)
- **Continue button:** text "Continue" — use `selector: "text=Continue"`
- **Create account button:** text "Create account" — use `selector: "text=Create account"`
- **Email preferences checkbox:** `input[type="checkbox"]`
- **Email verification input:** `input[type="text"]` — appears after email is sent

GitHub uses **native HTML elements** throughout (not custom div-based dropdowns), so standard CSS selectors work reliably. Use `extract_text` if you need to confirm the exact field labels.

## Common failure modes

| Issue | Mitigation |
|-------|-----------|
| Username taken | Try appending numbers, hyphens, or underscores. Real-time check shows status below the field. |
| Email already in use | Use a different email address. GitHub shows "Email is already registered" error. |
| Visual puzzle failure | Read instructions carefully; retry if wrong. If stuck, use `await_operator`. |
| Password too weak | Ensure password is ≥15 chars, OR ≥8 chars with a number AND lowercase letter. |
| Launch code expired | Codes expire after ~15 minutes. Use `await_operator` to request a new one. |
| Rate limited | Wait a few minutes and retry. GitHub may temporarily block repeated signups from same IP. |

## Safety / compliance notes

- Only create accounts for legitimate purposes.
- Respect GitHub's Terms of Service and Community Guidelines.
- If the operator has not explicitly requested a GitHub account, ask for confirmation before proceeding.
