"""``/motion.js`` run for real under Node with a fake DOM and a fake Lottie engine.

What must hold: reduced motion never plays or loops (last frame, then still); loops pause when
the tab is hidden or the element leaves the screen; at most four animations play at once; a
second submit is refused while the first is in flight; and every failure (no library, failed
fetch, bad JSON, engine that throws, missing containers, unknown names) leaves the page alone
without throwing. Skipped, with the reason stated, when Node is not installed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from osa.core.settings import Settings
from osa.panel import app as panel

PORT = 8765

HARNESS = Path(__file__).with_name("motion_harness.js")


def _run(tmp_path: Path, scenario: str, config: dict[str, Any]) -> dict[str, Any]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js not installed: motion.js cannot be executed here")
    application = panel.create_app(session_factory=lambda: None, settings=Settings(), port=PORT)
    client = TestClient(
        application, base_url=f"http://127.0.0.1:{PORT}", client=("127.0.0.1", 50000)
    )
    script = tmp_path / "motion.js"
    script.write_text(client.get("/motion.js").text, encoding="utf-8")
    result = subprocess.run(  # noqa: S603  # fixed argv, local files only
        [node, str(HARNESS), str(script), scenario, json.dumps(config)],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    data: dict[str, Any] = json.loads(result.stdout)
    return data


ALL_HOSTS = [
    {"name": "check", "mode": "once"},
    {"name": "spinner", "mode": "loop"},
    {"kind": "theme"},
]
PLAYING = {"play", "goToAndPlay", "playSegments"}


def _verbs(anim: dict[str, Any]) -> list[str]:
    return [call[0] for call in anim["calls"]]


def test_reduced_motion_never_plays_or_loops_and_shows_the_last_frame(tmp_path: Path) -> None:
    run = _run(tmp_path, "plain", {"reduce": True, "hosts": ALL_HOSTS, "nav": True, "forms": True})

    assert run["errors"] == []
    by_name = {a["name"]: a for a in run["afterLoad"]}
    assert set(by_name) == {"check", "spinner", "theme"}  # the nav mounts nothing
    for anim in run["afterLoad"]:
        assert anim["loop"] is False and anim["autoplay"] is False
        assert not PLAYING & set(_verbs(anim)), anim["name"]
    for name in ("check", "spinner"):
        assert by_name[name]["calls"] == [["goToAndStop", 49, True]]
    assert by_name["theme"]["calls"] == [["goToAndStop", 0, True]]  # the resting sun


def test_reduced_motion_theme_switch_jumps_instead_of_playing(tmp_path: Path) -> None:
    run = _run(tmp_path, "theme", {"reduce": True, "hosts": [{"kind": "theme"}]})

    assert run["errors"] == []
    assert run["toDark"][-1] == ["goToAndStop", 24, True]
    assert run["toLight"][-1] == ["goToAndStop", 0, True]
    assert not any(call[0] in PLAYING for call in run["toLight"])


def test_reduced_motion_adds_no_wake(tmp_path: Path) -> None:
    run = _run(tmp_path, "nav", {"reduce": True, "nav": True})

    assert run["errors"] == []
    assert run["timers"] == 0 and run["anims"] == 0 and run["wake"] == []
    assert run["animsAfterHover"] == 0


def test_check_plays_once_without_looping(tmp_path: Path) -> None:
    run = _run(tmp_path, "plain", {"hosts": [{"name": "check", "mode": "once"}]})

    (anim,) = run["afterLoad"]
    assert anim["loop"] is False and anim["autoplay"] is False
    assert anim["calls"] == [["goToAndPlay", 0, True]]
    assert run["hostClasses"] == [["lottie-on"]]


def test_loops_wait_for_the_screen_pause_when_off_screen_and_when_the_tab_is_hidden(
    tmp_path: Path,
) -> None:
    hosts = [{"name": "spinner", "mode": "loop"}]
    run = _run(tmp_path, "visibility", {"hosts": hosts})

    assert run["errors"] == []
    assert run["afterLoad"][0]["loop"] is True
    assert run["afterLoad"][0]["calls"] == []  # nothing plays before it is on screen
    assert run["visible"] == [["play"]]
    assert run["offscreen"] == [["play", "pause"]]
    assert run["hidden"] == [["play", "pause", "play", "pause"]]
    assert run["back"] == [["play", "pause", "play", "pause", "play"]]


def test_a_page_opened_in_a_hidden_tab_plays_nothing(tmp_path: Path) -> None:
    hosts = [{"name": "check", "mode": "once"}, {"name": "spinner", "mode": "loop"}]
    run = _run(tmp_path, "plain", {"hosts": hosts, "hiddenAtStart": True})

    assert run["errors"] == []
    assert all(not PLAYING & set(_verbs(a)) for a in run["afterLoad"])


def test_no_intersection_observer_still_plays_loops_once(tmp_path: Path) -> None:
    run = _run(tmp_path, "plain", {"hosts": [{"name": "spinner", "mode": "loop"}], "io": False})

    assert run["errors"] == []
    assert run["afterLoad"][0]["calls"] == [["play"]]


def test_at_most_four_animations_play_at_once(tmp_path: Path) -> None:
    hosts = [{"name": "spinner", "mode": "loop"} for _ in range(8)]
    run = _run(tmp_path, "cap", {"hosts": hosts})

    assert run["errors"] == []
    assert run["started"] == 4


def test_theme_switch_plays_the_sun_moon_segments_in_both_directions(tmp_path: Path) -> None:
    run = _run(tmp_path, "theme", {"hosts": [{"kind": "theme"}]})

    assert run["errors"] == []
    assert run["toDark"][0] == ["goToAndStop", 0, True]  # starts on the sun
    assert run["toDark"][-1] == ["playSegments", [0, 24], True]
    assert run["toLight"][-1] == ["playSegments", [24, 48], True]
    assert run["noop"] == len(run["toLight"])  # same state again: nothing new


def test_submit_shows_a_spinner_and_refuses_the_second_submit(tmp_path: Path) -> None:
    run = _run(tmp_path, "submit", {"forms": True})

    assert run["errors"] == []
    assert run["firstPrevented"] is False  # the first POST goes through untouched
    assert run["busy"] == ["true", "true"]
    (spinner,) = run["spinner"]
    assert spinner["loop"] is True and spinner["calls"] == [["play"]]
    assert run["secondPrevented"] is True
    assert run["afterRestore"] == [None, None]  # back/forward cache restores an unlocked page
    assert run["spinnerDestroyed"] is True
    assert run["thirdPrevented"] is False
    assert run["timerCount"] >= 1  # the safety unlock exists


def test_reduced_motion_spinner_does_not_spin(tmp_path: Path) -> None:
    run = _run(tmp_path, "submit", {"forms": True, "reduce": True})

    assert run["errors"] == []
    (spinner,) = run["spinner"]
    assert spinner["loop"] is False and not PLAYING & set(_verbs(spinner))
    assert spinner["calls"] == [["goToAndStop", 49, True]]


def test_nav_wakes_the_current_section_and_never_mounts_a_glow(tmp_path: Path) -> None:
    run = _run(tmp_path, "nav", {"nav": True})

    assert run["errors"] == []
    # page open: the current link gets .wake (a CSS pop, scale only); no timer, no animation
    assert run["wake"] == ["wake"] and run["timers"] == 0 and run["anims"] == 0
    # hovering or focusing a menu item mounts nothing (the pop is CSS)
    assert run["navFocus"] == [] and run["animsAfterHover"] == 0


@pytest.mark.parametrize("mode", ["reject", "notok", "badjson"])
def test_a_failed_or_broken_file_leaves_the_static_markup_alone(tmp_path: Path, mode: str) -> None:
    run = _run(tmp_path, "plain", {"fetch": mode, "hosts": ALL_HOSTS, "forms": True})

    assert run["errors"] == []
    assert run["afterLoad"] == []
    assert all("lottie-on" not in classes for classes in run["hostClasses"])


def test_no_library_means_no_work_and_no_error(tmp_path: Path) -> None:
    run = _run(tmp_path, "plain", {"noLib": True, "hosts": ALL_HOSTS, "nav": True, "forms": True})

    assert run["errors"] == []
    assert run["fetched"] == [] and run["afterLoad"] == []
    # the row highlight needs no library: only its two document listeners are registered
    assert run["docListeners"] == ["input", "change"]


def test_an_engine_that_throws_is_survived_and_its_layer_removed(tmp_path: Path) -> None:
    run = _run(tmp_path, "plain", {"loadThrows": True, "hosts": ALL_HOSTS})

    assert run["errors"] == []
    assert all("lottie-on" not in classes for classes in run["hostClasses"])


def test_missing_containers_do_not_throw(tmp_path: Path) -> None:
    run = _run(tmp_path, "plain", {"hosts": [], "nav": False, "forms": False})

    assert run["errors"] == [] and run["afterLoad"] == []


@pytest.mark.parametrize(
    "name", ["evil", "breathe", "../../MANIFEST", "check/../../x", "CHECK", ""]
)
def test_a_data_attribute_cannot_pick_an_arbitrary_file_to_fetch(tmp_path: Path, name: str) -> None:
    run = _run(tmp_path, "plain", {"hosts": [{"name": name, "mode": "once"}]})

    assert run["errors"] == []
    assert run["fetched"] == [] and run["afterLoad"] == []


BRAND = {"brand": True}


def test_the_brand_is_idle_at_start_and_asks_for_no_frames(tmp_path: Path) -> None:
    run = _run(tmp_path, "brand-idle", BRAND)

    assert run["errors"] == []
    assert run["live"] is True and run["rafAtStart"] == 0  # nothing runs on its own
    assert {"pointerenter", "pointermove", "pointerleave", "pointerdown", "pointerup"} <= set(
        run["listeners"]
    )
    assert "pointercancel" in run["listeners"] and "focusin" in run["listeners"]


def test_the_brand_follows_the_mouse_within_limits_smoothly_and_settles_back(
    tmp_path: Path,
) -> None:
    run = _run(tmp_path, "brand-track", BRAND)

    assert run["errors"] == []
    assert run["queuedAfterManyMoves"] == 1  # fifty moves, one frame requested (rAF throttle)
    assert run["active"] is True
    # Top-left of the mark pulls up and left, bottom-right pulls down and right.
    assert run["tl"]["x"] < -2 and run["tl"]["y"] < -2 and run["tl"]["r"] < -4
    assert run["br"]["x"] > 2 and run["br"]["y"] > 2 and run["br"]["r"] > 4
    assert run["tl"]["k"] == pytest.approx(1.08, abs=0.01)  # light hover scale
    limits = run["limits"]
    assert limits["x"] <= 4 and limits["y"] <= 4  # a few px at most, overshoot included
    assert limits["r"] <= 8 and limits["kMax"] <= 1.12 and limits["kMin"] >= 0.99
    # No sudden jumps between consecutive frames (16.7 ms apart): continuous motion.
    steps = run["steps"]
    assert steps["x"] < 0.7 and steps["y"] < 0.7 and steps["r"] < 1.3 and steps["k"] < 0.02
    assert run["queuedWhileResting"] == 0  # resting under the pointer: no work
    # Leaving brings it back gradually, to exact rest, and then stops asking for frames.
    assert abs(run["backFirst"]["x"] - run["mid"]["x"]) < 0.7  # starts from where it was
    assert run["propsAfterRest"] == {} and run["activeAfterRest"] is False
    assert run["queuedAfterRest"] == 0
    assert run["usesOnlyCustomProps"] is True  # CSSOM custom properties, never a style attribute


def test_touch_and_pen_get_only_the_press_pop_never_tracking(tmp_path: Path) -> None:
    run = _run(tmp_path, "brand-touch", BRAND)

    assert run["errors"] == []
    for kind in ("touch", "pen"):
        assert run[f"queued_{kind}"] == 0  # entering/moving schedules nothing
        press = run[f"press_{kind}"]
        assert press["minK"] < 0.97  # it sinks on press
        assert press["x"] == press["y"] == press["r"] == 0  # and never moves toward a pointer
        assert press["propsLeft"] == 0 and press["queued"] == 0  # back at rest, loop stopped


def test_the_press_sinks_softly_and_springs_back_over_the_hover_scale(tmp_path: Path) -> None:
    run = _run(tmp_path, "brand-press", BRAND)

    assert run["errors"] == []
    assert run["hoverK"] == pytest.approx(1.08, abs=0.01)
    assert run["minK"] < 0.96  # the sink
    assert run["maxKAfterRelease"] > run["hoverK"]  # spring overshoot on the way back
    assert run["endK"] == pytest.approx(1.08, abs=0.01)  # still hovering: rests on hover scale
    assert run["steps"]["k"] < 0.03  # no snap on press or release


def test_keyboard_focus_pops_without_tracking_and_returns_to_rest(tmp_path: Path) -> None:
    run = _run(tmp_path, "brand-focus", {**BRAND, "focusVisible": True})

    assert run["errors"] == []
    assert run["maxK"] > 1.05 and run["maxK"] <= 1.14
    assert run["xyr"] == 0  # a pop only
    assert run["propsAfter"] == {} and run["queuedAfter"] == 0


def test_focus_that_is_not_keyboard_focus_does_nothing(tmp_path: Path) -> None:
    run = _run(tmp_path, "brand-focus", {**BRAND, "focusVisible": False})

    assert run["errors"] == []
    assert run["rows"] == 0 and run["propsAfter"] == {}


def test_reduced_motion_turns_the_brand_off_entirely(tmp_path: Path) -> None:
    run = _run(tmp_path, "brand-idle", {**BRAND, "reduce": True})

    assert run["errors"] == []
    assert run["live"] is False and run["listeners"] == [] and run["rafAtStart"] == 0


def test_without_request_animation_frame_the_brand_stays_plain(tmp_path: Path) -> None:
    run = _run(tmp_path, "brand-idle", {**BRAND, "noRaf": True})

    assert run["errors"] == []
    assert run["live"] is False and run["listeners"] == []


def test_the_brand_does_not_depend_on_the_lottie_library(tmp_path: Path) -> None:
    run = _run(tmp_path, "brand-idle", {**BRAND, "noLib": True})

    assert run["errors"] == [] and run["live"] is True and run["fetched"] == []


def test_the_pointer_is_measured_from_the_marks_resting_centre(tmp_path: Path) -> None:
    run = _run(tmp_path, "brand-centre", BRAND)

    assert run["errors"] == []
    assert run["x"] < 0.05 and run["y"] < 0.05 and run["r"] < 0.05  # dead centre: no lean
    # Half a deflection distance to the right leans about half of the maximum.
    assert run["half"]["x"] == pytest.approx(1.6, abs=0.15)
    assert run["half"]["r"] == pytest.approx(3.25, abs=0.3)


def test_a_press_dragged_off_the_brand_and_released_outside_leaves_it_at_rest(
    tmp_path: Path,
) -> None:
    run = _run(tmp_path, "brand-drag-out", BRAND)

    assert run["errors"] == []
    assert run["sunk"] < 0.97  # the press did sink the mark
    # Leaving with the button still down cancels the press: back to rest, loop stopped.
    assert run["leaveProps"] == {} and run["leaveActive"] is False and run["leaveQueued"] == 0
    # A cancel or a release anywhere on the page also ends the press.
    assert run["cancelProps"] == {} and run["cancelActive"] is False
    assert run["cancelQueued"] == 0
    assert run["upProps"] == {} and run["upQueued"] == 0
    assert {"pointercancel", "pointerup"} <= set(run["winEvents"])


def test_reduced_motion_switched_on_and_off_at_runtime_stops_then_resumes(
    tmp_path: Path,
) -> None:
    config = {**BRAND, "hosts": [{"name": "spinner", "mode": "loop"}], "nav": True}
    run = _run(tmp_path, "reduce-flip", config)

    assert run["errors"] == []
    assert run["start"] == ["play"] and run["liveStart"] is True
    # On: the loop is settled on its last frame, the brand goes plain and ignores the mouse.
    assert run["onLast"] == ["goToAndStop", 49, True]
    assert run["liveOn"] is False and run["queuedOn"] == 0
    # Off again: no reload needed; the loop plays again and the brand follows the mouse.
    assert run["off"] == ["play", "goToAndStop", "setLoop", "play"]
    assert run["liveOff"] is True and run["queuedOff"] == 1
    assert run["restAfter"] == {}
    assert run["media"] == 1  # one listener for the whole script, not one per feature


def test_reduced_motion_at_load_then_switched_off_starts_the_loop_and_the_brand(
    tmp_path: Path,
) -> None:
    config = {**BRAND, "hosts": [{"name": "spinner", "mode": "loop"}], "nav": True, "reduce": True}
    run = _run(tmp_path, "reduce-flip", config)

    assert run["errors"] == []
    assert run["start"] == ["goToAndStop"] and run["liveStart"] is False
    assert run["liveOn"] is False and run["queuedOn"] == 0
    assert run["off"] == ["goToAndStop", "goToAndStop", "setLoop", "play"]
    assert run["liveOff"] is True and run["queuedOff"] == 1
    assert run["wake"] == []  # the page-open wake belongs to page load only


def test_a_finished_animation_lets_a_waiting_one_start(tmp_path: Path) -> None:
    hosts = [{"name": "check", "mode": "once"} for _ in range(6)]
    run = _run(tmp_path, "cap-complete", {"hosts": hosts})

    assert run["errors"] == []
    assert run["before"] == 4  # the cap
    assert run["after"] == 5  # one completed: the next one in line starts at once


def test_a_form_without_a_submit_button_still_unlocks_after_the_safety_delay(
    tmp_path: Path,
) -> None:
    run = _run(tmp_path, "submit-nobutton", {"forms": True})

    assert run["errors"] == []
    assert run["busy"] == "true"
    assert run["timers"] >= 1  # the safety unlock exists even without a button
    assert run["afterTimer"] is None
    assert run["againPrevented"] is False


@pytest.mark.parametrize(
    ("banner", "search", "hash_", "expected"),
    [
        ("success", "?ok=dono-cadastrado&id=7", "", [[None, "", "/departments"]]),
        ("success", "?ok=departamento-salvo&id=3", "#lista", [[None, "", "/departments#lista"]]),
        ("success", "?ok=modelo-salvo", "", [[None, "", "/departments"]]),
        ("success", "?ok=departamento-salvo&id=3&aviso=pastas", "", [[None, "", "/departments"]]),
        ("info", "?aviso=pastas", "", [[None, "", "/departments"]]),
        ("success", "?ok=1", "", []),  # the old code is gone: left alone
        ("success", "?ok=dono-cadastrado&id=7&x=2", "", []),  # anything else in the query
        ("success", "?ok=dono-cadastrado&id=abc", "", []),
        ("success", "?ok=Dono-Cadastrado&id=7", "", []),
        ("success", "?ok=%3Cscript%3E&id=7", "", []),
        ("success", "?id=7&ok=dono-cadastrado", "", []),
        (None, "?ok=dono-cadastrado&id=7", "", []),  # no banner on the page: nothing to forget
        ("success", "", "", []),
    ],
)
def test_the_saved_banner_query_is_dropped_so_refresh_does_not_replay_it(
    tmp_path: Path, banner: str | None, search: str, hash_: str, expected: list[Any]
) -> None:
    config = {"banner": banner, "search": search, "hash": hash_}
    run = _run(tmp_path, "banner", config)

    assert run["errors"] == []
    assert run["history"] == expected


def test_the_banner_cleanup_survives_a_history_api_that_throws(tmp_path: Path) -> None:
    run = _run(
        tmp_path,
        "banner",
        {"banner": "success", "search": "?ok=modelo-salvo", "historyThrows": True},
    )

    assert run["errors"] == [] and run["history"] == []


def test_the_banner_cleanup_does_not_need_the_lottie_library(tmp_path: Path) -> None:
    run = _run(
        tmp_path, "banner", {"banner": "success", "search": "?ok=modelo-salvo", "noLib": True}
    )

    assert run["errors"] == [] and run["history"] == [[None, "", "/departments"]]


# --- the Save button of a row lights up only when its row changed -------------------------------

ROW_A = {
    "controls": [
        {"kind": "hidden"},
        {"kind": "text", "value": "Fiscal"},
        {"kind": "checkbox", "checked": False},
        {"kind": "select", "options": [{"selected": True}, {"selected": False}]},
        {"kind": "other"},
        {"kind": "save"},
    ]
}
ROW_B = {"controls": [{"kind": "text", "value": "Pessoal"}, {"kind": "save"}]}


def test_a_rows_save_button_is_highlighted_only_while_something_in_the_row_differs(
    tmp_path: Path,
) -> None:
    run = _run(tmp_path, "rows", {"rows": {"a": ROW_A, "b": ROW_B}})

    assert run["errors"] == []
    assert run["start"] == {"a": False, "b": False}  # nothing changed: nothing is highlighted
    assert run["afterText"] is True and run["otherRowUntouched"] is False
    assert run["afterRevert"] is False  # typing it back removes the highlight
    assert run["afterCheckbox"] is True
    assert run["afterSelect"] is True and run["afterSelectRevert"] is False
    assert run["afterRestore"] is True  # values the browser restored count on pageshow
    assert run["listeners"] == ["input", "change"] or set(run["listeners"]) == {"input", "change"}


def test_values_that_differ_at_load_are_highlighted_and_other_forms_are_ignored(
    tmp_path: Path,
) -> None:
    restored = {"controls": [{"kind": "text", "value": "Fiscal", "current": "Contábil"},
                             {"kind": "save"}]}  # fmt: skip
    plain = {"track": False, "controls": [{"kind": "text", "value": "x"}, {"kind": "save"}]}
    run = _run(tmp_path, "rows", {"rows": {"a": ROW_A, "b": restored, "plain": plain}})

    assert run["errors"] == []
    assert run["start"] == {"a": False, "b": True}  # a restored edit is highlighted at load
    assert run["plainDirty"] is False  # not a data-track row form


def test_rows_work_without_the_lottie_library_and_under_reduced_motion(tmp_path: Path) -> None:
    for config in ({"noLib": True}, {"reduce": True}):
        run = _run(tmp_path, "rows", {"rows": {"a": ROW_A, "b": ROW_B}, **config})

        assert run["errors"] == []
        assert run["afterText"] is True and run["afterRevert"] is False
