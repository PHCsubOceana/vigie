# La Vigie

*[Version française](README.fr.md)*

A small desk companion that watches your AI agents and sorts your mail, **locally**.

A Jetson Orin Nano runs a local LLM (Llama 3.2 3B via Ollama). It follows the Claude Code
sessions on the PCs at home and checks the mailboxes, then shows on a round touch screen who
is working, who is done, who is waiting for an answer, and which email really matters.
Nothing leaves the local network.

```
PC (Claude Code) --HTTP hooks--> Jetson: "vigie" brain + local LLM (Ollama)
Mailboxes (IMAP) <--polling--    Jetson: "vigie-courrier" mail sorting
                                 Jetson: MQTT broker (mosquitto)
                                        |
                                        v  Wi-Fi, local network
                                 Round ESP32-P4 screen (display, sounds, camera)
```

The step-by-step build guide (in French) is published on
[explorations360.com](https://explorations360.com).

## What's in the repository

| Folder | Runs on | Role |
|---|---|---|
| [`vigie/`](vigie/) | Jetson | The brain: receives events, derives a mood, has the LLM write one sentence, publishes over MQTT |
| [`vigie-postes/`](vigie-postes/) | each PC (Git Bash) | Installs the Claude Code hooks, tagged with machine and account names |
| [`vigie-courrier/`](vigie-courrier/) | Jetson | Mail sorting: read-only IMAP, known contacts, classification by the LLM |
| [`vigie-corps/`](vigie-corps/) | round screen | ESP-IDF 6.1 / LVGL 9 firmware: display, gestures, sounds, camera and face detection |

Each folder has its own README (in French) with detailed setup steps. The user interface,
the LLM prompts and the generated sentences are in French.

## Hardware

- NVIDIA Jetson Orin Nano Super Developer Kit (8 GB), JetPack 6.2.x
- Waveshare ESP32-P4-WIFI6-Touch-LCD-3.4C (800 × 800 round touch screen), 2.4 GHz Wi-Fi
- 8 Ω speaker (shipped with the screen), optional OV5647 camera
- One or more PCs running Claude Code

## Setup order

1. **Jetson**: JetPack, Ollama (JetPack build) and the `llama3.2:3b-instruct-q5_K_M` model.
2. **`vigie/`**: `bash setup-jetson.sh`. It prints the token and the Jetson's IP address.
3. **`vigie-postes/`** on each PC: `bash installer-postes.sh` in Git Bash.
4. **`vigie-corps/`**: set Wi-Fi and broker in menuconfig, build and flash.
5. **`vigie-courrier/`** (optional): `bash installer-courrier.sh`, then fill in `courrier.toml`.

## Privacy

- Everything runs on your local network. The Jetson does not need to be exposed to the internet.
- Mailboxes are opened **read-only**: the Vigie never marks an email as read.
- Passwords and tokens stay on the Jetson (`vigie.env`, `courrier.toml`, mode 600) and are
  excluded from the repository by `.gitignore`.

## Disclaimer

Personal project, provided **as is**, with no warranty and no support. Not affiliated with
Anthropic, NVIDIA, Waveshare, Espressif or Microsoft. Product names belong to their
respective owners.

## License

[MIT](LICENSE) © 2026 Philippe Carrez, explorations360.
The Nunito font bundled in `vigie-corps/main/fonts/` is licensed under the
[SIL Open Font License 1.1](vigie-corps/main/fonts/OFL.txt).
