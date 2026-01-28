# Inspect AI Quick Reference

A barebones technical guide for the UK AISI's [Inspect](https://inspect.ai-safety-institute.org.uk/) library.

If you want to see more details on running evals, you can follow the content from ARENA:

- [ARENA Evals content](https://arena-chapter3-llm-evals.streamlit.app/)

## Installation

```bash
pip install inspect_ai
```

## Core Concepts

Inspect has 4 main components:
- **Task**: The evaluation container
- **Dataset**: List of `Sample` objects (questions)
- **Solver**: Functions that modify the conversation/generate responses
- **Scorer**: Functions that score model output

```
Dataset → [Solver chain] → Scorer → Results
```

---

## 1. Datasets & Samples

A `Sample` has these key fields:
- `input`: List of `ChatMessage` objects (system/user messages)
- `choices`: Answer options (for MCQ)
- `target`: Correct answer
- `metadata`: Optional extra info

### Loading from JSON

```python
from inspect_ai.dataset import json_dataset, Sample
from inspect_ai.model import ChatMessageSystem, ChatMessageUser

def record_to_sample(record: dict) -> Sample:
    return Sample(
        input=[
            ChatMessageSystem(content=record["system"]),
            ChatMessageUser(content=record["question"]),
        ],
        target=record["answer"],
        choices=list(record["answers"].values()),
        metadata={"category": record.get("category")},
    )

dataset = json_dataset("path/to/data.json", sample_fields=record_to_sample)
```

### Loading from HuggingFace

```python
from inspect_ai.dataset import hf_dataset

dataset = hf_dataset(
    path="allenai/ai2_arc",
    name="ARC-Challenge",
    sample_fields=record_to_sample,
    split="validation",
    trust=True,
)
```

### Built-in datasets

```python
from inspect_ai.dataset import example_dataset

dataset = example_dataset("theory_of_mind")
```

---

## 2. Solvers

Solvers modify `TaskState` (conversation history). Structure:

```python
from inspect_ai.solver import solver, Solver, TaskState, Generate

@solver
def my_solver(arg: str) -> Solver:
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        # Modify state.messages or state.user_prompt
        return state
    return solve
```

### Key `TaskState` attributes
- `state.messages` - Chat history (list of `ChatMessage`)
- `state.user_prompt` - First user message (`ChatMessageUser`)
- `state.user_prompt.text` - Text content of user prompt
- `state.choices` - MCQ choices (if applicable)
- `state.output` - Model's output after `generate()`

### Built-in solvers

```python
from inspect_ai.solver import generate, chain_of_thought, self_critique, chain

# generate() - Calls the model API
# chain_of_thought() - Adds CoT instruction to prompt
# self_critique() - Model critiques its own answer
# chain() - Combines multiple solvers
```

### Example: Custom solver

```python
from inspect_ai.model import ChatMessageUser, ChatMessageSystem

@solver
def system_message(msg: str) -> Solver:
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        state.messages.insert(0, ChatMessageSystem(content=msg))
        return state
    return solve

@solver
def prompt_template(template: str) -> Solver:
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        state.user_prompt.text = template.format(prompt=state.user_prompt.text)
        return state
    return solve
```

### Using `generate` inside a solver

```python
from inspect_ai.model import get_model

@solver
def my_solver_with_generation(model_id: str) -> Solver:
    model = get_model(model_id)
    
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        response = await model.generate("Some prompt")
        # Use response.completion
        return state
    return solve
```

---

## 3. Scorers

Scorers evaluate model output. Structure:

```python
from inspect_ai.scorer import scorer, Score, Target

@scorer(metrics=[])
def my_scorer():
    async def score(state: TaskState, target: Target) -> Score:
        is_correct = state.output.completion == target.target[0]
        return Score(
            value="C" if is_correct else "I",
            answer=state.output.completion,
            explanation="...",
        )
    return score
```

### Built-in scorers

```python
from inspect_ai.scorer import match, answer, model_graded_fact, model_graded_qa

# match() - Exact match with target
# answer("letter") - Parses "ANSWER: X" format
# model_graded_fact() - LLM grades factual correctness
# model_graded_qa() - LLM grades open-ended answers
```

---

## 4. Tasks

A `Task` combines dataset, solver, and scorer:

```python
from inspect_ai import Task, task, eval

@task
def my_eval() -> Task:
    return Task(
        dataset=json_dataset("data.json", record_to_sample),
        solver=[
            chain_of_thought(),
            generate(),
        ],
        scorer=match(),
    )
```

---

## 5. Running Evaluations

```python
from inspect_ai import eval

# Run evaluation
logs = eval(
    my_eval(),
    model="openai/gpt-4o-mini",  # or "anthropic/claude-3-5-sonnet-20240620"
    limit=10,  # Number of samples
    log_dir="./logs",
)
```

### View logs

```bash
inspect view --log-dir ./logs --port 7575
```

Or use the VS Code Inspect extension.

---

## 6. Tools (for Agents)

Tools let models call functions:

```python
from inspect_ai.tool import tool

@tool
def calculator():
    async def execute(expression: str) -> str:
        """
        Evaluates a math expression.

        Args:
            expression: The math expression to evaluate.

        Returns:
            The result as a string.
        """
        return str(eval(expression))
    return execute
```

---

## 7. Agents

Agents are solvers that use tools in a loop:

```python
from inspect_ai.agent import agent, AgentState
from inspect_ai.tool import execute_tools
from inspect_ai.model import get_model

@agent
def my_agent(tools: list):
    async def execute(state: AgentState) -> AgentState:
        while not done:
            # Generate with tools
            state.output = await get_model().generate(
                input=state.messages,
                tools=tools,
                tool_choice="auto",
            )
            state.messages.append(state.output.message)
            
            # Execute tool calls if any
            if state.output.message.tool_calls:
                messages, state.output = await execute_tools(
                    state.messages, tools=tools
                )
                state.messages.extend(messages)
        
        return state
    return execute
```

### Running agents

```python
from inspect_ai.agent import as_solver

@task
def agent_task() -> Task:
    return Task(
        dataset=[Sample(input="", target="")],
        message_limit=40,  # Prevent infinite loops
    )

eval(agent_task(), solver=as_solver(my_agent(tools=[calculator()])))
```

---

## Full Example

```python
from inspect_ai import Task, task, eval
from inspect_ai.dataset import json_dataset, Sample
from inspect_ai.model import ChatMessageUser
from inspect_ai.solver import chain, generate, solver, Solver, TaskState, Generate
from inspect_ai.scorer import answer

# 1. Define record_to_sample
def record_to_sample(record: dict) -> Sample:
    return Sample(
        input=[ChatMessageUser(content=record["question"])],
        target=record["answer"],
        choices=list(record["choices"].values()),
    )

# 2. Define custom solver
@solver
def mcq_format() -> Solver:
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        choices_str = "\n".join(
            f"{chr(65+i)}) {c.value}" for i, c in enumerate(state.choices)
        )
        state.user_prompt.text = f"""{state.user_prompt.text}

{choices_str}

Answer with 'ANSWER: X' where X is the letter."""
        return state
    return solve

# 3. Define task
@task
def my_benchmark() -> Task:
    return Task(
        dataset=json_dataset("questions.json", record_to_sample, limit=50),
        solver=chain(mcq_format(), generate()),
        scorer=answer("letter"),
    )

# 4. Run
if __name__ == "__main__":
    eval(my_benchmark(), model="openai/gpt-4o-mini", log_dir="./logs")
```

---

## Common Imports

```python
# Core
from inspect_ai import Task, task, eval

# Dataset
from inspect_ai.dataset import json_dataset, hf_dataset, Sample, Dataset

# Model/Messages
from inspect_ai.model import ChatMessageSystem, ChatMessageUser, ChatMessageAssistant, get_model

# Solvers
from inspect_ai.solver import (
    solver, Solver, TaskState, Generate,
    chain, generate, chain_of_thought, self_critique
)

# Scorers
from inspect_ai.scorer import scorer, Score, Target, match, answer, model_graded_fact

# Tools/Agents
from inspect_ai.tool import tool, execute_tools
from inspect_ai.agent import agent, AgentState, as_solver
```
