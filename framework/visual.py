"""Playwright screenshot baselines and pixel comparison."""
import os
import re

from PIL import Image, ImageChops

from framework.constants import REPORTS_DIR, VISUAL_DIFF_PIXEL_RATIO


def _safe_name(value):
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-") or "checkpoint"


def capture_checkpoint(page, test_name, checkpoint, run_id, full_page=False, update_baseline=False):
    checkpoint_name = _safe_name(checkpoint)
    test_name = _safe_name(test_name)
    baseline_dir = os.path.join(REPORTS_DIR, "baselines", test_name)
    actual_dir = os.path.join(REPORTS_DIR, "actual", run_id)
    diff_dir = os.path.join(REPORTS_DIR, "diffs", run_id)
    for directory in (baseline_dir, actual_dir, diff_dir):
        os.makedirs(directory, exist_ok=True)

    baseline_path = os.path.join(baseline_dir, f"{checkpoint_name}.png")
    actual_path = os.path.join(actual_dir, f"{checkpoint_name}.png")
    diff_path = os.path.join(diff_dir, f"{checkpoint_name}.png")
    page.screenshot(path=actual_path, full_page=full_page)

    if update_baseline or not os.path.exists(baseline_path):
        with open(actual_path, "rb") as source, open(baseline_path, "wb") as target:
            target.write(source.read())
        return {"status": "BASELINE_CREATED", "checkpoint": checkpoint, "baseline": baseline_path, "actual": actual_path}

    baseline = Image.open(baseline_path).convert("RGBA")
    actual = Image.open(actual_path).convert("RGBA")
    if baseline.size != actual.size:
        return {
            "status": "DIFF",
            "checkpoint": checkpoint,
            "baseline": baseline_path,
            "actual": actual_path,
            "diff": None,
            "reason": "image_size_changed",
            "diff_ratio": 1.0,
        }

    difference = ImageChops.difference(baseline, actual)
    pixels = list(difference.getdata())
    changed = sum(1 for pixel in pixels if any(channel > 0 for channel in pixel))
    diff_ratio = changed / max(1, len(pixels))
    if changed:
        difference.save(diff_path)
    return {
        "status": "PASS" if diff_ratio <= VISUAL_DIFF_PIXEL_RATIO else "DIFF",
        "checkpoint": checkpoint,
        "baseline": baseline_path,
        "actual": actual_path,
        "diff": diff_path if changed else None,
        "diff_ratio": round(diff_ratio, 6),
        "threshold": VISUAL_DIFF_PIXEL_RATIO,
    }
