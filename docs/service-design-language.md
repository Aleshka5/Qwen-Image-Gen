# Filenkov design language

This file is the whole visual contract for a Filenkov service. It is written so an independent codebase can implement it without any other file from the Filenkov repository. The stylesheet in the last section is the normative CSS. The markup and the header script above it are the normative HTML and behavior.

The interface language is English. The color scheme is light. A service ships no dark theme.

---

## 1. What to build

A Filenkov screen is a sheet of warm paper, one column of text at a comfortable measure, hairline rules, and a single copper accent. Corners are square. The page has no drop shadow.

Every service draws the same frame:

- a fixed header with the diamond mark, the wordmark **Filenkov**, the service list, and a sign-in slot
- a page that opens with a small uppercase kicker, one `h1`, and an optional muted sentence
- a footer with a hairline on top
- controls, tables, banners, and choice-rows from the stylesheet

Build the service's own screens inside that frame. The reading column is `34rem`. A tool (a file list, a directory, a canvas) may run wider than that and still uses the same page padding, the same type scale, and the same header.

### Leave these out

These belong to the personal site at `filenkov.store` and would make a tool look like a second résumé.

- A résumé tree, a path spine, and article-to-article navigation as the information architecture. A service has its own screens.
- The owl illustration, the greeting bubble, the docked back-to-top button, and the read-progress ring.
- Rounded panels and drop shadows. The only shadow in the whole product sits on that owl chat. A floating surface in a service is `.panel`: raised paper, a 1px rule, square corners, no shadow.
- A second Google button. Sign-in is a link to the hub, specified below.
- A heading size chosen by hand. The `h1` is `--step-4`.

The browser-tab icon for an independent service is the diamond mark in the favicon section. The hub's own tab is an owl drawing; other services use the diamond so the icon matches the header and this file stays complete.

---

## 2. Color

All ratios are WCAG contrast of the first color on the second.

| Token | Hex | Use |
|---|---|---|
| `--paper` | `#f4efe6` | Page background |
| `--paper-raised` | `#fffdf8` | Figures, panels, tables |
| `--ink` | `#1a1612` | Body text and the primary button. 15.7:1 on paper |
| `--ink-muted` | `#5c5348` | Secondary text. 6.6:1 on paper |
| `--rule` | `#d9d1c3` | Hairlines, field borders, skeleton bars |
| `--rule-strong` | `#b9ae9c` | A rule that must stay visible when `--rule` is too quiet |
| `--accent` | `#b85c38` | Dots, bars, selection, strokes of at least 3px. 4.0:1 on paper, so it is never text |
| `--accent-text` | `#8f4128` | Any copper text or a text-sized icon. 6.2:1 on paper |
| `--chip` | `#e6ddd0` | Tags, table headers, ghost-button hover, neutral banners. Ink on chip is 13.4:1. Muted ink on chip is 5.6:1 |
| `--danger` | `#8c2f24` | Errors, the role `BLOCKED`, destructive buttons. 7.2:1 on paper |
| `--danger-wash` | `#f4e4dc` | Background of an error banner. Danger text on it is 6.7:1 |

Selection is `--accent` behind `--paper` text.

A new color is a new token in `:root`, with a one-line note of what it is for. A raw hex in a component is a bug. The stylesheet below already contains every color a service needs.

---

## 3. Type, space, motion

### Faces

Headings, the wordmark, and big numbers use **Fraunces**. Body, labels, and UI use **Public Sans**. Both are variable fonts, weight axis 100–900, SIL Open Font License. Fraunces is roman only. Public Sans is roman and italic.

Serve the files from the service itself. The page makes no request to a font CDN.

| URL path | Face |
|---|---|
| `/fonts/fraunces-latin.woff2` | Fraunces roman, Latin |
| `/fonts/fraunces-latin-ext.woff2` | Fraunces roman, Latin extended (this is where `₽` U+20BD lives) |
| `/fonts/public-sans-latin.woff2` | Public Sans roman, Latin |
| `/fonts/public-sans-latin-ext.woff2` | Public Sans roman, Latin extended |
| `/fonts/public-sans-italic-latin.woff2` | Public Sans italic, Latin |
| `/fonts/public-sans-italic-latin-ext.woff2` | Public Sans italic, Latin extended |

Obtain them from the Fontsource packages `@fontsource-variable/fraunces` and `@fontsource-variable/public-sans`, taking the latin and latin-ext subsets, or subset the upstream variable fonts to the unicode ranges in the stylesheet. `font-display` is `swap`.

The document head preloads the two Latin files:

```html
<link rel="preload" as="font" type="font/woff2" href="/fonts/fraunces-latin.woff2" crossorigin />
<link rel="preload" as="font" type="font/woff2" href="/fonts/public-sans-latin.woff2" crossorigin />
```

Metric-matched fallbacks are in the stylesheet (`Public Sans Fallback` at `size-adjust: 107%`, `Fraunces Fallback` at `104%`) so the swap to the real faces does not move the page. An English screen downloads the Latin files. Latin-ext loads when the text needs it.

If subsetting is impractical, one roman Fraunces file and one roman plus one italic Public Sans file are acceptable. Keep the family names `Fraunces` and `Public Sans`, keep `font-display: swap`, and preload the files that cover Latin. Drop the `unicode-range` lines in that case.

### Scale

| Token | Value | Role |
|---|---|---|
| `--step--1` | `0.8125rem` (13px) | Meta, nav links, tags, captions |
| `--step-0` | `1.0625rem` (17px) | Body. Also the root font size |
| `--step-1` | `1.25rem` (20px) | Lede, row titles |
| `--step-2` | `1.5rem` (24px) | `h3`, metric values |
| `--step-3` | `1.9rem` | `h2` |
| `--step-4` | `clamp(2.2rem, 4.5vw, 3.1rem)` | `h1` |
| `--measure` | `34rem` | Reading column, about 66 characters at body size |

Body is Public Sans, `--step-0`, line-height `1.65`, letter-spacing `0.01em`, antialiased.

`h1`, `h2`, and `h3` are Fraunces, weight `650`, letter-spacing `-0.03em`, line-height `1.15`, margin `0`.

| Role | Spec |
|---|---|
| Wordmark | Fraunces, `--step-1`, weight `650`, letter-spacing `-0.02em` |
| Kicker | `0.72rem`, weight `600`, letter-spacing `0.18em`, uppercase, `--accent-text` |
| Meta line | `--ink-muted`, `--step--1`, letter-spacing `0.04em` |
| Lede | `--step-1`, line-height `1.5` |
| Prose link | `--accent-text`, underline 1px, offset 2px |
| Nav link | No underline. `--ink-muted`, weight `600`, `--step--1`. Hover turns `--accent-text` |
| Big number | Fraunces, `--step-2`, tabular figures, `--accent-text` |
| Field label and table header | `0.72rem`, weight `600`, letter-spacing `0.12em`, uppercase, `--ink-muted` |

