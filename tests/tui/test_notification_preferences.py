"""Attention events are emitted at user-visible lifecycle boundaries."""

from __future__ import annotations

from src.core.preferences import Preferences


def test_should_allow_disabling_desktop_notifications_in_preferences() -> None:
    assert Preferences().desktop_notifications
    preferences = Preferences.from_dict({'desktop_notifications': False})
    assert not preferences.desktop_notifications
    assert preferences.to_dict()['desktop_notifications'] is False
