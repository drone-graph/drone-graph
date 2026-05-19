# Google Account Creation

Create a new Google account via the browser. This skill uses `cm_authenticated_browser` to navigate the Google signup flow using the authenticated Chrome profile.

## Prerequisites

- An authenticated Chrome profile is configured. First call `cm_check_auth_profile` to verify.
- If no profile exists (`has_profile` is `false`), use `cm_browser` to create the account via form-filling instead.
- You have a working phone number that can receive SMS for verification.
- You know the desired first name, last name, username (Gmail address prefix), password, and birth date.

## Step-by-step flow

1. **Check profile availability**
   - First, call `cm_check_auth_profile` to verify an authenticated profile exists.
   - If `has_profile` is `true`, proceed with `cm_authenticated_browser` for the rest of the flow.
   - If `has_profile` is `false`, use `cm_browser` to create the account via form-filling instead (the authenticated lane is not available).

2. **Open the signup page**
   - Navigate to `https://accounts.google.com/signup`.
   - Use `cm_authenticated_browser(action="open_url", url="https://accounts.google.com/signup")`
   - Wait for the page to fully load. The first page shows a multi-step wizard with "First name" and "Last name (optional)" fields followed by a "Next" button.

3. **Fill personal info (Page 1 — Name entry)**
   - Use `cm_authenticated_browser(action="extract_text")` to verify the page loaded. Look for `"First name"` and `"Last name"` in the extracted text.
   - Use `cm_authenticated_browser(action="type", selector='input[type="text"]', text="…")` — but be precise: Google renders **two** `<input>` elements on this page. The first is "First name", the second is "Last name".
   - **Recommended approach:** Use `fill_form` with explicit selectors:
     ```json
     {
       "action": "fill_form",
       "fields": [
         {"selector": "input[type='text']:nth-of-type(1)", "value": "Firstname"},
         {"selector": "input[type='text']:nth-of-type(2)", "value": "Lastname"}
       ],
       "submit_selector": "button:has-text('Next'), span:has-text('Next'), [role='button']:has-text('Next')"
     }
     ```
   - If `fill_form` with a combined `submit_selector` does not find the button, use separate calls: `type` for each field, then `click` with `selector: "text=Next"`.
   - **After clicking Next**, take a `screenshot` and `extract_text` to confirm transition to Page 2 (birth date / gender).

