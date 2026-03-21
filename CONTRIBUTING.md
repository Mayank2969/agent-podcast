# Contributing to AgentCast

First off, thank you for considering contributing to AgentCast! It's people like you that make open source such a great community.

## Where do I go from here?

If you've noticed a bug or have a feature request, make sure to check if there's already an issue for it. If not, open a new issue!

## Fork & create a branch

If this is something you think you can fix, then fork AgentCast and create a branch with a descriptive name.

A good branch name would be (where issue #325 is the ticket you're working on):

```sh
git checkout -b 325-add-new-tts-provider
```

## Get the test suite running

Make sure you're running the tests before you start changing code. We use `pytest` for testing the backend.

```bash
# Install dependencies
pip install -r backend/requirements.txt
pip install -r pipecat_host/requirements.txt
pip install pytest httpx

# Run tests
PYTHONPATH=. pytest backend/guardrails/tests/ -v
PYTHONPATH=. pytest tests/ -v
```

## Implement your fix or feature

At this point, you're ready to make your changes. Feel free to ask for help if you run into any roadblocks. 

## Code Review Process

1. Ensure all tests pass.
2. Ensure your code is well-commented and clean.
3. Submit a Pull Request (PR) describing the problem and your solution.
4. The core team will review your PR, suggest changes, or merge it.

## Setting up your development environment

For a full local stack:
1. Ensure Docker and Docker Compose are installed.
2. Copy `.env.example` to `.env` and fill in API keys if you are working on the Pipecat Host.
3. Run `bash run_podcast.sh`.

Thank you for contributing!
