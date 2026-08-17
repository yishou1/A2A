# xBD Damage Assessor

Frozen dual-entry xBD damage assessment service.

## Input modes

- `features`: `handcrafted_features` plus optional `cnn_embedding`
- `images`: `pre_image`, `post_image`, and required `polygon`

## Golden cases

- `case_001_request.json`: features mode
- `case_002_request.json`: images mode with polygon

## Model artifacts

- `models/xbd_damage_classifier.pkl`
- `models/xbd_damage_classifier.metadata.json`

Train locally with:

```powershell
python scripts/train_xbd_damage_classifier.py --feature-csv <path-to-xbd_damage_features_train.csv> --cnn-npz <path-to-xbd_cnn_embeddings_train.npz>
```
