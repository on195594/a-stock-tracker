"""Scroll orchestration regressions; real canvas coverage remains test-mobile."""

from types import SimpleNamespace

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from tests import test_mobile


class ScrollPage:
    def __init__(self, y, consumed=256, *, editable=False):
        self.viewport_size = {"width": 360, "height": 844}
        self.url = "http://127.0.0.1/synthetic"
        self.y, self.consumed = y, consumed
        self.clock = 0.0
        self.wheels = []
        self.clicked = False
        self.editable = editable
        self.focus_waits = []
        self.mouse = SimpleNamespace(move=lambda *args: None, wheel=self.wheel)

    def wheel(self, x, delta):
        self.wheels.append(delta)
        self.y -= min(abs(delta), self.consumed) * (1 if delta > 0 else -1)

    def wait_for_function(self, script, *, arg, timeout):
        assert "250" in script
        assert 0 < timeout <= 5000
        if "_testFocusedSince" in script:
            assert self.clicked and self.editable and arg is self
            self.focus_waits.append(timeout)
        self.clock += 0.25

    def wait_for(self, **kwargs):
        assert 0 < kwargs["timeout"] <= 5000

    def bounding_box(self):
        return {"x": 20, "y": self.y, "width": 320, "height": 48}

    def element_handle(self, *, timeout):
        assert 0 < timeout <= 5000
        return self

    def evaluate(self, script):
        if "el.matches" in script:
            return self.editable
        return True

    def click(self, *, timeout):
        assert 0 < timeout <= 5000
        assert 0 <= self.y and self.y + 48 <= 844
        self.clicked = True


@pytest.mark.parametrize("y", [1245, -900])
def test_partial_wheel_consumption_remeasures_before_click(monkeypatch, y):
    page = ScrollPage(y)
    monkeypatch.setattr(test_mobile.time, "monotonic", lambda: page.clock)
    test_mobile.click_settled(page, page)
    assert page.clicked and len(page.wheels) >= 2
    assert page.clock <= 5


def test_visible_control_is_not_scrolled_away(monkeypatch):
    page = ScrollPage(112)
    monkeypatch.setattr(test_mobile.time, "monotonic", lambda: page.clock)
    test_mobile.click_settled(page, page)
    assert page.clicked and not page.wheels


def test_unreachable_control_still_fails_with_original_time_budget(monkeypatch):
    page = ScrollPage(1245, consumed=0)
    monkeypatch.setattr(test_mobile.time, "monotonic", lambda: page.clock)
    with pytest.raises(PlaywrightTimeoutError, match="5000ms"):
        test_mobile.click_settled(page, page)
    assert not page.clicked and page.clock == 5


def test_click_uses_the_element_whose_geometry_was_stabilized(monkeypatch):
    page = ScrollPage(112)
    monkeypatch.setattr(test_mobile.time, "monotonic", lambda: page.clock)
    # A locator could resolve a replacement after the stability check. It must
    # never provide the final geometry or click a different, unmeasured node.
    locator = SimpleNamespace(
        wait_for=page.wait_for,
        element_handle=page.element_handle,
        bounding_box=lambda: pytest.fail("re-resolved geometry"),
        click=lambda: pytest.fail("re-resolved click"),
    )
    test_mobile.click_settled(page, locator)
    assert page.clicked


def test_locator_lookup_receives_only_remaining_budget(monkeypatch):
    page = ScrollPage(112)
    monkeypatch.setattr(test_mobile.time, "monotonic", lambda: page.clock)

    def visibility(**kwargs):
        assert kwargs["timeout"] == 5000
        page.clock = 4.5

    def lookup(*, timeout):
        assert timeout == 500
        return page

    locator = SimpleNamespace(wait_for=visibility, element_handle=lookup)
    test_mobile.click_settled(page, locator)
    assert page.clicked and page.clock == 4.75


def test_editable_focus_settles_after_click_with_remaining_budget(monkeypatch):
    page = ScrollPage(112, editable=True)
    monkeypatch.setattr(test_mobile.time, "monotonic", lambda: page.clock)

    def visibility(**kwargs):
        page.clock = 4.5

    locator = SimpleNamespace(wait_for=visibility, element_handle=page.element_handle)
    test_mobile.click_settled(page, locator)
    assert page.clicked and page.focus_waits == [250]
    assert page.clock == 5


def test_editable_focus_failure_does_not_extend_click_budget(monkeypatch):
    page = ScrollPage(112, editable=True)
    monkeypatch.setattr(test_mobile.time, "monotonic", lambda: page.clock)
    original_wait = page.wait_for_function

    def fail_focus(script, *, arg, timeout):
        if "_testFocusedSince" in script:
            assert page.clicked and timeout == 4750
            page.clock += timeout / 1000
            raise PlaywrightTimeoutError("Editing focus did not settle")
        original_wait(script, arg=arg, timeout=timeout)

    monkeypatch.setattr(page, "wait_for_function", fail_focus)
    with pytest.raises(PlaywrightTimeoutError, match="Editing focus"):
        test_mobile.click_settled(page, page)
    assert page.clock == 5


@pytest.mark.parametrize("y,valid", [(874, True), (100, False)])
def test_touch_target_measurement_distinguishes_viewport_clipping(y, valid):
    button = SimpleNamespace(bounding_box=lambda: {"x": 280, "y": y, "width": 720, "height": 26})
    page = SimpleNamespace(
        viewport_size={"width": 1280, "height": 900},
        evaluate=lambda _: True,
        get_by_role=lambda role: SimpleNamespace(all=lambda: [button] if role == "button" else []),
    )
    if valid:
        test_mobile.assert_layout(page, 1280)
    else:
        with pytest.raises(AssertionError):
            test_mobile.assert_layout(page, 1280)
