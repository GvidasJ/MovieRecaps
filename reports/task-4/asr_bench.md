| engine | device | WER plain | WER normalised | deadpool (plain) | spiderman-school (plain) | zendaya-age (plain) | timing: engine | timing: aligned | timing: aligned + onsets | speed (x real time) |
|---|---|---|---|---|---|---|---|---|---|---|
| small.en | cuda | 8.1 % | 7.7 % | 6.5 % | 6.8 % | 17.9 % | 40 ms (16 % in 1 frame, 44 % in 2) | 36 ms (24 % in 1 frame, 45 % in 2) | 37 ms (24 % in 1 frame, 44 % in 2) | 13x |
| medium.en | cuda | 7.1 % | 6.5 % | 7.3 % | 4.8 % | 15.4 % | 43 ms (16 % in 1 frame, 39 % in 2) | 35 ms (24 % in 1 frame, 45 % in 2) | 36 ms (23 % in 1 frame, 44 % in 2) | 9x |
| large-v3 | cuda | 4.2 % | 4.8 % | 1.6 % | 4.1 % | 12.8 % | 87 ms (7 % in 1 frame, 18 % in 2) | 40 ms (26 % in 1 frame, 46 % in 2) | 40 ms (27 % in 1 frame, 46 % in 2) | 8x |
| large-v3-turbo | cuda | 6.5 % | 6.2 % | 8.9 % | 2.7 % | 12.8 % | 43 ms (15 % in 1 frame, 35 % in 2) | 39 ms (24 % in 1 frame, 44 % in 2) | 39 ms (24 % in 1 frame, 44 % in 2) | 27x |
| parakeet-tdt-0.6b-v2 | cuda | 7.4 % | 6.8 % | 7.3 % | 4.8 % | 17.9 % | 50 ms (19 % in 1 frame, 40 % in 2) | 34 ms (25 % in 1 frame, 49 % in 2) | 34 ms (25 % in 1 frame, 47 % in 2) | 214x |
| parakeet-tdt-0.6b-v3 | cuda | 8.1 % | 7.4 % | 6.5 % | 4.1 % | 28.2 % | 62 ms (15 % in 1 frame, 30 % in 2) | 34 ms (27 % in 1 frame, 48 % in 2) | 35 ms (26 % in 1 frame, 46 % in 2) | 20x |
| canary-qwen-2.5b | cuda | 8.7 % | 7.7 % | 4.0 % | 9.5 % | 20.5 % | - | 37 ms (24 % in 1 frame, 44 % in 2) | 37 ms (25 % in 1 frame, 44 % in 2) | 1x |
| cohere-transcribe | not run: gated on Hugging Face: accept its terms at https://huggingface.co/CohereLabs/cohere-transcribe-03-2026 and log in with `hf auth login`, then run again | | | | | | | | | |
