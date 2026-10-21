# New York Life design rules for the Citi fee accruals tool

These rules apply to the HTML demo and to the Streamlit app. Both use one stylesheet,
[nyl.css](nyl.css). If a rule here and the stylesheet ever disagree, fix the stylesheet.

## Colour

| Colour | Hex | Used for |
|---|---|---|
| Dark Blue | `#000A62` | Main surfaces, headlines, buttons, table headers, metric numbers |
| Blue | `#0468FF` | Eyebrow labels above headings, section markers, links |
| Green on Light Green | `#016355` on `#A5EFBF` | Pass and match states |
| White and Grey | `#FFFFFF`, `#E3E0DD` | The only light backgrounds |

Status chips are the only other coloured backgrounds:

| Chip | Background | Text |
|---|---|---|
| Match, pass | `#A5EFBF` | `#016355` |
| Review | `#FFE8CF` | `#6B3B00` |
| No match, fail | `#FBE3E4` | `#9B111E` |
| Info, approved, manual | `#DCEBFF` | `#000A62` |
| Plain | transparent, `#E3E0DD` border | body text |

## Type

- Georgia for every headline, metric number and hero figure. Arial for everything else.
- Eyebrows (the small label above a heading) are Blue Georgia. Titles are Dark Blue Georgia.
- Sentence case everywhere. No all-caps labels, no middle-dot separators, and no arrows in buttons.

## Flat

- No gradients and no shadows.
- No rounded corners on buttons or inputs. Chips can have 2px corners at most.
- Cards are white with a 1px Grey `#E3E0DD` border.
- No coloured ribbon bars, no coloured left borders on notices, and no accent line under titles.

## The signature diagonal

- The hero is a Dark Blue field cut by a steep diagonal of about 67 degrees, over a Grey right side. In
  CSS it's `clip-path: polygon(0 0, 60% 0, 51% 100%, 0 100%)`.
- Hero text sits only on the Dark Blue field, and right-side content sits only on the Grey.
- Under 960px wide, drop the diagonal. The hero text gets its own Dark Blue block with a slanted bottom
  edge, and the right-side content goes on Grey below it.
- Text must never sit across the diagonal or on the wrong background.

## Logo

- Use `assets/nyl_logo.png` exactly as supplied. Never recreate it in CSS or text, recolour it, crop it or
  stretch it.
- It appears in the header and in the footer.

## Footer and names

- The footer is the logo plus "New York Life Insurance Company. For internal use only."
- No person's name appears anywhere in the product. That covers code, comments, sample data and the UI.
- Examples use generic addresses such as `analyst@newyorklife.com`, and the email draft is signed
  "[Your name]".

## Process steps

- Four chevrons in this order: Dark Blue, Blue, Green, Light Green.
- Light Green has Dark Blue text. The others have white text.

## Charts

- Flat bars, Dark Blue for a single series.
- A thin `#888888` axis and no gridlines.

## Dark mode

- Every colour is a `:root` variable, so dark mode only changes values.
- Dark mode applies when the page sets `data-theme="dark"` on `<html>`, or when the browser prefers dark and
  the page hasn't forced `data-theme="light"`.

| Element | Dark value |
|---|---|
| Page background | `#000A62` |
| Cards | `#0A1570` |
| Alternate band | `#111D7C` |
| Text | white |
| Eyebrows | `#80BAFF` |
| Buttons | white with Dark Blue text |
| Chart bars | `#80BAFF` |
| First process step | `#1A2690`, so it stays visible against the page |

- Status chips keep their light colours in dark mode.
- The header and footer follow the theme. The supplied logo reads on both white and Dark Blue.
