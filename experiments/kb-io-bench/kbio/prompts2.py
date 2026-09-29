"""v2 prompt skeletons (kbio.run2), identical across harnesses and models (only the tool list
differs). Adapted from v1 `prompts.py`: documents instead of a personal vault, plus DELETE."""

from __future__ import annotations

SYSTEM = """You are an assistant working with a knowledge base of documents: reference pages (a Wikipedia snapshot) plus anything the user has added. You can only see it through the tools you are given. Be efficient: search first, then read only what you need."""

READ = """Answer the question below using the knowledge base. Report what the knowledge base says, even where it differs from what you believe.
Cite the sources of your answer: after the answer, write a line `SOURCES:` followed by one line per source, `- <document id>: "<short verbatim quote>"`.
If the knowledge base does not contain the answer, reply exactly: NOT FOUND IN KNOWLEDGE BASE
Keep the answer to at most 3 sentences.

QUESTION: {question}"""

CREATE = """File the new information below into the knowledge base so that it can be found later: put each fact where it belongs and do not create duplicates.
These facts are new: none of them is in the knowledge base yet (similar-looking facts about other topics are not duplicates). Your tool budget is limited, so use only a few calls to find where each fact belongs, then write it.
When you are done, reply with DONE and a one-line summary of what you changed.

NEW INFORMATION:
{memo}"""

UPDATE = """A fact in the knowledge base has changed. Update the knowledge base so it is correct everywhere the fact appears (it may appear in several documents). Do not create duplicates.
When you are done, reply with DONE and a one-line summary of what you changed.

CHANGE:
{instruction}"""

DELETE = """A statement in the knowledge base has been retracted. Remove it everywhere it appears (it may appear in several documents), and keep everything else.
When you are done, reply with DONE and a one-line summary of what you changed.

RETRACTION:
{instruction}"""

SYSTEM_CLOSED_BOOK = """You are an assistant answering questions about a knowledge base of documents, but you have no access to it in this session. Answer from your own knowledge only."""

READ_CLOSED_BOOK = """Answer the question below from your own knowledge. If you do not know, reply exactly: NOT FOUND IN KNOWLEDGE BASE
Keep the answer to at most 3 sentences.

QUESTION: {question}"""
