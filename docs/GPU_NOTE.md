# Note: the 9B model on a laptop without a GPU

Seen on 12 November 2026 on a Windows 11 laptop with 16 GB RAM and Intel Iris Xe integrated graphics (no
dedicated GPU), running `qwen3.5:9b` through Ollama.

- Ollama cannot use integrated graphics for this model, so it ran entirely on the processor. `ollama ps` showed
  `qwen3.5:9b   6.2 GB   100% CPU`.
- System memory was at 97% (15.3 of 15.8 GB) with the model loaded.
- Going by the terminal timestamps, each model check took roughly 20 to 40 seconds. Checks are meant to be about
  30 seconds apart; they came 32 to 49 seconds apart.
- The score does not update while the model is answering, so the Live page stalls for that time.

In short: on a 16 GB laptop without a dedicated GPU, the 9B model is too heavy to run in the background all day.
A smaller model (`qwen3.5:4b` or `qwen3.5:2b`) or a model hosted on another machine would be needed. Neither has
been tried yet on this laptop.
