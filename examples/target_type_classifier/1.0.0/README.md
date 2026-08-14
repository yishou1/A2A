# Target Type Classifier

Structured JSON algorithm package for target type completion and confidence scoring.

This package is part of the Track Threat Agent algorithm group and maps to **M04 特征编码与分类** in the integration guide.

It uses structured detection/track fields such as declared `object_type`, `speed`, `alt`, and `confidence` to produce normalized type scores for `aircraft`, `ship`, `uav`, and `unknown`.

It does not perform raw image/radar classification, friend-or-foe identification, KG/RAG reasoning, weapon control, or engagement decision-making.
