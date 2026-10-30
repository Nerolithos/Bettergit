"""Test-only worker. Parent sends SIGKILL at a durable lifecycle checkpoint."""
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bettergit as bg
from bettergit_workflow import Engine, atomic_json


def checkpoint(name, operation):
    if name == sys.argv[1]:
        atomic_json(Path(sys.argv[2]), {'id': operation['id'], 'checkpoint': name})
        while True:
            time.sleep(1)


workflow = Engine(bg.probe, bg.run_git, lambda _: True, bg.say, checkpoint=checkpoint)
with workflow.store.lock():
    operation = workflow.prepare()
    workflow.apply(operation)
