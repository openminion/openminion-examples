# Software feature builder

A bounded coding example for one disposable Git repository. The application
owns feature criteria, approval, verifier command, checkpoint, and terminal
state. OpenMinion supplies planning and implementation turns.

The provider-free test freezes this sequence: request, structured plan,
approval, process restart, first implementation, seeded verifier failure,
revision, passing verification, Git commit, and truthful completion.

It is not a company builder, workflow framework, or permission to edit an
arbitrary repository.

```bash
python -m pip install -e ".[dev]"
pytest
```

Set `OPENMINION_LIVE_CONFIG` and run `pytest -m live` for an opt-in MiniMax
smoke. It closes and recreates the runtime after approval, seeds one verifier
failure, runs the revision, and checks the final commit and clean Git state in a
disposable repository.