### Space

A 4px scale. Every margin, padding, and gap is one of these.

| Token | Value |
|---|---|
| `--s1` | `0.25rem` |
| `--s2` | `0.5rem` |
| `--s3` | `0.75rem` |
| `--s4` | `1rem` |
| `--s5` | `1.5rem` |
| `--s6` | `2rem` |
| `--s7` | `3rem` |
| `--s8` | `4.5rem` |

### Motion

Two durations and one curve.

| Token | Value | Use |
|---|---|---|
| `--dur-fast` | `140ms` | Hover, small fades |
| `--dur` | `240ms` | Header, a view entering |
| `--ease` | `cubic-bezier(0.2, 0, 0, 1)` | Both |

`prefers-reduced-motion: reduce` sets both durations to `1ms`. A view entrance animation is disabled under that query. `scrollTo` uses `behavior: 'auto'` under the same query.

| What moves | How |
|---|---|
| A view entering | Opacity `0 → 1` and `translateY(8px → 0)` over `--dur`. Never translate on the X axis |
| Header show and hide | `transform: translateY` and height, over `--dur` |
| Wordmark and service list fading in the condensed header | Opacity over `--dur-fast` |
| A choice row | A 2px copper bar on the left, `scaleY(0 → 1)` from the top, over `--dur-fast` |
| A button | Background color over `--dur-fast` |
| Anything else | Still |

---

## 4. Document

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <meta name="color-scheme" content="light" />
    <title>Files · Storage · Filenkov</title>
    <meta name="description" content="One sentence about this service." />
    <link rel="preload" as="font" type="font/woff2" href="/fonts/fraunces-latin.woff2" crossorigin />
    <link rel="preload" as="font" type="font/woff2" href="/fonts/public-sans-latin.woff2" crossorigin />
    <link rel="stylesheet" href="/filenkov.css" />
  </head>
  <body>
    <div class="shell">
      <a class="skip-link" href="#main">Skip to content</a>
      <header class="header">
        <!-- brand, service list, sign-in slot: sections 5 and 6 -->
      </header>
      <main class="main" id="main">
        <!-- the service -->
      </main>
      <footer class="footer">
        <span>Filenkov · Storage</span>
        <span>Google sign-in · httpOnly cookies on .filenkov.store</span>
      </footer>
    </div>
    <script src="/header.js"></script>
  </body>
</html>
```

The title form is `<view> · <service> · Filenkov`. Replace `Storage` in the footer with the service name.

Landmarks, in order: skip link, `header`, `main`, `footer`. The skip link is the first focusable control. It is hidden above the viewport and slides into place on `:focus-visible`. One `h1` per view. When a view change replaces the `h1`, move focus to that heading (`tabindex="-1"`) and write the title into a visually hidden live region:

```html
<p class="sr-only" aria-live="polite"></p>
```

The first load of a document does not steal focus.

The page background is `--paper`. One grain layer sits on `body::before`: fixed, covering the viewport, `pointer-events: none`, `z-index: 0`, opacity `0.025`. It does not repaint on scroll. `.shell` is `z-index: 1`.

Z-index: grain `0`, shell `1`, header `20`, skip link `30`.

---

## 5. Header

The header is fixed to the top, height `--header-h` (`4.5rem`). `.shell` pads the top by that same amount so content starts below it. Horizontal padding is `--s5`. The items are a row with gap `--s5`, vertically centered.

```html
<header class="header">
  <a class="brand" href="https://filenkov.store">
    <svg class="brand-mark" viewBox="0 0 32 32" aria-hidden="true">
      <path d="M16 3 L29 16 L16 29 L3 16 Z" fill="none" stroke="currentColor" stroke-width="1.75" />
      <circle cx="16" cy="16" r="3.2" fill="var(--accent)" />
    </svg>
    <span class="brand-name">Filenkov</span>
  </a>
  <nav class="service-nav" aria-label="Primary">
    <!-- section 5.1 -->
  </nav>
  <div class="header-right">
    <!-- section 6 -->
  </div>
</header>
```

The mark is 26×26. The diamond stroke uses `currentColor` (ink). The dot is `--accent`. The wordmark links to `https://filenkov.store`.

### 5.1 Service list

Same labels, same order, on every host. The current host is a `<span aria-current="page">`, colored `--ink` at weight `650`, and is not a link.

| Label | Href | Who sees it |
|---|---|---|
| Storage | `https://storage.filenkov.store` | Everyone. Guests also see a lock |
| Image | `https://image.filenkov.store` | Only when the person's **image** service role is `FAMILY` or `ADMIN`. Hidden otherwise. Never locked |
| Chat | `https://llm.filenkov.store` | Only when the hub role `global_role` is `ADMIN`. Hidden otherwise. Never locked |
| Admin | `https://filenkov.store/admin` | Only when `global_role` is `ADMIN`. Hidden otherwise |

A guest lock is the character `🔒` inside `<span class="service-lock" aria-hidden="true">`. On the current host the lock sits inside the `<span aria-current="page">`. On another host the anchor has `title="Sign in required"`. Guests may open Storage. The gateway sends them through sign-in and back. Image, Chat, and Admin stay hidden unless the role check passes. A link that would answer 403 is noise.

`global_role` arrives on the service's backend as the request header `X-Auth-Role`. That is enough to show or hide Chat and Admin. The image role is a different fact and is not in `X-Auth-Role`. Show Image only when the service's own backend knows that role. When it does not, omit Image.

```html
<nav class="service-nav" aria-label="Primary">
  <span aria-current="page">Storage</span>
  <!-- Image, Chat, Admin appended only when the role check passes -->
</nav>
```

Guest version of the current service:

```html
<span aria-current="page">
  Storage<span class="service-lock" aria-hidden="true">🔒</span>
</span>
```

### 5.2 Three header states

The scroll script writes `data-header` on `<html>`. It never re-renders the page to do it.

| `data-header` | When | Look |
|---|---|---|
| `top` | Scroll position under 8px | Full height, transparent, wordmark and service list visible |
| `hidden` | The reader has scrolled down, past 96px, and focus is outside the header | The header is `translateY(-100%)` |
| `condensed` | The reader has scrolled up, or focus is inside the header | Height `--header-h-c` (`3.5rem`). Background is paper at 82% mixed with transparent, `backdrop-filter: blur(8px)`, a 1px bottom rule. Wordmark and service list fade to opacity 0 and ignore hits. The mark and the sign-in slot stay |

Constants:

| Name | Value | Meaning |
|---|---|---|
| `JITTER_FLOOR` | 6px | A smaller frame-to-frame delta is trackpad noise |
| `TRAVEL_THRESHOLD` | 24px | Travel accumulated in one direction before the state flips |
| `TOP_AT` | 8px | Below this the header is `top` |
| `HIDE_BELOW` | 96px | The header stays visible above this |

