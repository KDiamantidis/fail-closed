# Shot list

Record at 1920×1080 (browser at 100% zoom, window maximised). One take per scene is fine; cut
between scenes. Before **every** app scene: Overview → **Reset demo** (the strip says "Normal").
Times are where each scene starts in `video-script.md` (speaking time at 110 words per minute).

| # | Time | On screen | Start state | Must be visible |
|---|---|---|---|---|
| 1 | 0:00 | Slide 2 "The problem" | slides, full screen | both cards; the disclaimer line at the bottom |
| 2 | 0:27 | Slide 3 "The cost nobody counts" | | "Both exchanges paused withdrawals for all customers"; the disclaimer |
| 3 | 0:45 | Slide 4 "The idea" | | the title "Check what a signature will do, not who asks." |
| 4 | 1:03 | Slide 5 "How it works" (vm or laptop version) | | the whole diagram; the dashed AI arrow "never decides"; the red "Blocked: no approval" box |
| 5 | 1:50 | App: Overview | Reset demo done; risk strip "Normal" | the "Try it in 60 seconds" box or link; the replay list |
| 6 | 1:50 | App: Overview → click "Run replay" on **Hot wallet delegation drain (EIP-7702)** | | the outcome "Blocked ×1" |
| 7 | 1:56 | App: request detail (View requests → the row) | | banner "Blocked: the wallet would hand control to an unknown contract"; "Nothing was signed."; "Signature refused: no approval from the Guard"; "Delegates to … unknown contract" |
| 8 | 2:03 | Same page, scrolled to **Evidence from the sandbox** | | "Control handed over"; "sweep() moved 100 ETH" |
| 9 | 2:10 | Same page, **AI analysis** card | wait until the card has text (a few seconds) | the model pill (glm-5.3); "AI explains, it never decides"; the risk strip "High: 1 wallet paused" |
| 10 | 2:22 | App: Overview → **Reset demo** | | strip back to "Normal" |
| 11 | 2:24 | Overview → "Run replay" on **Coordinated attack on several wallets and chains** | | the strip changing colour step by step (amber → orange → red → deep red) |
| 12 | 2:47 | Overview, level 4 | replay finished | the deep red banner "Global stop: no withdrawals are being signed"; the "Risk level" card "Global stop"; the admin token field (leave it empty) |
| 13 | 2:50 | Overview → **Reset demo** | | strip "Normal" |
| 14 | 2:52 | App: **Containment** | a saved report exists: Plan B `.\deploy\planb.ps1 redteam`, the VM `deploy.sh <IP> verify` | "All 7 checks contained." and the 7 rows (the saved report; the live button is off on the public demo) |
| 15 | 3:14 | Slide 9 "Measured results" | | the table and the note with the file names |
| 16 | 3:33 | Slide 10 "Business value" | | the title "Protect the wallets without stopping normal customers." and the five points |
| 17 | 3:41 | Slide 6 "Where Fail Closed fits" | | the three columns |
| 18 | 3:50 | Slide 11 "Honest limits · What's next" | | both cards |
| 19 | 4:11 | Slide 1 | | title, tagline, the Vultr line |

Do not show: the `.env` file, a terminal with environment variables, the admin token, the Vultr
console, or browser tabs with other accounts. On Plan B, the header may say "Updates every 2 s":
that is expected.
