import pytest

from app.evaluation.metrics import mean_reciprocal_rank, recall_at_k, reciprocal_rank


def test_recall_at_k_uses_unique_relevant_evidence():
    assert recall_at_k(["a", "a", "b"], {"a", "b", "c"}, 3) == pytest.approx(2 / 3)
    assert recall_at_k(["x", "a"], {"a"}, 1) == 0.0


def test_reciprocal_rank_and_mean():
    assert reciprocal_rank(["x", "a", "b"], {"a", "b"}) == 0.5
    assert reciprocal_rank(["x"], {"a"}) == 0.0
    assert mean_reciprocal_rank([(["a"], {"a"}), (["x", "b"], {"b"})]) == 0.75


@pytest.mark.parametrize(
    ("function", "args"),
    [
        (recall_at_k, (["a"], {"a"}, 0)),
        (recall_at_k, (["a"], set(), 1)),
        (reciprocal_rank, (["a"], set())),
        (mean_reciprocal_rank, ([] ,)),
    ],
)
def test_metrics_reject_invalid_denominators(function, args):
    with pytest.raises(ValueError):
        function(*args)
