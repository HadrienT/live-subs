# live-subs

Near-live Japanese + English subtitles for YouTube streams watched in Firefox.
A Firefox extension captures the player's audio and streams it over the LAN to
a GPU server that transcribes Japanese (Whisper specialised for Japanese) and
translates it to English (local LLM via llama.cpp). Nothing leaves the LAN.

Status: design phase — see [`blueprint/`](blueprint/README.md) for the
architecture, decisions and work packages.
