"""Prompt skeletons, identical across conditions (only the tool list differs)."""

from __future__ import annotations

SYSTEM = """You are an assistant working with a user's personal knowledge base: a vault of markdown notes (the user's own concept notes plus Wikipedia reference pages stored under Wikipedia/). You can only see it through the tools you are given. Be efficient: search first, then read only what you need."""

SYSTEM_CLOSED_BOOK = """You are an assistant answering questions about a user's personal knowledge base, but you have no access to it in this session. Answer from your own knowledge only."""

READ = """Answer the question below using the knowledge base. Report what the knowledge base says, even where it differs from what you believe.
Cite the sources of your answer: after the answer, write a line `SOURCES:` followed by one line per source, `- <file path or node id>: "<short verbatim quote>"`.
If the knowledge base does not contain the answer, reply exactly: NOT FOUND IN KNOWLEDGE BASE
Keep the answer to at most 3 sentences.

QUESTION: {question}"""

READ_CLOSED_BOOK = """Answer the question below from your own knowledge. If you do not know, reply exactly: NOT FOUND IN KNOWLEDGE BASE
Keep the answer to at most 3 sentences.

QUESTION: {question}"""

WRITE = """File the new information below into the knowledge base, the way this knowledge base expects: look at how existing notes are organised first, put each fact where it belongs, link it to the right concept, and do not create duplicates.
These facts are new: none of them is in the knowledge base yet (similar-looking facts about other topics are not duplicates). Your tool budget is limited, so use only a few calls to find where each fact belongs, then write it.
When you are done, reply with DONE and a one-line summary of what you changed.

NEW INFORMATION:
{memo}"""

UPDATE = """A fact in the knowledge base has changed. Update the knowledge base so it is correct everywhere the fact appears (it may appear in several notes). Do not create duplicates.
When you are done, reply with DONE and a one-line summary of what you changed.

CHANGE:
{instruction}"""