The previous sample advances only after a frame clears the jitter floor, so a slow drift still adds up. A negative `scrollY` (rubber-banding) is clamped to 0.

Focus inside the header forces `condensed`. The hidden header is translated off-screen and remains focusable, so a keyboard user tabbing into it brings it back. `display: none` and `inert` are the wrong tools for this.

A page that does not scroll may leave `data-header` unset. The unset header is the `top` look.

Save this as `/header.js`:

```html
<script>
  (function () {
    var JITTER_FLOOR = 6
    var TRAVEL_THRESHOLD = 24
    var TOP_AT = 8
    var HIDE_BELOW = 96
    var root = document.documentElement
    root.dataset.header = 'top'

    var last = Math.max(0, window.scrollY)
    var acc = 0
    var frame = 0

    function headerHasFocus() {
      var active = document.activeElement
      if (!active) return false
      var header = document.querySelector('.header')
      return header !== null && header.contains(active)
    }

    function apply() {
      frame = 0
      var y = Math.max(0, window.scrollY)

      if (y < TOP_AT) {
        root.dataset.header = 'top'
        last = y
        acc = 0
        return
      }
      if (headerHasFocus()) {
        root.dataset.header = 'condensed'
        last = y
        acc = 0
        return
      }

      var d = y - last
      if (Math.abs(d) < JITTER_FLOOR) return
      acc = Math.sign(d) === Math.sign(acc) ? acc + d : d
      last = y

      if (acc > TRAVEL_THRESHOLD && y > HIDE_BELOW) root.dataset.header = 'hidden'
      else if (acc < -TRAVEL_THRESHOLD) root.dataset.header = 'condensed'
    }

    window.addEventListener(
      'scroll',
      function () {
        if (frame === 0) frame = window.requestAnimationFrame(apply)
      },
      { passive: true },
    )
  })()
</script>
```

### 5.3 Breakpoints

| Width | Change |
|---|---|
| `1080px` and below | A side column, if the screen has one, drops before the page stacks |
| `760px` and below | Main and header padding become `--s4`. Header gap becomes `--s4`. On a choice row, the meta wraps under the title |
| `620px` and below | The wordmark hides. The diamond still names the product. The service list gap becomes `--s3`, and the list scrolls horizontally with the scrollbar hidden |

---

## 6. Sign-in slot

The slot is the right side of the header (`margin-left: auto`).

| State | Markup |
|---|---|
| Session unknown | `<span class="muted">Checking sign-in…</span>` |
| Guest | `<a class="btn btn-small" href="SIGN_IN">Sign in</a>` |
| Signed in | `<span class="user-name">USERNAME</span>` and `<button type="button" class="btn btn-ghost btn-small">Sign out</button>` |

`USERNAME` is the person's username. Email stays out of the header.

`SIGN_IN` is:

```text
https://filenkov.store/auth?return_to=<current page, absolute URL, percent-encoded>
```

`return_to` is an `https` URL whose host is `filenkov.store` or a subdomain of it (`storage.filenkov.store`, `messanger.filenkov.store`, `image.filenkov.store`, `llm.filenkov.store`, and any later `*.filenkov.store` host). The hub rejects anything else.

### Session facts

The credentials are two httpOnly cookies, `auth_access` and `auth_refresh`, on the domain `.filenkov.store`, `SameSite=Lax`, `Secure` in production. The page never reads the tokens. A service stores nothing in `localStorage` and sends no `Authorization` header of its own.

There is also an httpOnly cookie `auth_session`. It is an opaque id for product vaults. It is not the sign-in credential, and a missing `auth_session` still leaves the person signed in.

The Auth API is same-origin on the hub and sends no CORS headers. These routes exist on `https://filenkov.store` only:

| Method | Path | What it does |
|---|---|---|
| `GET` | `/oauth/google?return_to=` | Starts Google sign-in. The hub auth page calls this. A service does not |
| `POST` | `/api/refresh` | Rotates the refresh cookie. The gateway does this before it proxies a request with an expired access cookie. The service UI does not call it |
| `POST` | `/api/logout` | Revokes the session and clears the cookies. Returns `200` and `{ "status": "ok" }` |
| `GET` | `/api/me` | Returns the signed-in person to the hub. A service does not call it |

A page on `storage.filenkov.store` (or any other product host) cannot `fetch` those URLs. The browser will block it.

What the service can do:

- **Who is signed in.** The gateway injects these headers on the request to the service's backend, and strips any incoming header of the same name so the browser cannot forge them:
  - `X-Auth-User-Id` — the person's UUID
  - `X-Auth-Email`
  - `X-Auth-Name` — the username, which is the header text
  - `X-Auth-Role` — hub `global_role`
- The service's own same-origin API reads those headers and returns the username to its page. That response fills the sign-in slot.
- **Sign in** is a top-level navigation to the `SIGN_IN` URL above. The service draws no Google logo and no password form.
- **Sign out.** There is no logout route on a product host today. `POST /api/logout` lives on the hub, and a cross-origin `fetch` will not clear the cookies. The Sign out control navigates to `https://filenkov.store`. The header there has the button that posts logout. Opening the hub does not sign the person out by itself. A real same-origin logout is a later gateway route on that host. It is not a new color, and it is not a second session. While a logout request is in flight, the button is disabled (`opacity: 0.55`).

Roles, wherever they are shown, use these exact strings: `ADMIN`, `FAMILY`, `STRANGER`, `BLOCKED`. `BLOCKED` gets the class `status-blocked` (danger color, weight 600). A `<select>` with that class also takes a danger border.

`global_role` and a per-service role are different. Chat and Admin follow `global_role`. Image follows the image service role. A service shows its own permission with the same four words.

---

## 7. Page head

Every view opens the same way:

```html
<div class="page-head">
  <div>
    <p class="kicker">Storage</p>
    <h1>Files</h1>
  </div>
</div>
<p class="muted page-lead">One sentence. What this screen is for.</p>
```

The kicker names the service or the section. The `h1` names the view. The lead is optional.

A short form or a dialog sits in `.panel` (max-width `28rem`, padding `--s6`, raised paper, 1px rule). `.stack` is a column with `--s4` between children. `.auth-page` centers that panel horizontally on a gate screen. A service uses it for its own short gate, and the Google button stays on the hub.

---

## 8. Controls

### Buttons

`.btn` is inline-flex, square, `min-height: 2.75rem` (44px), padding `0.7rem 1.1rem`, weight `600`. Fill and border are `--ink`. The label is `--paper`. Hover turns fill and border to `--accent-text`.

| Class | Look | Typical label |
|---|---|---|
| `btn` | Ink fill | The one primary action |
| `btn btn-ghost` | Transparent, ink label. Hover fills `--chip` | Sign out, a secondary action |
| `btn btn-danger` | Transparent, danger label and border. Hover fills danger with a paper label | A destructive action |
| `btn btn-small` | `min-height: 2.25rem`, padding `--s2 --s3`, size `--step--1` | Header controls only |

