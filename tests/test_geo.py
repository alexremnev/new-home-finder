from __future__ import annotations

import json
import urllib.error
from typing import Any

from worker.sources.geo import BATCH, Ask, nearest


def reply(*found: tuple[Ask, str | None]) -> dict[str, Any]:
    """A postcodes.io bulk reply for these asks, in this order."""

    return {
        "status": 200,
        "result": [
            {
                "query": {"latitude": ask.lat, "longitude": ask.lng},
                "result": (
                    None if postcode is None
                    else [{"postcode": postcode, "outcode": postcode.split(" ")[0],
                           "distance": 40.0}]
                ),
            }
            for ask, postcode in found
        ],
    }


class Service:
    """A stand-in for the API that records what it was asked."""

    def __init__(self, *replies: dict[str, Any]) -> None:
        self.replies = list(replies)
        self.sent: list[list[dict[str, Any]]] = []

    def __call__(self, url: str, body: bytes) -> dict[str, Any]:
        self.sent.append(json.loads(body)["geolocations"])
        return self.replies.pop(0) if self.replies else {"result": []}


def test_a_coordinate_becomes_the_postcode_it_sits_in() -> None:
    asks = [
        Ask("a", 51.5055, -0.0235, "E14"),
        Ask("b", 51.4934, -0.0536, "SE16"),
    ]
    service = Service(reply((asks[0], "E14 4AP"), (asks[1], "SE16 2FB")))

    assert nearest(asks, post=service) == {"a": "E14 4AP", "b": "SE16 2FB"}
    # One request for both, which is the whole reason this is in bulk.
    assert len(service.sent) == 1


def test_a_postcode_from_the_wrong_district_is_refused() -> None:
    # A fifth of London points have a second outcode within 250m and portals
    # round the coordinates of a rental, so this is the common failure and not
    # an exotic one. A neighbour's postcode in an alert reads as fact.
    asks = [Ask("a", 51.5074, -0.1278, "E14")]
    service = Service(reply((asks[0], "WC2N 5DU")))

    assert nearest(asks, post=service) == {}


def test_a_point_with_nothing_near_it_is_simply_absent() -> None:
    asks = [Ask("a", 51.0, 1.9, None)]
    service = Service(reply((asks[0], None)))

    assert nearest(asks, post=service) == {}


def test_the_answer_is_paired_by_coordinate_not_by_position() -> None:
    # The service answers in order, but the pairing is read from each entry's
    # own `query`: assuming the index would put one listing's postcode on
    # another listing the day that stops being true.
    asks = [Ask("a", 51.5055, -0.0235, "E14"), Ask("b", 51.4934, -0.0536, "SE16")]
    service = Service(reply((asks[1], "SE16 2FB"), (asks[0], "E14 4AP")))

    assert nearest(asks, post=service) == {"a": "E14 4AP", "b": "SE16 2FB"}


def test_more_than_one_batch_is_more_than_one_request() -> None:
    asks = [Ask(str(n), 51.5, -0.1, None) for n in range(BATCH + 20)]
    service = Service({"result": []}, {"result": []})

    nearest(asks, post=service)

    assert [len(sent) for sent in service.sent] == [BATCH, 20]


def test_a_failed_batch_loses_only_itself() -> None:
    asks = [Ask(str(n), 51.5, -0.1, None) for n in range(BATCH + 1)]
    last = asks[BATCH]

    def service(url: str, body: bytes) -> dict[str, Any]:
        sent = json.loads(body)["geolocations"]
        if len(sent) == BATCH:
            raise urllib.error.URLError("down")
        return reply((last, "E14 4AP"))

    # The first hundred are lost and the one after them is not: a postcode
    # nobody could look up leaves the listing exactly as it was, which is the
    # state this is an improvement on rather than a fault to raise.
    assert nearest(asks, post=service) == {str(BATCH): "E14 4AP"}


def test_a_reply_that_makes_no_sense_is_not_a_postcode() -> None:
    asks = [Ask("a", 51.5055, -0.0235, "E14")]
    service = Service({"result": [{"query": {"latitude": 51.5055, "longitude": -0.0235},
                                   "result": [{"postcode": "", "outcode": "nonsense"}]}]})

    assert nearest(asks, post=service) == {}
