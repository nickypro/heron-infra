"""
HuggingFace Dataset Eval with Chain-of-Thought - run with:
    inspect eval hf_gsm8k_eval.py --model openai/gpt-4o-mini

Uses GSM8K (Grade School Math 8K) - a classic CoT benchmark.
"""

from inspect_ai import eval as inspect_eval
from inspect_ai import Task, task
from inspect_ai.dataset import Sample, hf_dataset
from inspect_ai.model import ChatMessageUser
from inspect_ai.solver import chain_of_thought, generate
from inspect_ai.scorer import match
import re


def record_to_sample(record: dict) -> Sample:
    """Convert a GSM8K record to an Inspect Sample."""
    # GSM8K has 'question' and 'answer' fields
    # The answer contains step-by-step reasoning followed by #### and the final answer
    question = record["question"]
    full_answer = record["answer"]
    
    # Extract the final numeric answer after ####
    final_answer = full_answer.split("####")[-1].strip()
    # Remove commas from numbers like "1,000" -> "1000"
    final_answer = final_answer.replace(",", "")
    
    return Sample(
        input=[ChatMessageUser(content=f"{question}\n\nProvide your final answer as a number only.")],
        target=final_answer,
        metadata={"full_solution": full_answer},
    )


@task
def gsm8k_eval() -> Task:
    """GSM8K evaluation with chain-of-thought prompting."""
    return Task(
        dataset=hf_dataset(
            path="openai/gsm8k",
            name="main",
            sample_fields=record_to_sample,
            split="test",
            shuffle=True,
            limit=10,  # Small subset for quick testing
            trust=True,
        ),
        solver=[
            chain_of_thought(),  # Adds "Let's think step by step" prompt
            generate(),
        ],
        scorer=match(numeric=True),  # Use numeric matching for math answers
    )


if __name__ == "__main__":
    logs = inspect_eval(
        gsm8k_eval(),
        model="openrouter/openai/gpt-4o-mini",
        log_dir="./logs",
    )
    print(f"\nResults: {logs[0].results}")
