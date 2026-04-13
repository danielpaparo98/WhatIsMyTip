import pytest
from app.models_ml.elo import EloModel
from app.models_ml.form import FormModel
from app.models_ml.home_advantage import HomeAdvantageModel
from app.models_ml.value import ValueModel


class TestEloModel:
    def test_elo_model_exists(self):
        model = EloModel()
        assert model is not None

    def test_elo_get_name(self):
        model = EloModel()
        assert model.get_name() == "Elo"

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
        assert model.get_name() == "Form"

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
        assert model.get_name() == "HomeAdvantage"

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
        assert model.get_name() == "Value"

    def test_value_initial_state(self):
        model = ValueModel()
        assert model.team_win_rates == {}
