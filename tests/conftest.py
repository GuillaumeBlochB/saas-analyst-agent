import pytest
from fastapi.testclient import TestClient

from saas_analyst.api import create_app
from saas_analyst.dataset import generate


@pytest.fixture(scope="session")
def dataset():
    return generate()


@pytest.fixture(scope="session")
def http(dataset):
    with TestClient(create_app(dataset)) as client:
        yield client
