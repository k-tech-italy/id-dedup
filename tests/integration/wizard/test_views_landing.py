from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.urls import reverse


class TestLandingPage:
    @pytest.mark.django_db(transaction=True)
    def test_anonymous_gets_about_page(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"Face image deduplication" in resp.content
        assert b"Login" in resp.content

    @pytest.mark.django_db(transaction=True)
    def test_authenticated_gets_dashboard(self, logged_in_client):
        resp = logged_in_client.get("/")
        assert resp.status_code == 200
        assert b"Welcome, testuser" in resp.content
        assert b"New Upload" in resp.content

    @pytest.mark.django_db(transaction=True)
    def test_dashboard_has_logout_form(self, logged_in_client):
        resp = logged_in_client.get("/")
        assert resp.status_code == 200
        assert b"logout" in resp.content
        assert b"csrfmiddlewaretoken" in resp.content

    @pytest.mark.django_db(transaction=True)
    def test_logout_redirects_to_landing_then_shows_about(self, client):
        User.objects.create_user(username="testuser", password="testpass123")
        client.login(username="testuser", password="testpass123")
        resp = client.post(reverse("logout"))
        assert resp.status_code == 302
        assert resp.url == "/"

        resp = client.get("/")
        assert resp.status_code == 200
        assert b"Face image deduplication" in resp.content
        assert b"Login" in resp.content
