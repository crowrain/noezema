# Local LLM compatibility smoke test

The opt-in test sends one constrained Russian request to an already running OpenAI-compatible
endpoint. It verifies the JSON Schema path, tool choice, usage metadata and recorded fingerprints.

Required environment variables:

```text
NOEZEMA_LLM_BASE_URL=http://127.0.0.1:8080/v1
NOEZEMA_LLM_MODEL=thinker-local
NOEZEMA_LLM_MODEL_SHA256=<64 lowercase hex characters>
NOEZEMA_LLM_TOKENIZER_SHA256=<64 lowercase hex characters>
NOEZEMA_LLM_TEMPLATE_SHA256=<64 lowercase hex characters>
NOEZEMA_LLM_GRAMMAR_SHA256=<64 lowercase hex characters>
NOEZEMA_LLM_BACKEND=llama.cpp
NOEZEMA_LLM_BACKEND_VERSION=<version>
NOEZEMA_LLM_BUILD_FINGERPRINT=<commit and compile flags>
```

Optional: `NOEZEMA_LLM_API_KEY` and `NOEZEMA_LLM_QUANTIZATION`.

Run:

```shell
python -m pytest -m model_compatibility tests/model_compatibility
```

The regular test suite never requires a live model; this module skips unless the complete profile
is supplied.