A link that does the same job as a button uses `<a class="btn">`. Disabled buttons and selects are `opacity: 0.55` with `cursor: not-allowed`.

Primary actions use `.btn`. `.btn-small` is the header exception.

### Fields

`input`, `select`, `textarea`, and `button` inherit the body font.

```html
<div class="field">
  <label for="album-name">Name</label>
  <input id="album-name" name="album-name" type="text" />
</div>
```

A horizontal filter:

```html
<div class="filters">
  <label for="user-email">Email</label>
  <input id="user-email" name="user-email" type="search" autocomplete="off" spellcheck="false" placeholder="Filter by email" />
</div>
```

Fields are square. Background `--paper`, border `1px solid var(--rule)`, text `--ink`. Selects have `min-width: 7.5rem` and slightly tighter padding.

### Banners, tables, empty

```html
<div class="banner">A neutral notice.</div>
<div class="banner error">Could not save the change.</div>

<div class="table-wrap">
  <table>
    <thead>
      <tr><th>Username</th><th>Email</th><th>Role</th></tr>
    </thead>
    <tbody>
      <tr>
        <td>ada</td>
        <td>ada@example.com</td>
        <td class="status-blocked">BLOCKED</td>
      </tr>
    </tbody>
  </table>
</div>

<p class="empty">No one matches that email.</p>
```

The table wraps in a raised surface and scrolls horizontally. Header cells use the uppercase label style on `--chip`. Body cells are nowrap, with a hairline under each row except the last. An empty table, an empty list, and a loading list all use `.empty`, centered, muted, padding `--s6 --s4`.

A dense table adds `table-dense` on `.table-wrap`. Body text drops to `--step--1`, cell padding to `--s1 --s2`, and header tracking to `0.06em` at `0.68rem`. Row actions use a 2rem-tall button. An email or other long value may wrap (`overflow-wrap: anywhere`, max width `16rem`). Numeric fields inside the row are only as wide as the value (`5.5rem` for a quota). A note that explains a column, such as where a role is edited, sits above the table in muted meta text, not inside the header cell.

### States

| State | UI |
|---|---|
| Session loading | The sentence `Checking sign-in…` in `.muted`. The rest of the header does not jump |
| List loading | `.empty` with a specific sentence, such as `Loading files…` |
| Fast response | A skeleton is allowed to paint only after the load has already lasted 120ms. A response that is already here never flashes one |
| Skeleton | Three or more bars, height `0.75rem`, color `--rule` at opacity `0.55`, gap `--s3` |
| Empty | `.empty` with a sentence about that screen |
| Failed action | `.banner.error` with the error text. The rest of the page stays |
| Failed view | `.error-block`: a 2px danger rule on the left, danger text, and one recovery control |
| Blocked role | `.status-blocked` |

```html
<div class="skeleton" aria-hidden="true"><span></span><span></span><span></span></div>

<div class="error-block">
  <p>Could not load files.</p>
  <button type="button" class="btn">Reload</button>
</div>
```

The 120ms gate. `setActive(true)` when the load starts, `setActive(false)` when it finishes. `onChange(true)` means the skeleton may paint. A load that finishes inside 120ms never calls `onChange(true)`.

```javascript
function delayedFlag(onChange, delayMs) {
  var delay = delayMs == null ? 120 : delayMs
  var timer = 0
  var shown = false
  return function setActive(active) {
    window.clearTimeout(timer)
    timer = 0
    if (!active) {
      if (shown) onChange(false)
      shown = false
      return
    }
    if (shown) return
    timer = window.setTimeout(function () {
      shown = true
      onChange(true)
    }, delay)
  }
}
```

Loading copy names the thing that is loading. A spinner is not part of the language.

---

## 9. Prose

Reading text sits in a column of `--measure`, centered:

```html
<article class="prose">
  <p class="lede">The first sentence, one step larger than body.</p>
  <p>Body text. A <a href="/folders/photos">prose link</a> is copper and underlined.</p>
</article>
```

Compose longer text from the blocks below. Plain `p`, `ul`, and `ol` are the rest of the vocabulary.

### Section

The only `h2` on the page. A hairline above it.

```html
<section class="a-section">
  <h2>Sharing</h2>
  <p>Who can open this folder.</p>
  <h3>Links</h3>
  <p>A link expires after seven days.</p>
</section>
```

Inside a section the `h2` is `--step-2` and an `h3` is `--step-1`.

### Tags

```html
<ul class="a-chips">
  <li>PDF</li>
  <li>Family</li>
</ul>
```

Render nothing when the list is empty.

### Figure

The raised surface. Give the image `width` and `height` so the box is reserved. Loading is lazy.

```html
<figure class="a-figure">
  <img src="/shot.png" alt="The folder list" width="800" height="450" loading="lazy" decoding="async" />
  <figcaption>The folder list after upload.</figcaption>
</figure>
```

A figure wider than the column scrolls inside `.a-figure-scroll`. The image in that scroller has `min-width: 34rem` and no max-width.

```html
<figure class="a-figure">
  <div class="a-figure-scroll">
    <img src="/wide.png" alt="A wide diagram" width="1400" height="600" loading="lazy" decoding="async" />
  </div>
  <figcaption>Read it by scrolling sideways.</figcaption>
</figure>
```

### Callout

A 2px copper rule on the left. The optional title is weight `650`.

```html
<aside class="a-callout">
  <p class="a-callout-title">Keep the original</p>
  <p>Re-encoding this file drops the camera metadata.</p>
</aside>
```

### Metrics

A number, what it is, and how it was measured. The third line is the point of the block.

```html
<ul class="a-metrics">
  <li class="a-metric">
    <span class="a-metric-value">2×</span>
    <span class="a-metric-label">Smaller uploads</span>
    <span class="a-metric-how">Median bytes, same folder, March</span>
  </li>
</ul>
```

### Timeline

```html
<ol class="a-timeline">
  <li>
    <span class="a-timeline-when">12 Mar</span>
    <span class="a-timeline-what">Uploaded</span>
    <span class="a-timeline-detail">Four photos from the camera</span>
  </li>
</ol>
```

### Definition list

```html
<dl class="a-deflist">
  <div>
    <dt>Owner</dt>
    <dd>The person who created the folder</dd>
  </div>
</dl>
```

### Role line

A Fraunces copper line under a title, for a subtitle that is not the kicker and not the lead:

```html
<p class="role-line">Family archive</p>
```

---

## 10. Choice rows

When the reader picks a next place — a folder, a thread, a document — the row is a hairline list, not a card. The whole row is one link. Minimum height `4.5rem`.

