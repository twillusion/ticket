import json

from ticket.jsonscan import json_in_script


def test_assignment_and_json_parse_string_and_push_payload():
    state = {"grid": {"items": [{"listingId": 1, "section": "16", "row": "2", "price": 10}]}, "pad": "x" * 600}
    js = f"window.__STATE__ = {json.dumps(state)};\nfoo(1);"
    assert state in json_in_script(js)

    inner = json.dumps(state)
    js2 = f"self.__next_f.push([1, {json.dumps('3:' + inner)}])"
    assert state in json_in_script(js2)


def test_ignores_non_json_js():
    assert json_in_script("var a = {b: 1, c: function(){ return [1,2] }};" * 50) == []
