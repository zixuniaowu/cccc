import re
from playwright.sync_api import Page, expect

def test_homepage_loads(page: Page):
    page.goto("http://localhost:8080")
    expect(page).to_have_title(re.compile("Solar System Simulation"))
    page.screenshot(path="/home/zixuniaowu/cccc/.cccc/work/solar-system-sim/screenshot.png")
