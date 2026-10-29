import pytest
import requests


def test_json_dirty_json_raises_jsondecodeerror(client):
    resp = client.get_daily("101010100", scenario="dirty_json")
    assert resp.status_code == 200
    with pytest.raises(requests.exceptions.JSONDecodeError):
        resp.json()


def test_code_204_without_data(client) -> None:
    resp = client.lookup_city("北京", scenario="code204")
    assert resp.status_code == 200
    assert resp.json() == {"code": "204"}
    assert "location" not in resp.json()
