"""The models beside the main one: loaded when first asked for, let go of when nobody has asked for a while.
What loads a model is a stand-in: it makes a recognizer that answers with the name of its model."""
import threading

import pytest

from tolmach.config import ModelConfig
from tolmach.gateway.extras import Extras, ModelFailed, NotInstalled, UnknownModel

ENGLISH = ModelConfig(name="english", language="en", encoder="e/e.onnx", decoder="e/d.onnx", joiner="e/j.onnx", tokens="e/t.txt")
GERMAN = ModelConfig(name="german", language="de", encoder="g/e.onnx", decoder="g/d.onnx", joiner="g/j.onnx", tokens="g/t.txt")


class World:
    def __init__(self, idle_s: float = 600.0):
        self.now = 1000.0
        self.loaded: list[str] = []          # every load that was begun, in order
        self.missing: set[str] = set()       # the models whose files are not there
        self.broken: Exception | None = None
        self.gate: threading.Event | None = None     # a load waits for it, when the test wants a load to take a while
        self.extras = Extras([ENGLISH, GERMAN], self._load, idle_s, clock=lambda: self.now,
                             installed=lambda model: model.name not in self.missing)

    def _load(self, model: ModelConfig):
        self.loaded.append(model.name)
        if self.gate is not None:
            assert self.gate.wait(5)
        if self.broken is not None:
            raise self.broken
        return lambda samples: f"{model.name}:{len(samples)}"


@pytest.fixture
def world():
    return World()


def test_a_model_is_loaded_when_it_is_first_asked_for_and_only_once(world):
    assert world.loaded == [] and world.extras.loaded() is None          # nothing is loaded for nothing
    recognize = world.extras.acquire("english")
    assert recognize([0] * 5) == "english:5" and world.extras.loaded() == "english"
    assert world.extras.acquire("english")([0] * 2) == "english:2" and world.loaded == ["english"]


def test_a_model_is_let_go_of_after_it_idled_long_enough_and_not_while_it_is_used(world):
    world.extras.acquire("english")
    world.extras.acquire("english")                                      # two requests at once
    world.now += 3600
    assert world.extras.sweep() is None and world.extras.loaded() == "english"     # in use: the hour does not count
    world.extras.release("english")
    world.now += 3600
    assert world.extras.sweep() is None                                  # one of the two is still at it
    world.extras.release("english")                                      # from now on nobody uses it
    world.now += 599
    assert world.extras.sweep() is None and world.extras.loaded() == "english"
    world.now += 1
    assert world.extras.sweep() == "english" and world.extras.loaded() is None
    assert world.extras.sweep() is None                                  # and there is nothing more to let go of
    world.extras.acquire("english")                                      # asked for again: loaded again
    assert world.loaded == ["english", "english"]


def test_with_no_idle_time_a_model_goes_as_soon_as_nobody_uses_it():
    world = World(idle_s=0.0)
    world.extras.acquire("english")
    assert world.extras.sweep() is None
    world.extras.release("english")
    assert world.extras.sweep() == "english"


def test_only_one_such_model_is_in_memory_at_a_time(world):
    world.extras.acquire("english")
    waited = []
    other = threading.Thread(target=lambda: waited.append(world.extras.acquire("german")([0])))
    other.start()
    other.join(0.3)
    assert other.is_alive() and world.loaded == ["english"]              # the other waits: the first is in use
    world.extras.release("english")
    other.join(5)
    assert waited == ["german:1"] and world.loaded == ["english", "german"] and world.extras.loaded() == "german"


def test_two_who_ask_at_once_cause_one_load(world):
    world.gate = threading.Event()
    got = []
    askers = [threading.Thread(target=lambda: got.append(world.extras.acquire("english"))) for _ in range(3)]
    for asker in askers:
        asker.start()
    askers[0].join(0.3)
    assert world.extras.loaded() is None and world.extras.loading() == "english"      # being loaded: not there yet
    world.gate.set()
    for asker in askers:
        asker.join(5)
    assert len(got) == 3 and world.loaded == ["english"]


def test_what_cannot_be_given_says_why(world):
    with pytest.raises(UnknownModel):
        world.extras.acquire("klingon")
    world.missing = {"english"}
    with pytest.raises(NotInstalled):
        world.extras.acquire("english")
    assert world.loaded == []                                            # nothing was even tried
    world.missing, world.broken = set(), RuntimeError("the file is damaged")
    with pytest.raises(ModelFailed, match="the file is damaged"):
        world.extras.acquire("english")
    assert world.extras.loaded() is None and world.extras.loading() is None
    world.broken = None
    assert world.extras.acquire("english")([0]) == "english:1"           # and the next one who asks gets it


def test_the_models_are_told_with_what_is_there_and_what_is_in_memory(world):
    world.missing = {"german"}
    world.extras.acquire("english")
    assert world.extras.told() == [
        {"id": "english", "language": "en", "installed": True, "loaded": True},
        {"id": "german", "language": "de", "installed": False, "loaded": False}]
    assert world.extras.known("german") and not world.extras.known("klingon")


def test_a_load_that_failed_fails_for_everyone_who_waited_for_it_and_holds_nothing(world):
    world.gate, world.broken = threading.Event(), RuntimeError("the file is damaged")
    got: list = []

    def ask(name: str) -> None:
        try:
            got.append((name, world.extras.acquire(name)([0])))
        except ModelFailed as e:
            got.append((name, str(e)))

    askers = [threading.Thread(target=ask, args=(name,)) for name in ("english", "english", "german")]
    for asker in askers:
        asker.start()
    for _ in range(500):
        if world.loaded:
            break
        threading.Event().wait(0.01)
    world.gate.set()                                                     # the load that was waited for fails...
    for asker in askers:
        asker.join(5)
    assert sorted(got) == [("english", "the file is damaged"), ("english", "the file is damaged"), ("german", "the file is damaged")]
    assert world.extras.loading() is None and world.extras.loaded() is None     # ...and nothing is left half taken
    world.broken = None
    assert world.extras.acquire("german")([0]) == "german:1"             # the next one who asks gets a load of its own


def test_a_model_whose_files_went_away_is_refused_and_leaves_memory_in_its_time(world):
    world.extras.acquire("english")
    world.extras.release("english")
    world.missing = {"english"}                                          # deleted from the disk while it is in memory
    with pytest.raises(NotInstalled):
        world.extras.acquire("english")
    assert world.extras.loaded() == "english" and world.extras.sweep() is None
    world.now += 600
    assert world.extras.sweep() == "english" and world.extras.loaded() is None
