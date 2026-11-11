# Local Qwen coding model

This laptop runs Qwen2.5-Coder 3B Instruct Q4_K_M through llama.cpp on CPU. The model is at `~/local-llm/models/Qwen2.5-Coder-3B-Instruct/qwen2.5-coder-3b-instruct-q4_k_m.gguf`; the CPU-only llama.cpp build is at `~/local-llm/llama.cpp/src/build/bin/`.

The launchers live in `~/.local/bin`:

- `qwen-server start|stop|status|logs` manages an on-demand `llama-server` bound only to `127.0.0.1:8080`, using four threads, a 4096-token context, one request slot and mmap loading (mlock disabled).
- `qwen-chat` opens an interactive terminal conversation using the chat template embedded in the model. It also accepts a single piped prompt and exits after one response.
- `qwen-web` starts the server if needed and prints the local Web UI URL.

The server is OpenAI-compatible at `http://127.0.0.1:8080/v1`. Start it with `qwen-server start`; stop it when finished with `qwen-server stop`. With four threads, the benchmark measured about 8.7 generated tokens/s on short requests and about 4.2 GiB server RSS. The launcher keeps the 4096-token context and starts only on demand to limit idle resource use. Avoid running `qwen-chat` while the server is loaded; both load the model into RAM.

Example one-line API request:

```sh
curl -s http://127.0.0.1:8080/v1/chat/completions -H 'Content-Type: application/json' -d '{"model":"local","messages":[{"role":"user","content":"Write a Python function that returns x + 1. Code only."}],"temperature":0,"max_tokens":64}'
```

For a coding tool that supports a custom OpenAI-compatible base URL, set `OPENAI_BASE_URL=http://127.0.0.1:8080/v1` and use a placeholder local API key such as `OPENAI_API_KEY=local`; choose the tool's local model entry or the model name returned by `/v1/models`. Keep the endpoint local to this machine.

`~/.bashrc` already includes this PATH line, so no shell configuration change was needed:

```sh
export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:$PATH"
```

The benchmark comparison and raw logs are under `~/local-llm/results/comparison.md` and `~/local-llm/bench-logs/`. End-to-end launcher checks are recorded in `~/local-llm/results/setup-verification.md`.
