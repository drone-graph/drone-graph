# Reddit Account Creation

Create a new Reddit account via the browser using the `cm_browser` tool.

## Prerequisites

- A browser profile is open via `cm_browser`.
- You have a valid email address (for verification) and desired username/password.
- **Note:** Reddit's signup page (`/register/`) uses Cloudflare anti-bot protection. The page may initially show a "Please wait for verification" challenge. This is handled automatically by Playwright's headed browser — **do not give up if the page doesn't load instantly.**

## Step-by-step flow

1. **Open the signup page**
   - Navigate to `https://www.reddit.com/register/`.
   - Use `cm_browser(action="open_url", profile="…", url="https://www.reddit.com/register/")`
   - **Cloudflare handling:** After opening the URL, use `wait_for` to wait for the actual signup form to appear (not the Cloudflare interstitial):
     - `wait_for(selector="form", timeout_s=15)` — wait up to 15s for the form element to appear
     - OR `wait_for(selector="text=Continue", timeout_s=15)` — wait for the Continue button
   - Use `extract_text` to verify the page loaded properly. Look for text like "Sign up" or "Continue with" or email field labels.
   - If Cloudflare is still blocking after 15 seconds, take a `screenshot` and use `await_operator` to ask the human to solve the challenge.

2. **Enter email**
   - Reddit presents an email field first (on desktop). The exact selector may vary — use `extract_text` to find visible labels.
   - Try these selectors in order:
     - `type(selector='input[type="email"]', text="user@example.com")`
     - OR `type(selector='input[name="email"]', text="user@example.com")`
     - OR if a standard `<input>` without type="email": `type(selector='input[type="text"]', text="user@example.com")`
   - Click **Continue**: `click(selector="text=Continue")`.
   - Wait briefly for the form to advance.

3. **Choose username**
   - Reddit may suggest usernames (generated) or let you type your own.
   - Use `extract_text` to read suggestions and find the username input.
   - Type your desired username: `type(selector='input[type="text"]', text="DesiredUsername")` OR `type(selector='input[name="username"]', text="DesiredUsername")`
   - Reddit checks availability in real time. If your preferred username is taken, try appending numbers or underscores.
   - The **Continue** button may be at the top or bottom of the form. Click it: `click(selector="text=Continue")`.

4. **Set password**
   - Enter a strong password (minimum 8 characters): `type(selector='input[type="password"]', text="YourStrongPassword123!")`
   - Click **Continue**: `click(selector="text=Continue")`.

5. **Interests selection (optional)**
   - Reddit may ask you to pick topics of interest to personalize your feed.
   - Topics are usually presented as grid of buttons/cards. You can:
     - Click a few to personalize: `click(selector="text=Technology")`, `click(selector="text=Gaming")`, etc.
     - OR skip: Look for a **"Skip"** link or button: `click(selector="text=Skip")`
   - After selecting interests, click **Continue** or **Next** if the button appears.

6. **Email verification**
   - Reddit sends a verification link to the provided email address.
   - Use `await_operator` to ask the human to verify:
     ```json
     {
       "action": "await_operator",
       "profile": "…",
       "prompt": "Reddit sent a verification email to [email]. Please click the verification link in the email, and then type 'done' here when the page shows the Reddit homepage."
     }
     ```
   - After verification, the Reddit homepage or onboarding flow should appear.

7. **Confirm success**
   - The Reddit homepage or onboarding flow should appear. Look for the Reddit logo, feed posts, or "Welcome to Reddit" messaging.
   - Use `extract_text` to verify the page loaded (look for "reddit" or community names).
   - Capture a screenshot as proof of completion: `screenshot(label="reddit-account-done")`.

8. **Register the profile (optional)**
   - If the operator wants to reuse this session, register the profile:
     ```json
     {
       "action": "register_profile",
       "profile": "…",
       "summary": "Logged into Reddit account",
       "services": ["reddit"]
     }
     ```

## cm_browser action reference (for this skill)

| Action | Parameters | Purpose |
|--------|-----------|---------|
| `open_url` | `url`, `profile` | Navigate to signup page |
| `extract_text` | `profile`, `selector?` | Read page content to verify state |
| `screenshot` | `profile`, `label?` | Visual verification |
| `type` | `profile`, `selector`, `text`, `submit?` | Fill text fields |
| `click` | `profile`, `selector` | Click buttons / topic selections |
| `wait_for` | `profile`, `selector`/`url`, `timeout_s?` | Wait for form to load past Cloudflare |
| `await_operator` | `profile`, `prompt`, `timeout_s?` | Ask human for email verification |
| `register_profile` | `profile`, `summary`, `services[]` | Save session for reuse |

## Cloudflare handling

Reddit's `/register/` page is protected by Cloudflare. This means:

1. **First load:** The page may show "Please wait for verification" or a Cloudflare challenge for 1–5 seconds.
2. **Automatic handling:** Playwright's headed browser (which uses a real Chromium instance) resolves Cloudflare automatically in most cases. **Do not assume failure on first `extract_text`** — wait with `wait_for`.
3. **If Cloudflare persists >15 seconds:**
   - Take a `screenshot` to see what's actually showing.
   - Use `await_operator` to ask the human: "Reddit's signup is blocked by Cloudflare. Please solve the challenge in the browser window."
4. **After Cloudflare resolves:** The normal signup form appears. Proceed with Step 2.

## Known selectors (approximate — may vary)

Reddit's signup form uses standard HTML elements. The exact CSS classes and IDs may change as Reddit updates their site. Always use `extract_text` + `screenshot` before interacting to confirm the page state:

- **Email field:** Look for `input[type="email"]` or `input[name="email"]`
- **Username field:** Look for `input[type="text"]` or `input[name="username"]`
- **Password field:** `input[type="password"]`
- **Continue button:** text "Continue" — use `selector: "text=Continue"`
- **Skip link:** text "Skip" — use `selector: "text=Skip"`
- **Topic selection buttons:** text-based selectors like `selector: "text=Technology"`

**Pro tip:** Use `extract_text(profile="…")` (no selector) to get all visible text on the page. This tells you exactly what Reddit is showing at each step. Then use text selectors to interact.

## Common failure modes

| Issue | Mitigation |
|-------|-----------|
| Username taken | Try variations; Reddit usernames are unique and permanent. Use `extract_text` to check availability message. |
| Email already in use | Use a different email address. Reddit shows error below the email field. |
| Cloudflare challenge unsolvable | Use `await_operator` to ask human to solve the CAPTCHA. |
| "I'm not a robot" CAPTCHA | Solve manually or ask the operator. |
| Suspicious activity warning | Reddit may require additional verification. Pause and ask the operator. |
| Verification email not received | Check spam folder. Use `await_operator` to ask human to check. |
| Page stuck after "Continue" | Use `extract_text` + `screenshot` to check for validation errors or account restrictions. |

## Safety / compliance notes

- Only create accounts for legitimate purposes.
- Respect Reddit's Content Policy and User Agreement.
- If the operator has not explicitly requested a Reddit account, ask for confirmation before proceeding.
