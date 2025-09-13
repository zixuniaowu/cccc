import re
from playwright.sync_api import Page, expect

def test_detailed_functionality(page: Page):
    page.goto("http://localhost:8080")

    # Test play/pause
    play_pause_button = page.locator("#playPause")
    expect(play_pause_button).to_have_text("▶️ Play")
    play_pause_button.click()
    expect(play_pause_button).to_have_text("⏸️ Pause")
    play_pause_button.click()
    expect(play_pause_button).to_have_text("▶️ Play")

    # Test speed control
    speed_control = page.locator("#speedControl")
    speed_control.evaluate("e => e.value = 5")

    # Test zoom control
    zoom_control = page.locator("#zoomControl")
    zoom_control.evaluate("e => e.value = 2")

    page.screenshot(path="/home/zixuniaowu/cccc/.cccc/work/solar-system-sim/controls_test.png")

    # Test planet info
    # Get canvas dimensions
    canvas = page.locator("#solarSystem")
    box = canvas.bounding_box()
    center_x = box['x'] + box['width'] / 2
    center_y = box['y'] + box['height'] / 2

    # Earth's initial position (angle=0)
    earth_distance = 100 # from planets.js
    earth_x = center_x + earth_distance
    earth_y = center_y

    # Click on Earth
    page.mouse.click(earth_x, earth_y)

    # Check if info is displayed
    info_display = page.locator("#planetInfo")
    expect(info_display).to_be_visible()
    expect(info_display).to_contain_text("Earth")

    page.screenshot(path="/home/zixuniaowu/cccc/.cccc/work/solar-system-sim/planet_info_test.png")
