from resolvelab.cache import CachedModel
from resolvelab.models import ModelReply


class Counting:
    name = "counting"

    def __init__(self):
        self.calls = 0

    def generate(self, system, messages, tools):
        self.calls += 1
        return ModelReply(text="hello", input_tokens=3, output_tokens=2)


def test_second_identical_call_is_served_from_disk(tmp_path):
    inner = Counting()
    model = CachedModel(inner, tmp_path)
    msgs = [{"role": "user", "content": "hi"}]
    first = model.generate("sys", msgs, [])
    second = model.generate("sys", msgs, [])
    assert first.text == second.text == "hello"
    assert inner.calls == 1 and (model.hits, model.misses) == (1, 1)


def test_different_input_is_a_new_call(tmp_path):
    inner = Counting()
    model = CachedModel(inner, tmp_path)
    model.generate("sys", [{"role": "user", "content": "a"}], [])
    model.generate("sys", [{"role": "user", "content": "b"}], [])
    assert inner.calls == 2
