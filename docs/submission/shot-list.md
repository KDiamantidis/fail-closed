# Shot list

Record at 1920×1080 (browser at 100% zoom, window maximised). One take per scene is fine; cut
between scenes. Before **every** app scene: Overview → **Reset demo** (the strip says "Normal").

| # | Time | On screen | Start state | Must be visible |
|---|---|---|---|---|
| 1 | 0:00 | Slide 2 "The problem" | slides, full screen | both cards; the disclaimer line at the bottom |
| 2 | 0:20 | Slide 3 "The cost nobody counts" | | "Both exchanges paused withdrawals for all customers"; the disclaimer |
| 3 | 0:40 | Slide 4 "The idea" | | the title "Check what a signature will do, not who asks." |
| 4 | 1:05 | Slide 5 "How it works" (vm or laptop version) | | the whole diagram; the dashed AI arrow "never decides"; the red "Blocked: no approval" box |
| 5 | 1:35 | App: Overview | Reset demo done; risk strip "Normal" | the "Try it in 60 seconds" box or link; the replay list |
| 6 | 1:45 | App: Overview → click "Run replay" on **Hot wallet delegation drain (EIP-7702)** | | the outcome "Blocked ×1" |
| 7 | 1:55 | App: request detail (View requests → the row) | | banner "Blocked: the wallet would hand control to an unknown contract"; "Nothing was signed."; "Signature refused: no approval from the Guard"; "Delegates to … unknown contract" |
| 8 | 2:05 | Same page, scrolled to **Evidence from the sandbox** | | "Control handed over"; "sweep() moved 100 ETH" |
| 9 | 2:15 | Same page, **AI analysis** card | wait until the card has text (a few seconds) | the model pill (glm-5.3); "AI explains, it never decides"; the risk strip "High: 1 wallet paused" |
| 10 | 2:25 | App: Overview → **Reset demo** | | strip back to "Normal" |
| 11 | 2:35 | Overview → "Run replay" on **Coordinated attack on several wallets and chains** | | the strip changing colour step by step (amber → orange → red → deep red) |
| 12 | 3:00 | Overview, level 4 | replay finished | the deep red banner "Global stop: no withdrawals are being signed"; the "Risk level" card "Global stop"; the admin token field (leave it empty) |
| 13 | 3:10 | Overview → **Reset demo** | | strip "Normal" |
| 14 | 3:15 | App: **Containment** | a saved report exists: Plan B `.\deploy\planb.ps1 redteam`, the VM `deploy.sh <IP> verify` | "All 7 checks contained." and the 7 rows (the saved report; the live button is off on the public demo) |
| 15 | 3:40 | Slide 9 "Measured results" | | the table and the note with the file names |
| 16 | 4:05 | Slide 6 "Where Fail Closed fits" | | the three columns |
| 17 | 4:25 | Slide 11 "Honest limits · What's next" | | both cards |
| 18 | 4:50 | Slide 1 | | title, tagline, the Vultr line |

Do not show: the `.env` file, a terminal with environment variables, the admin token, the Vultr
console, or browser tabs with other accounts. On Plan B, the header may say "Updates every 2 s":
that is expected.