4. **Set birth date and gender (Page 2)**
   - **CRITICAL AUTO-DEBUG — run this evaluate script FIRST, before touching ANY dropdown:**
     Google may render EITHER native `<select>` elements OR custom div-based dropdowns, and selectors change frequently. Before interacting with Month, Day, Year, or Gender, ALWAYS dump the full outerHTML and option values of every `<select>` / `[role=listbox]` / `div[role=button]` element on the page. This is a **mandatory** standard step — not optional.

     ```json
     {
       "action": "evaluate",
       "script": "(() => { const els = document.querySelectorAll('select, [role=listbox], div[role=button]'); return Array.from(els).map((s, i) => ({ index: i, tag: s.tagName, id: s.id || '', name: s.name || '', outerHTML: s.outerHTML.slice(0,600), ariaLabel: s.getAttribute('aria-label') || '', title: s.title || '', placeholder: s.placeholder || '', role: s.getAttribute('role') || '', options: s.tagName === 'SELECT' ? Array.from(s.options).map(o => ({ value: o.value, text: o.text.trim() })) : [] })); })()"
     }
     ```

     **Parse the output to identify each field:**
     - Look for `id`, `name`, `ariaLabel`, `placeholder`, `title` containing "month", "day", "year", "gender"
     - For `<select>` elements, examine the `options` array to see every available value/text pair — this tells you exactly what values to set
     - For gender specifically, scan option `value` and `text` fields for patterns like `"male"`, `"female"`, `"rather"`, `"other"`, `"custom"`, `"prefer"`, `"unspecified"` — these reveal which numeric value (e.g., `"2"`) corresponds to your chosen option
     - If all elements have `tag: "SELECT"`, Google is using native `<select>` — use the **value-based** strategies below
     - If elements have `tag: "DIV"`, Google is using custom dropdowns — use the **div-based click** strategies

   - **For EACH dropdown (Month → Day → Year → Gender), use this cascading fallback strategy:**

     **Fallback level 1 — text selector (fastest, most robust):**
     ```json
     {"action": "click", "selector": "text=Month"}
     {"action": "wait_for", "selector": "text=January", "timeout_s": 3}
     {"action": "click", "selector": "text=June"}
     ```

     **Fallback level 2 — id / name / aria-label selector:**
     If `text=Month` fails, try id-based selectors first:
     ```json
     {"action": "click", "selector": "select#month, [id*='month'], [name*='month'], [aria-label*='Month']"}
     ```
     Then click the option by its value or visible text:
     ```json
     {"action": "click", "selector": "text=June, [role='option']:has-text('June'), option[value='6']"}
     ```

     **Fallback level 3 — JavaScript evaluate (bulletproof):**
     If both level 1 and 2 fail, use `evaluate` to scan the page for the correct element by its options or placeholder text, then set its value directly:

     **Month example:**
     ```json
     {
       "action": "evaluate",
       "script": "(() => { const s = Array.from(document.querySelectorAll('select, [role=listbox], input')).find(el => { const p = (el.placeholder||'').toLowerCase(); const t = (el.title||'').toLowerCase(); const a = (el.getAttribute('aria-label')||'').toLowerCase(); const id = (el.id||'').toLowerCase(); return p.includes('month') || t.includes('month') || a.includes('month') || id.includes('month'); }); if (!s) return 'NOT_FOUND'; if (s.tagName === 'SELECT') { s.value = '6'; s.dispatchEvent(new Event('change',{bubbles:true})); s.dispatchEvent(new Event('input',{bubbles:true})); return 'SELECT_SET_6'; } s.click(); return 'CLICKED_FALLBACK'; })()"
     }
     ```

     **Year example:**
     ```json
     {
       "action": "evaluate",
       "script": "(() => { const el = Array.from(document.querySelectorAll('input, select')).find(e => { const p=(e.placeholder||'').toLowerCase(); const a=(e.getAttribute('aria-label')||'').toLowerCase(); const id=(e.id||'').toLowerCase(); return p.includes('year') || a.includes('year') || id.includes('year'); }); if (!el) return 'NOT_FOUND'; if (el.tagName === 'SELECT') { el.value = '1992'; el.dispatchEvent(new Event('change',{bubbles:true})); return 'SELECT_SET_1992'; } if (el.tagName === 'INPUT') { el.value = '1992'; el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true})); return 'INPUT_SET_1992'; } el.click(); return 'CLICKED'; })()"
     }
     ```

     **Day example:**
     ```json
     {
       "action": "evaluate",
       "script": "(() => { const el = Array.from(document.querySelectorAll('input, select')).find(e => { const p=(e.placeholder||'').toLowerCase(); const a=(e.getAttribute('aria-label')||'').toLowerCase(); const id=(e.id||'').toLowerCase(); return p.includes('day') || a.includes('day') || id.includes('day'); }); if (!el) return 'NOT_FOUND'; if (el.tagName === 'SELECT') { el.value = '15'; el.dispatchEvent(new Event('change',{bubbles:true})); return 'SELECT_SET_15'; } if (el.tagName === 'INPUT') { el.value = '15'; el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true})); return 'INPUT_SET_15'; } el.click(); return 'CLICKED'; })()"
     }
     ```

     **Gender — value-based selection (USE THIS if the auto-debug dump shows native `<select>` elements):**
     If the debug dump showed the gender is a native `<select>` (tag: "SELECT"), use the option values from the dump. For example, if Male has `value: "2"`:
     ```json
     {
       "action": "evaluate",
       "script": "(() => { const s = document.querySelector('select#gender, select[name=\"gender\"], select[aria-label*=\"gender\" i], select[aria-label*=\"Gender\" i]'); if (!s) return 'NOT_FOUND_DIRECT'; s.value = '2'; s.dispatchEvent(new Event('change',{bubbles:true})); s.dispatchEvent(new Event('input',{bubbles:true})); return 'GENDER_SET_VALUE_2'; })()"
     }
     ```
     **Replace `'2'` with the actual value for your chosen option** (Male=2, Female=1, Rather not say=3, Custom=4 — but verify from the debug dump output).

     **Gender — pattern-matching evaluate (MOST ROBUST — works even if selectors/values change):**
     This evaluate script scans ALL `<select>` elements on the page and compares every `<option>`'s value and text against common gender patterns ("male", "female", "rather", "other", "custom", "prefer", "unspecified"). It finds the right select and option dynamically without hardcoding any selector or value:
     ```json
     {
       "action": "evaluate",
       "script": "(() => { const genderPatterns = ['male','female','rather','other','custom','prefer','unspecified','gender']; const allSelects = document.querySelectorAll('select'); for (const s of allSelects) { const opts = Array.from(s.options); const matched = opts.some(o => genderPatterns.some(p => o.value.toLowerCase().includes(p) || o.text.toLowerCase().includes(p))); if (!matched) continue; /* prefer Male by pattern, else fallback to second option or first */ const target = opts.find(o => /^male$/i.test(o.value) || /male/i.test(o.text)) || opts.find(o => /^2$/.test(o.value)) || opts[1] || opts[0]; if (!target) continue; s.value = target.value; s.dispatchEvent(new Event('change',{bubbles:true})); s.dispatchEvent(new Event('input',{bubbles:true})); return 'GENDER_SET_' + target.value + '_' + target.text.trim().replace(/\\s+/g,'_'); } return 'NOT_FOUND'; })()"
     }
     ```

     **Gender — div-based click fallback (if auto-debug shows tag: "DIV"):**
     If Google renders custom div-based dropdowns, find the gender trigger by attribute matching, click it, then find the option by pattern matching (not exact textContent):
     ```json
     {
       "action": "evaluate",
       "script": "(() => { const genderPatterns = ['male','female','rather','other','custom','prefer','unspecified','gender']; const els = Array.from(document.querySelectorAll('[role=listbox], div[role=button]')); const el = els.find(e => { const a=(e.getAttribute('aria-label')||'').toLowerCase(); const id=(e.id||'').toLowerCase(); const t=(e.title||'').toLowerCase(); return a.includes('gender') || id.includes('gender') || t.includes('gender') || e.textContent.toLowerCase().includes('gender'); }); if (!el) return 'DIV_NOT_FOUND'; el.click(); return 'CLICKED_GENDER_DIV'; })()"
     }
     ```
     After clicking, find the option by value pattern matching rather than exact text:
     ```json
     {
       "action": "evaluate",
       "script": "(() => { const patterns = ['male','female','rather','other','custom','prefer']; const items = Array.from(document.querySelectorAll('[role=option], [role=menuitem], div[role=listitem], li')); const match = items.find(el => { const t = el.textContent.trim().toLowerCase(); const v = el.getAttribute('value')||''; return patterns.some(p => t.includes(p) || v.toLowerCase().includes(p)); }); if (!match) return 'NO_GENDER_OPTION_MATCH'; match.click(); return 'CLICKED_' + match.textContent.trim().replace(/\\s+/g,'_'); })()"
     }
     ```

   - **Gender is mandatory** — you must choose one of the following before you can click **Next**:
     - **Female**
     - **Male**
     - **Rather not say**
     - **Custom** (lets you type a gender identity and select a pronoun)
   - If the operator did not specify a gender, choose **"Rather not say"**.
   - If you selected **Custom**, a text field and pronoun dropdown will appear. Fill them, then proceed.
   - **After all four dropdowns are set**, click **Next** using `click` with `selector: "text=Next"`.

