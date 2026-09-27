"""Answer every question from each condition's retrieved context on Purdue GenAI Studio, then
score it: a blind LLM judge grades correctness against the gold answer, and code checks citations
against the gold spans. Every call is cached under results/cache/ so reruns resume."""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from common import DATA

sys.path.insert(
    0, "/home/richardhcli/projects/personal-projects/akasha-wikipedia-codegraph/scripts"
)
import run_purdue_experiment as rp  # noqa: E402  (the user's Purdue API caller)
from retrieve import CONDITIONS, render  # noqa: E402

CACHE = DATA / "results" / "cache"
ANSWER_SYSTEM = "You answer questions strictly from supplied sources and cite them."
ANSWER_PROMPT = """Answer the question using ONLY the numbered sources below (excerpts from a personal notes vault).
Cite every claim with its source number in square brackets, e.g. [S2]. If the sources do not contain the answer, reply exactly: NOT FOUND IN SOURCES.
Be concise: at most 4 sentences.

SOURCES:
{context}

QUESTION: {question}
ANSWER:"""
JUDGE_SYSTEM = "You are a strict, fair grader. Output only JSON."
JUDGE_PROMPT = """Grade the candidate answer against the reference answer for the question.
Score 2 = correct and complete (all key facts of the reference, nothing contradicting it);
1 = partially correct (some key facts right, some missing, or minor errors);
0 = wrong, missing, or claims the information is unavailable when the reference has an answer.
If the reference is "Not in the notes.", score 2 only if the candidate says the information is not available, and 0 if it gives a substantive answer.
Ignore citation markers like [S3] and ignore style. Return exactly: {{"score": 0|1|2, "reason": "<one sentence>"}}

QUESTION: {question}
REFERENCE ANSWER: {reference}
CANDIDATE ANSWER: {candidate}"""
CITE_RE = re.compile(r"\[([^\]]*S\d+[^\]]*)\]")


MIN_INTERVAL = 4.0  # seconds between request starts, across all threads (Purdue rate limit)
_throttle = threading.Lock()
_last_start = 0.0


def _wait_turn() -> None:
    global _last_start
    with _throttle:
        delay = _last_start + MIN_INTERVAL - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        _last_start = time.monotonic()


def call(model: str, system: str, prompt: str, max_tokens: int) -> tuple[str, dict]:
    last: Exception | None = None
    for attempt in range(7):
        _wait_turn()
        try:
            client = rp.PurdueGenAIStudio(
                model=model, system_prompt=system, max_tokens=max_tokens, timeout=300
            )
            return client.ask(prompt)
        except Exception as e:  # network / 5xx / rate limit: back off and retry
            last = e
            time.sleep(60 * (attempt + 1) if "Rate limit" in str(e) else 5 * 2**attempt)
    raise RuntimeError(f"{model} failed after retries: {last}")


def cited(answer: str) -> list[int]:
    out: list[int] = []
    for grp in CITE_RE.findall(answer):
        for n in re.findall(r"S(\d+)", grp):
            if int(n) not in out:
                out.append(int(n))
    return out


def neutral(answer: str) -> str:
    """Strip anything that could reveal the condition to the judge (ids, paths)."""
    answer = re.sub(r"#?\^?tm-[a-z2-7]{8}", "", answer)
    return re.sub(r"\S+\.md\b", "", answer)


def run_one(
    q: dict, cond: str, budget: int, rec: dict, answer_model: str, judge_model: str
) -> dict:
    path = CACHE / f"{cond}@{budget}" / f"{q['id']}.json"
    if path.exists():
        return json.loads(path.read_text())
    chunks = rec["chunks"]
    prompt = ANSWER_PROMPT.format(context=render(chunks), question=q["question"])
    answer, am = call(answer_model, ANSWER_SYSTEM, prompt, 400)
    judge_raw, jm = call(
        judge_model,
        JUDGE_SYSTEM,
        JUDGE_PROMPT.format(
            question=q["question"], reference=q["answer"], candidate=neutral(answer)
        ),
        200,
    )
    try:
        verdict = rp.extract_json(judge_raw)
        score = int(verdict["score"])
    except (ValueError, KeyError, TypeError):
        verdict, score = {"reason": "unparseable judge output"}, None
    nums = [n for n in cited(answer) if 1 <= n <= len(chunks)]
    cited_chunks = [chunks[n - 1] for n in nums]
    gold_sources = {g["source"] for g in q["gold"]}
    span_cov = [any(g["span"] in c["body"] for c in cited_chunks) for g in q["gold"]]
    result = {
        "qid": q["id"],
        "type": q["type"],
        "condition": cond,
        "budget": budget,
        "question": q["question"],
        "gold_answer": q["answer"],
        "gold": q["gold"],
        "answer": answer,
        "score": score,
        "judge_reason": verdict.get("reason"),
        "cited": [{"n": n, "cite": chunks[n - 1]["cite"]} for n in nums],
        "retrieved": [c["cite"] for c in chunks],
        "context_gold_hits": rec["gold_hits"],
        "cited_gold_span_recall": (sum(span_cov) / len(span_cov)) if span_cov else None,
        "cited_source_precision": (
            sum(c["source"] in gold_sources for c in cited_chunks) / len(cited_chunks)
        )
        if cited_chunks and gold_sources
        else None,
        "not_found": "NOT FOUND" in answer.upper(),
        "prompt_tokens": am.get("prompt_tokens"),
        "completion_tokens": am.get("completion_tokens"),
        "context_tokens": rec["context_tokens"],
        "latency_ms": am.get("client_latency_ms"),
        "answer_model": am.get("model"),
        "judge_model": jm.get("model"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n")
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budgets", type=int, nargs="+", default=[500, 1000])
    ap.add_argument("--conditions", nargs="+", default=CONDITIONS)
    ap.add_argument("--questions", nargs="*")
    ap.add_argument("--answer-model", default="llama3.3:70b")
    ap.add_argument("--judge-model", default="qwen2.5:72b")
    ap.add_argument("--workers", type=int, default=3)
    args = ap.parse_args()
    rp.load_dotenv()
    questions = json.loads((DATA / "questions.json").read_text())
    if args.questions:
        questions = [q for q in questions if q["id"] in args.questions]
    retrieval = json.loads((DATA / "results" / "retrieval.json").read_text())["runs"]
    jobs = []
    for budget in args.budgets:
        for cond in args.conditions:
            recs = {r["qid"]: r for r in retrieval[f"{cond}@{budget}"]}
            jobs += [(q, cond, budget, recs[q["id"]]) for q in questions]
    done = 0

    def work(job: tuple) -> dict | None:
        nonlocal done
        try:
            r = run_one(*job, args.answer_model, args.judge_model)
        except RuntimeError as e:  # leave it uncached; a rerun retries it
            print(f"FAILED {job[1]}@{job[2]} {job[0]['id']}: {e}", flush=True)
            r = None
        done += 1
        if done % 10 == 0 or done == len(jobs):
            print(f"{done}/{len(jobs)}", flush=True)
        return r

    with ThreadPoolExecutor(args.workers) as pool:
        list(pool.map(work, jobs))


if __name__ == "__main__":
    main()