```html
<ul class="choice-list">
  <li class="choice">
    <a href="/folders/photos">
      <span class="choice-num">01</span>
      <span class="choice-body">
        <span class="choice-title">Photos</span>
        <span class="choice-teaser">Family albums from this year</span>
      </span>
      <span class="choice-meta">128</span>
      <span class="choice-arrow" aria-hidden="true">→</span>
    </a>
  </li>
  <li class="choice">
    <a href="/folders/scans">
      <span class="choice-num">02</span>
      <span class="choice-body">
        <span class="choice-title">Scans</span>
        <span class="choice-teaser">Documents</span>
      </span>
      <span class="choice-meta">14</span>
      <span class="choice-arrow" aria-hidden="true">→</span>
    </a>
  </li>
</ul>
```

The index is Fraunces, tabular figures, `--accent-text`, `--step--1`. The title is `--step-1`, weight `600`. The teaser and the meta are `--step--1` and muted. The teaser line-height is `1.45`. Hover and keyboard focus grow the copper bar and turn the title `--accent-text`.

A sibling list under a row, when the screen has one, is a wrap of muted links, weight `600`, `--step--1`, gap `--s2` by `--s4`. Hover turns them `--accent-text`. A link back up is the same type, with `margin-top: var(--s6)`.

```html
<ul class="siblings">
  <li><a href="/folders/photos">Photos</a></li>
  <li><a href="/folders/scans">Scans</a></li>
</ul>
<a class="up" href="/folders">Up</a>
```

---

## 11. Focus and targets

```css
:focus-visible {
  outline: 2px solid var(--accent-text);
  outline-offset: 2px;
  border-radius: 1px;
}
```

That ring is the only radius on the page, apart from the diamond's dot. Hit targets for a primary control are at least 44×44px. `.btn` meets that. `.btn-small` is the header exception at `2.25rem`. A button inside `.table-dense` is `2rem` tall. Choice rows are at least `4.5rem` tall.

`.sr-only` hides text visually and keeps it available to assistive tech. The stylesheet defines it.

A keyboard walk from the top of the document reaches the skip link, then the header, then the main content, with the ring visible on every stop.

---

## 12. Checklist

A service looks like the others when every line is true.

1. The background is `--paper`, the grain is the one in the stylesheet, and the service CSS contains no `box-shadow` and no hex outside `:root`.
2. Copper text is `--accent-text`. `--accent` is a mark, a bar, a selection, or a stroke.
3. The header shows the diamond, the wordmark linking to `https://filenkov.store`, the service list from section 5.1 with this host as `aria-current="page"`, and either `Checking sign-in…`, `Sign in`, or the username plus `Sign out`.
4. Guests reach `https://filenkov.store/auth?return_to=…`. The service draws no Google button and reads no JWT.
5. The first focusable control is the skip link. The view has one `h1` under a kicker. Buttons, fields, banners, and tables use the classes in the stylesheet.
6. A list the reader chooses from is a hairline choice row. A short form sits in `.panel`.
7. Loading, empty, and error copy are specific sentences. A skeleton waits 120ms.
8. At 620px the wordmark is gone and the service list still scrolls. At 760px the page padding is `--s4`, a dense table is a stack of records, and the document itself does not scroll sideways. See section 15.
9. `prefers-reduced-motion` collapses the two durations. A keyboard walk shows the focus ring on every stop.
10. `lang` is `en`, `color-scheme` is `light`, the faces are the self-hosted files, and the tab icon is the diamond below.

---

## 13. Stylesheet

Save this as `/filenkov.css`. It is the entire visual layer.