5. **Choose Gmail address (Page 3)**
   - After clicking Next on the birthday/gender page, Google shows one of **three page variants**. Use `extract_text` to read the page and detect which variant is showing. Look for text containing "@gmail.com", "suggested", "Gmail address", or "Create your own". If none of these are found, Google may have skipped this screen entirely — proceed directly to Page 5 (password).

   - **Variant A — Suggested address cards (most common):**
     Google shows 2–3 clickable radio-button cards with full addresses like `firstname.lastname@gmail.com`.
     1. **Dump all clickable elements** to find the suggestion radio buttons/cards:
        ```json
        {
          "action": "evaluate",
          "script": "(() => { return Array.from(document.querySelectorAll('[role=radio], label, span, div[role=button], [role=option]')).slice(0,30).map((el,i) => ({ index:i, tag:el.tagName, text:el.textContent.trim().slice(0,120), role:el.getAttribute('role')||'', id:el.id||'', class:el.className?.slice(0,80)||'', for:el.getAttribute('for')||'' })); })()"
        }
        ```
        Look for elements whose `text` contains `@gmail.com` — those are the suggested address cards.
     2. **Click the first suggested address** using evaluate (most reliable — IDs are dynamic):
        ```json
        {
          "action": "evaluate",
          "script": "(() => { const els = Array.from(document.querySelectorAll('[role=radio], label, span, div[role=button], [role=option]')); const suggestions = els.filter(el => el.textContent.includes('@gmail.com')); if (suggestions.length === 0) { return 'NO_SUGGESTIONS'; } const first = suggestions[0]; first.click(); first.dispatchEvent(new Event('input',{bubbles:true})); first.dispatchEvent(new Event('change',{bubbles:true})); return 'CLICKED_SUGGESTED_' + first.textContent.trim().replace(/\\s+/g,'_').slice(0,40); })()"
        }
        ```
     3. **If evaluate returns `NO_SUGGESTIONS`**, try a text-based click:
        - `click(selector="text=@gmail.com")` — should match the first element containing `@gmail.com`
        - Or `click(selector='[role="radio"]:nth-of-type(1)')` to click the first radio button.

   - **Variant B — Plain text input + @gmail.com label:**
     Google shows just a text input field where you type the username. The `@gmail.com` domain is shown as a label or suffix next to the input (not typed by the user).
     - `extract_text` will show `@gmail.com` in the page text but there are NO radio-button suggestions — only a single text input.
     1. Identify the input: `extract_text` or `evaluate` to find `input[type="text"]`.
     2. Type the desired username only (do NOT include `@gmail.com`):
        `type(selector='input[type="text"]', text="desiredusername")`
     3. The `@gmail.com` suffix is automatically appended by the page.

   - **Variant C — "Create your own Gmail address" option:**
     Google shows suggested cards first but also has a "Create your own Gmail address" link/button.
     - Try Variant A first. If the suggested address cards are not clickable or the username is already taken:
     1. Click "Create your own": `click(selector="text=Create your own Gmail address")` or `click(selector="text=Create your own")`
     2. Wait for the username input: `wait_for(selector='input[type="text"]', timeout_s=5)`
     3. Type the desired username: `type(selector='input[type="text"]', text="desiredusername")`
     4. If "username taken", try variations with dots, numbers, or underscores.

   - Click **Next**: `click(selector="text=Next")`.

