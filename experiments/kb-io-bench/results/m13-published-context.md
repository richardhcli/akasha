# M13 published context: MemoryAgentBench Conflict Resolution (other models, context only)

Source: Hu, Wang, McAuley, *Evaluating Memory in LLM Agents via Incremental Multi-Turn
Interactions* (MemoryAgentBench, ICLR 2026), arXiv 2507.05257, Table 3 "Overall Performance
Comparison". Fetched 2026-09-29. The caption reads: "In the absence of a specified model, All RAG
agents and commercial memory agents use GPT-4o-mini as the backbone."

**Caveats:**
- The paper doesn't say which context length (6k/32k/64k/262k) or average these columns are.
- It gives no per-length breakdown for these methods; its Table 5 covers only GPT-4o and o4-mini
  at 6k/32k.
- The backbones differ from ours (gpt-oss:120b on Purdue, 65,536-token limit).

**Use:** context only. These numbers are not comparable to M13's per-question paired tests, and
no claim may rest on them.

| method | backbone | FC-SH | FC-MH |
|---|---|---:|---:|
| Long context | GPT-4o (128K) | 60.0 | 5.0 |
| Long context | GPT-4o-mini (128K) | 45.0 | 5.0 |
| Long context | Claude-3.7-Sonnet (200K) | 43.0 | 2.0 |
| Long context | GPT-5-mini (400K) | 78.0 | 28.0 |
| Long context | GPT-4.1-mini (1M) | 36.0 | 5.0 |
| Long context | Gemini-2.0-Flash (1M) | 30.0 | 3.0 |
| BM25 RAG | GPT-4o-mini | 48.0 | 3.0 |
| Contriever RAG | GPT-4o-mini | 18.0 | 7.0 |
| Text-Embed-3-Small RAG | GPT-4o-mini | 28.0 | 3.0 |
| Text-Embed-3-Large RAG | GPT-4o-mini | 28.0 | 4.0 |
| Qwen3-Embedding-4B RAG | GPT-4o-mini | 29.0 | 3.0 |
| RAPTOR | GPT-4o-mini | 14.0 | 1.0 |
| GraphRAG | GPT-4o-mini | 14.0 | 2.0 |
| MemoRAG | GPT-4o-mini | 21.0 | 7.0 |
| HippoRAG-v2 | GPT-4o-mini | 54.0 | 5.0 |
| Mem0 | GPT-4o-mini | 18.0 | 2.0 |
| Cognee | GPT-4o-mini | 28.0 | 3.0 |
| Zep | GPT-4o-mini | 7.0 | 3.0 |
| Self-RAG | GPT-4o-mini | 19.0 | 3.0 |
| MemGPT | GPT-4o-mini | 28.0 | 3.0 |
| MIRIX | GPT-4o-mini | 14.0 | 2.0 |
| MIRIX | GPT-4.1-mini | 20.0 | 3.0 |

The paper: "all methods fail on the multi-hop situation (with achieving at most 28% accuracy)."

**Knowl** (`methods/knowl.py` in the submodule, with supersede on/off arms): no published numbers
were found in the submodule, so none are cited.
