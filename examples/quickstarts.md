# Quickstarts

## Git

Git lets you back up your code online and collaborate with others. When multiple people work on the same codebase, git tracks who changed what and helps merge changes together.

You should have a repo, and be pushing changes pretty often. it makes deleting code by accident much less costly, since you can look at the version you had before, and see what broke.

```bash
git add . && git commit -m "message" && git push
```

Branches let you experiment without breaking the main code. Create one with `git checkout -b my-experiment`, switch back with `git checkout main`.

## Hugging Face

Free cloud storage for datasets and model checkpoints. Just dump your files there instead of losing them when a machine goes away or when switching machine, without necessarily needing to push them to github. lambda volumes should mostly handle issues with this, but it's pretty easy to just upload files.

```bash
pip install huggingface_hub
huggingface-cli login
huggingface-cli upload my-username/my-stuff ./local-folder
```

## uv

Python's pip is painfully slow. uv is a drop-in replacement that's 10-100x faster. It also handles Python versions and virtual environments cleanly and is neater to use than conda or pyenv.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh # should be preinstalled

uv venv && source .venv/bin/activate   # instant venv creation
uv pip install torch            # just like pip, but fast
```

## Weights & Biases (wandb)

Tracks your experiments—logs metrics, hyperparameters, and outputs so you can compare runs and see what actually worked. Much better than scrolling through terminal output or keeping notes in a spreadsheet.

```bash
pip install wandb
wandb login
```

Then in your training script:
```python
import wandb
wandb.init(project="my-project")
wandb.log({"loss": loss, "accuracy": acc})
```

The free tier has limits, but you can usually email wandb for a free education account if you're a student/researcher.

## tmux

If you SSH into a machine and start a training run, it dies when you disconnect. tmux keeps your session alive in the background, just use it any time you do a training run of >5 minutes.

```bash
tmux          # start a session
tmux a        # reattach to it later, short for tmux attatch-session
```

Inside tmux, press `Ctrl+b` then type `:set mouse on` + enter to enable scrolling with your mouse.

**Parallel sweeps across GPUs:** Open multiple tmux sessions and use `CUDA_VISIBLE_DEVICES` to assign GPUs:
```bash
CUDA_VISIBLE_DEVICES=0 python train.py --lr 1e-3   # in one session
CUDA_VISIBLE_DEVICES=1 python train.py --lr 1e-4   # in another
```

You can combine this with Wandb sweep agents to run a different sweep sweep instance in a different tab of tmux, just ask claude to run a command for you.

# nvtop

you can just check all your GPUs with a nice graph by running `nvtop`
this makes it pretty easy to see which things are running, and if you should increase your batch size or not. Should be pre-installed, but you can install with `apt install nvtop`