6. **Set password (Page 4)**
   - Enter a strong password (at least 8 characters, mix of letters, numbers, symbols).
   - Confirm the password (Google shows two fields: "Password" and "Confirm").
   - Use `type(selector='input[type="password"]:nth-of-type(1)', text="…")` and `type(selector='input[type="password"]:nth-of-type(2)', text="…")`
   - Or use `fill_form` with both password fields.
   - Click **Next**: `click(selector="text=Next")`.

7. **Phone verification (Page 5)**
   - Enter a valid phone number into the `input[type="tel"]` field.
   - Click **Next**: `click(selector="text=Next")`.
   - Google sends a 6-digit SMS code.
   - **Use `await_operator`** to ask the human for the code:
     ```json
     {
       "action": "await_operator",
       "prompt": "A 6-digit verification code was sent via SMS to [phone]. Please paste it here so I can complete the Google account signup."
     }
     ```
   - Enter the code using `type(selector='input[type="text"]', text="…")` or the OTP input fields.
   - Click **Verify**: `click(selector="text=Verify")`.

8. **Recovery info (Page 6 — optional but recommended)**
   - Add a recovery email or phone. Fields are standard `<input>` elements.
   - Click **Skip** if you want to do this later: `click(selector="text=Skip")`.

9. **Review terms (Page 7)**
   - Scroll through the Terms of Service and Privacy Policy.
   - Use `scroll(dy=500)` to scroll down if needed.
   - Click **I agree**: `click(selector="text=I agree")`.

