# Brand

The LetMeHandle logo and every asset made from it.

## The logo

`lmh` in monoline with a smile beneath it. The lettering is drawn, not typed, so it needs no
font and scales as a vector.

| File | Use |
|---|---|
| `source/lockup.svg`, `lockup-reversed.svg` | mark and name together — the default logo |
| `source/mark.svg`, `mark-reversed.svg` | the mark alone, on light / on violet or dark |
| `source/mark-mono.svg` | one colour: masks, system tinting |
| `source/mark-small.svg` | heavier strokes, for 32 px and below |
| `source/wordmark.svg`, `wordmark-reversed.svg` | the name alone |
| `source/app-icon.svg` | 1024 px app icon, full bleed |

## Colour

| | |
|---|---|
| Violet `#7C6BEA` | the letters and the brand |
| Violet deep `#4F3FB8` | text in the brand colour |
| Icon ground `#8E7CF3 → #6D5AE0` | app icon gradient |
| Apricot `#F59A5E` / `#FFC9A3` | the smile, on light / on violet |
| Ink `#1D1436` | "LetMe" in the name |
| Ground `#F8F6FF` | launch screens and backgrounds |

## Rules

- Clear space around the logo: at least the height of the mark's arches.
- Minimum size: logo 120 px wide; mark 24 px, or 16 px with `mark-small.svg`.
- Keep the smile apricot. Don't set the name in a typeface, stretch, rotate, outline, or add effects.

## Assets

- **Web** (`web/`, served from the site root): favicons, Apple touch icon, PWA and maskable icons, Safari pinned-tab icon, `site.webmanifest`, 1200 × 630 `og-image.png`, logo SVGs. `head.html` is the `<head>` block that wires them up, title included.
- **Stores and social** (`export/`): App Store icon (no alpha), Play Store icon, Play feature graphic, social avatar, and the 2560 × 1280 `readme-banner.png` at the top of the repository README.
- **iOS** (`apps/mobile/ios/LetMeHandle/Images.xcassets`): `AppIcon`, `LaunchLogo`, `LaunchBackground`, used by `LaunchScreen.storyboard`.
- **Android** (`apps/mobile/android/app/src/main/res`): adaptive icon with monochrome layer, legacy launcher PNGs, `ic_notification` for push, launch screen and API 31+ splash, brand colours in `values/colors.xml`.
