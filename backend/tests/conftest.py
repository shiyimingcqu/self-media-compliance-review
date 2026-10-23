import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    application = create_app(Settings(data_dir=tmp_path))
    with TestClient(application) as test_client:
        yield test_client