10. **Confirm success**
    - Look for the Google account dashboard or a welcome screen titled "Welcome" or "My Account".
    - Use `extract_text` to verify the page content includes account-related text.
    - Capture a screenshot as proof of completion: `screenshot(label="google-account-done")`.

11. **Register the profile (optional)**
    - If the operator wants to reuse this session, register the profile:
      ```json
      {
        "action": "register_profile",
        "summary": "Logged into Google account",
        "services": ["google"]
      }
      ```

## Debug helper — dump all form elements + outerHTML

When selectors fail on the birthday/gender page (or any page), run this `evaluate` script to get a structured dump of every `<input>`, `<select>`, and `[role=button]` element on the page, including their **full outerHTML**, ids, names, aria-labels, placeholders, values, and visible text. Use this output to discover the correct selectors and option values.

```json
{
  "action": "evaluate",
  "script": "(() => { const out = []; document.querySelectorAll('input, select, textarea, [role=button], [role=listbox]').forEach((el, i) => { const t = el.tagName.toLowerCase(); out.push({ index: i, tag: t, outerHTML: el.outerHTML.slice(0, 800), id: el.id || '', name: el.name || '', type: el.type || '', ariaLabel: el.getAttribute('aria-label') || '', placeholder: el.placeholder || '', title: el.title || '', role: el.getAttribute('role') || '', value: el.value || '', text: (el.textContent || '').trim().slice(0, 80), options: t === 'select' ? Array.from(el.options).map(o => ({ value: o.value, text: o.text.trim() })) : [] }); }); return out.slice(0, 50); })()"
}
```

The result is a JSON array. Each entry includes the full **outerHTML** (first 800 chars) so you can see the actual rendered DOM structure. For `<select>` elements, the `options` array shows both `value` and `text` for every option — this is critical for gender:

```json
[
  {"index":0, "tag":"input", "outerHTML":"<input ...>", "id":"firstName", ...},
  {"index":1, "tag":"select", "outerHTML":"<select id=\"month\"...>", "options":[{"value":"1","text":"January"}, ...]},
  {"index":3, "tag":"select", "outerHTML":"<select ...>", "options":[
    {"value":"1","text":"Female"},
    {"value":"2","text":"Male"},
    {"value":"3","text":"Rather not say"},
    {"value":"4","text":"Custom"}
  ]}
]
```

**How to read the gender options:**
- If you select Male (the default), use `value: "2"` in the value-based evaluate script
- If you select Rather not say (the fallback), use `value: "3"`
- These values come directly from the `options` array in the debug dump — **always read them from the dump, don't hardcode them**

