# Benchmark Report

## 1. Setup

**Models and providers**

| Model | Provider |
|---|---|
| `qwen/qwen3.8-27b` | Groq |
| `openai/gpt-oss-120b` | Groq |
| `nvidia/nemotron-3-super-120b-a12b:free` | OpenRouter |
| `cohere/north-mini-code:free` | OpenRouter |
| `google/gemma-4-31b-it` | OpenRouter |

**Tasks:** `scikit-learn-13439`, `django-11066`, and `sympy-18189`.

I choose these task cause they are the most complicated among those provided by the LLM.

## 2. Results

| Model | Task | Pass | Iters | Input Tokens | Output Tokens | Time (s) |
|---|---|---|---:|---:|---:|---:|
| Qwen 3.8 | sklearn | ✅ | 5 | 11,215 | 228 | 9.8 |
| Qwen 3.8 | Django | ✅ | 4 | 17,549 | 239 | 9.1 |
| Qwen 3.8 | SymPy | ✅ | 19 | 50,234 | 1,159 | 171 |
| GPT-OSS 120B | All | ❌ | 1 | ~1.6–2.2k | ~80 | ~1 |
| Nemotron 120B | sklearn | ✅ | 30 | 129,112 | 8,331 | 98 |
| Nemotron 120B | Django | ❌ | 11 | 25,043 | 902 | 26 |
| Nemotron 120B | SymPy | ✅ | 30 | 110,232 | 4,720 | 99 |
| North Mini Code | All | ❌ | 30 | 67k–107k | ~1k | ~25 |
| Gemma 4 | sklearn | ✅ | 4 | 8,534 | 187 | 7 |
| Gemma 4 | Django | ✅ | 3 | 9,373 | 120 | 7 |
| Gemma 4 | SymPy | ✅ | 6 | 15,912 | 453 | 38 |

**Pass rate:** Qwen 3/3, Gemma 3/3, Nemotron 2/3, North Mini 0/3, GPT-OSS 0/3.

## 3. Provider Reliability

| Model | Avg. Request (ms) | Issues |
|---|---:|---|
| Qwen / Groq | ~400 | No major error |
| GPT-OSS / Groq | — | HTTP 400 errors; failed on step 1 |
| Nemotron / OpenRouter | ~1,500 | Frequent retries and free-tier limits |
| North Mini / OpenRouter | ~600 | Often failed to produce valid Python code |
| Gemma / OpenRouter | ~450 | Lots of rate limiting |

## 4. Agent Behavior

**First successful file read**

| Model | sklearn | Django | SymPy |
|---|---:|---:|---:|
| Qwen | 2 | 1 | 2 |
| Gemma | 2 | 1 | 1 |
| Nemotron | 2 | Never | 2 |
| North Mini | 7 | 4 | 4 |

**Submission discipline**

- **Qwen:** Usually submitted correctly, but over-verified SymPy.
- **Gemma:** Submitted cleanly on all tasks.
- **Nemotron:** Never called `final_answer`; the harness recovered its patches.
- **North Mini:** Never produced a working edit.

## 5. Prompt Ablation

Added those rule:

> NEVER attempt to use native LLM tool-calling, external plugins, or format JSON tool requests.
> You have NO native tools enabled at the API level.


gpt-os-120b kept repeating the same behavior. The stricter prompt made no meaningful difference. Model struggled to follow the harness's execution format.

## 6. Conclusions

**Keep**

- **Gemma 4:** Best default. Fast, cheap, clean, and solved all three tasks in 13 iterations.
- **Qwen 3.8:** Best for harder tasks, but uses more tokens.

**Drop**

- **GPT-OSS 120B:** Incompatible with the harness's tool restrictions.
- **North Mini Code:** 0/3; failed to produce usable edits within 30 iterations.
- **Nemotron 120B:** 2/3, but required harness recovery and never submitted cleanly.

**Bottom line:**  Qwen was the most reliable and had virtually no rate limit when for my usage.
