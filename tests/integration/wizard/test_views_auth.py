from __future__ import annotations

import pytest
from django.urls import reverse


class TestLoginLogout:
    @pytest.mark.django_db(transaction=True)
    def test_login_page_renders(self, client):
        resp = client.get(reverse("login"))
        assert resp.status_code == 200
        assert b"Sign in" in resp.content

    @pytest.mark.django_db(transaction=True)
    def test_login_invalid_credentials_shows_error(self, client):
        resp = client.post(reverse("login"), {"username": "nobody", "password": "wrong"})
        assert resp.status_code == 200
        assert b"Invalid username or password" in resp.content

    @pytest.mark.django_db(transaction=True)
    def test_authenticated_user_redirected_from_login(self, logged_in_client):
        resp = logged_in_client.get(reverse("login"))
        assert resp.status_code == 302
        assert resp.url == reverse("home")
