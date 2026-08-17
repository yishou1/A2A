# Track State Updater

Multi-target tracking and localization algorithm package for **M05 多目标跟踪与定位**.

This package associates structured detections with existing tracks using nearest-neighbor association and updates track state, quality, timestamp, and bounded `history_path`.

It is intended for demo-level track maintenance inside the Track Threat Agent boundary. It does not replace production radar/EO/IR sensor tracking, raw signal processing, weapon control, or engagement decision-making.
