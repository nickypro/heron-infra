"""
Minimal Inspect AI eval - run with:
    inspect eval minimal_eval.py --model openai/gpt-4o-mini
"""

from inspect_ai import eval as inspect_eval
from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.model import ChatMessageUser
from inspect_ai.solver import generate
from inspect_ai.scorer import match

@task
def minimal_eval() -> Task:
    return Task(
        dataset=[
            Sample(
                input=[ChatMessageUser(content="What is 2 + 2? Reply with just the number.")],
                target="4",
            ),
            Sample(
                input=[ChatMessageUser(content="What is the capital of France? Reply with just the city name.")],
                target="Paris",
            ),
        ],
        solver=generate(),
        scorer=match(),
    )

if __name__ == "__main__":
    task = minimal_eval()
    
    inspect_eval(task, model="openrouter/openai/gpt-4o-mini")