```css
/* Filenkov service stylesheet. One file. */

@font-face {
  font-family: 'Fraunces';
  font-style: normal;
  font-weight: 100 900;
  font-display: swap;
  src: url('/fonts/fraunces-latin.woff2') format('woff2');
  unicode-range: U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308,
    U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD;
}

@font-face {
  font-family: 'Fraunces';
  font-style: normal;
  font-weight: 100 900;
  font-display: swap;
  src: url('/fonts/fraunces-latin-ext.woff2') format('woff2');
  unicode-range: U+0100-02BA, U+02BD-02C5, U+02C7-02CC, U+02CE-02D7, U+02DD-02FF, U+0304, U+0308, U+0329,
    U+1D00-1DBF, U+1E00-1E9F, U+1EF2-1EFF, U+2020, U+20A0-20AB, U+20AD-20C0, U+2113, U+2C60-2C7F, U+A720-A7FF;
}

@font-face {
  font-family: 'Public Sans';
  font-style: normal;
  font-weight: 100 900;
  font-display: swap;
  src: url('/fonts/public-sans-latin.woff2') format('woff2');
  unicode-range: U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308,
    U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD;
}

@font-face {
  font-family: 'Public Sans';
  font-style: normal;
  font-weight: 100 900;
  font-display: swap;
  src: url('/fonts/public-sans-latin-ext.woff2') format('woff2');
  unicode-range: U+0100-02BA, U+02BD-02C5, U+02C7-02CC, U+02CE-02D7, U+02DD-02FF, U+0304, U+0308, U+0329,
    U+1D00-1DBF, U+1E00-1E9F, U+1EF2-1EFF, U+2020, U+20A0-20AB, U+20AD-20C0, U+2113, U+2C60-2C7F, U+A720-A7FF;
}

@font-face {
  font-family: 'Public Sans';
  font-style: italic;
  font-weight: 100 900;
  font-display: swap;
  src: url('/fonts/public-sans-italic-latin.woff2') format('woff2');
  unicode-range: U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308,
    U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD;
}

@font-face {
  font-family: 'Public Sans';
  font-style: italic;
  font-weight: 100 900;
  font-display: swap;
  src: url('/fonts/public-sans-italic-latin-ext.woff2') format('woff2');
  unicode-range: U+0100-02BA, U+02BD-02C5, U+02C7-02CC, U+02CE-02D7, U+02DD-02FF, U+0304, U+0308, U+0329,
    U+1D00-1DBF, U+1E00-1E9F, U+1EF2-1EFF, U+2020, U+20A0-20AB, U+20AD-20C0, U+2113, U+2C60-2C7F, U+A720-A7FF;
}

@font-face {
  font-family: 'Public Sans Fallback';
  src: local('Arial'), local('Helvetica'), local('Liberation Sans');
  size-adjust: 107%;
  ascent-override: 92%;
  descent-override: 24%;
  line-gap-override: 0%;
}

@font-face {
  font-family: 'Fraunces Fallback';
  src: local('Georgia'), local('Times New Roman'), local('Liberation Serif');
  size-adjust: 104%;
  ascent-override: 94%;
  descent-override: 24%;
  line-gap-override: 0%;
}

:root {
  --paper: #f4efe6;
  --paper-raised: #fffdf8;
  --ink: #1a1612;
  --ink-muted: #5c5348;
  --rule: #d9d1c3;
  --rule-strong: #b9ae9c;
  --accent: #b85c38;
  --accent-text: #8f4128;
  --chip: #e6ddd0;
  --danger: #8c2f24;
  --danger-wash: #f4e4dc;

  --serif: 'Fraunces', 'Fraunces Fallback', 'Iowan Old Style', 'Palatino Linotype', serif;
  --sans: 'Public Sans', 'Public Sans Fallback', system-ui, 'Segoe UI', sans-serif;

  --step--1: 0.8125rem;
  --step-0: 1.0625rem;
  --step-1: 1.25rem;
  --step-2: 1.5rem;
  --step-3: 1.9rem;
  --step-4: clamp(2.2rem, 4.5vw, 3.1rem);

  --measure: 34rem;

  --s1: 0.25rem;
  --s2: 0.5rem;
  --s3: 0.75rem;
  --s4: 1rem;
  --s5: 1.5rem;
  --s6: 2rem;
  --s7: 3rem;
  --s8: 4.5rem;

  --dur-fast: 140ms;
  --dur: 240ms;
  --ease: cubic-bezier(0.2, 0, 0, 1);

  --header-h: 4.5rem;
  --header-h-c: 3.5rem;
}

@media (prefers-reduced-motion: reduce) {
  :root {
    --dur-fast: 1ms;
    --dur: 1ms;
  }
}

*,
*::before,
*::after {
  box-sizing: border-box;
}

html,
body {
  min-height: 100%;
}

html {
  scroll-behavior: auto;
}

body {
  margin: 0;
  color: var(--ink);
  background: var(--paper);
  font-family: var(--sans);
  font-size: var(--step-0);
  line-height: 1.65;
  letter-spacing: 0.01em;
  -webkit-font-smoothing: antialiased;
}

body::before {
  content: '';
  position: fixed;
  inset: 0;
  pointer-events: none;
  z-index: 0;
  opacity: 0.025;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='120' height='120'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2'/%3E%3C/filter%3E%3Crect width='120' height='120' filter='url(%23n)'/%3E%3C/svg%3E");
}

::selection {
  background: var(--accent);
  color: var(--paper);
}

a {
  color: inherit;
}

button,
select,
input,
textarea {
  font: inherit;
}

:focus-visible {
  outline: 2px solid var(--accent-text);
  outline-offset: 2px;
  border-radius: 1px;
}

h1,
h2,
h3 {
  font-family: var(--serif);
  font-weight: 650;
  letter-spacing: -0.03em;
  line-height: 1.15;
  margin: 0;
}

h1 { font-size: var(--step-4); }
h2 { font-size: var(--step-3); }
h3 { font-size: var(--step-2); }

.kicker {
  font-size: 0.72rem;
  font-weight: 600;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--accent-text);
  margin: 0 0 var(--s3);
}

.muted { color: var(--ink-muted); }

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  margin: -1px;
  padding: 0;
  overflow: hidden;
  clip: rect(0 0 0 0);
  clip-path: inset(50%);
  white-space: nowrap;
  border: 0;
}

.shell {
  position: relative;
  z-index: 1;
  min-height: 100svh;
  display: flex;
  flex-direction: column;
  padding-top: var(--header-h);
}

.skip-link {
  position: absolute;
  left: var(--s4);
  top: var(--s2);
  z-index: 30;
  transform: translateY(-200%);
  background: var(--ink);
  color: var(--paper);
  padding: var(--s2) var(--s4);
  text-decoration: none;
  font-weight: 600;
}

.skip-link:focus-visible { transform: translateY(0); }

.header {
  position: fixed;
  inset: 0 0 auto;
  z-index: 20;
  height: var(--header-h);
  display: flex;
  align-items: center;
  gap: var(--s5);
  padding: 0 var(--s5);
  transition:
    transform var(--dur) var(--ease),
    height var(--dur) var(--ease);
}

[data-header='hidden'] .header { transform: translateY(-100%); }

[data-header='condensed'] .header {
  height: var(--header-h-c);
  background: color-mix(in srgb, var(--paper) 82%, transparent);
  backdrop-filter: blur(8px);
  border-bottom: 1px solid var(--rule);
}

[data-header='condensed'] .brand-name,
[data-header='condensed'] .service-nav {
  opacity: 0;
  pointer-events: none;
}

.brand {
  display: flex;
  align-items: center;
  gap: var(--s3);
  text-decoration: none;
  color: var(--ink);
  flex: none;
}

.brand-mark {
  width: 26px;
  height: 26px;
  flex: none;
}

.brand-name,
.service-nav { transition: opacity var(--dur-fast) var(--ease); }

.brand-name {
  font-family: var(--serif);
  font-size: var(--step-1);
  font-weight: 650;
  letter-spacing: -0.02em;
}

.service-nav {
  display: flex;
  align-items: center;
  gap: var(--s4);
  min-width: 0;
  overflow: hidden;
}

.service-nav a,
.service-nav [aria-current='page'] {
  text-decoration: none;
  color: var(--ink-muted);
  font-size: var(--step--1);
  font-weight: 600;
  white-space: nowrap;
}

.service-nav a:hover { color: var(--accent-text); }

.service-nav [aria-current='page'] {
  color: var(--ink);
  font-weight: 650;
}

.service-lock {
  font-size: 0.7em;
  margin-left: 0.25em;
}

.header-right {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: var(--s3);
  flex: none;
}

.user-name {
  font-weight: 600;
  font-size: var(--step--1);
}

.main {
  flex: 1;
  min-width: 0;
  padding: var(--s5) var(--s5) var(--s8);
}

.footer {
  display: flex;
  justify-content: space-between;
  gap: var(--s4);
  flex-wrap: wrap;
  padding: var(--s5);
  border-top: 1px solid var(--rule);
  color: var(--ink-muted);
  font-size: var(--step--1);
}

.page-head {
  display: flex;
  justify-content: space-between;
  align-items: end;
  gap: var(--s4);
  margin-bottom: var(--s5);
}

.page-lead { margin: calc(var(--s2) * -1) 0 var(--s5); }

.prose { max-width: var(--measure); }

.prose p,
.prose ul,
.prose ol { margin: 0 0 var(--s4); }

.prose ul,
.prose ol { padding-left: var(--s5); }

.prose li + li { margin-top: var(--s2); }

.prose a {
  color: var(--accent-text);
  text-decoration-thickness: 1px;
  text-underline-offset: 2px;
}

.lede {
  font-size: var(--step-1);
  line-height: 1.5;
  color: var(--ink);
  margin: 0 0 var(--s5);
}

.role-line {
  margin: var(--s3) 0 var(--s5);
  font-family: var(--serif);
  font-size: var(--step-2);
  font-weight: 650;
  color: var(--accent-text);
}

.a-section {
  margin: var(--s7) 0 0;
  padding-top: var(--s5);
  border-top: 1px solid var(--rule);
}

.a-section > h2 {
  font-size: var(--step-2);
  margin: 0 0 var(--s4);
}

.a-section h3 {
  font-size: var(--step-1);
  margin: var(--s5) 0 var(--s3);
}

.a-chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--s2);
  list-style: none;
  margin: 0 0 var(--s4);
  padding: 0;
}

.a-chips li {
  background: var(--chip);
  border: 1px solid var(--rule);
  padding: 0.1rem 0.5rem;
  font-size: var(--step--1);
  color: var(--ink);
}

.a-figure {
  margin: var(--s5) 0;
  background: var(--paper-raised);
  border: 1px solid var(--rule);
  padding: var(--s4);
}

.a-figure-scroll { overflow-x: auto; }

.a-figure img {
  display: block;
  max-width: 100%;
  height: auto;
}

.a-figure-scroll img {
  max-width: none;
  min-width: 34rem;
}

.a-figure figcaption {
  margin-top: var(--s3);
  color: var(--ink-muted);
  font-size: var(--step--1);
}

.a-callout {
  margin: var(--s5) 0;
  padding-left: var(--s4);
  border-left: 2px solid var(--accent);
}

.a-callout p { margin: 0 0 var(--s2); }
.a-callout-title { font-weight: 650; }

.a-metrics {
  list-style: none;
  margin: var(--s5) 0;
  padding: 0;
  border-top: 1px solid var(--rule);
}

.a-metric {
  display: grid;
  grid-template-columns: minmax(6rem, auto) minmax(0, 1fr);
  gap: var(--s1) var(--s4);
  padding: var(--s4) 0;
  border-bottom: 1px solid var(--rule);
}

.a-metric-value {
  grid-row: 1 / span 2;
  font-family: var(--serif);
  font-size: var(--step-2);
  font-variant-numeric: tabular-nums;
  color: var(--accent-text);
  line-height: 1.2;
}

.a-metric-label { font-weight: 600; }

.a-metric-how {
  color: var(--ink-muted);
  font-size: var(--step--1);
}

.a-timeline {
  list-style: none;
  margin: var(--s5) 0;
  padding: 0 0 0 var(--s4);
  border-left: 1px solid var(--rule);
}

.a-timeline li {
  display: grid;
  gap: var(--s1);
  padding-bottom: var(--s4);
}

.a-timeline-when {
  font-size: var(--step--1);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--accent-text);
}

.a-timeline-what { font-weight: 600; }
.a-timeline-detail { color: var(--ink-muted); }

.a-deflist { margin: var(--s5) 0; }

.a-deflist > div {
  padding: var(--s3) 0;
  border-bottom: 1px solid var(--rule);
}

.a-deflist dt { font-weight: 650; }

.a-deflist dd {
  margin: var(--s1) 0 0;
  color: var(--ink-muted);
}

.choice-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.choice + .choice { border-top: 1px solid var(--rule); }
.choice:last-child { border-bottom: 1px solid var(--rule); }

.choice > a {
  position: relative;
  display: grid;
  grid-template-columns: 2.5rem minmax(0, 1fr) auto auto;
  align-items: baseline;
  gap: var(--s3);
  min-height: 4.5rem;
  padding: var(--s4) var(--s2) var(--s4) var(--s3);
  text-decoration: none;
  color: inherit;
}

.choice > a::before {
  content: '';
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 2px;
  background: var(--accent);
  transform: scaleY(0);
  transform-origin: top;
  transition: transform var(--dur-fast) var(--ease);
}

.choice > a:hover::before,
.choice > a:focus-visible::before { transform: scaleY(1); }

.choice > a:hover .choice-title,
.choice > a:focus-visible .choice-title { color: var(--accent-text); }

.choice-num {
  font-family: var(--serif);
  font-variant-numeric: tabular-nums;
  color: var(--accent-text);
  font-size: var(--step--1);
}

.choice-body {
  display: grid;
  gap: var(--s1);
  min-width: 0;
}

.choice-title {
  font-size: var(--step-1);
  font-weight: 600;
  line-height: 1.25;
}

.choice-teaser {
  font-size: var(--step--1);
  color: var(--ink-muted);
  line-height: 1.45;
}

.choice-meta {
  font-size: var(--step--1);
  color: var(--ink-muted);
  white-space: nowrap;
}

.choice-arrow { color: var(--ink-muted); }

.siblings {
  list-style: none;
  display: flex;
  flex-wrap: wrap;
  gap: var(--s2) var(--s4);
  margin: 0;
  padding: var(--s3) 0 0;
}

.siblings a,
.up {
  color: var(--ink-muted);
  text-decoration: none;
  font-weight: 600;
  font-size: var(--step--1);
}

.up {
  display: inline-block;
  margin-top: var(--s6);
}

.siblings a:hover,
.up:hover { color: var(--accent-text); }

.panel {
  background: var(--paper-raised);
  border: 1px solid var(--rule);
  padding: var(--s6);
  max-width: 28rem;
}

.auth-page {
  display: grid;
  place-items: start;
  justify-content: center;
}

.stack {
  display: flex;
  flex-direction: column;
  gap: var(--s4);
}

.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--s3);
  border: 1px solid var(--ink);
  background: var(--ink);
  color: var(--paper);
  padding: 0.7rem 1.1rem;
  min-height: 2.75rem;
  cursor: pointer;
  text-decoration: none;
  font-weight: 600;
  letter-spacing: 0.01em;
  transition: background var(--dur-fast) var(--ease);
}

.btn:hover {
  background: var(--accent-text);
  border-color: var(--accent-text);
}

.btn-ghost {
  background: transparent;
  color: var(--ink);
}

.btn-ghost:hover {
  background: var(--chip);
  color: var(--ink);
}

.btn-danger {
  background: transparent;
  color: var(--danger);
  border-color: var(--danger);
}

.btn-danger:hover {
  background: var(--danger);
  color: var(--paper);
}

.btn-small {
  padding: var(--s2) var(--s3);
  min-height: 2.25rem;
  font-size: var(--step--1);
}

.banner {
  padding: var(--s3) var(--s4);
  border: 1px solid var(--rule);
  background: var(--chip);
  color: var(--ink);
  margin-bottom: var(--s4);
}

.banner.error {
  border-color: var(--danger);
  background: var(--danger-wash);
  color: var(--danger);
}

.table-wrap {
  overflow-x: auto;
  border: 1px solid var(--rule);
  background: var(--paper-raised);
}

table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.9rem;
}

th,
td {
  text-align: left;
  padding: var(--s3);
  border-bottom: 1px solid var(--rule);
  vertical-align: middle;
  white-space: nowrap;
}

th {
  font-size: 0.72rem;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--ink-muted);
  font-weight: 600;
  background: var(--chip);
}

tr:last-child td { border-bottom: 0; }

.table-dense {
  position: relative;
}

.table-dense table {
  font-size: var(--step--1);
}

.table-dense th,
.table-dense td {
  padding: var(--s1) var(--s2);
}

.table-dense th {
  font-size: 0.68rem;
  letter-spacing: 0.06em;
}

.table-dense .cell-email {
  white-space: normal;
  overflow-wrap: anywhere;
  max-width: 16rem;
}

.table-dense .actions {
  flex-wrap: nowrap;
}

.table-dense .btn {
  min-height: 2rem;
  padding: var(--s1) var(--s2);
  font-size: var(--step--1);
}

.status-blocked {
  color: var(--danger);
  font-weight: 600;
}

select.status-blocked { border-color: var(--danger); }

.field {
  display: flex;
  flex-direction: column;
  gap: var(--s2);
}

.filters {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--s3);
  margin-bottom: var(--s5);
}

.field > label,
.filters label {
  font-size: 0.72rem;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--ink-muted);
  font-weight: 600;
}

.field input,
.field textarea,
.filters input,
select {
  background: var(--paper);
  border: 1px solid var(--rule);
  color: var(--ink);
  border-radius: 0;
}

.field input,
.field textarea,
.filters input {
  padding: var(--s2) var(--s3);
}

.filters input {
  flex: 1 1 18rem;
  min-width: 12rem;
}

.field textarea {
  width: 100%;
  resize: vertical;
}

select {
  padding: var(--s1) var(--s2);
  min-width: 7.5rem;
}

select:disabled,
button:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.empty {
  padding: var(--s6) var(--s4);
  text-align: center;
  color: var(--ink-muted);
}

.skeleton {
  min-height: 18rem;
  display: grid;
  gap: var(--s3);
  align-content: start;
}

.skeleton span {
  height: 0.75rem;
  background: var(--rule);
  opacity: 0.55;
}

.error-block {
  min-height: 18rem;
  border-left: 2px solid var(--danger);
  padding-left: var(--s4);
}

.error-block p {
  margin: 0 0 var(--s4);
  color: var(--danger);
}

.view-in { animation: view-in var(--dur) var(--ease) both; }

@keyframes view-in {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
}

@media (max-width: 760px) {
  .main {
    padding-left: var(--s4);
    padding-right: var(--s4);
  }

  .header {
    gap: var(--s4);
    padding: 0 var(--s4);
  }

  .choice > a { grid-template-columns: 2rem minmax(0, 1fr) auto; }

  .choice-meta {
    grid-column: 2;
    white-space: normal;
  }

  .filters {
    align-items: stretch;
  }

  .filters input {
    flex: 1 1 100%;
    min-width: 0;
  }

  .page-head {
    flex-direction: column;
    align-items: stretch;
  }

  .footer {
    padding: var(--s4);
  }

  .table-dense {
    overflow: visible;
    border: 0;
    background: transparent;
  }

  .table-dense table,
  .table-dense tbody,
  .table-dense tr,
  .table-dense td {
    display: block;
    width: 100%;
  }

  .table-dense thead {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
  }

  .table-dense tr {
    border: 1px solid var(--rule);
    background: var(--paper-raised);
    margin-bottom: var(--s3);
  }

  .table-dense td {
    display: grid;
    grid-template-columns: minmax(6.5rem, 38%) minmax(0, 1fr);
    align-items: center;
    gap: var(--s2);
    max-width: none;
    white-space: normal;
    text-align: right;
    padding: var(--s2) var(--s3);
  }

  .table-dense td::before {
    content: attr(data-label);
    text-align: left;
    font-size: 0.68rem;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--ink-muted);
    font-weight: 600;
  }

  .table-dense td[colspan] {
    display: block;
    text-align: center;
  }

  .table-dense td[colspan]::before {
    content: none;
  }

  .table-dense .cell-email {
    max-width: none;
  }

  .table-dense .actions {
    justify-content: flex-end;
  }

  .table-dense .quota-input {
    justify-self: end;
  }
}

@media (max-width: 620px) {
  .brand-name { display: none; }

  .service-nav {
    gap: var(--s3);
    overflow-x: auto;
    scrollbar-width: none;
  }

  .service-nav::-webkit-scrollbar { display: none; }
}

@media (prefers-reduced-motion: reduce) {
  .view-in { animation: none; }
}
```

