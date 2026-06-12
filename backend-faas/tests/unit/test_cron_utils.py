"""Unit tests for shared utility functions and ML model instantiation.

Ported/adapted from backend/tests/unit/test_cron_utils.py and
backend/tests/unit/test_models.py.  The FaaS codebase does not have
``classify_error`` — instead we test the shared utils and ML model
constructors.
"""


from packages.shared.models_ml.elo import EloModel
from packages.shared.models_ml.form import FormModel
from packages.shared.models_ml.home_advantage import HomeAdvantageModel
from packages.shared.models_ml.value import ValueModel
from packages.shared.utils import generate_slug

# ---------------------------------------------------------------------------
# generate_slug
# ---------------------------------------------------------------------------

class TestGenerateSlug:
    def test_default_length(self):
        slug = generate_slug()
        assert len(slug) == 10

    def test_custom_length(self):
        slug = generate_slug(length=20)
        assert len(slug) == 20

    def test_alphanumeric_characters_only(self):
        slug = generate_slug(length=100)
        assert slug.isalnum()

    def test_lowercase_only(self):
        slug = generate_slug(length=100)
        assert slug == slug.lower()

    def test_uniqueness(self):
        """Two generated slugs should differ (extremely high probability)."""
        slugs = {generate_slug() for _ in range(50)}
        assert len(slugs) == 50

    def test_length_one(self):
        slug = generate_slug(length=1)
        assert len(slug) == 1


# ---------------------------------------------------------------------------
# ML Model instantiation tests (ported from backend/tests/unit/test_models.py)
# ---------------------------------------------------------------------------

class TestEloModel:
    def test_elo_model_exists(self):
        model = EloModel()
        assert model is not None

    def test_elo_get_name(self):
        model = EloModel()
        assert model.get_name() == "elo"

    def test_elo_default_k_factor(self):
        model = EloModel()
        assert model.k_factor == 32.0

    def test_elo_default_home_advantage(self):
        model = EloModel()
        assert model.home_advantage == 50.0

    def test_elo_custom_params(self):
        model = EloModel(k_factor=20.0, home_advantage=30.0)
        assert model.k_factor == 20.0
        assert model.home_advantage == 30.0

    def test_elo_instance_ratings_empty(self):
        model = EloModel()
        assert model.ratings == {}


class TestFormModel:
    def test_form_model_exists(self):
        model = FormModel()
        assert model is not None

    def test_form_get_name(self):
        model = FormModel()
        assert model.get_name() == "form"

    def test_form_default_games_to_consider(self):
        model = FormModel()
        assert model.games_to_consider == 5

    def test_form_custom_games_to_consider(self):
        model = FormModel(games_to_consider=10)
        assert model.games_to_consider == 10


class TestHomeAdvantageModel:
    def test_home_advantage_model_exists(self):
        model = HomeAdvantageModel()
        assert model is not None

    def test_home_advantage_get_name(self):
        model = HomeAdvantageModel()
        assert model.get_name() == "home_advantage"

    def test_home_advantage_initial_state(self):
        model = HomeAdvantageModel()
        assert model.home_win_rate == {}
        assert model.overall_home_advantage == 0.0


class TestValueModel:
    def test_value_model_exists(self):
        model = ValueModel()
        assert model is not None

    def test_value_get_name(self):
        model = ValueModel()
        assert model.get_name() == "value"

    def test_value_initial_state(self):
        model = ValueModel()
        assert model.team_win_rates == {}