Use the `id`, `name`, `ariaLabel`, and `outerHTML` fields to construct exact selectors for `click`, `type`, or the `evaluate` fallback scripts. The `outerHTML` field is especially useful for finding the correct CSS selector when other attributes are missing.

## `cm_authenticated_browser` action reference (for this skill)

| Action | Parameters | Purpose |
|--------|-----------|---------|
| `open_url` | `url` | Navigate to signup page |
| `extract_text` | `selector?` | Read page content to verify state |
| `screenshot` | `label?` | Visual verification |
| `type` | `selector`, `text`, `submit?` | Fill text fields |
| `fill_form` | `fields[]`, `submit_selector?` | Fill multiple fields at once |
| `click` | `selector` | Click buttons / dropdowns / options |
| `wait_for` | `selector`/`url`, `timeout_s?` | Wait for element to appear |
| `scroll` | `dy` | Scroll down |
| `evaluate` | `script` | Run JavaScript on the page and return the result |
| `await_operator` | `prompt`, `timeout_s?` | Ask human for SMS code |
| `register_profile` | `summary`, `services[]` | Save session for reuse |

**IMPORTANT selector notes for Google signup:**
- Google uses `text=` as visible labels (e.g. `text=Next`, `text=Month`, `text=January`). Playwright's text selector matches any element containing that text.
- Text selectors like `text=Next` match buttons, spans, divs — whatever contains the text. This is the most robust approach.
- For form inputs, use CSS selectors: `input[type="text"]`, `input[type="password"]`, `input[type="tel"]`.
- When selectors break, use the **Debug helper** `evaluate` script above to inspect the actual DOM.
- `cm_authenticated_browser` does **not** accept a `profile` parameter — the profile path is resolved server-side from configuration.

## Common failure modes

| Issue | Mitigation |
|-------|-----------|
| Phone number rejected | Use a different number; some VOIP numbers are blocked. |
| Username taken | Try variations with dots, numbers, or underscores. |
| "Unusual activity" block | Google may require additional verification. Pause and ask the operator. |
| CAPTCHA challenge | Solve it manually or ask the operator. |
| Age too young | Birth year must indicate age ≥ 13 (or local minimum). |
| Dropdowns not responding to selectors | Google may render EITHER native `<select>` OR custom div-based dropdowns. Run the **auto-debug evaluate script** (Step 4) to dump outerHTML + option values. If native `<select>`, use the value-based or pattern-matching evaluate scripts. If div-based, use `click` to open then click the option. |
| Gender field failing to fill | Run the auto-debug evaluate script to dump all `<select>` option values. Look for options containing "male", "female", "rather", "other", "custom" in their `value` or `text` fields. Use the **pattern-matching evaluate** script which scans all `<select>` elements dynamically without hardcoded selectors. |
| Page doesn't advance after "Next" | Use `extract_text` + `screenshot` to check for error messages below fields. Google highlights invalid fields in red. An unfilled gender field is a common cause. |
| SMS code not arriving | Wait 30–60s and retry; use `await_operator` to ask the human if another method should be used. |
| Selectors changed (birthday/gender page) | Run the **auto-debug evaluate script** (mandatory first step in Section 4) to dump outerHTML + option values of all selects/dropdowns. The pattern-matching evaluate scripts will auto-detect the correct selectors dynamically. If they still fail, use the **Debug helper** to inspect all form elements and update selectors manually. |
| `cm_check_auth_profile` returns `false` | No authenticated profile configured. Use `cm_browser` instead with a fresh profile to create the account via form-filling. |

## Safety / compliance notes

- Only create accounts for legitimate purposes.
- Do not attempt to bypass Google's anti-abuse systems.
- If the operator has not explicitly requested a Google account, ask for confirmation before proceeding.
- `cm_authenticated_browser` has a **confirmation gate** — each action requires operator approval. This is a safety feature. Wait for approval before proceeding.
- **Never** ask for or specify profile names or paths. The system manages profiles securely.
