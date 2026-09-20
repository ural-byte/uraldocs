from app.config import Settings


def test_database_url_escapes_password():
    config = Settings(database_url="", postgres_user="uraldocs", postgres_db="uraldocs", postgres_password="password@with/slash")
    assert config.database_url == "postgresql+psycopg://uraldocs:password%40with%2Fslash@db:5432/uraldocs"