---

## 14. Favicon

Save this as `/favicon.svg`.

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
  <rect width="32" height="32" fill="#f4efe6"/>
  <path d="M16 3 L29 16 L16 29 L3 16 Z" fill="none" stroke="#1a1612" stroke-width="1.75"/>
  <circle cx="16" cy="16" r="3.2" fill="#b85c38"/>
</svg>
```

---

## 15. Narrow screens

Look at every view at `1280px` and at `390px` wide. The document itself does not scroll horizontally. `.main` is `min-width: 0`, so a wide table scrolls inside `.table-wrap` instead of stretching the page. A control that can be reached only by flicking the page sideways is a failed layout. Two strips may scroll on their own: the service list in the header, and a storage side nav that has become a horizontal row.

| Width | What changes |
|---|---|
| `1080px` and below | A storage side nav becomes one horizontal strip under the header. Labels stay visible, the strip scrolls, and the collapse control is hidden. The usage line sits under that strip, full width. |
| `760px` and below | Page and footer padding are `--s4`. The page head stacks, title above actions. Filters wrap, and the search field takes the full row. A pager’s action pair stretches across the row. Card grids are one column. |
| `620px` and below | The wordmark is hidden. The service list scrolls inside the header. |

### Dense tables on a phone

Under `760px`, `.table-dense` stops being a wide grid. Each `tr` is a raised block with a hairline border and `--paper-raised` background. The header row is clipped off screen and stays in the document. Each `td` carries `data-label` with the same words as its header. The label is on the left, the value on the right, in a two-column grid (`minmax(6.5rem, 38%)` and the rest). A loading or empty cell uses `colspan` and does not grow a label. Row actions sit at the end of their line. A numeric field aligns to the end of its cell.

Use the stack when the table has more than four columns. A table of three columns may keep scrolling inside `.table-wrap`.

```html
<div class="table-wrap table-dense">
  <table>
    <thead>
      <tr><th>Email</th><th>Role</th><th>Actions</th></tr>
    </thead>
    <tbody>
      <tr>
        <td class="cell-email" data-label="Email">ada@example.com</td>
        <td data-label="Role">ADMIN</td>
        <td data-label="Actions"><div class="actions"><button type="button" class="btn btn-small btn-danger">Block</button></div></td>
      </tr>
    </tbody>
  </table>
</div>
```

### What stays large

Primary actions outside a table stay at least 44×44px. The phone layout uses smaller type and padding inside records. It does not shrink the page’s main buttons. Segmented filters may wrap onto a second line. Their hit target stays `2.75rem`.

Long emails wrap anywhere. Dates and quotas stay on one line inside their value column. Disk and other summary cards already use a wrapping grid. On a phone that grid is a single column so a meter and its figures are readable without sideways scrolling.

